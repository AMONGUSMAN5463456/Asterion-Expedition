#!/usr/bin/env python3
"""Fly the actual app from orbit to contact; save frames and measured results.

Run with the game's interpreter: .venv/bin/python tools/validate_descents.py
All gameplay/save activity uses a disposable save directory.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import platform
import random
import statistics
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs' / 'validation-v1.3')
    parser.add_argument('--hardware', action='store_true', help='Use the native graphics driver')
    parser.add_argument('--scenario', help='Run one named scenario')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    from panda3d.core import NodePath, Vec3, loadPrcFileData
    config = 'window-type offscreen\nwin-size 960 540\naudio-library-name null\nnotify-level error\nmodel-cache-dir'
    if not args.hardware:
        config += '\nload-display p3tinydisplay'
    loadPrcFileData('descent-validation', config)
    from asterion import __version__
    from asterion.app import ExpeditionApp
    from asterion.planetary import vector_to_local
    from asterion.state import GameState

    results = []
    with tempfile.TemporaryDirectory(prefix='asterion-descent-validation-') as saves:
        app = ExpeditionApp(save_dir=saves, offscreen=True, no_audio=True)
        try:
            app.new_game()
            app.game.settings.update(quality='medium', camera_motion=0, flight_assist=False)
            ocean_field = app.world.fields[app.system['planets'][1]['id']]
            rng = random.Random(1907)
            ocean = None
            for _ in range(5000):
                direction = Vec3(rng.uniform(-1, 0), rng.uniform(-1, 0), rng.uniform(.2, 1)).normalized()
                if ocean_field.seabed_elevation(direction) < ocean_field.water_level - 12:
                    ocean = tuple(direction)
                    break
            assert ocean is not None, 'No deep-ocean validation site'
            scenarios = [('talora-land', 0, (0, 0, 1)), ('deep-ocean', 1, ocean),
                         ('far-hemisphere', 0, (0, 0, -1)),
                         ('north-pole', 2, (0, 1, 0)), ('south-pole', 2, (0, -1, 0))]
            for name, planet_index, normal in scenarios:
                if args.scenario and args.scenario != name:
                    continue
                planet = app.system['planets'][planet_index]
                field = app.world.fields[planet['id']]
                direction = Vec3(*normal).normalized()
                app._remove_ship()
                app.frame = None
                app.world.set_frame(None)
                app.planet = planet
                app.game.planet_index = planet_index
                app.autopilot = app.transition = None
                app.close_panel()
                app.notice, app.notice_time = '', 0
                point = field.center + direction * (field.radius + field.elevation(direction) + 7000)
                app.controller.set_mode('orbit', point)
                app.controller.reset_keys()
                app.controller.set_flight_collision(app.world.move_sphere)
                app.controller.velocity = -direction * 900
                tangent = Vec3(0, 1, 0).cross(direction)
                if tangent.lengthSquared() < .01:
                    tangent = Vec3(1, 0, 0)
                tangent.normalize()
                marker = NodePath('validation-camera')
                marker.lookAt(tangent * .6 - direction * .8, direction)
                h, p, r = marker.getHpr()
                marker.removeNode()
                app.controller.heading, app.controller.pitch, app.controller.reference_roll = h, p, r
                app._update_flight_environment()
                root, bodies = app.world.root, dict(app.world.body_roots)
                phases, hits, snapshots, times = set(), set(), [], []
                maximum_step = 0
                marks = [6900, 3500, 2200, 950, 650, 400, 120, 30, 5]
                start = time.perf_counter()
                previous = app.world_position()
                saved = False
                for frame in range(540):
                    tick = time.perf_counter()
                    app.step(1 / 30)
                    times.append(time.perf_counter() - tick)
                    current = app.world_position()
                    distance = (current - previous).length()
                    maximum_step = max(maximum_step, distance)
                    assert distance < 30.7, (name, 'position discontinuity', distance)
                    previous = current
                    assert app.world.root == root and app.world.body_roots == bodies, 'Scene replaced during descent'
                    assert app.transition is None and app.fade.isHidden(), 'Atmosphere transition/fade'
                    altitude = app.atmosphere_data['altitude']
                    assert altitude >= 2.5, (name, 'Hull passed below visible surface', altitude)
                    phases.add(app.controller.mode)
                    hits.update(app.controller.collision_ids)
                    if not saved and altitude < 950:
                        assert app.save_game(False)
                        loaded = GameState.load(app.save_path)
                        assert loaded.mode == 'flight'
                        assert (Vec3(*loaded.velocity) - app.controller.velocity).length() < .001
                        saved = True
                    if marks and altitude <= marks[0]:
                        mark = marks.pop(0)
                        app.ui.update(app._view())
                        app.graphicsEngine.renderFrame()
                        app.graphicsEngine.renderFrame()
                        filename = f'{name}-{mark:04d}m.png'
                        assert app.win.saveScreenshot(str((args.output / filename).resolve()))
                        snapshots.append(dict(file=filename, altitude=round(altitude, 3),
                                              mode=app.controller.mode, frame=frame))
                    if altitude < 5 and any('terrain' in key for key in hits):
                        break
                assert altitude < 5 and any('terrain' in key for key in hits), (name, 'No surface contact', altitude, hits)
                assert phases == {'flight', 'orbit'}, (name, phases)
                assert not any('atmosphere' in key for key in hits)
                contacted = altitude
                landed = False
                if name == 'talora-land':
                    # F performs the normal low/slow touchdown action after the
                    # hull reaches land; no approach or surface loader is used.
                    app.controller.velocity = Vec3(0)
                    app.controller.speed = 0
                    app.flight_action()
                    landed = app.controller.mode == 'surface' and app.ship is not None
                    assert landed, app.notice
                    # Let the normal app tick switch cockpit/tool and refresh
                    # the HUD before capturing the completed F touchdown.
                    app.step(1 / 30)
                    app.step(1 / 30)
                    app.ui.update(app._view())
                    app.graphicsEngine.renderFrame()
                    assert app.win.saveScreenshot(str((args.output / 'talora-touchdown.png').resolve()))
                results.append(dict(scenario=name, planet=planet['name'], diameter_m=field.radius * 2,
                    surface='ocean' if field.seabed_elevation(direction) < field.water_level else 'land',
                    descent_speed_m_s=900, simulated_seconds=round((frame + 1) / 30, 3),
                    wall_seconds=round(time.perf_counter() - start, 3), contact_altitude_m=round(contacted, 4),
                    maximum_frame_travel_m=round(maximum_step, 4), modes=sorted(phases),
                    normal_touchdown=landed, hit_ids=sorted(hits), snapshots=snapshots,
                    median_update_ms=round(statistics.median(times) * 1000, 2),
                    p95_update_ms=round(sorted(times)[int(len(times) * .95)] * 1000, 2)))
                print(json.dumps(results[-1], indent=2), flush=True)
        finally:
            app.cleanup()
    report = dict(version=__version__, platform=platform.platform(), python=platform.python_version(),
                  renderer='native' if args.hardware else 'Panda3D TinyDisplay',
                  status='passed', scenarios=results)
    (args.output / 'descent-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(f'Passed {len(results)} physical descents; report: {args.output / "descent-report.json"}')


if __name__ == '__main__':
    main()
