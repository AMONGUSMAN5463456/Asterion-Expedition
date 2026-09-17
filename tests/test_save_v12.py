"""Save compatibility for physical system coordinates and moving planet frames."""

import copy
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from panda3d.core import NodePath, Vec3

from asterion.content import BUILDINGS
from asterion.controller import PlayerController
from asterion.planetary import (
    PlanetField, PlanetFrame, frame_to_local, frame_to_world,
    vector_to_local, vector_to_world,
)
from asterion.state import GameState, SAVE_VERSION
from asterion.universe import SPACE_SCALE, get_planet


class SeamlessSaveTests(unittest.TestCase):
    def test_new_expedition_has_current_stationary_home_frame(self):
        state = GameState()
        saved = state.to_dict()
        self.assertEqual(SAVE_VERSION, 5)
        self.assertEqual(saved["version"], 5)
        self.assertEqual(saved["coordinate_version"], 2)
        self.assertEqual(saved["frame_up"], [0, 0, 1])
        self.assertEqual(saved["velocity"], [0, 0, 0])
        self.assertEqual(saved["reference_roll"], 0)

    def test_legacy_orbit_scales_once_through_disk_save_and_reload(self):
        for version in (1, 2, 3):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "save.json"
                legacy = dict(version=version, mode="orbit", system_id=3,
                              position=[1280, -750.25, 350], ship_position=[7, 6, 20],
                              heading=141.25, pitch=-32.5)
                path.write_text(json.dumps(legacy), encoding="utf-8")
                state = GameState.load(path)
                self.assertEqual(state.mode, "orbit")
                self.assertEqual(state.position, [122880, -72024, 33600])
                self.assertEqual(state.ship_position, [7, 6, 20])
                self.assertIsNone(state.frame_up)
                self.assertEqual(state.velocity, [0, 0, 0])
                self.assertEqual(state.reference_roll, 0)
                self.assertEqual((state.heading, state.pitch), (141.25, -32.5))
                for _ in range(3):
                    self.assertTrue(state.save(path)[0])
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(payload["version"], 5)
                    self.assertEqual(payload["coordinate_version"], 2)
                    state = GameState.load(path)
                    self.assertEqual(state.position, [122880, -72024, 33600])
                    self.assertIsNone(state.frame_up)

    def test_legacy_surface_and_flight_retain_home_local_positions(self):
        for mode in ("surface", "flight"):
            with self.subTest(mode=mode):
                state, _ = GameState._from_dict(dict(version=3, mode=mode,
                    position=[340, -195, 980], ship_position=[21, 13, 20],
                    frame_up=[0, 1, 0], velocity=[30, 50, 70], reference_roll=57))
                self.assertEqual(state.mode, mode)
                self.assertEqual(state.position, [340, -195, 980])
                self.assertEqual(state.ship_position, [21, 13, 20])
                self.assertEqual(state.frame_up, [0, 0, 1])
                self.assertEqual(state.velocity, [0, 0, 0])
                self.assertEqual(state.reference_roll, 0)
                self.assertEqual(state.coordinate_version, 2)

    def test_current_coordinate_marker_overrides_older_file_version(self):
        for version in (1, 2, 3, 4):
            with self.subTest(version=version):
                state, _ = GameState._from_dict(dict(version=version, coordinate_version=2, world_scale=SPACE_SCALE,
                    mode="orbit", position=[85000, -19000, 2500], frame_up=None,
                    velocity=[25, 180, -40], reference_roll=31))
                self.assertEqual(state.position, [85000, -19000, 2500])
                self.assertIsNone(state.frame_up)
                self.assertEqual(state.velocity, [25, 180, -40])
                self.assertEqual(state.reference_roll, 31)

    def test_current_save_with_missing_or_invalid_marker_does_not_rescale(self):
        for value in (None, True, "2", [], {}, -1, 2.5):
            with self.subTest(marker=value):
                payload = dict(version=5, mode="orbit", position=[51000, -2500, 200])
                if value is not None:
                    payload["coordinate_version"] = value
                state, _ = GameState._from_dict(payload)
                self.assertEqual(state.position, [51000, -2500, 200])
                self.assertEqual(state.to_dict()["coordinate_version"], 2)

    def test_midflight_roundtrip_preserves_far_hemisphere_pose_and_velocity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "save.json"
            state = GameState()
            state.mode = "flight"
            state.system_id, state.planet_index = 19, 3
            state.position = [-230.125, 175.75, 1320.5]
            state.frame_up = [0, -.6, -.8]
            state.velocity = [-13.25, 90.5, -61.75]
            state.heading, state.pitch, state.reference_roll = 231.5, -47.75, 73.125
            state.ship_position = [60, -80, 24]
            expected = state.to_dict()
            self.assertTrue(state.save(path)[0])
            restored = GameState.load(path)
            for key in ("mode", "system_id", "planet_index", "position", "velocity",
                        "heading", "pitch", "reference_roll", "ship_position"):
                self.assertEqual(getattr(restored, key), expected[key], key)
            for got, want in zip(restored.frame_up, state.frame_up):
                self.assertAlmostEqual(got, want, places=14)
            self.assertEqual(restored.coordinate_version, 2)

    def test_disk_reload_resumes_actual_flight_with_polar_attitude_and_momentum(self):
        planet = get_planet(19, 3)
        field = PlanetField(planet)
        settings = {"flight_assist": False, "camera_motion": 0, "mouse_smoothing": 0}

        def controller_for(state):
            camera = NodePath("save-flight-camera")
            app = SimpleNamespace(camera=camera, win=None, disableMouse=lambda: None)
            controller = PlayerController(app)
            self.addCleanup(camera.removeNode)
            self.addCleanup(controller.destroy)
            frame = PlanetFrame.from_planet(planet, state.frame_up) if state.frame_up else None

            def sweep(position, displacement, radius):
                result = field.sweep(frame_to_world(frame, position),
                                     vector_to_world(frame, displacement), radius)
                result.position = frame_to_local(frame, result.position)
                result.normals = [vector_to_local(frame, n) for n in result.normals]
                return result

            controller.set_mode(state.mode, state.position, state.heading, state.pitch)
            controller.velocity = Vec3(*state.velocity)
            controller.speed = controller.velocity.length()
            controller.reference_roll = state.reference_roll
            controller.set_space_blend(.5)
            controller.set_flight_collision(sweep)
            controller.set_enabled(True)
            controller._apply_camera()
            return controller, camera, frame

        def world_pose(controller, camera, frame):
            return (frame_to_world(frame, controller.position),
                    vector_to_world(frame, controller.velocity),
                    vector_to_world(frame, camera.getQuat().getForward()),
                    vector_to_world(frame, camera.getQuat().getUp()))

        for mode in ("flight", "orbit"):
            for normal, pitch in (([0, -.6, -.8], -47.75), ([0, 1, 0], 90),
                                  ([0, -1, 0], -90), (None, 90)):
                with self.subTest(mode=mode, normal=normal, pitch=pitch), tempfile.TemporaryDirectory() as directory:
                    state = GameState()
                    state.mode, state.system_id, state.planet_index = mode, 19, 3
                    state.frame_up = normal
                    state.position = ([-230.125, 175.75, 1320.5] if normal else
                                      list(field.point(0, 0, 3200)))
                    state.velocity = [-13.25, 90.5, -61.75]
                    state.heading, state.pitch, state.reference_roll = 231.5, pitch, 73.125
                    live, live_camera, live_frame = controller_for(state)
                    # Save a moving controller after real physics has advanced.
                    live.update(.1, lambda x, y: 0, {"fuel": 100}, {}, settings)
                    state.position, state.velocity = list(live.position), list(live.velocity)
                    state.heading, state.pitch = live.heading, live.pitch
                    state.reference_roll = live.reference_roll
                    before = world_pose(live, live_camera, live_frame)
                    path = Path(directory) / "moving-save.json"
                    self.assertTrue(state.save(path)[0])
                    restored = GameState.load(path)
                    self.assertEqual(restored.pitch, pitch)
                    loaded, loaded_camera, loaded_frame = controller_for(restored)
                    after = world_pose(loaded, loaded_camera, loaded_frame)
                    for index, (got, wanted) in enumerate(zip(after, before)):
                        self.assertLess((got-wanted).length(), .02 if index < 2 else 2e-6)
                    # Reload must continue the same unassisted trajectory.
                    for _ in range(30):
                        for controller in (live, loaded):
                            controller.update(1 / 60, lambda x, y: 0,
                                              {"fuel": 100}, {}, settings)
                    for index, (got, wanted) in enumerate(zip(
                            world_pose(loaded, loaded_camera, loaded_frame),
                            world_pose(live, live_camera, live_frame))):
                        self.assertLess((got-wanted).length(), .025 if index < 2 else 2e-6)
                    self.assertGreater((frame_to_world(loaded_frame, loaded.position)-before[0]).length(), 40)

    def test_migration_preserves_economy_progression_and_geographic_records(self):
        state = GameState()
        state.visit(get_planet(3, 2))
        state.record("mined", 23)
        state.inventory = dict(BUILDINGS["extractor"]["cost"])
        state.elapsed = 210
        self.assertTrue(state.build("extractor", "s3-p2", [640, -195, 24], 125)[0])
        state.inventory = {"ferrite": 80, "crystal": 19}
        state.depleted = {"s3-p2": ["stone-1", "plant-2"], "orbit:3": ["asteroid-4"]}
        state.discover("s3-p2:flora:7", dict(name="Opal branch", kind="flora", planet="Survey world"))
        self.assertTrue(state.accept_contract(state.available_contracts()[0]["id"])[0])
        expected = state.to_dict()
        legacy = copy.deepcopy(expected)
        for field in ("coordinate_version", "frame_up", "velocity", "reference_roll"):
            legacy.pop(field)
        legacy.update(version=3, mode="orbit", position=[300, 900, 110])
        migrated, _ = GameState._from_dict(legacy)
        actual = migrated.to_dict()
        for field in ("seed", "inventory", "credits", "upgrades", "stats", "discoveries",
                      "visited", "depleted", "bases", "story_stage", "contracts", "elapsed"):
            self.assertEqual(actual[field], expected[field], field)

    def test_frame_normals_normalize_finite_extreme_and_small_vectors(self):
        for normal, expected in (([0, 3, -4], [0, .6, -.8]),
                                 ([1e308, 1e308, 0], [2 ** -.5, 2 ** -.5, 0]),
                                 ([5e-324, 0, 0], [1, 0, 0])):
            with self.subTest(normal=normal):
                state, _ = GameState._from_dict(dict(version=5, coordinate_version=2,
                                                     frame_up=normal))
                self.assertAlmostEqual(math.hypot(*state.frame_up), 1, places=14)
                for got, want in zip(state.frame_up, expected):
                    self.assertAlmostEqual(got, want, places=14)

    def test_invalid_frame_normals_fall_back_without_changing_position(self):
        invalid = ([], {}, "up", [0, 0, 0], [1, 2], [True, 0, 1],
                   [float("nan"), 0, 1], [0, float("inf"), 1], [10 ** 1000, 0, 1])
        for mode, expected in (("surface", [0, 0, 1]), ("flight", [0, 0, 1]), ("orbit", None)):
            for normal in invalid:
                with self.subTest(mode=mode, normal=normal):
                    state, _ = GameState._from_dict(dict(version=5, coordinate_version=2,
                        mode=mode, position=[120, -240, 350], frame_up=normal))
                    self.assertEqual(state.frame_up, expected)
                    self.assertEqual(state.position, [120, -240, 350])
                    json.dumps(state.to_dict(), allow_nan=False)

    def test_unanchored_frame_is_preserved_in_either_flight_label(self):
        for mode in ("flight", "orbit"):
            state, _ = GameState._from_dict(dict(version=5, coordinate_version=2,
                                                 mode=mode, frame_up=None))
            self.assertIsNone(state.to_dict()["frame_up"])

    def test_velocity_and_roll_sanitization_are_finite_and_bounded(self):
        for velocity in (None, {}, "fast", [1, 2], [float("nan"), float("inf"), -float("inf")],
                         [True, "30", object()], [10 ** 1000, -10 ** 1000, 50],
                         [1e308, -1e308, 1e308], [10000, 10000, 10000]):
            with self.subTest(velocity=velocity):
                state, _ = GameState._from_dict(dict(version=5, coordinate_version=2,
                                                     velocity=velocity))
                self.assertEqual(len(state.velocity), 3)
                self.assertTrue(all(math.isfinite(x) for x in state.velocity))
                self.assertLessEqual(math.hypot(*state.velocity), 10000.000000001)
                json.dumps(state.to_dict(), allow_nan=False)
        for roll in (None, True, {}, [], "90", float("nan"), float("inf"), 10 ** 1000, 1e308):
            with self.subTest(roll=roll):
                state, _ = GameState._from_dict(dict(version=5, coordinate_version=2,
                                                     reference_roll=roll))
                self.assertTrue(-180 <= state.reference_roll < 180)
        for roll, expected in ((390, 30), (-450, -90), (720, 0), (73.125, 73.125)):
            state, _ = GameState._from_dict(dict(version=5, coordinate_version=2,
                                                 reference_roll=roll))
            self.assertEqual(state.reference_roll, expected)

    def test_serialization_sanitizes_live_fields_without_mutating_them(self):
        state = GameState()
        state.frame_up = [0, 0, -7]
        state.velocity = [float("nan"), 60, 80]
        state.reference_roll = 390
        state.renderer = object()
        saved = state.to_dict()
        json.dumps(saved, allow_nan=False)
        self.assertEqual(saved["frame_up"], [0, 0, -1])
        self.assertEqual(saved["velocity"], [0, 60, 80])
        self.assertEqual(saved["reference_roll"], 30)
        self.assertNotIn("renderer", saved)
        self.assertEqual(state.frame_up, [0, 0, -7])
        self.assertTrue(math.isnan(state.velocity[0]))
        self.assertEqual(state.reference_roll, 390)

    def test_bounded_legacy_coordinates_survive_migration(self):
        state, _ = GameState._from_dict(dict(version=3, mode="orbit",
            position=[1e308, -1e308, float("nan")]))
        self.assertTrue(all(math.isfinite(x) and abs(x) <= 4e7 for x in state.position))
        self.assertEqual(GameState._from_dict(state.to_dict())[0].position, state.position)

    def test_recall_resets_frame_motion_and_orientation_at_home(self):
        state = GameState()
        state.mode, state.frame_up = "flight", [0, 0, -1]
        state.velocity, state.reference_roll = [120, -75, 90], 54
        inventory, credits = dict(state.inventory), state.credits
        self.assertTrue(state.rescue()[0])
        self.assertEqual(state.mode, "surface")
        self.assertEqual(state.frame_up, [0, 0, 1])
        self.assertEqual(state.velocity, [0, 0, 0])
        self.assertEqual(state.reference_roll, 0)
        self.assertEqual(state.inventory, inventory)
        self.assertEqual(state.credits, credits)


if __name__ == "__main__":
    unittest.main()
