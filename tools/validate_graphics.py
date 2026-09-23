#!/usr/bin/env python3
"""Capture actual gameplay graphics using isolated saves and a native renderer.

Use --quick for the opening scene and shadow comparison. All captures are
rendered by the real app; no screenshot is retouched or composited afterward.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"docs"/"validation-v1.4")
    parser.add_argument("--quick",action="store_true")
    parser.add_argument("--software",action="store_true")
    parser.add_argument("--onscreen",action="store_true",
                        help="Benchmark a visible desktop window")
    parser.add_argument("--fullscreen",action="store_true",
                        help="Use full screen with --onscreen")
    parser.add_argument("--borderless",action="store_true",
                        help="Use an undecorated window with --onscreen")
    parser.add_argument("--origin-x",type=int,
                        help="Horizontal display position for the window")
    parser.add_argument("--origin-y",type=int,
                        help="Vertical display position for the window")
    parser.add_argument("--width",type=int,default=1280)
    parser.add_argument("--height",type=int,default=720)
    parser.add_argument("--quality",choices=("low","medium","high"),default="medium")
    parser.add_argument("--sprint-frames",type=int,default=0,
                        help="Measure forward sprinting after the surface settles")
    parser.add_argument("--hud",action="store_true",
                        help="Keep the gameplay HUD visible during timing")
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    from panda3d.core import Vec3, loadPrcFileData
    config=["window-type onscreen" if args.onscreen else "window-type offscreen",
            f"win-size {args.width} {args.height}",
            "audio-library-name null","notify-level warning","model-cache-dir",
            "sync-video false","gl-version 3 2",
            "textures-power-2 up" if args.software else "textures-power-2 none",
            "framebuffer-multisample true","multisamples 4",
            "framebuffer-srgb false","texture-anisotropic-degree 8"]
    if args.software:
        config += ["load-display p3tinydisplay"]
    if args.fullscreen:
        config.append("fullscreen true")
    if args.borderless:
        config.append("undecorated true")
    if args.origin_x is not None or args.origin_y is not None:
        config.append(f"win-origin {args.origin_x or 0} {args.origin_y or 0}")
    loadPrcFileData("celestial-validation","\n".join(config))
    from asterion import __version__
    from asterion.app import ExpeditionApp
    from asterion.state import GameState
    timings={}
    with tempfile.TemporaryDirectory(prefix="asterion-celestial-validation-") as saves:
        start=time.perf_counter()
        app=ExpeditionApp(save_dir=saves,offscreen=not args.onscreen,no_audio=True)
        app.controller._poll_keys=lambda:None
        startup=time.perf_counter()-start
        def settle(count=6):
            for _ in range(count):
                app.step(1/60)
                app.graphicsEngine.renderFrame()
        def capture(name,*,step=True):
            if step:
                settle()
            app.ui.update(app._view())
            app.graphicsEngine.renderFrame()
            app.graphicsEngine.renderFrame()
            path=args.output/(name+".png")
            assert app.win.saveScreenshot(str(path.resolve()))
            print(f"Captured {path}",flush=True)
        def measure(count):
            samples, updates, renders = [], [], []
            for _ in range(count):
                tick=time.perf_counter()
                app.step(1/60)
                draw=time.perf_counter()
                app.graphicsEngine.renderFrame()
                updates.append((draw-tick)*1000)
                renders.append((time.perf_counter()-draw)*1000)
                samples.append((time.perf_counter()-tick)*1000)
            index=max(0, min(count-1, int(count*.95)-1))
            return {"median_ms":round(statistics.median(samples),2),
                    "p95_ms":round(sorted(samples)[index],2),
                    "update_median_ms":round(statistics.median(updates),2),
                    "render_median_ms":round(statistics.median(renders),2),
                    "loaded_chunks":len(app.world.chunks),
                    "queued_chunks":len(app.world._chunk_queue)}
        try:
            capture("title")
            app.game.settings["quality"]=args.quality
            app.new_game()
            app.game.settings["camera_motion"]=0
            app.notice_time=0
            settle(90)
            capture("surface")
            app.ui.set_visible(False)
            capture("surface-clean")
            if app.visuals.gpu:
                app.render.setShaderInput("ae_shadow_enabled",0.)
                capture("surface-no-shadows",step=False)
                app.visuals.update()
            if args.hud:
                app.ui.set_visible(True)
            # Repeated warm render timings include real streaming and drawing.
            timings["surface"]=measure(45)
            if args.sprint_frames:
                app.controller._poll_keys=lambda:None
                app.controller.keys["w"]=True
                app.controller.keys["shift"]=True
                start=Vec3(app.controller.position)
                timings["sprint"]=measure(args.sprint_frames)
                timings["sprint"]["distance_m"]=round(
                    (app.controller.position-start).length(),2)
            if not args.quick:
                app.ui.set_visible(True)
                for panel in ("inventory","craft","map","journal","build","settings","help"):
                    app.open_panel(panel)
                    capture(panel)
                    app.close_panel()
                app.enter_orbit()
                app.notice_time=0
                capture("orbit")
                app.ui.set_visible(False)
                capture("orbit-clean")
                # Both systems together contain every original biome.
                for system_id in (0,1):
                    for index in range(4):
                        app.game.system_id=system_id
                        app.game.planet_index=index
                        app._load_surface(fresh=True)
                        app.notice_time=0
                        app.controller.heading=-20
                        app.controller.pitch=-6
                        settle(85)
                        capture("biome-"+app.planet["biome"])
                app.ui.set_visible(True)
            report={"version":__version__,"renderer":app.visuals.renderer,
                    "gpu":app.visuals.gpu,"size":[args.width,args.height],
                    "actual_size":[app.win.getXSize(),app.win.getYSize()],
                    "world_radius":app.world._radius,
                    "window":"onscreen" if args.onscreen else "offscreen",
                    "fullscreen":args.fullscreen,
                    "borderless":args.borderless,
                    "hud":args.hud,
                    "quality":args.quality,
                    "startup_seconds":round(startup,2),"timings":timings,
                    "images":sorted(p.name for p in args.output.glob("*.png"))}
            if hasattr(app.win, "getRequestedProperties"):
                requested = app.win.getRequestedProperties()
                if requested.hasSize():
                    report["requested_size"] = [requested.getXSize(), requested.getYSize()]
            (args.output/"graphics-report.json").write_text(json.dumps(report,indent=2)+"\n")
            print(json.dumps(report,indent=2),flush=True)
        finally:
            app.cleanup()


if __name__=="__main__":
    main()
