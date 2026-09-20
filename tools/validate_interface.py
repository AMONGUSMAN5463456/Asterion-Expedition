"""Render real panel models without generating a planet or opening a save.

Usage: .venv/bin/python tools/validate_interface.py --size 1280 720
The empty scene intentionally isolates font rasterization, clipping and spacing.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, nargs=2, default=(1280, 720))
    parser.add_argument("--renderer", choices=("tiny", "gl"), default="tiny")
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "validation-v1.4" / "ui")
    args = parser.parse_args()
    from panda3d.core import loadPrcFileData
    loadPrcFileData("interface-visual-validation", "\n".join((
        "load-display " + ("pandagl" if args.renderer == "gl" else "p3tinydisplay"),
        "window-type offscreen", f"win-size {args.size[0]} {args.size[1]}",
        "audio-library-name null", "sync-video false", "notify-level warning",
        "model-cache-dir", "framebuffer-multisample true", "multisamples 4",
        "gl-version 3 2", "texture-anisotropic-degree 8",
        "textures-power-2 up" if args.renderer == "tiny" else "textures-power-2 none",
        "framebuffer-srgb false", "text-encoding utf8",
    )))
    from direct.showbase.ShowBase import ShowBase
    from asterion.app import ExpeditionApp
    from asterion.state import GameState
    from asterion.ui import GameUI
    from asterion.universe import generate_system

    app = ShowBase(windowType="offscreen")
    app.setBackgroundColor(.025, .046, .063)
    ui = GameUI(app, lambda *_: None)
    controller = SimpleNamespace(mode="orbit", position=(0, 0, 0),
                                 set_enabled=lambda *_: None, reset_keys=lambda: None)
    context = SimpleNamespace(
        game=GameState(), system=generate_system(0), controller=controller,
        started=True, transition=False, _clear_beam=lambda: None, ui=ui,
        button=ExpeditionApp.button, save_path=Path("/nonexistent/asterion-ui-validation.json"),
        world=SimpleNamespace(interactables=lambda: []),
    )
    context.planet = context.system["planets"][0]
    context._tabs = MethodType(ExpeditionApp._tabs, context)
    context.game.credits = 12480
    context.game.story_stage = 1
    args.output.mkdir(parents=True, exist_ok=True)
    audits = []

    def record_ancestor(path):
        ancestor = path.getParent()
        while not ancestor.isEmpty() and ancestor != ui.root:
            if ancestor.getName().startswith("terminal-record-"):
                return ancestor
            ancestor = ancestor.getParent()
        return None

    def audit_layout():
        """Check native text bounds, including rows outside the scroll viewport."""
        checked = 0
        for path in ui.root.findAllMatches("**/+TextNode"):
            if path.isHidden() or not path.node().getText():
                continue
            row = record_ancestor(path)
            if row is not None:
                low, high = path.getTightBounds(row)
                frame_low, frame_high = row.find("interface-gradient").getTightBounds(row)
                valid = (low.x >= frame_low.x - .1 and high.x <= frame_high.x + .1 and
                         low.z >= frame_low.z - .1 and high.z <= frame_high.z + .1)
            else:
                low, high = path.getTightBounds(ui.root)
                valid = (low.x >= -.1 and high.x <= ui.width + .1 and
                         low.z >= -ui.height - .1 and high.z <= .1)
            if not valid:
                raise AssertionError(f"Text overflow: {path.node().getText()!r}, {low}, {high}")
            checked += 1
        return checked

    def capture(name):
        checked = audit_layout()
        for _ in range(3):
            app.graphicsEngine.renderFrame()
        path = args.output / f"{name}-{args.size[0]}x{args.size[1]}.png"
        app.win.getScreenshot().write(str(path))
        audits.append({"screen": name, "checked_text_nodes": checked, "image": path.name})
        print(path)

    try:
        ui.show_title(False)
        capture("title")
        ui.show_title(True)
        capture("title-continue")
        for panel in ("inventory", "craft", "map", "galaxy", "journal", "discoveries", "contracts", "build", "trade", "settings", "help", "pause", "rescue", "reload", "new_confirm"):
            ExpeditionApp.open_panel(context, panel)
            capture(panel)
        ui.hide_panel()
        view = dict(location="Velorum Prime", biome="Verdant", system_name="ASTERION SYSTEM",
                    credits=12480, cargo=143, capacity=240, heading=315,
                    coordinates=(1234, -9821, 30), speed=7.2, altitude=31,
                    target="Ancient navigation terminal", prompt="E / Activate terminal",
                    objective=dict(title="Follow the ancient signal",
                                   description="Find a ruin and activate its terminal to decode the next star chart.",
                                   progress="1 / 3 discoveries", fraction=.33),
                    vitals=dict(oxygen=73, hazard=91, energy=54, shield=100, fuel=38),
                    notice="Discovery recorded. A new world awaits.")
        for mode in ("surface", "flight", "orbit"):
            view.update(mode=mode, grounded=mode == "surface", vertical_speed=-14.5,
                        pitch=30, flight_assist=True, throttle=.4,
                        speed=7.2 if mode == "surface" else 1040, altitude=31 if mode == "surface" else 48210)
            ui.update(view)
            capture("hud-" + mode)
        report = {"size": args.size, "renderer": app.win.getGsg().getDriverRenderer(),
                  "screens": audits, "passed": True}
        (args.output / f"validation-{args.size[0]}x{args.size[1]}.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
    finally:
        ui.destroy()
        app.destroy()


if __name__ == "__main__":
    main()
