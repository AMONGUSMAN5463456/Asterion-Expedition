#!/usr/bin/env python3
"""Photograph production assets and effects in a disposable native expedition.

Camera poses are selected for inspection. Geometry, materials, lighting and
effects are the game implementations; screenshots are never composited.
"""
from pathlib import Path
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from panda3d.core import Vec3, loadPrcFileData
    loadPrcFileData("art-detail-validation", "\n".join((
        "window-type offscreen", "win-size 1440 900", "audio-library-name null",
        "sync-video false", "notify-level warning", "model-cache-dir",
        "gl-version 3 2", "textures-power-2 none", "texture-anisotropic-degree 8",
        "framebuffer-multisample true", "multisamples 4", "framebuffer-srgb false",
    )))
    from asterion.app import ExpeditionApp
    from asterion.exploration_vfx import mining_beam
    output = ROOT / "docs" / "validation-v1.4" / "details"
    output.mkdir(parents=True, exist_ok=True)
    images = []
    with tempfile.TemporaryDirectory(prefix="asterion-art-validation-") as saves:
        app = ExpeditionApp(save_dir=saves, offscreen=True, no_audio=True)
        try:
            app.new_game()
            app.game.settings["camera_motion"] = 0
            app.ui.set_visible(False)
            app.effects.root.hide()

            def settle(count=85):
                for _ in range(count):
                    app.step(1/60)
                    app.graphicsEngine.renderFrame()

            def pose(target, offset, fov=58):
                app.camera.setPos(target+Vec3(*offset))
                app.camera.lookAt(target)
                app.camLens.setFov(fov)
                app.visuals.update()

            def capture(name):
                for _ in range(4):
                    app.graphicsEngine.renderFrame()
                path = output / (name+".png")
                assert app.win.saveScreenshot(str(path))
                images.append(path.name)
                print(path, flush=True)

            settle()
            pose(app.ship.getPos(app.render)+Vec3(0,0,1.5), (-9,12,5.5))
            capture("courier")
            for kind, offset, height in (("outpost",(-19,-24,10),3),
                                         ("ruin",(-24,-27,13),5)):
                entity = next(e for e in app.world.interactables() if e["kind"] == kind)
                target = entity["node"].getPos(app.render)+Vec3(0,0,height)
                app.controller.position = target+Vec3(*offset)
                settle()
                pose(target, offset)
                capture(kind)
            for i, kind in enumerate(("habitat","solar","extractor","beacon")):
                app.world.add_building(dict(id="art-validation-"+kind,kind=kind,
                                            pos=[-15+i*11,-24,0],heading=0))
            target = Vec3(1,-24,app.world.height(1,-24)+2)
            app.controller.position = target+Vec3(-23,-25,5)
            settle()
            pose(target,(-30,-34,17),64)
            capture("field-base")
            fauna = next(e for e in app.world.interactables()
                         if e["kind"] == "fauna" and ":landing:" in e["id"])
            target = fauna["node"].getPos(app.render)+Vec3(0,0,.8)
            facing = fauna["node"].getQuat(app.render).xform(Vec3(-5,7,2.5))
            pose(target,tuple(facing),52)
            capture("wildlife")
            mineral = next(e for e in app.world.interactables()
                           if e.get("resource") == "crystal" and ":landing:" in e["id"])
            target = mineral["node"].getPos(app.render)+Vec3(0,0,.9)
            pose(target,(-5,-8,2.8),58)
            beam = mining_beam(app.render,app.camera.getPos(app.render)+Vec3(1,1,-.5),target,.7)
            capture("mineral-extraction")
            beam.removeNode()
            target = Vec3(0,0,app.world.height(0,0))
            app.controller.position = target+Vec3(0,0,1.8)
            settle()
            app.exploration_vfx.scan()
            for _ in range(6):
                app.exploration_vfx.update(.1)
            pose(target,(-26,-30,19),68)
            capture("survey-wave")
            app.enter_orbit()
            station = next(e for e in app.world.interactables() if e["kind"] == "station")
            target = station["node"].getPos(app.render)
            app.controller.position = target+Vec3(-245,-310,145)
            settle(12)
            pose(target,(-245,-310,145),58)
            capture("orbital-exchange")
            (output / "art-report.json").write_text(json.dumps(dict(
                renderer=app.visuals.renderer, size=[1440,900], images=images,
                method="Production game assets, native render, inspection camera poses",
            ),indent=2)+"\n")
        finally:
            app.cleanup()


if __name__ == "__main__":
    main()
