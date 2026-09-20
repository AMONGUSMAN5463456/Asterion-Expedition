#!/usr/bin/env python3
"""Photograph redesigned assets in the actual native game renderer.

The camera is posed for close, unobstructed views inside a temporary expedition.
HUD and first-person equipment are hidden; geometry, environment and lighting
are the normal game assets. Screenshots are never retouched or composited.
"""
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
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "validation-v1.4" / "heroes")
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--only", default="ship,outpost,ruin,habitat,station",
                        help="Comma-separated selection: ship,outpost,ruin,habitat,station")
    parser.add_argument("--software", action="store_true")
    args = parser.parse_args()
    selected = set(args.only.split(","))
    if selected - {"ship", "outpost", "ruin", "habitat", "station"}:
        parser.error("Unknown asset family in --only")
    args.output.mkdir(parents=True, exist_ok=True)

    from panda3d.core import Point3, Vec3, loadPrcFileData
    config = ["window-type offscreen", f"win-size {args.width} {args.height}",
              "audio-library-name null", "notify-level warning", "model-cache-dir",
              "sync-video false", "gl-version 3 2", "textures-power-2 none",
              "framebuffer-srgb false", "texture-anisotropic-degree 8"]
    if args.software:
        config += ["load-display p3tinydisplay", "textures-power-2 up"]
    loadPrcFileData("asterion-hero-capture", "\n".join(config))
    from asterion.app import ExpeditionApp

    records = []
    with tempfile.TemporaryDirectory(prefix="asterion-hero-capture-") as save_dir:
        app = ExpeditionApp(save_dir=save_dir, offscreen=True, no_audio=True)
        try:
            app.new_game()
            app.game.settings["camera_motion"] = 0
            app.notice_time = 0
            app.game.elapsed = 12.0
            app.ui.set_visible(False)
            app.effects.root.hide()
            app.fade.hide()

            def settle(position, target, fov=52, count=45):
                position, target = Vec3(*position), Vec3(*target)
                app.controller.position = Vec3(position)
                app.controller.velocity = Vec3(0)
                app.controller.speed = 0
                app.camera.setPos(position)
                app.camera.lookAt(target)
                app.camLens.setFov(fov)
                for _ in range(count):
                    # Stream and draw without advancing physics or disturbing
                    # the selected photographic camera with controller motion.
                    app.world.update(0, position, app.game.elapsed, 0)
                    app.visuals.update()
                    app.graphicsEngine.renderFrame()
                return position, target

            def capture(name, position, target, fov=52, *, count=45):
                position, target = settle(position, target, fov, count)
                for _ in range(3):
                    app.graphicsEngine.renderFrame()
                path = args.output / (name + ".png")
                if not app.win.saveScreenshot(str(path.resolve())):
                    raise RuntimeError(f"Could not capture {path}")
                records.append(dict(image=path.name, camera=list(position), target=list(target),
                                    horizontal_fov=fov, renderer=app.visuals.renderer))
                print(f"Captured {path}", flush=True)

            def posed(node, position, target):
                matrix = node.getMat(app.render)
                return matrix.xformPoint(Point3(*position)), matrix.xformPoint(Point3(*target))

            def landmark(kind):
                return next(entity["node"] for identifier, entity in app.world._entities.items()
                            if identifier == f"{app.planet['id']}:{kind}:landing")

            if "ship" in selected:
                position, target = posed(app.ship, (-10.1, 13.2, 6.9), (0, .05, 1.35))
                capture("wayfarer-front-quarter", position, target, 43)
                position, target = posed(app.ship, (-9.5, -12.5, 5.1), (0, -.30, 1.40))
                capture("wayfarer-engine-detail", position, target, 43, count=16)
            if "outpost" in selected:
                node = landmark("outpost")
                position, target = posed(node, (-18.8, -22.8, 11.1), (0, -.35, 3.10))
                capture("survey-exchange", position, target, 51)
            if "ruin" in selected:
                node = landmark("ruin")
                position, target = posed(node, (13.3, -26.6, 8.3), (0, 0, 5.45))
                capture("unfinished-halo", position, target, 48)
            if "habitat" in selected:
                # Use the real construction path in this isolated expedition.
                # No user save or published content is changed.
                site = next(((x, y) for x, y in app._site_candidates(18, -22)
                             if app._site_clear(x, y, radius=4.1)), None)
                if site is None:
                    raise RuntimeError("No clear temporary habitat site found")
                x, y = site
                app.world.add_building(dict(id="hero-habitat", kind="habitat", pos=[x, y, 0], heading=0))
                node = app.world._entities["hero-habitat"]["node"]
                position, target = posed(node, (-11, -14, 7.1), (0, 0, 1.55))
                capture("field-habitat", position, target, 44)
            if "station" in selected:
                position = Vec3(*app.system["station"]) + Vec3(202, -256, 165)
                app.enter_orbit(position=position)
                app.ui.set_visible(False)
                app.effects.root.hide()
                node = app.world._entities[f"s{app.system['id']}:station"]["node"]
                position, target = posed(node, (202, -256, 165), (0, -15, 0))
                capture("orbital-exchange", position, target, 48, count=12)
                position, target = posed(node, (-73, -194, 45), (0, -33, 0))
                capture("orbital-docking-approach", position, target, 60, count=8)
            report = dict(renderer=app.visuals.renderer, gpu=app.visuals.gpu,
                          size=[args.width, args.height], screenshots=records,
                          method="Native game renderer; temporary save; camera-only staging; no compositing")
            (args.output / "hero-report.json").write_text(json.dumps(report, indent=2) + "\n")
        finally:
            app.cleanup()


if __name__ == "__main__":
    main()
