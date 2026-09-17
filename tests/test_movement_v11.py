"""Movement feel and collision integration; no renderer or graphics window."""

import math
import unittest
from types import SimpleNamespace

from panda3d.core import NodePath, PerspectiveLens, Vec3

from asterion.collision import CollisionWorld
from asterion.controller import EYE_HEIGHT, SHIP_CLEARANCE, PlayerController
from tests.test_controller import FakeWindow


class MovementV11Tests(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(camera=NodePath("camera"), camLens=PerspectiveLens(),
                                   win=None, disableMouse=lambda: None)
        self.controller = PlayerController(self.app)
        self.controller.set_mode("surface", (0, 0, EYE_HEIGHT))
        self.controller.set_enabled(True)
        self.world = CollisionWorld()
        self.controller.set_collision_world(self.world)
        self.settings = {"camera_motion": 0.0, "mouse_smoothing": 0.0,
                         "flight_assist": True}
        self.vitals = {"energy": 100.0, "fuel": 100.0}

    def tearDown(self):
        self.controller.destroy()
        self.app.camera.removeNode()

    def run_for(self, duration, dt=1 / 60, height_fn=lambda x, y: 0):
        remaining = duration
        while remaining > 1e-9:
            frame = min(dt, remaining)
            self.controller.update(frame, height_fn, self.vitals, {}, self.settings)
            remaining -= frame

    def box(self, name, center, half):
        self.world.set_group(name, [{"id": name, "type": "box", "center": center,
                                     "half": half, "heading": 0}])

    def test_walking_slides_along_wall_and_stops_inward_velocity(self):
        self.box("wall", (1.5, 0, 2), (0.2, 30, 2))
        self.controller.keys.update(w=True, d=True)
        self.run_for(1.2)
        self.assertLessEqual(self.controller.position.x, 0.921)
        self.assertGreater(self.controller.position.y, 6)
        self.assertAlmostEqual(self.controller.velocity.x, 0, delta=0.001)
        self.assertGreater(self.controller.velocity.y, 6)
        self.assertIn("wall", self.controller.collision_ids)
        self.assertFalse(self.world.overlaps_capsule(self.controller.position))

    def test_lands_on_roof_and_remains_above_terrain(self):
        self.box("roof", (0, 0, 3), (6, 6, 0.2))
        self.controller.set_mode("surface", (0, 0, 10))
        self.controller.velocity.z = -6
        self.run_for(2)
        self.assertTrue(self.controller.grounded)
        self.assertAlmostEqual(self.controller.position.z, 3.2 + EYE_HEIGHT, delta=0.004)
        self.controller.keys["w"] = True
        self.run_for(0.4)
        self.assertTrue(self.controller.grounded)
        self.assertAlmostEqual(self.controller.position.z, 3.2 + EYE_HEIGHT, delta=0.004)
        self.assertFalse(self.world.overlaps_capsule(self.controller.position))

    def test_roof_edge_falls_then_lands_on_terrain(self):
        self.box("roof", (0, 0, 3), (2, 2, 0.2))
        self.controller.set_mode("surface", (0, 0, 5.001))
        self.run_for(0.1)
        self.controller.keys["w"] = True
        self.run_for(0.4)
        self.assertGreater(self.controller.position.z, EYE_HEIGHT + 2)
        self.assertFalse(self.controller.grounded)
        self.run_for(1)
        self.assertTrue(self.controller.grounded)
        self.assertAlmostEqual(self.controller.position.z, EYE_HEIGHT, delta=0.002)

    def test_ceiling_stops_jump_and_held_jetpack(self):
        self.box("ceiling", (0, 0, 3), (5, 5, 0.2))
        self.controller.keys["space"] = True
        self.run_for(0.8)
        self.assertLess(self.controller.position.z, 2.8)
        self.assertGreater(self.controller.position.z, 2.7)
        self.assertTrue(self.controller.ceiling)
        self.assertTrue(self.controller.jetpacking)
        self.assertLessEqual(self.controller.velocity.z, 0)
        self.assertFalse(self.world.overlaps_capsule(self.controller.position))

    def test_low_step_can_be_walked_over(self):
        self.box("step", (0, 3, 0.15), (2, 1, 0.15))
        self.controller.keys["w"] = True
        self.run_for(0.4)
        self.assertGreater(self.controller.position.y, 2.5)
        self.assertAlmostEqual(self.controller.position.z, EYE_HEIGHT + 0.3, delta=0.004)
        self.assertTrue(self.controller.grounded)
        self.run_for(1)
        self.assertGreater(self.controller.position.y, 10)
        self.assertAlmostEqual(self.controller.position.z, EYE_HEIGHT, delta=0.002)

    def test_coyote_jump_works_after_leaving_a_ledge(self):
        height = lambda x, y: 0 if y < 2 else -8
        self.controller.keys["w"] = True
        self.run_for(0.3, height_fn=height)
        self.assertGreater(self.controller.position.y, 2)
        self.assertFalse(self.controller.grounded)
        self.run_for(0.04, height_fn=height)
        self.controller.keys["space"] = True
        self.run_for(0.04, height_fn=height)
        self.assertGreater(self.controller.velocity.z, 6)
        self.assertGreater(self.controller.position.z, EYE_HEIGHT)

    def test_coyote_window_expires(self):
        height = lambda x, y: 0 if y < 2 else -8
        self.controller.keys["w"] = True
        self.run_for(0.55, height_fn=height)
        self.controller.keys["space"] = True
        self.run_for(0.05, height_fn=height)
        self.assertLess(self.controller.velocity.z, 0)
        self.assertFalse(self.controller.jetpacking)

    def test_quick_press_is_buffered_until_landing(self):
        self.controller.set_mode("surface", (0, 0, EYE_HEIGHT + 0.35))
        self.controller.velocity.z = -4
        self.controller._set_key("space", True)
        self.controller._set_key("space", False)
        self.run_for(0.16)
        self.assertGreater(self.controller.position.z, EYE_HEIGHT + 0.2)
        self.assertGreater(self.controller.velocity.z, 2)
        self.assertFalse(self.controller.grounded)
        self.assertFalse(self.controller.jetpacking)

    def test_holding_jump_goes_higher_than_tapping_without_energy(self):
        peaks = []
        for hold in (0.03, 0.65):
            self.controller.set_mode("surface", (0, 0, EYE_HEIGHT))
            peak = EYE_HEIGHT
            for frame in range(180):
                self.vitals["energy"] = 0
                self.controller.keys["space"] = frame / 120 < hold
                self.run_for(1 / 120)
                peak = max(peak, self.controller.position.z)
            peaks.append(peak)
        self.assertGreater(peaks[1], peaks[0] + 0.75)

    def test_air_brake_and_hover_cancel_fall_and_drift(self):
        self.controller.set_mode("surface", (0, 0, 20))
        self.controller.velocity = Vec3(10, 0, -20)
        self.controller.keys.update(control=True, space=True)
        self.run_for(1)
        self.assertGreater(self.controller.position.z, 16)
        self.assertLess(abs(self.controller.velocity.z), 0.1)
        self.assertLess(abs(self.controller.velocity.x), 0.01)
        self.assertTrue(self.controller.jetpacking)
        self.assertTrue(self.controller.braking)
        self.assertLess(self.vitals["energy"], 100)
        held = self.controller.position.z
        self.run_for(1)
        self.assertAlmostEqual(self.controller.position.z, held, delta=0.02)

    def test_fast_ship_sweep_uses_hull_radius_and_clips_impact(self):
        self.box("station", (0, 0, 0), (20, 0.1, 20))
        self.controller.set_mode("orbit", (0, -20, 0))
        self.controller.velocity = Vec3(0, 1800, 0)
        self.settings["flight_assist"] = False
        self.run_for(0.05)
        self.assertLess(self.controller.position.y, -3.1)
        self.assertGreater(self.controller.position.y, -3.11)
        self.assertLess(self.controller.velocity.length(), 0.001)
        self.assertGreater(self.controller.collision_feedback, 0.5)
        self.assertIn("station", self.controller.collision_ids)

    def test_surface_flight_does_not_skip_a_ridge(self):
        height = lambda x, y: 50 if 4 <= y <= 6 else 0
        self.controller.set_mode("flight", (0, 0, 20))
        self.controller.velocity = Vec3(0, 1600, 0)
        self.settings["flight_assist"] = False
        self.run_for(0.02, height_fn=height)
        self.assertLess(self.controller.position.y, 4)
        self.assertLess(self.controller.position.z, 23)
        self.assertLess(self.controller.velocity.y, 2)
        self.assertGreater(self.controller.collision_feedback, 0.5)

    def test_surface_flight_lands_at_clearance(self):
        self.controller.set_mode("flight", (0, 0, 25))
        self.controller.velocity = Vec3(0, 0, -100)
        self.settings["flight_assist"] = False
        self.run_for(0.5)
        self.assertTrue(self.controller.grounded)
        self.assertAlmostEqual(self.controller.position.z, SHIP_CLEARANCE, delta=0.003)
        self.assertAlmostEqual(self.controller.velocity.z, 0, delta=0.001)

    def test_assist_off_preserves_momentum_when_looking_and_coasting(self):
        self.controller.set_mode("orbit", (0, 0, 0))
        velocity = Vec3(30, 17, 4)
        self.controller.velocity = Vec3(velocity)
        self.controller.keys["arrow_left"] = True
        self.settings["flight_assist"] = False
        self.run_for(0.6)
        self.assertLess((self.controller.position - velocity * 0.6).length(), 0.001)
        self.assertEqual(self.controller.velocity, velocity)
        self.assertTrue(self.controller.drifting)
        self.settings["flight_assist"] = True
        self.run_for(0.6)
        self.assertLess(self.controller.speed, velocity.length() * 0.42)
        self.assertFalse(self.controller.drifting)

    def test_s_brakes_then_reverses_with_either_assist_setting(self):
        for assist in (True, False):
            with self.subTest(flight_assist=assist):
                self.controller.set_mode("flight", (0, 0, 100))
                self.controller.velocity = Vec3(0, 100, 0)
                self.controller.keys["s"] = True
                self.settings["flight_assist"] = assist
                self.run_for(0.2)
                self.assertGreater(self.controller.velocity.y, 0)
                self.assertLess(self.controller.velocity.y, 30)
                self.assertTrue(self.controller.braking)
                self.run_for(0.5)
                self.assertLess(self.controller.velocity.y, -10)

    def test_empty_tank_preserves_unassisted_coast_without_boost(self):
        self.controller.set_mode("orbit", (0, 0, 0))
        self.controller.velocity = Vec3(0, 100, 0)
        self.settings["flight_assist"] = False
        self.vitals["fuel"] = 0
        self.controller.keys.update(w=True, shift=True)
        self.run_for(1)
        self.assertAlmostEqual(self.controller.position.y, 100, delta=0.002)
        self.assertEqual(self.controller.speed, 100)
        self.assertFalse(self.controller.boosting)
        self.assertEqual(self.controller.throttle, 0)

    def test_camera_motion_is_optional_and_never_changes_physics(self):
        results = []
        for motion in (0.0, 1.0):
            self.controller.set_mode("surface", (0, 0, EYE_HEIGHT))
            self.settings.update(camera_motion=motion, fov=83)
            self.controller.keys.update(w=True, d=True, shift=True, arrow_left=True)
            self.run_for(1)
            results.append(Vec3(self.controller.position))
            if motion == 0:
                self.assertEqual(self.app.camera.getPos(), self.controller.position)
                self.assertEqual(self.app.camera.getR(), 0)
                self.assertEqual(self.controller.fov_offset, 0)
                self.assertAlmostEqual(self.app.camLens.getHfov(), 83, places=4)
            else:
                self.assertGreater(self.controller.camera_offset.length(), 0.005)
                self.assertGreater(abs(self.app.camera.getR()), 0.5)
                self.assertGreater(self.app.camLens.getHfov(), 85)
        self.assertEqual(results[0], results[1])
        self.settings["camera_motion"] = 0
        self.run_for(1 / 60)
        self.assertEqual(self.app.camera.getPos(), self.controller.position)
        self.assertEqual(self.app.camera.getR(), 0)
        self.assertEqual(self.controller.fov_offset, 0)
        self.assertAlmostEqual(self.app.camLens.getHfov(), 83, places=4)

    def test_mouse_smoothing_retains_total_pointer_motion(self):
        self.app.win = FakeWindow()
        self.controller.set_enabled(False)
        self.controller.set_enabled(True)
        self.settings["mouse_smoothing"] = 1
        self.app.win.x += 50
        self.run_for(1 / 60)
        initial_turn = 360 - self.controller.heading
        self.assertGreater(initial_turn, 0)
        self.assertLess(initial_turn, 8)
        self.run_for(1)
        self.assertAlmostEqual(self.controller.heading, 352, delta=0.005)

    def test_sprint_and_steering_are_stable_across_frame_rates(self):
        for mode in ("surface", "flight", "orbit"):
            positions = []
            velocities = []
            for dt in (1 / 30, 1 / 60, 1 / 144):
                self.controller.set_mode(mode, (0, 0, EYE_HEIGHT if mode == "surface" else 100))
                self.controller.keys.update(w=True, shift=True, arrow_left=True)
                self.run_for(1.5, dt)
                positions.append(Vec3(self.controller.position))
                velocities.append(Vec3(self.controller.velocity))
            for position, velocity in zip(positions[1:], velocities[1:]):
                self.assertLess((position - positions[0]).length(), 0.03, mode)
                self.assertLess((velocity - velocities[0]).length(), 0.03, mode)

    def test_settings_and_collision_world_can_be_replaced_safely(self):
        self.box("wall", (0, 2, 2), (2, 0.2, 2))
        self.controller.keys["w"] = True
        self.run_for(0.3)
        self.assertIn("wall", self.controller.collision_ids)
        self.controller.set_collision_world(None)
        self.assertEqual(self.controller.collision_ids, ())
        self.settings.update(fov=math.nan, camera_motion=math.inf, mouse_smoothing=-10)
        self.run_for(0.5)
        self.assertGreater(self.controller.position.y, 4)
        self.assertTrue(all(math.isfinite(value) for value in (*self.app.camera.getPos(),
                                                             self.controller.fov)))


if __name__ == "__main__":
    unittest.main()
