"""Quantum travel owns its timing and follows every verified route segment."""

from itertools import repeat
import math
import unittest

from panda3d.core import Vec3

from asterion.navigation import plan_route
from asterion.quantum import (
    COOLDOWN_DURATION, MAX_SPEED, MAX_WAYPOINTS, SPOOL_DURATION, QuantumDrive,
)


class QuantumTests(unittest.TestCase):
    def prepared(self, route=None, start=(0, 0, 0)):
        drive = QuantumDrive()
        self.assertTrue(drive.begin(start, route or [(400000, 0, 0)], "Destination", 100.0))
        return drive

    def travelling(self, route=None, start=(0, 0, 0)):
        drive = self.prepared(route, start)
        drive.advance(SPOOL_DURATION)
        self.assertTrue(drive.launch(100.0))
        return drive

    def test_spool_requires_separate_activation_then_arrival_and_cooldown(self):
        drive = self.prepared()
        start = drive.position
        self.assertTrue(drive.active)
        self.assertFalse(drive.travelling)
        self.assertEqual(drive.target_name, "Destination")
        self.assertFalse(drive.launch(100.0))
        self.assertEqual(drive.advance(1.5), start)
        self.assertEqual(drive.spool_progress, .5)
        drive.advance(1000.0)
        self.assertEqual(drive.phase, "ready")
        self.assertEqual(drive.spool_progress, 1.0)
        self.assertEqual(drive.position, start)
        self.assertEqual(drive.velocity, Vec3(0))
        drive.advance(1000.0)
        self.assertEqual(drive.phase, "ready")
        self.assertTrue(drive.launch(100.0))
        self.assertTrue(drive.travelling)
        self.assertFalse(drive.launch(100.0))
        drive.advance(drive.duration)
        self.assertEqual(drive.phase, "cooldown")
        self.assertFalse(drive.active)
        self.assertEqual(drive.position, Vec3(400000, 0, 0))
        self.assertEqual(drive.velocity, Vec3(0))
        self.assertEqual(drive.progress, 1.0)
        self.assertEqual(drive.remaining, 0.0)
        self.assertEqual(drive.eta, 0.0)
        self.assertEqual(drive.cooldown_remaining, COOLDOWN_DURATION)
        self.assertFalse(drive.begin(drive.position, [(800000, 0, 0)], "Other", 100.0))
        drive.advance(COOLDOWN_DURATION / 2)
        self.assertEqual(drive.cooldown_progress, .5)
        drive.advance(COOLDOWN_DURATION / 2)
        self.assertEqual(drive.phase, "idle")
        self.assertTrue(drive.begin(drive.position, [(800000, 0, 0)], "Other", 100.0))

    def test_cost_uses_full_detour_and_launch_rechecks_fuel(self):
        drive = QuantumDrive()
        route = [(100000, 0, 0), (100000, 100000, 0)]
        self.assertFalse(drive.begin((0, 0, 0), route, "Planet", 4.999))
        self.assertEqual(drive.phase, "idle")
        fuel = 5.0
        self.assertTrue(drive.begin((0, 0, 0), route, "Planet", fuel))
        self.assertEqual(drive.cost, 5.0)
        self.assertEqual(drive.total_distance, 200000.0)
        self.assertEqual(fuel, 5.0)
        drive.advance(SPOOL_DURATION)
        for invalid in (4.999, 0, -1, math.nan, math.inf, None, "bad"):
            self.assertFalse(drive.launch(invalid))
            self.assertEqual(drive.phase, "ready")
        self.assertTrue(drive.launch(fuel))
        fuel -= drive.cost
        self.assertEqual(fuel, 0.0)
        self.assertFalse(drive.launch(5.0))

    def test_prelaunch_cancel_is_free_and_transit_cancel_stops_in_place(self):
        for spool in (1.0, SPOOL_DURATION):
            drive = self.prepared()
            drive.advance(spool)
            self.assertTrue(drive.cancel())
            self.assertEqual(drive.phase, "idle")
            self.assertEqual(drive.position, Vec3(0))
            self.assertEqual(drive.cooldown_remaining, 0.0)
        drive = self.travelling()
        drive.advance(2.0)
        position, progress = drive.position, drive.progress
        self.assertGreater(drive.velocity.length(), 0)
        self.assertTrue(drive.cancel())
        self.assertEqual(drive.phase, "cooldown")
        self.assertEqual(drive.position, position)
        self.assertEqual(drive.progress, progress)
        self.assertEqual(drive.velocity, Vec3(0))
        self.assertFalse(drive.cancel())
        drive.advance(100.0)
        self.assertEqual(drive.position, position)
        self.assertEqual(drive.velocity, Vec3(0))
        self.assertEqual(drive.phase, "idle")

    def test_caller_can_correct_to_collision_point_even_after_large_frame(self):
        for elapsed in (2.0, 100000.0):
            drive = self.travelling()
            drive.advance(elapsed)
            safe_position = Vec3(20, 0, 0)
            self.assertTrue(drive.cancel(position=safe_position))
            self.assertEqual(drive.position, safe_position)
            self.assertEqual(drive.phase, "cooldown")
            self.assertEqual(drive.cooldown_remaining, COOLDOWN_DURATION)
            self.assertEqual(drive.velocity, Vec3(0))
            self.assertEqual(drive.motion_points, ())

    def test_large_step_reports_every_corner_in_order_without_overshoot(self):
        route = [(100000, 0, 0), (100000, 100000, 0), (200000, 100000, 0),
                 (200000, 100000, 50000)]
        drive = self.travelling(route)
        self.assertEqual(drive.next_direction, Vec3(1, 0, 0))
        self.assertEqual(drive.advance(1e30), Vec3(*route[-1]))
        self.assertEqual(drive.motion_points, tuple(Vec3(*point) for point in route))
        self.assertEqual(drive.velocity, Vec3(0))
        self.assertEqual(drive.phase, "idle")
        self.assertEqual(drive.progress, 1.0)
        self.assertEqual(drive.next_direction, Vec3(0))
        drive.advance(1e30)
        self.assertEqual(drive.position, Vec3(*route[-1]))
        self.assertEqual(drive.motion_points, ())

    def test_partial_frame_reports_corner_and_continues_only_on_next_segment(self):
        drive = self.travelling([(100000, 0, 0), (100000, 100000, 0)])
        drive.advance(drive.duration * .6)
        self.assertEqual(len(drive.motion_points), 2)
        self.assertEqual(drive.motion_points[0], Vec3(100000, 0, 0))
        self.assertEqual(drive.motion_points[-1], drive.position)
        self.assertEqual(drive.position.x, 100000)
        self.assertGreater(drive.position.y, 0)
        self.assertLess(drive.position.y, 100000)
        self.assertEqual(drive.next_direction, Vec3(0, 1, 0))
        self.assertEqual(drive.velocity.x, 0)
        previous = drive.position
        drive.advance(.1)
        self.assertEqual(len(drive.motion_points), 1)
        self.assertEqual(drive.position.x, previous.x)
        self.assertGreater(drive.position.y, previous.y)

    def test_route_around_planet_keeps_clear_sweeps_when_frame_skips_corners(self):
        start, goal = (-150000, 0, 0), (150000, 0, 0)
        radius = 70000.0
        route = plan_route(start, goal, [{"center": (0, 0, 0), "radius": radius}])
        self.assertGreater(len(route), 1)
        drive = self.travelling(route, start)
        drive.advance(drive.duration)
        previous = Vec3(*start)
        for point in drive.motion_points:
            vector = point - previous
            fraction = max(0.0, min(1.0, -previous.dot(vector) / vector.lengthSquared()))
            self.assertGreaterEqual((previous + vector*fraction).length(), radius - .1)
            previous = point
        self.assertEqual(previous, Vec3(*goal))

    def test_frame_rate_independence_and_elapsed_cooldown(self):
        route = [(100000, 0, 0), (100000, 100000, 0), (300000, 100000, 30000)]
        for elapsed in (0.75, 2.25, 4.75, 9.25, 20.0):
            coarse, fine = self.travelling(route), self.travelling(route)
            coarse.advance(elapsed)
            for _ in range(240):
                fine.advance(elapsed / 240.0)
            self.assertLess((coarse.position - fine.position).length(), .02)
            self.assertLess((coarse.velocity - fine.velocity).length(), .02)
            self.assertAlmostEqual(coarse.progress, fine.progress, places=10)
            self.assertAlmostEqual(coarse.eta, fine.eta, places=10)
            self.assertAlmostEqual(coarse.cooldown_remaining, fine.cooldown_remaining, places=10)
            self.assertEqual(coarse.phase, fine.phase)

    def test_speed_ramps_to_fast_cruise_and_brakes_to_zero(self):
        drive = self.travelling()
        speeds = []
        for _ in range(3):
            drive.advance(.5)
            speeds.append(drive.velocity.length())
        self.assertGreater(speeds[0], 0)
        self.assertLess(speeds[0], speeds[1])
        self.assertLess(speeds[1], speeds[2])
        self.assertAlmostEqual(speeds[-1], MAX_SPEED, places=2)
        self.assertGreater(MAX_SPEED, 5000 * 10)
        self.assertLess(drive.duration, 10.0)
        drive.advance(drive.duration - 3.0)
        braking = []
        for _ in range(3):
            drive.advance(.5)
            braking.append(drive.velocity.length())
        self.assertGreater(braking[0], braking[1])
        self.assertGreater(braking[1], braking[2])
        self.assertEqual(braking[-1], 0)
        self.assertEqual(drive.position, Vec3(400000, 0, 0))

    def test_exact_phase_boundaries_at_normal_frame_rates_do_not_stick(self):
        drive = self.prepared()
        for _ in range(180):
            drive.advance(1 / 60)
        self.assertEqual(drive.phase, "ready")
        self.assertTrue(drive.launch(100.0))
        step = drive.duration / 600
        for _ in range(600):
            drive.advance(step)
        self.assertEqual(drive.phase, "cooldown")
        self.assertEqual(drive.progress, 1.0)
        self.assertEqual(drive.velocity, Vec3(0))
        for _ in range(240):
            drive.advance(1 / 60)
        self.assertEqual(drive.phase, "idle")

    def test_duplicates_short_route_and_copied_vectors(self):
        start, corner, goal = Vec3(0), Vec3(1, 0, 0), Vec3(1, 1, 0)
        drive = self.prepared([start, corner, corner, goal])
        corner.x = 500
        self.assertEqual(drive.total_distance, 2.0)
        drive.waypoints[0].x = 600
        drive.position.x = 700
        self.assertEqual(drive.waypoints, (Vec3(1, 0, 0), goal))
        self.assertEqual(drive.position, Vec3(0))
        drive.advance(SPOOL_DURATION)
        self.assertTrue(drive.launch(100.0))
        drive.advance(drive.duration)
        self.assertEqual(drive.position, goal)
        drive.motion_points[-1].x = 800
        self.assertEqual(drive.position, goal)
        self.assertEqual(drive.velocity, Vec3(0))

    def test_invalid_inputs_fail_without_mutating_active_trip(self):
        invalid_points = [(math.nan, 0, 0), (0, math.inf, 0), (1e20, 0, 0),
                          None, (), (1, 2), (1, 2, 3, 4), ("bad", 0, 0)]
        for invalid in invalid_points:
            drive = QuantumDrive()
            self.assertFalse(drive.begin(invalid, [(400000, 0, 0)], "Planet", 100))
            self.assertFalse(drive.begin((0, 0, 0), [invalid], "Planet", 100))
            self.assertEqual(drive.phase, "idle")
        for route in (None, [], [(0, 0, 0)], repeat((1, 0, 0)), [(1, 0, 0)]*(MAX_WAYPOINTS + 1)):
            drive = QuantumDrive()
            self.assertFalse(drive.begin((0, 0, 0), route, "Planet", 100))
        drive = self.travelling()
        drive.advance(1.0)
        original = (drive.phase, drive.position, drive.velocity, drive.progress,
                    drive.motion_points, drive.eta)
        for dt in (math.nan, math.inf, -math.inf, -1, None, "bad"):
            with self.assertRaises(ValueError):
                drive.advance(dt)
            self.assertEqual((drive.phase, drive.position, drive.velocity, drive.progress,
                              drive.motion_points, drive.eta), original)
        self.assertFalse(drive.begin((0, 0, 0), [(1, 0, 0)], "New", 100))
        self.assertFalse(drive.cancel((math.nan, 0, 0)))
        self.assertEqual((drive.phase, drive.position, drive.velocity, drive.progress,
                          drive.motion_points, drive.eta), original)


if __name__ == "__main__":
    unittest.main()
