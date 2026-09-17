"""Continuity of one physical world across every local tangent chart."""

import math
import random
import unittest

from panda3d.core import Vec3

from asterion.planetary import (
    ATMOSPHERE_TOP, PlanetField, PlanetFrame, chart_direction, direction_chart,
    frame_to_local, frame_to_world, vector_to_local, vector_to_world,
)
from asterion.universe import get_planet


class PlanetFrameTests(unittest.TestCase):
    def setUp(self):
        self.planet = get_planet(0, 0)

    def assertVectorClose(self, actual, expected, tolerance=.015):
        self.assertLess((Vec3(*actual)-Vec3(*expected)).length(), tolerance)

    def test_home_frame_uses_physical_size_without_second_scale(self):
        frame = PlanetFrame.from_planet(self.planet)
        self.assertEqual(frame.radius, self.planet["size"])
        self.assertVectorClose(frame.origin, Vec3(*self.planet["position"])+Vec3(0, 0, frame.radius))
        self.assertVectorClose(frame.right, (1, 0, 0))
        self.assertVectorClose(frame.north, (0, 1, 0))

    def test_all_frames_and_quaternions_are_rigid_and_invertible(self):
        rng = random.Random(47)
        directions = [(0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0),
                      (1e-12, 1, -1e-12), (1, 2, 3)]
        directions += [tuple(rng.uniform(-1, 1) for _ in range(3)) for _ in range(20)]
        for direction in directions:
            with self.subTest(direction=direction):
                frame = PlanetFrame.from_planet(self.planet, direction)
                local = Vec3(251.2, -411.8, 1037.6)
                vector = Vec3(-12.3, 73.7, 420.4)
                self.assertVectorClose(frame.to_local(frame.to_world(local)), local)
                self.assertVectorClose(frame.vector_to_local(frame.vector_to_world(vector)), vector, .0002)
                self.assertVectorClose(frame.rotation.xform(vector), frame.vector_to_world(vector), .0002)
                self.assertVectorClose(frame.right.cross(frame.north), frame.normal, .000001)
                for axis in (frame.right, frame.north, frame.normal):
                    self.assertAlmostEqual(axis.length(), 1.0, places=6)

    def test_reframe_preserves_pose_velocity_and_non_upright_camera(self):
        old = PlanetFrame.from_planet(self.planet, (.8, .5, -.2))
        new = PlanetFrame.from_planet(self.planet, (-.6, -.4, -.7))
        position = old.to_world((230, 380, 2500))
        velocity = old.vector_to_world((67, -91, 430))
        forward = old.vector_to_world((.6, .6, -.529150262))
        up = old.vector_to_world((-.70710678, .70710678, 0))
        for frame in (None, new, None, old):
            position = frame_to_world(frame, frame_to_local(frame, position))
            velocity = vector_to_world(frame, vector_to_local(frame, velocity))
            forward = vector_to_world(frame, vector_to_local(frame, forward))
            up = vector_to_world(frame, vector_to_local(frame, up))
        self.assertVectorClose(position, old.to_world((230, 380, 2500)))
        self.assertVectorClose(velocity, old.vector_to_world((67, -91, 430)), .0002)
        self.assertVectorClose(forward, old.vector_to_world((.6, .6, -.529150262)), .000002)
        self.assertVectorClose(up, old.vector_to_world((-.70710678, .70710678, 0)), .000002)

    def test_canonical_chart_roundtrips_poles_and_antipode(self):
        radius = self.planet["size"]
        for normal in ((0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0), (.5, .3, -.7)):
            expected = Vec3(*normal).normalized()
            uv = direction_chart(normal, radius)
            self.assertVectorClose(chart_direction(*uv, radius), expected, .000001)
        self.assertEqual(direction_chart((0, 0, 1), radius), (0, 0))


class PlanetFieldTests(unittest.TestCase):
    def setUp(self):
        self.planet = get_planet(0, 0)
        self.field = PlanetField(self.planet)
        self.frame = PlanetFrame.from_planet(self.planet)

    def test_home_shelf_is_dry_and_blends_continuously(self):
        for u, v in ((0, 0), (40, 0), (0, -41), (20, 20)):
            self.assertAlmostEqual(self.field.elevation(chart_direction(u, v, self.field.radius)), 20, places=6)
        for edge in (42, 200):
            left = self.field.elevation(chart_direction(edge-.001, 0, self.field.radius))
            right = self.field.elevation(chart_direction(edge+.001, 0, self.field.radius))
            self.assertLess(abs(left-right), .01)
        self.assertGreater(20, self.planet["water_level"])

    def test_spherical_sampler_has_no_antipodal_chart_seam(self):
        other = PlanetField(dict(self.planet))
        radius = self.field.radius
        for latitude in (-1.3, -.2, 0, .4, 1.3):
            a = chart_direction(math.pi*radius-.001, latitude*radius, radius)
            b = chart_direction(-math.pi*radius+.001, latitude*radius, radius)
            self.assertLess(abs(self.field.elevation(a)-self.field.elevation(b)), .01)
            self.assertLess(max(abs(x-y) for x, y in zip(self.field.color(a), self.field.color(b))), .001)
            self.assertEqual(self.field.elevation(a), other.elevation(a))
            self.assertEqual(self.field.color(a), other.color(a))

    def test_curved_local_height_matches_world_ground_at_arbitrary_frames(self):
        for normal in ((0, 0, 1), (0, 1, 0), (0, -1, 0), (0, 0, -1), (.7, -.3, .4)):
            frame = PlanetFrame.from_planet(self.planet, normal)
            for x, y in ((0, 0), (50, -60), (-390, 470), (1100, 900)):
                height = self.field.local_height(frame, x, y)
                self.assertLess(abs(self.field.altitude(frame.to_world((x, y, height)))), .02)
        self.assertLess(self.field.local_height(self.frame, 40, 0), 20)

    def test_point_altitude_and_colors_share_the_same_geography(self):
        for u, v in ((0, 0), (150, 80), (7200, -3800), (-9100, 1800)):
            self.assertAlmostEqual(self.field.altitude(self.field.point(u, v, 37)), 37, delta=.015)
            self.assertTrue(all(0 <= component <= 1 for component in self.field.color(chart_direction(u, v, self.field.radius))))

    def test_atmosphere_is_continuous_and_zero_above_top(self):
        for altitude in (0, 220, 280, 410, 540, 700, 1550, ATMOSPHERE_TOP):
            before = self.field.atmosphere(self.field.point(0, 0, altitude-.02), (0, 0, -700))
            after = self.field.atmosphere(self.field.point(0, 0, altitude+.02), (0, 0, -700))
            for key in ("density", "space_blend", "cloud", "heat", "entry", "exit"):
                self.assertLess(abs(before[key]-after[key]), .001)
        high = self.field.atmosphere(self.field.point(0, 0, 2400), (0, 0, -900))
        self.assertEqual(high["density"], 0)
        self.assertEqual(high["heat"], 0)
        self.assertEqual(high["space_blend"], 1)

    def test_entry_heat_requires_inward_speed_and_exit_is_distinct(self):
        for direction in ((0, 0, 1), (0, 0, -1), (0, 1, 0)):
            normal = Vec3(*direction)
            location = self.field.center+normal*(self.field.radius+self.field.elevation(normal)+500)
            entry = self.field.atmosphere(location, -normal*800)
            exit_data = self.field.atmosphere(location, normal*800)
            still = self.field.atmosphere(location)
            self.assertGreater(entry["heat"], .1)
            self.assertGreater(entry["entry"], .1)
            self.assertEqual(entry["exit"], 0)
            self.assertEqual(exit_data["heat"], 0)
            self.assertGreater(exit_data["exit"], .1)
            self.assertEqual(exit_data["entry"], 0)
            self.assertEqual(still["heat"], 0)
            self.assertAlmostEqual(entry["radial_speed"], -800, delta=.001)

    def test_fast_sweep_cannot_tunnel_through_entire_planet(self):
        for direction in ((0, 0, 1), (0, 0, -1), (0, 1, 0), (.6, -.8, 0)):
            normal = Vec3(*direction).normalized()
            start = self.field.center+normal*(self.field.radius+12000)
            result = self.field.sweep(start, -normal*(self.field.radius*2+24000), radius=3)
            self.assertTrue(result.hit_ids)
            self.assertGreater((result.position-self.field.center).dot(normal), self.field.radius-160)
            self.assertGreaterEqual(self.field.altitude(result.position), 2.98)
            self.assertTrue(all(abs(n.length()-1) < 1e-6 for n in result.normals))
            # The huge residual slides along slopes and can leave a curved
            # surface again. Its final altitude need not equal contact height.

    def test_fast_descent_first_contact_reaches_terrain_in_a_physics_step(self):
        for direction in ((0, 0, 1), (0, 0, -1), (0, 1, 0), (.6, -.8, 0)):
            with self.subTest(direction=direction):
                normal = Vec3(*direction).normalized()
                position = self.field.center + normal * (
                    self.field.radius + self.field.elevation(normal) + 80)
                for _ in range(12):
                    result = self.field.sweep(position, -normal * (1600 / 120), 3)
                    position = result.position
                    self.assertGreaterEqual(self.field.altitude(position), 2.98)
                    if result.hit_ids:
                        self.assertLess(self.field.altitude(position), 3.2)
                        break
                else:
                    self.fail("A high-speed descent did not contact physical terrain")

    def test_sweep_uses_solid_ground_and_permits_clear_atmosphere_crossing(self):
        start = self.field.point(0, 0, 2600)
        result = self.field.sweep(start, (0, 0, -2000), radius=3)
        self.assertEqual(result.hit_ids, [])
        self.assertAlmostEqual(self.field.altitude(result.position), 600, delta=.01)

    def test_contact_slides_and_motion_away_is_unimpeded(self):
        start = self.field.point(0, 0, 10)
        result = self.field.sweep(start, self.frame.vector_to_world((15, 9, -30)), 3)
        local = self.frame.to_local(result.position)
        self.assertTrue(result.hit_ids)
        self.assertGreater(local.x, 14.8)
        self.assertGreater(local.y, 8.8)
        self.assertGreaterEqual(self.field.altitude(result.position), 3)
        away = self.field.sweep(result.position, (0, 0, 100), 3)
        self.assertGreater(away.position.z-result.position.z, 99.9)

    def test_initial_penetration_and_invalid_velocity_remain_finite(self):
        for start in (self.field.center, self.field.point(0, 0, -5)):
            result = self.field.sweep(start, (math.nan, 0, 0))
            self.assertTrue(all(math.isfinite(v) for v in result.position))
            self.assertGreaterEqual(self.field.altitude(result.position), 2.99)
            self.assertTrue(result.hit_ids)


if __name__ == "__main__":
    unittest.main()
