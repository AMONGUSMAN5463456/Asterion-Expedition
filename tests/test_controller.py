"""Movement, collision, and pointer lifecycle checks without opening a window."""

import math
import unittest
from types import SimpleNamespace

from direct.showbase.DirectObject import DirectObject
from direct.showbase.MessengerGlobal import messenger
from panda3d.core import NodePath, Vec3, WindowProperties

from asterion.controller import EYE_HEIGHT, SHIP_CLEARANCE, PlayerController


class FakeWindow:
    def __init__(self, can_warp=True):
        self.width, self.height = 1280, 720
        self.x, self.y = 640, 360
        self.can_warp = can_warp
        self.hidden = False
        self.properties = WindowProperties()
        self.properties.setForeground(True)

    def getXSize(self):
        return self.width

    def getYSize(self):
        return self.height

    def getPointer(self, index):
        return SimpleNamespace(getX=lambda: self.x, getY=lambda: self.y,
                               getInWindow=lambda: True)

    def movePointer(self, index, x, y):
        if self.can_warp:
            self.x, self.y = x, y
        return self.can_warp

    def requestProperties(self, properties):
        self.hidden = properties.getCursorHidden()

    def getProperties(self):
        return self.properties


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.app = SimpleNamespace(camera=NodePath("camera"), win=None,
                                   disableMouse=lambda: None)
        self.controller = PlayerController(self.app)
        self.vitals = {"energy": 100.0, "fuel": 100.0}
        self.settings = {"sensitivity": 0.16}
        self.controller.set_mode("surface", (0, 0, EYE_HEIGHT))
        self.controller.set_enabled(True)

    def tearDown(self):
        self.controller.destroy()
        self.app.camera.removeNode()

    def simulate(self, seconds, dt=1 / 60, height_fn=lambda x, y: 0, **kwargs):
        frames = round(seconds / dt)
        for _ in range(frames):
            self.controller.update(dt, height_fn, self.vitals, {}, self.settings, **kwargs)

    def test_forward_matches_panda_camera(self):
        for heading, pitch in ((0, 0), (90, 30), (-45, -70), (280, 85)):
            self.controller.set_mode("surface", (0, 0, 5), heading, pitch)
            expected = self.app.camera.getQuat().getForward()
            self.assertLess((self.controller.forward() - expected).length(), 1e-5)

    def test_walk_sprint_and_diagonal_have_expected_speeds(self):
        self.controller.keys["w"] = True
        self.simulate(1)
        self.assertAlmostEqual(self.controller.speed, 9, delta=0.02)
        self.assertGreater(self.controller.position.y, 8)
        self.assertAlmostEqual(self.controller.position.z, EYE_HEIGHT, places=5)
        self.controller.keys["d"] = True
        self.simulate(1)
        self.assertAlmostEqual(self.controller.speed, 9, delta=0.02)
        self.controller.keys["shift"] = True
        self.simulate(1)
        self.assertAlmostEqual(self.controller.speed, 16, delta=0.02)

    def test_heading_walks_in_panda_direction_without_pitch_affecting_speed(self):
        self.controller.set_mode("surface", (0, 0, EYE_HEIGHT), 90, 80)
        self.controller.keys["w"] = True
        self.simulate(1)
        self.assertLess(self.controller.position.x, -8)
        self.assertAlmostEqual(self.controller.position.y, 0, places=4)
        self.assertAlmostEqual(self.controller.position.z, EYE_HEIGHT, places=5)

    def test_smooth_terrain_follows_floor_and_cliff_blocks(self):
        self.controller.keys["w"] = True
        self.simulate(1, height_fn=lambda x, y: y * 0.3)
        self.assertAlmostEqual(self.controller.position.z,
                               self.controller.position.y * 0.3 + EYE_HEIGHT, places=5)
        self.controller.set_mode("surface", (0, 0, EYE_HEIGHT))
        self.controller.keys["w"] = True
        self.simulate(2, height_fn=lambda x, y: 0 if y < 2 else 12)
        self.assertLess(self.controller.position.y, 2)
        self.assertAlmostEqual(self.controller.position.z, EYE_HEIGHT, places=5)

    def test_jump_does_not_require_energy_and_lands_at_eye_height(self):
        self.vitals["energy"] = 0
        self.controller.keys["space"] = True
        self.simulate(0.1)
        self.assertGreater(self.controller.position.z, EYE_HEIGHT + 0.5)
        self.assertFalse(self.controller.grounded)
        self.controller.keys["space"] = False
        self.simulate(2)
        self.assertTrue(self.controller.grounded)
        self.assertAlmostEqual(self.controller.position.z, EYE_HEIGHT, places=5)

    def test_holding_space_jetpacks_and_regenerates_after_release(self):
        self.controller.keys["space"] = True
        self.simulate(2)
        self.assertGreater(self.controller.position.z, 13)
        self.assertTrue(self.controller.jetpacking)
        self.assertLess(self.vitals["energy"], 80)
        self.controller.keys["space"] = False
        self.simulate(8)
        self.assertTrue(self.controller.grounded)
        self.assertFalse(self.controller.jetpacking)
        self.assertAlmostEqual(self.vitals["energy"], 100, places=4)

    def test_surface_flight_accelerates_boosts_brakes_and_clears_ground(self):
        self.controller.set_mode("flight", (0, 0, 15))
        self.controller.keys["w"] = True
        self.simulate(2)
        self.assertAlmostEqual(self.controller.speed, 35, delta=0.2)
        self.controller.keys["shift"] = True
        self.simulate(2)
        self.assertAlmostEqual(self.controller.speed, 100, delta=0.2)
        self.assertTrue(self.controller.boosting)
        self.assertGreater(self.vitals["fuel"], 99)
        self.controller.keys["w"] = False
        self.controller.keys["shift"] = False
        self.controller.keys["s"] = True
        self.simulate(0.5)
        self.assertLess(self.controller.velocity.y, 0)
        self.controller.keys["control"] = True
        self.simulate(2)
        self.assertGreaterEqual(self.controller.position.z, SHIP_CLEARANCE)

    def test_orbit_does_not_sample_surface_and_uses_modest_fuel(self):
        def forbidden_height(x, y):
            self.fail("Orbit must not sample a surface height")

        self.controller.set_mode("orbit", (0, 0, -500))
        self.controller.keys.update(w=True, shift=True)
        self.simulate(3, height_fn=forbidden_height)
        self.assertAlmostEqual(self.controller.speed, 800, delta=3)
        self.assertGreater(self.controller.position.y, 1900)
        self.assertGreater(self.vitals["fuel"], 99)
        self.assertAlmostEqual(self.controller.position.z, -500, places=4)

    def test_empty_fuel_stops_thrust_without_nan(self):
        self.controller.set_mode("orbit", (0, 0, 0))
        self.vitals["fuel"] = 0
        self.controller.keys.update(w=True, shift=True)
        self.simulate(1)
        self.assertEqual(self.controller.speed, 0)
        self.assertEqual(self.vitals["fuel"], 0)
        self.assertFalse(self.controller.boosting)

    def test_disabling_resets_input_and_freezes_motion(self):
        self.controller.keys["w"] = True
        self.simulate(0.5)
        position = Vec3(self.controller.position)
        self.controller.mouse_down = True
        self.controller.set_enabled(False)
        self.assertFalse(any(self.controller.keys.values()))
        self.assertFalse(self.controller.mouse_down)
        self.simulate(1, enabled=False)
        self.assertEqual(self.controller.position, position)

    def test_frame_rate_independence_for_walk_and_flight(self):
        for mode in ("surface", "flight", "orbit"):
            results = []
            for dt in (1 / 30, 1 / 60, 1 / 144):
                self.controller.set_mode(mode, (0, 0, EYE_HEIGHT if mode == "surface" else 100))
                self.controller.keys["w"] = True
                self.simulate(2, dt)
                results.append(Vec3(self.controller.position))
            for result in results[1:]:
                self.assertLess((results[0] - result).length(), 0.02, mode)

    def test_bad_numbers_and_long_frame_do_not_corrupt_camera(self):
        self.controller.set_mode("surface", (math.nan, math.inf, 1.8), math.nan, math.inf)
        self.controller.keys["w"] = True
        self.controller.update(1e10, lambda x, y: math.nan, self.vitals, {}, self.settings)
        self.assertLess(self.controller.position.length(), 5)
        for dt in (math.nan, math.inf, -1, None):
            self.controller.update(dt, lambda x, y: math.inf, self.vitals, {}, self.settings)
        for value in (*self.controller.position, self.controller.heading,
                      self.controller.pitch, self.controller.speed):
            self.assertTrue(math.isfinite(value))
        self.assertEqual(self.app.camera.getPos(),
                         self.controller.position + self.controller.camera_offset)
        self.assertLess(self.controller.camera_offset.length(), 0.1)

    def test_arrows_turn_without_pointer_and_clamp_pitch(self):
        self.controller.keys.update(arrow_right=True, arrow_up=True)
        self.simulate(2)
        self.assertAlmostEqual(self.controller.heading, 160, places=4)
        self.assertEqual(self.controller.pitch, 85)

    def test_mouse_capture_recenters_and_release_shows_cursor(self):
        window = FakeWindow()
        self.app.win = window
        self.controller.set_enabled(False)
        self.controller.set_enabled(True)
        self.assertTrue(window.hidden)
        window.x += 20
        window.y -= 10
        self.simulate(1 / 60)
        self.assertAlmostEqual(self.controller.heading, 356.8)
        self.assertAlmostEqual(self.controller.pitch, 1.6)
        self.assertEqual((window.x, window.y), (640, 360))
        self.controller.set_enabled(False)
        self.assertFalse(window.hidden)

    def test_drag_fallback_and_inverted_look(self):
        self.app.win = FakeWindow(can_warp=False)
        self.controller.set_enabled(False)
        self.controller.set_enabled(True)
        self.assertFalse(self.app.win.hidden)
        self.controller._set_mouse(3, True)
        self.simulate(1 / 60)
        self.app.win.x += 10
        self.app.win.y += 10
        self.settings["invert_y"] = True
        self.simulate(1 / 60)
        self.assertAlmostEqual(self.controller.heading, 358.4)
        self.assertAlmostEqual(self.controller.pitch, 1.6)

    def test_focus_loss_releases_pointer_and_clears_keys(self):
        self.app.win = FakeWindow()
        self.controller.set_enabled(False)
        self.controller.set_enabled(True)
        self.controller.keys["w"] = True
        self.app.win.properties.setForeground(False)
        messenger.send("window-event", [self.app.win])
        self.assertFalse(self.app.win.hidden)
        self.assertFalse(any(self.controller.keys.values()))
        position = Vec3(self.controller.position)
        self.simulate(1)
        self.assertEqual(position, self.controller.position)

    def test_modifier_events_and_destroy_preserve_application_hotkeys(self):
        other = DirectObject()
        called = []
        other.accept("e", lambda: called.append("interact"))
        try:
            messenger.send("shift-w")
            self.assertTrue(self.controller.keys["w"])
            messenger.send("w-up")
            self.assertFalse(self.controller.keys["w"])
            self.controller.destroy()
            messenger.send("e")
            self.assertEqual(called, ["interact"])
        finally:
            other.ignoreAll()


if __name__ == "__main__":
    unittest.main()
