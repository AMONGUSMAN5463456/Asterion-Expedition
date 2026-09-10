#!/usr/bin/env python3
"""Launch Asterion Expedition. Run bootstrap.py for first-time installation."""
from __future__ import annotations

import argparse
import ctypes.util
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback


def main() -> int:
    parser = argparse.ArgumentParser(description="Asterion Expedition — procedural space exploration")
    parser.add_argument("--smoke-test", action="store_true", help="Run isolated offscreen gameplay checks")
    parser.add_argument("--offscreen", action="store_true", help="Render without a desktop window")
    parser.add_argument("--software", action="store_true", help="Use the software renderer")
    parser.add_argument("--screenshot", type=Path, help="Save the last rendered frame as a PNG")
    parser.add_argument("--frames", type=int, default=0, help="Exit after this many frames")
    parser.add_argument("--scene", choices=("surface", "orbit"), default="surface")
    parser.add_argument("--panel", choices=("inventory", "craft", "map", "journal", "build", "help", "settings"))
    parser.add_argument("--autostart", action="store_true", help="Start a fresh expedition immediately")
    parser.add_argument("--save-dir", type=Path, help="Override the normal per-user save directory")
    parser.add_argument("--no-audio", action="store_true")
    args = parser.parse_args()
    try:
        from panda3d.core import loadPrcFileData
    except ImportError:
        print("Panda3D is not installed. Run: python3 bootstrap.py")
        return 1
    offscreen = args.offscreen or args.smoke_test
    config = [
        "window-title Asterion Expedition", "win-size 1280 720", "sync-video true",
        "show-frame-rate-meter false", "textures-power-2 up", "framebuffer-srgb false",
        "audio-library-name null" if args.no_audio or offscreen else "audio-library-name p3openal_audio",
        "notify-level warning", "default-directnotify-level warning", "model-cache-dir",
        "background-color 0.025 0.04 0.08 1", "text-encoding utf8",
    ]
    if args.software or (offscreen and sys.platform.startswith("linux") and not ctypes.util.find_library("EGL")):
        config += ["load-display p3tinydisplay", "aux-display p3tinydisplay", "textures-power-2 up"]
    elif offscreen and sys.platform.startswith("linux"):
        config.append("load-display p3headlessgl")
    if offscreen:
        config.append("window-type offscreen")
    loadPrcFileData("asterion", "\n".join(config))
    from asterion.app import ExpeditionApp
    temporary = tempfile.TemporaryDirectory(prefix="asterion-qa-") if args.smoke_test and not args.save_dir else None
    app = None
    try:
        app = ExpeditionApp(save_dir=args.save_dir or (Path(temporary.name) if temporary else None),
                            offscreen=offscreen, no_audio=args.no_audio or offscreen)
        if args.smoke_test:
            report = app.run_smoke_checks()
            print(json.dumps(report, indent=2))
        elif args.autostart or args.scene == "orbit" or args.panel:
            app.new_game()
        if args.scene == "orbit":
            app.enter_orbit()
        if args.panel:
            app.open_panel(args.panel)
        if args.frames or offscreen:
            count = max(3, min(args.frames or 12, 36000))
            for _ in range(count):
                app.step(1 / 60)
                app.graphicsEngine.renderFrame()
            if args.screenshot:
                args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                if not app.win.saveScreenshot(str(args.screenshot.resolve())):
                    raise RuntimeError("Screenshot could not be saved")
                print(f"Screenshot: {args.screenshot.resolve()}")
        else:
            app.run()
        return 0
    except Exception:
        details = traceback.format_exc()
        print(details, file=sys.stderr)
        if app is not None:
            try:
                (app.save_dir / "crash.log").write_text(details, encoding="utf-8")
                print(f"Error details: {app.save_dir / 'crash.log'}", file=sys.stderr)
            except OSError:
                pass
        return 1
    finally:
        if app is not None:
            app.cleanup()
        if temporary:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
