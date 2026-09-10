"""Orbital routes must clear entire segments, including compound obstructions."""

import math
import random
import unittest
from unittest.mock import patch

from panda3d.core import Vec3

from asterion.navigation import plan_route


def sphere(center, radius, identity="body"):
    return {"center": center, "radius": radius, "id": identity}


def segment_distance(a, b, center):
    # Independent double-precision oracle, using endpoint and cross-product
    # distances instead of the planner's closest-point implementation.
    a, b, center = tuple(a), tuple(b), tuple(center)
    direction = tuple(b[i] - a[i] for i in range(3))
    offset = tuple(center[i] - a[i] for i in range(3))
    length2 = sum(n*n for n in direction)
    projection = sum(offset[i]*direction[i] for i in range(3))
    if not length2 or projection <= 0:
        return math.dist(a, center)
    if projection >= length2:
        return math.dist(b, center)
    cross = (offset[1]*direction[2] - offset[2]*direction[1],
             offset[2]*direction[0] - offset[0]*direction[2],
             offset[0]*direction[1] - offset[1]*direction[0])
    return math.hypot(*cross) / math.sqrt(length2)


class NavigationTests(unittest.TestCase):
    def assert_safe_route(self, start, goal, obstacles):
        route = plan_route(start, goal, obstacles)
        self.assertTrue(route, "A safe route should be found for this arrangement")
        self.assertEqual(route[-1], Vec3(*goal))
        self.assertLessEqual(len(route), 383)
        previous = Vec3(*start)
        for waypoint in route:
            self.assertIsInstance(waypoint, Vec3)
            self.assertTrue(all(math.isfinite(n) for n in waypoint))
            for obstacle in obstacles:
                clearance = segment_distance(previous, waypoint, obstacle["center"])
                self.assertGreaterEqual(clearance, obstacle["radius"] - 1e-7,
                                        (tuple(previous), tuple(waypoint), obstacle))
            previous = waypoint
        return route

    def test_empty_space_and_distant_obstacles_use_one_segment(self):
        start, goal = (0, 0, 0), (400, -800, 90)
        for obstacles in ([], [sphere((0, 800, 90), 100)]):
            self.assertEqual(self.assert_safe_route(start, goal, obstacles), [Vec3(*goal)])

    def test_single_planet_requires_clear_detour(self):
        obstacles = [sphere((0, 0, 0), 400)]
        route = self.assert_safe_route((-1800, 0, 0), (1500, 0, 0), obstacles)
        self.assertGreater(len(route), 1)
        self.assertLess(sum(math.dist(a, b) for a, b in
                            zip([(-1800, 0, 0), *route], route)), 4400)

    def test_start_and_goal_touch_opposite_sides(self):
        self.assert_safe_route((-10, 0, 0), (10, 0, 0), [sphere((0, 0, 0), 10)])

    def test_touching_departure_and_tangent_direct_route(self):
        obstacles = [sphere((0, 0, 0), 10)]
        self.assertEqual(self.assert_safe_route((10, 0, 0), (20, 0, 0), obstacles),
                         [Vec3(20, 0, 0)])
        self.assertEqual(self.assert_safe_route((-20, 10, 0), (20, 10, 0), obstacles),
                         [Vec3(20, 10, 0)])

    def test_near_surface_non_axis_aligned_route(self):
        center = (3500, -1700, 240)
        start = tuple(center[i] + (180, 240, 0)[i]*1.0001 for i in range(3))
        goal = tuple(center[i] - (600, 800, 200)[i] for i in range(3))
        self.assert_safe_route(start, goal, [sphere(center, 300)])

    def test_multiple_planets_and_off_axis_asteroids(self):
        obstacles = [sphere((-700, 0, 0), 330, "planet-a"),
                     sphere((400, 120, 60), 440, "planet-b"),
                     sphere((1200, -90, -20), 230, "planet-c")]
        obstacles.extend(sphere((x, y, z), 110, str(i)) for i, (x, y, z) in enumerate(
            [(-1100, 320, 0), (-300, -250, -180), (650, 420, 280),
             (1000, -300, 0), (1300, 180, -180)]))
        self.assert_safe_route((-1900, 0, 0), (2000, 0, 0), obstacles)

    def test_overlapping_spheres_are_checked_as_union(self):
        obstacles = [sphere((x, y, z), 190, str(i)) for i, (x, y, z) in enumerate(
            [(-280, 0, 0), (-90, 20, 0), (90, 0, 20), (260, -30, 0),
             (0, 170, 80), (0, -160, -80)])]
        self.assert_safe_route((-800, 0, 0), (850, 0, 0), obstacles)

    def test_nested_and_duplicate_spheres_do_not_create_fake_openings(self):
        obstacles = [sphere((0, 0, 0), 20, "outer"),
                     sphere((0, 0, 0), 10, "inner"),
                     sphere((0, 0, 0), 20, "outer")]
        self.assert_safe_route((-60, 0, 0), (60, 0, 0), obstacles)

    def test_deterministic_and_obstacle_order_independent(self):
        obstacles = [sphere((0, 0, 0), 30, "planet"),
                     sphere((-10, 35, 5), 12, "asteroid-a"),
                     sphere((20, -30, -10), 18, "asteroid-b")]
        expected = self.assert_safe_route((-100, 0, 0), (100, 0, 0), obstacles)
        for order in (obstacles, obstacles[::-1], [obstacles[1], obstacles[2], obstacles[0]]):
            self.assertEqual(plan_route((-100, 0, 0), (100, 0, 0), order), expected)

    def test_embedded_endpoints_fail_instead_of_escaping_through_body(self):
        obstacles = [sphere((0, 0, 0), 10)]
        self.assertEqual(plan_route((0, 0, 0), (30, 0, 0), obstacles), [])
        self.assertEqual(plan_route((-30, 0, 0), (9.9, 0, 0), obstacles), [])
        self.assertEqual(plan_route((0, 0, 0), (0, 0, 0), obstacles), [])

    def test_coincident_clear_goal_returns_success_and_zero_radius_is_empty(self):
        self.assertEqual(plan_route((1, 2, 3), (1, 2, 3), []), [Vec3(1, 2, 3)])
        self.assertEqual(plan_route((-1, 0, 0), (1, 0, 0), [sphere((0, 0, 0), 0)]),
                         [Vec3(1, 0, 0)])

    def test_nonfinite_and_malformed_inputs_fail_closed(self):
        invalid_points = [(math.nan, 0, 0), (0, math.inf, 0), (0, 0, -math.inf),
                          (1e20, 0, 0), None, (), (1, 2), ("bad", 0, 0)]
        for point in invalid_points:
            self.assertEqual(plan_route(point, (1, 0, 0), []), [], repr(point))
            self.assertEqual(plan_route((0, 0, 0), point, []), [], repr(point))
        invalid_obstacles = [None, [None], [{}], [sphere((0, 0, math.nan), 1)],
                             [sphere((0, 0, 0), math.inf)], [sphere((0, 0, 0), -1)],
                             [sphere((0, 0, 0), 1e12)], [sphere((0, 0), 1)],
                             [sphere((0, 0, 0), 1, identity=123)]]
        for obstacles in invalid_obstacles:
            self.assertEqual(plan_route((-10, 0, 0), (10, 0, 0), obstacles), [], repr(obstacles))

    def test_input_and_search_budgets_fail_closed(self):
        obstacles = [sphere((0, 0, 0), 10)]
        self.assertEqual(plan_route((-30, 0, 0), (30, 0, 0), obstacles * 257), [])
        with patch("asterion.navigation._MAX_EDGES", 0):
            self.assertEqual(plan_route((-30, 0, 0), (30, 0, 0), obstacles), [])
        with patch("asterion.navigation._MAX_NODES", 2):
            self.assertEqual(plan_route((-30, 0, 0), (30, 0, 0), obstacles), [])

    def test_typical_orbit_with_four_planets_and_seventy_asteroids(self):
        rng = random.Random(31997)
        obstacles = [sphere((x, 0, 0), 310 + i*20, "planet-" + str(i))
                     for i, x in enumerate((-3000, -1000, 1000, 3000))]
        obstacles.extend(sphere((rng.uniform(-4000, 4000), rng.uniform(-1800, 1800),
                                 rng.uniform(-1000, 1000)), rng.uniform(15, 60), str(i))
                         for i in range(70))
        original = [dict(obstacle) for obstacle in obstacles]
        self.assert_safe_route((-4600, 0, 0), (4600, 0, 0), obstacles)
        self.assertEqual(obstacles, original)


if __name__ == "__main__":
    unittest.main()
