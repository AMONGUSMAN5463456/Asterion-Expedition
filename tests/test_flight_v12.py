"""Rigid-frame and atmosphere continuity without opening a graphics window."""

import math
import unittest
from types import SimpleNamespace

from panda3d.core import NodePath, PerspectiveLens, Vec3

from asterion.collision import CollisionWorld, MoveResult
from asterion.controller import EYE_HEIGHT, SHIP_RADIUS, PlayerController
from asterion.planetary import PlanetFrame, frame_to_world, vector_to_world


class FlightV12Tests(unittest.TestCase):
    def setUp(self):
        self.instances = []
        self.controller, self.app = self.make_controller()
        self.settings = {"camera_motion": 0.6, "flight_assist": True,
                         "mouse_smoothing": 0, "fov": 81}
        self.vitals = {"energy": 100.0, "fuel": 100.0}
        self.planet = {"id": "far-world", "size": 6200.0,
                       "position": [48000.0, -18000.0, 22000.0]}

    def tearDown(self):
        for controller, app in self.instances:
            controller.destroy()
            app.camera.removeNode()

    def make_controller(self):
        app = SimpleNamespace(camera=NodePath("camera"), camLens=PerspectiveLens(),
                              win=None, disableMouse=lambda: None)
        controller = PlayerController(app)
        controller.set_mode("flight", (0, 0, 1000))
        controller.set_enabled(True)
        self.instances.append((controller, app))
        return controller, app

    @staticmethod
    def no_contact(position, displacement, radius):
        return MoveResult(position + displacement, [], False, False, [])

    def run_for(self, controller, seconds, vitals=None, settings=None):
        vitals = self.vitals if vitals is None else vitals
        settings = self.settings if settings is None else settings
        remaining = seconds
        while remaining > 1e-9:
            dt = min(1 / 60, remaining)
            controller.update(dt, lambda x, y: 0, vitals, {}, settings)
            remaining -= dt

    def assert_vector_close(self, actual, expected, tolerance=0.012):
        self.assertLess((actual - expected).length(), tolerance,
                        f"{actual} differs from {expected}")

    def world_pose(self, controller, app, frame):
        attitude = app.camera.getQuat()
        return (frame_to_world(frame, controller.position),
                vector_to_world(frame, controller.velocity),
                frame_to_world(frame, app.camera.getPos()),
                vector_to_world(frame, attitude.getForward()),
                vector_to_world(frame, attitude.getUp()))

    def test_reframe_preserves_pose_roll_and_state_in_both_directions(self):
        frames = [None] + [PlanetFrame.from_planet(self.planet, normal) for normal in
                           ((0, 0, 1), (0, 1, 0), (0, -1, 0),
                            (0.3, -0.4, -0.8660254), (0, 0, -1))]
        controller, app = self.controller, self.app
        for old_frame in frames:
            for new_frame in frames:
                with self.subTest(old=old_frame, new=new_frame):
                    controller.set_mode("flight", (127.25, -361.5, 1873.0), 123, 67)
                    controller.reference_roll = 47.0
                    controller.camera_roll = -3.4
                    controller._roll = -8.5
                    controller.camera_offset = Vec3(0.15, -0.32, 0.21)
                    controller.velocity = Vec3(132.5, -78.75, 460.25)
                    controller.speed = controller.velocity.length()
                    controller.throttle = 0.73
                    controller.fov_offset, controller.fov = 4.25, 85.25
                    controller.collision_feedback = 0.67
                    controller.collision_ids = ("terrain", "asteroid")
                    controller.keys.update(w=True, d=True, shift=True, space=True)
                    controller.mouse_down = controller._drag_down = True
                    controller._space_hold = 0.71
                    controller._jump_buffer = 0.08
                    controller._mouse_velocity = Vec3(22, -14, 0)
                    controller.set_space_blend(0.42)
                    controller.set_flight_collision(self.no_contact)
                    controller._apply_camera()
                    before = self.world_pose(controller, app, old_frame)
                    kept = (dict(controller.keys), controller.speed, controller.throttle,
                            controller.fov, controller.fov_offset, controller.camera_roll,
                            controller._roll, controller.collision_feedback,
                            controller.collision_ids, controller._space_hold,
                            controller._jump_buffer, Vec3(controller._mouse_velocity))

                    controller.reframe(old_frame, new_frame)

                    after = self.world_pose(controller, app, new_frame)
                    for index, (actual, expected) in enumerate(zip(after, before)):
                        self.assert_vector_close(actual, expected,
                                                 3e-5 if index >= 3 else 0.015)
                    self.assertEqual(kept, (dict(controller.keys), controller.speed,
                                           controller.throttle, controller.fov,
                                           controller.fov_offset, controller.camera_roll,
                                           controller._roll, controller.collision_feedback,
                                           controller.collision_ids, controller._space_hold,
                                           controller._jump_buffer, controller._mouse_velocity))
                    self.assertTrue(controller.mouse_down and controller._drag_down)
                    self.assert_vector_close(controller.forward(),
                                             app.camera.getQuat().getForward(), 2e-6)
                    self.assertAlmostEqual(app.camLens.getHfov(), 85.25, places=4)

    def test_polar_reframe_stays_exact_on_following_unsteered_update(self):
        frame = PlanetFrame.from_planet(self.planet, (0, 1, 0))
        controller, app = self.controller, self.app
        controller.set_mode("orbit", frame.to_world(Vec3(0, 0, 1600)), 0, 0)
        controller.reference_roll = 32.0
        controller.set_space_blend(0.7)
        controller.set_flight_collision(self.no_contact)
        controller._apply_camera()
        before = self.world_pose(controller, app, None)
        controller.reframe(None, frame)
        controller.mode = "flight"
        self.assertAlmostEqual(abs(controller.pitch), 90.0, places=3)
        self.run_for(controller, 0.1, settings={"camera_motion": 0})
        after = self.world_pose(controller, app, frame)
        for index, (actual, expected) in enumerate(zip(after, before)):
            self.assert_vector_close(actual, expected, 3e-5 if index >= 3 else 0.015)

    def test_held_ship_axes_and_steering_stay_physical_after_reframe(self):
        frame = PlanetFrame.from_planet(self.planet, (0.2, -0.5, -0.84))
        control, app = self.make_controller()
        for controller in (control, self.controller):
            controller.set_mode("orbit", frame.to_world(Vec3(160, 220, 1600)), 213, 24)
            controller.reference_roll = 38
            controller.velocity = Vec3(-84, 19, 110)
            controller.keys.update(w=True, d=True, space=True, shift=True,
                                   arrow_left=True, arrow_up=True)
            controller.set_space_blend(0.64)
            controller.set_flight_collision(self.no_contact)
            controller._apply_camera()
        self.controller.reframe(None, frame)
        self.controller.mode = "flight"
        self.run_for(control, 0.2, vitals={"fuel": 100}, settings={"camera_motion": 0})
        self.run_for(self.controller, 0.2, vitals={"fuel": 100},
                     settings={"camera_motion": 0})
        control_pose = self.world_pose(control, app, None)
        framed_pose = self.world_pose(self.controller, self.app, frame)
        for index, (actual, expected) in enumerate(zip(framed_pose, control_pose)):
            self.assert_vector_close(actual, expected, 5e-5 if index >= 3 else 0.035)

    def test_mode_label_crossings_do_not_change_dynamics_fuel_or_camera(self):
        for blend in (0.0, 0.4, 1.0):
            with self.subTest(blend=blend):
                control, _ = self.make_controller()
                vitals = [{"fuel": 100}, {"fuel": 100}]
                for controller in (control, self.controller):
                    controller.set_mode("flight", (0, 0, 1200), 23, 11)
                    controller.velocity = Vec3(17, 80, 9)
                    controller.set_space_blend(blend)
                    controller.set_flight_collision(self.no_contact)
                    controller.keys.update(w=True, d=True, shift=True)
                for frame in range(100):
                    if frame in (15, 65):
                        self.controller.mode = "orbit"
                    elif frame == 45:
                        self.controller.mode = "flight"
                    if frame == 60:
                        for controller in (control, self.controller):
                            controller.keys.update(w=False, d=False, shift=False)
                    for controller, fuel in zip((control, self.controller), vitals):
                        controller.update(1 / 60, lambda x, y: 0, fuel, {}, self.settings)
                    self.assertEqual(self.controller.position, control.position)
                    self.assertEqual(self.controller.velocity, control.velocity)
                    self.assertEqual(self.controller.throttle, control.throttle)
                    self.assertEqual(self.controller.fov, control.fov)
                    self.assertEqual(self.controller.camera_roll, control.camera_roll)
                    self.assertEqual(vitals[0], vitals[1])

    def test_atmospheric_tuning_has_continuous_cruise_and_boost_endpoints(self):
        for blend, cruise, boost in ((0.0, 35, 100), (0.5, 72.5, 450), (1.0, 110, 800)):
            for boosted, target in ((False, cruise), (True, boost)):
                with self.subTest(blend=blend, boosted=boosted):
                    controller = self.controller
                    controller.set_mode("flight", (0, 0, 1000))
                    controller.set_space_blend(blend)
                    controller.set_flight_collision(self.no_contact)
                    controller.keys.update(w=True, shift=boosted)
                    self.run_for(controller, 6, vitals={"fuel": 100})
                    self.assertAlmostEqual(controller.speed, target, delta=0.015)

    def test_blend_damping_and_boost_fuel_ignore_mode_and_none_restores_defaults(self):
        controller = self.controller
        controller.set_space_blend(0.5)
        controller.velocity = Vec3(0, 100, 0)
        self.run_for(controller, 1)
        self.assertAlmostEqual(controller.speed, 100 * math.exp(-2.25), delta=0.001)
        controller.keys.update(w=True, shift=True)
        self.run_for(controller, 1)
        self.assertAlmostEqual(self.vitals["fuel"], 99.81, places=6)
        for mode, expected in (("flight", 35), ("orbit", 110)):
            controller.set_mode(mode, (0, 0, 1000))
            controller.set_space_blend(None)
            controller.keys["w"] = True
            self.run_for(controller, 6)
            self.assertAlmostEqual(controller.speed, expected, delta=0.01)

    def test_one_flight_callback_sweeps_hull_in_both_modes_without_planar_ground(self):
        world = CollisionWorld()
        world.set_group("wall", [{"id": "wall", "type": "box",
                                  "center": (0, 0, -250), "half": (30, 0.1, 30)}])

        def forbidden(*args, **kwargs):
            self.fail("Flight callback must own terrain and solid collision")

        for mode in ("flight", "orbit"):
            with self.subTest(mode=mode):
                calls = []

                def sweep(position, displacement, radius):
                    calls.append((Vec3(position), Vec3(displacement), radius))
                    return world.move_sphere(position, displacement, radius=radius)

                controller = self.controller
                controller.set_mode(mode, (0, -20, -250))
                controller.set_collision_world(SimpleNamespace(move_sphere=forbidden))
                controller.set_flight_collision(sweep)
                controller.velocity = Vec3(7, 1800, 5)
                controller.update(0.05, forbidden, self.vitals, {},
                                  {"flight_assist": False, "camera_motion": 0})
                self.assertEqual(len(calls), 6)
                self.assertTrue(all(call[2] == SHIP_RADIUS for call in calls))
                self.assertAlmostEqual(controller.position.y, -3.101, delta=0.003)
                self.assertAlmostEqual(controller.position.x, 0.35, delta=0.003)
                self.assertAlmostEqual(controller.position.z, -249.75, delta=0.003)
                self.assertAlmostEqual(controller.velocity.y, 0, delta=0.001)
                self.assertAlmostEqual(controller.velocity.x, 7, delta=0.001)
                self.assertGreater(controller.collision_feedback, 0.5)
                self.assertIn("wall", controller.collision_ids)

    def test_flight_ceiling_contact_retains_velocity_tangent_to_tilted_surface(self):
        normal = Vec3(0, 1, -1).normalized()

        def sweep(position, displacement, radius):
            return MoveResult(position, [normal], False, True, ["far-hemisphere"])

        self.controller.set_mode("orbit", (0, 0, 0))
        self.controller.set_flight_collision(sweep)
        self.controller.velocity = Vec3(10, 0, 100)
        self.controller.update(1 / 120, lambda x, y: 0, self.vitals, {},
                               {"flight_assist": False, "camera_motion": 0})
        self.assert_vector_close(self.controller.velocity, Vec3(10, 50, 50), 0.001)
        self.assertTrue(self.controller.ceiling)

    def test_walking_keeps_capsule_collision_when_flight_adapter_exists(self):
        world = CollisionWorld()
        world.set_group("wall", [{"id": "wall", "type": "box",
                                  "center": (0, 3, 2), "half": (20, 0.2, 2)}])

        def forbidden(*args, **kwargs):
            self.fail("Walking must continue to use the capsule world")

        self.controller.set_mode("surface", (0, 0, EYE_HEIGHT))
        self.controller.set_collision_world(world)
        self.controller.set_flight_collision(forbidden)
        self.controller.keys["w"] = True
        self.run_for(self.controller, 1)
        self.assertLess(self.controller.position.y, 2.43)
        self.assertAlmostEqual(self.controller.position.z, EYE_HEIGHT, places=4)
        self.assertIn("wall", self.controller.collision_ids)
        self.assertTrue(self.controller.grounded)

    def test_explicit_set_mode_resets_physical_roll(self):
        self.controller.reference_roll = 76
        self.controller.camera_roll = 4
        self.controller.set_mode("surface", (0, 0, EYE_HEIGHT))
        self.assertEqual(self.controller.reference_roll, 0)
        self.assertEqual(self.app.camera.getR(), 0)

    def test_restoring_vertical_flight_pose_does_not_clamp_saved_pitch(self):
        controller = self.controller
        for mode in ("flight", "orbit"):
            for pitch in (-90.0, -88.5, 88.5, 90.0):
                with self.subTest(mode=mode, pitch=pitch):
                    controller.set_mode(mode, (60, 120, 1500), 24.0, pitch)
                    controller.reference_roll = -62.0
                    controller._apply_camera()
                    attitude = self.app.camera.getQuat()
                    controller.set_mode(mode, controller.position, controller.heading,
                                        controller.pitch)
                    controller.reference_roll = -62.0
                    controller._apply_camera()
                    self.assertEqual(controller.pitch, pitch)
                    self.assert_vector_close(self.app.camera.getQuat().getForward(),
                                             attitude.getForward(), 2e-6)
                    self.assert_vector_close(self.app.camera.getQuat().getUp(),
                                             attitude.getUp(), 2e-6)


if __name__ == "__main__":
    unittest.main()
