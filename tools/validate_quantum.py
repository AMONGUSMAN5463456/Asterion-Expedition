"""Capture the real quantum journey with temporary saves and either renderer."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, nargs=2, default=(1280, 720))
    parser.add_argument("--renderer", choices=("tiny", "gl"), default="tiny")
    parser.add_argument("--output", type=Path, default=ROOT / "docs/validation-quantum")
    args = parser.parse_args()
    from panda3d.core import loadPrcFileData
    loadPrcFileData("quantum-validation", "\n".join((
        "load-display " + ("pandagl" if args.renderer == "gl" else "p3tinydisplay"),
        "window-type offscreen", f"win-size {args.size[0]} {args.size[1]}",
        "audio-library-name null", "sync-video false", "notify-level error", "model-cache-dir",
        "gl-version 3 2", "framebuffer-srgb false",
        "textures-power-2 up" if args.renderer == "tiny" else "textures-power-2 none",
    )))
    from asterion.app import ExpeditionApp
    args.output.mkdir(parents=True, exist_ok=True)
    captures = []
    suffix = f"{args.renderer}-{args.size[0]}x{args.size[1]}"
    with tempfile.TemporaryDirectory(prefix="asterion-quantum-validation-") as saves:
        app = ExpeditionApp(save_dir=saves, offscreen=True, no_audio=True)
        try:
            app.game.settings.update(quality="low", camera_motion=0)
            app.new_game()
            app.enter_orbit()

            def capture(label):
                app.ui.update(app._view())
                for _ in range(3):
                    app.graphicsEngine.renderFrame()
                image = args.output / f"{label}-{suffix}.png"
                assert app.win.getScreenshot().write(str(image))
                captures.append(image.name)

            app.open_panel("map")
            capture("navigation")
            assert app.select_quantum_target(1)
            assert app.quantum_action()
            for _ in range(12):
                app.step(.1)
            capture("spooling")
            for _ in range(20):
                app.step(.1)
            assert app.quantum.phase == "ready", app.notice
            capture("ready")
            fuel_before = app.game.vitals["fuel"]
            cost = app.quantum.cost
            route_metres = app.quantum.total_distance
            duration = app.quantum.duration
            assert app.quantum_action()
            for _ in range(20):
                app.step(.1)
            assert app.quantum.travelling, app.notice
            capture("transit")
            for _ in range(500):
                if not app.quantum.travelling:
                    break
                app.step(.1)
            assert app.quantum.phase == "cooldown", app.notice
            assert app.game.planet_index == 1
            assert abs(app.atmosphere_data["altitude"] - 3500) < .15
            assert app.controller.speed == 0
            assert abs(fuel_before - app.game.vitals["fuel"] - cost) < .0001
            capture("arrival")
            report = dict(status="passed", renderer=args.renderer, size=args.size,
                          route_metres=round(route_metres), transit_seconds=round(duration, 2),
                          fuel=round(cost, 2), arrival_altitude=round(app.atmosphere_data["altitude"], 2),
                          captures=captures)
            (args.output / f"journey-{suffix}.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
        finally:
            app.cleanup()


if __name__ == "__main__":
    main()
