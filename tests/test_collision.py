"""Analytic collision and streaming regressions without renderer state."""

import math
import unittest

from panda3d.core import Vec3

from asterion.collision import CollisionWorld


def box(shape_id, center=(0, 0, 0), half=(1, 1, 1), heading=0):
    return dict(id=shape_id, type="box", center=center, half=half, heading=heading)


def sphere(shape_id, center=(0, 0, 0), radius=1):
    return dict(id=shape_id, type="sphere", center=center, radius=radius)


def cylinder(shape_id, center=(0, 0, 0), radius=1, height=2):
    return dict(id=shape_id, type="cylinder", center=center,
                radius=radius, height=height)


class CollisionTests(unittest.TestCase):
    def setUp(self):
        self.world = CollisionWorld()

    def assertVectorClose(self, actual, expected, tolerance=.004):
        self.assertLess((Vec3(actual) - Vec3(*expected)).length(), tolerance)

    def assertClear(self, position, radius=.38, height=1.8):
        self.assertFalse(self.world.overlaps_capsule(position, radius, height))

    def test_empty_world_preserves_eye_height_and_accepts_vec3_or_tuple(self):
        walk = self.world.move_capsule(Vec3(1, 2, 1.8), (3, -2, .5))
        ship = self.world.move_sphere((1, 2, 3), Vec3(4, 5, 6))
        self.assertVectorClose(walk.position, (4, 0, 2.3))
        self.assertVectorClose(ship.position, (5, 7, 9))
        for result in (walk, ship):
            self.assertIsInstance(result.position, Vec3)
            self.assertEqual(result.normals, [])
            self.assertEqual(result.hit_ids, [])
            self.assertFalse(result.grounded)
            self.assertFalse(result.ceiling)

    def test_fast_ship_cannot_tunnel_through_millimeter_wall(self):
        self.world.set_group("wall", [box("thin", half=(.0005, 20, 20))])
        for distance in (27, 800, 20000):
            with self.subTest(distance=distance):
                result = self.world.move_sphere((-distance / 2, 0, 0),
                                                (distance, 0, 0))
                self.assertAlmostEqual(result.position.x, -3.0015, delta=.003)
                self.assertVectorClose(result.normals[0], (-1, 0, 0))
                self.assertEqual(result.hit_ids, ["thin"])

    def test_fast_ship_sweeps_rotated_thin_wall(self):
        heading = 37
        angle = math.radians(heading)
        normal = Vec3(math.cos(angle), math.sin(angle), 0)
        self.world.set_group("wall", [box("angled", half=(.001, 100, 100),
                                                 heading=heading)])
        result = self.world.move_sphere(-normal * 1000, normal * 2000)
        self.assertAlmostEqual(result.position.dot(normal), -3.002, delta=.004)
        self.assertVectorClose(result.normals[0], -normal)

    def test_capsule_side_between_sample_spheres_blocks_narrow_beam(self):
        # The beam lies midway between common feet/middle sample centers.
        # Its nearest point still meets the full straight side of a capsule.
        self.world.set_group("beam", [box("rail", (0, 0, .64), (.02, 4, .01))])
        result = self.world.move_capsule((-2, 0, 1.8), (4, 0, 0), step_height=0)
        self.assertAlmostEqual(result.position.x, -.401, delta=.002)
        self.assertEqual(result.hit_ids, ["rail"])
        self.assertClear(result.position)

    def test_capsule_overlap_uses_entire_segment_and_rounded_ends(self):
        self.world.set_group("detail", [sphere("middle", (.35, 0, .64), .01)])
        self.assertTrue(self.world.overlaps_capsule((0, 0, 1.8)))
        self.world.set_group("detail", [sphere("near-foot", (.37, 0, .02), .01)])
        self.assertFalse(self.world.overlaps_capsule((0, 0, 1.8)))
        self.world.set_group("detail", [sphere("near-head", (.37, 0, 1.78), .01)])
        self.assertFalse(self.world.overlaps_capsule((0, 0, 1.8)))

    def test_box_wall_preserves_full_tangential_motion(self):
        self.world.set_group("wall", [box("side", (0, 0, 3), (.1, 20, 3))])
        result = self.world.move_capsule((-2, 0, 1.8), (4, 5, 0), step_height=0)
        self.assertVectorClose(result.position, (-.481, 5, 1.8))
        self.assertClear(result.position)
        # A second frame parallel to the contact must not stick to it.
        result = self.world.move_capsule(result.position, (0, 2, 0), step_height=0)
        self.assertVectorClose(result.position, (-.481, 7, 1.8))

    def test_obb_sliding_uses_panda_heading_and_local_faces(self):
        for heading in (-70, 35, 90):
            with self.subTest(heading=heading):
                angle = math.radians(heading)
                normal = Vec3(math.cos(angle), math.sin(angle), 0)
                tangent = Vec3(-math.sin(angle), math.cos(angle), 0)
                self.world.set_group("wall", [box("obb", (0, 0, 3),
                                                         (.1, 20, 3), heading)])
                start = -normal * 3 + Vec3(0, 0, 1.8)
                result = self.world.move_capsule(start, normal * 6 + tangent * 4,
                                                 step_height=0)
                expected = -normal * .481 + tangent * 4 + Vec3(0, 0, 1.8)
                self.assertVectorClose(result.position, expected)
                self.assertVectorClose(result.normals[0], -normal)
                self.assertClear(result.position)

    def test_capsule_slides_around_sphere_and_cylinder(self):
        for record in (sphere("solid", (0, 0, .9)),
                       cylinder("solid", (0, 0, 1))):
            with self.subTest(kind=record["type"]):
                self.world.set_group("obstacle", [record])
                result = self.world.move_capsule((-3, .75, 1.8), (6, 0, 0),
                                                 step_height=0)
                self.assertGreater(result.position.x, -.2)
                self.assertGreater(result.position.y, 2)
                self.assertAlmostEqual(result.position.z, 1.8, delta=.002)
                self.assertIn("solid", result.hit_ids)
                self.assertClear(result.position)

    def test_ship_slides_around_spherical_asteroid(self):
        self.world.set_group("orbit", [sphere("asteroid", radius=4)])
        result = self.world.move_sphere((-20, 4, 0), (40, 0, 0))
        self.assertGreater(result.position.x, 0)
        self.assertGreater(result.position.y, 7)
        self.assertIn("asteroid", result.hit_ids)
        self.assertGreaterEqual(result.position.length(), 7)

    def test_rounded_box_and_cylinder_edges_do_not_fill_expanded_aabb(self):
        for record in (box("solid"), cylinder("solid")):
            with self.subTest(kind=record["type"]):
                self.world.set_group("obstacle", [record])
                result = self.world.move_sphere((-3, 1.29, 1.29), (6, 0, 0), radius=.3)
                self.assertVectorClose(result.position, (3, 1.29, 1.29))
                self.assertEqual(result.hit_ids, [])

    def test_capsule_ceiling_stops_its_top_and_reports_downward_normal(self):
        for record in (box("ceiling", (0, 0, 3), (5, 5, .1)),
                       cylinder("ceiling", (0, 0, 3), 5, .2)):
            with self.subTest(kind=record["type"]):
                self.world.set_group("ceiling", [record])
                result = self.world.move_capsule((0, 0, 1.8), (0, 0, 5))
                self.assertAlmostEqual(result.position.z, 2.899, delta=.002)
                self.assertTrue(result.ceiling)
                self.assertFalse(result.grounded)
                self.assertVectorClose(result.normals[0], (0, 0, -1))
                self.assertClear(result.position)

    def test_finite_cylinder_has_walkable_endcaps_and_free_space_above(self):
        self.world.set_group("column", [cylinder("top", height=2)])
        result = self.world.move_capsule((0, 0, 8), (0, 0, -20))
        self.assertAlmostEqual(result.position.z, 2.801, delta=.002)
        self.assertTrue(result.grounded)
        self.assertClear(result.position)
        above = self.world.move_sphere((-5, 0, 3), (10, 0, 0), radius=.3)
        self.assertVectorClose(above.position, (5, 0, 3))
        self.assertEqual(above.hit_ids, [])

    def test_steps_up_exactly_point_four_meters(self):
        for height in (.2, .4):
            with self.subTest(height=height):
                self.world.set_group("step", [box("step", (2, 0, height / 2),
                                                          (1, 2, height / 2))])
                result = self.world.move_capsule((0, 0, 1.8), (2, 0, 0))
                self.assertAlmostEqual(result.position.x, 2, delta=.002)
                self.assertAlmostEqual(result.position.z, 1.8 + height, delta=.003)
                self.assertTrue(result.grounded)
                self.assertClear(result.position)

    def test_tall_step_blocks_and_disabling_steps_is_respected(self):
        for height, step_height in ((.401, .4), (.6, .4), (.4, 0)):
            with self.subTest(height=height, step_height=step_height):
                self.world.set_group("step", [box("step", (2, 0, height / 2),
                                                          (1, 2, height / 2))])
                result = self.world.move_capsule((0, 0, 1.8), (2, 0, 0),
                                                 step_height=step_height)
                self.assertLess(result.position.x, .63)
                self.assertAlmostEqual(result.position.z, 1.8, delta=.002)
                self.assertClear(result.position)

    def test_step_checks_headroom(self):
        self.world.set_group("stairs", [box("step", (2, 0, .2), (1, 2, .2)),
                                         box("low-ceiling", (0, 0, 2.1), (5, 5, .1))])
        result = self.world.move_capsule((0, 0, 1.8), (2, 0, 0))
        self.assertLess(result.position.x, .7)
        self.assertLess(result.position.z, 2)
        self.assertClear(result.position)

    def test_step_over_narrow_obstacle_returns_to_terrain_relative_height(self):
        self.world.set_group("sill", [box("sill", (1, 0, .15), (.1, 2, .15))])
        result = self.world.move_capsule((0, 0, 1.8), (3, 0, 0))
        self.assertVectorClose(result.position, (3, 0, 1.8))
        self.assertClear(result.position)

    def test_fall_lands_on_roof_and_horizontal_walk_keeps_support(self):
        self.world.set_group("building", [box("roof", (0, 0, 3), (3, 3, .1))])
        landed = self.world.move_capsule((0, 0, 10), (0, 0, -20))
        self.assertAlmostEqual(landed.position.z, 4.901, delta=.002)
        self.assertTrue(landed.grounded)
        walked = self.world.move_capsule(landed.position, (2, 1, 0))
        self.assertVectorClose(walked.position, (2, 1, 4.901))
        self.assertTrue(walked.grounded)
        self.assertIn("roof", walked.hit_ids)
        self.assertClear(walked.position)

    def test_compound_building_leaves_its_door_open(self):
        self.world.set_group("building", [box("left", (-2, 0, 1.5), (1, .1, 1.5)),
                                           box("right", (2, 0, 1.5), (1, .1, 1.5)),
                                           box("lintel", (0, 0, 2.75), (1, .1, .25))])
        through = self.world.move_capsule((0, -3, 1.8), (0, 6, 0))
        self.assertVectorClose(through.position, (0, 3, 1.8))
        blocked = self.world.move_capsule((2, -3, 1.8), (0, 6, 0))
        self.assertAlmostEqual(blocked.position.y, -.481, delta=.002)
        self.assertIn("right", blocked.hit_ids)

    def test_initial_penetration_recovers_for_each_shape(self):
        for record in (box("solid"), sphere("solid"), cylinder("solid")):
            with self.subTest(kind=record["type"]):
                self.world.set_group("obstacle", [record])
                self.assertTrue(self.world.overlaps_capsule((.1, .1, 1.1)))
                result = self.world.move_capsule((.1, .1, 1.1), (0, 0, 0))
                self.assertClear(result.position)
                self.assertIn("solid", result.hit_ids)
                self.assertLess(result.position.length(), 4)

    def test_initial_overlap_recovers_before_requested_motion(self):
        self.world.set_group("wall", [box("solid", half=(1, 5, 5))])
        result = self.world.move_sphere((.9, 0, 0), (1, 2, 0), radius=.38)
        self.assertVectorClose(result.position, (2.381, 2, 0))
        self.assertIn("solid", result.hit_ids)

    def test_corner_blocks_two_axes_while_preserving_crease_motion(self):
        self.world.set_group("corner", [box("x", (1, 0, 0), (.1, 10, 10)),
                                         box("y", (0, 1, 0), (10, .1, 10))])
        result = self.world.move_sphere((-2, -2, 0), (5, 5, 3), radius=.38)
        self.assertVectorClose(result.position, (.519, .519, 3))
        self.assertEqual(set(result.hit_ids), {"x", "y"})
        self.assertEqual(len(result.normals), 2)
        for _ in range(20):
            result = self.world.move_sphere(result.position, (.1, .1, -.1), radius=.38)
        self.assertVectorClose(result.position, (.519, .519, 1))

    def test_acute_corner_is_stable_and_can_be_left(self):
        self.world.set_group("corner", [box("a", (0, 0, 0), (.1, 20, 20), 30),
                                         box("b", (0, 0, 0), (.1, 20, 20), -30)])
        result = self.world.move_sphere((-4, 0, 0), (10, 0, 3), radius=.38)
        self.assertAlmostEqual(result.position.x, -.481 / math.cos(math.radians(30)),
                               delta=.004)
        self.assertAlmostEqual(result.position.y, 0, delta=.004)
        self.assertAlmostEqual(result.position.z, 3, delta=.004)
        escaped = self.world.move_sphere(result.position, (-2, 0, 0), radius=.38)
        self.assertAlmostEqual(escaped.position.x, result.position.x - 2, delta=.004)

    def test_group_replacement_removal_and_clear_update_queries(self):
        self.world.set_group("streamed", [box("old", (0, 0, 1), (1, 1, 1))])
        self.assertTrue(self.world.overlaps_capsule((0, 0, 1.8)))
        self.world.set_group("streamed", [box("new", (50, 0, 1), (1, 1, 1))])
        self.assertEqual(self.world.shape_count, 1)
        self.assertFalse(self.world.overlaps_capsule((0, 0, 1.8)))
        self.assertTrue(self.world.overlaps_capsule((50, 0, 1.8)))
        self.world.remove_group("streamed")
        self.world.remove_group("missing")
        self.assertEqual(self.world.shape_count, 0)
        self.assertFalse(self.world.overlaps_capsule((50, 0, 1.8)))
        self.world.set_group("one", [sphere("one")])
        self.world.set_group("two", [cylinder("two", (100, 0, 0), 200, 200)])
        self.world.clear()
        self.assertEqual(self.world.shape_count, 0)
        self.assertIsNone(self.world.raycast((-10, 0, 0), (1, 0, 0), 1000))

    def test_group_records_are_copied_and_duplicate_ids_are_replaced(self):
        record = box("wall", (0, 0, 1), (1, 1, 1))
        self.world.set_group("a", [record, dict(record, center=(10, 0, 1))])
        record["center"] = (100, 0, 1)
        self.assertEqual(self.world.shape_count, 1)
        self.assertFalse(self.world.overlaps_capsule((0, 0, 1.8)))
        self.assertTrue(self.world.overlaps_capsule((10, 0, 1.8)))
        self.world.set_group("b", [dict(record, center=(20, 0, 1))])
        self.assertEqual(self.world.shape_count, 2)
        self.world.remove_group("a")
        self.assertTrue(self.world.overlaps_capsule((20, 0, 1.8)))

    def test_failed_iterator_does_not_partially_replace_group(self):
        self.world.set_group("group", [sphere("old")])

        def broken_records():
            yield sphere("new", (10, 0, 0))
            raise RuntimeError("interrupted stream")

        with self.assertRaises(RuntimeError):
            self.world.set_group("group", broken_records())
        self.assertEqual(self.world.shape_count, 1)
        self.assertEqual(self.world.raycast((-10, 0, 0), (1, 0, 0), 20).id, "old")

    def test_ignore_accepts_group_or_shape_id_across_all_queries(self):
        self.world.set_group("parked-ship", [box("hull", (0, 0, 1), (1, 1, 1))])
        for ignore in ("parked-ship", ("parked-ship",), "hull", ["hull"]):
            with self.subTest(ignore=ignore):
                self.assertFalse(self.world.overlaps_capsule((0, 0, 1.8), ignore=ignore))
                walk = self.world.move_capsule((-4, 0, 1.8), (8, 0, 0), ignore=ignore)
                self.assertVectorClose(walk.position, (4, 0, 1.8))
                ship = self.world.move_sphere((-10, 0, 1), (20, 0, 0), ignore=ignore)
                self.assertVectorClose(ship.position, (10, 0, 1))
                self.assertIsNone(self.world.raycast((-10, 0, 1), (1, 0, 0), 20,
                                                     ignore=ignore))

    def test_ignore_one_shape_keeps_others_in_same_group(self):
        self.world.set_group("room", [box("near", (0, 0, 0)), box("far", (5, 0, 0))])
        hit = self.world.raycast((-10, 0, 0), (1, 0, 0), 20, ignore=("near",))
        self.assertEqual(hit.id, "far")
        self.assertAlmostEqual(hit.distance, 14, delta=.001)

    def test_raycast_returns_nearest_distance_unit_normal_and_position(self):
        for record in (box("target"), sphere("target"), cylinder("target")):
            with self.subTest(kind=record["type"]):
                self.world.set_group("targets", [box("far", (8, 0, 0)), record])
                hit = self.world.raycast((-10, 0, 0), (4, 0, 0), 30)
                self.assertEqual(hit.id, "target")
                self.assertAlmostEqual(hit.distance, 9, delta=.001)
                self.assertVectorClose(hit.normal, (-1, 0, 0))
                self.assertVectorClose(hit.position, (-1, 0, 0))
                self.assertIsNone(self.world.raycast((-10, 0, 0), (1, 0, 0), 8.9))

    def test_raycast_rotated_box_and_vertical_cylinder_cap(self):
        self.world.set_group("wall", [box("obb", half=(1, 5, 2), heading=45)])
        direction = Vec3(1, 1, 0).normalized()
        hit = self.world.raycast(-direction * 10, direction, 20)
        self.assertAlmostEqual(hit.distance, 9, delta=.001)
        self.assertVectorClose(hit.normal, -direction)
        self.world.set_group("wall", [cylinder("cap", height=4)])
        hit = self.world.raycast((0, 0, 10), (0, 0, -5), 20)
        self.assertAlmostEqual(hit.distance, 8, delta=.001)
        self.assertVectorClose(hit.normal, (0, 0, 1))

    def test_raycast_inside_origin_parallel_boundaries_and_tangent(self):
        self.world.set_group("obstacle", [box("box")])
        self.assertEqual(self.world.raycast((0, 0, 0), (1, 0, 0), 10).distance, 0)
        boundary = self.world.raycast((-5, 1, 0), (1, 0, 0), 10)
        self.assertAlmostEqual(boundary.distance, 4, delta=.001)
        self.assertIsNone(self.world.raycast((-5, 1.01, 0), (1, 0, 0), 10))
        self.world.set_group("obstacle", [sphere("sphere")])
        tangent = self.world.raycast((-5, 1, 0), (1, 0, 0), 10)
        self.assertIsNotNone(tangent)
        self.assertAlmostEqual(tangent.distance, 5, delta=.002)

    def test_large_shapes_and_long_queries_use_bounded_spatial_index(self):
        world = CollisionWorld(cell_size=.25)
        world.set_group("large", [box("wall", half=(.001, 1000000, 1000000))])
        self.assertEqual(world.shape_count, 1)
        result = world.move_sphere((-100000, 0, 0), (200000, 0, 0))
        self.assertAlmostEqual(result.position.x, -3.002, delta=.003)
        hit = world.raycast((-100000, 0, 0), (1, 0, 0), 200000)
        self.assertAlmostEqual(hit.distance, 99999.999, delta=.002)
        world.remove_group("large")
        self.assertIsNone(world.raycast((-100000, 0, 0), (1, 0, 0), 200000))

    def test_malformed_shape_records_are_skipped(self):
        invalid = [None, 2, [], {}, sphere(""), sphere("x" * 257),
                   dict(sphere("nan"), center=(0, math.nan, 0)),
                   dict(sphere("inf"), radius=math.inf),
                   dict(sphere("negative"), radius=-1),
                   dict(sphere("huge"), radius=1000001),
                   dict(sphere("far"), center=(10000001, 0, 0)),
                   dict(box("flat"), half=(1, 0, 1)),
                   dict(box("short"), half=(1, 1)),
                   dict(box("bad-angle"), heading=math.nan),
                   dict(cylinder("short"), height=0),
                   dict(cylinder("bad-height"), height="invalid"),
                   dict(sphere("wrong-type"), type="mesh")]
        self.world.set_group("mixed", invalid + [sphere("valid")])
        self.assertEqual(self.world.shape_count, 1)
        self.assertEqual(self.world.raycast((-5, 0, 0), (1, 0, 0), 10).id, "valid")
        self.world.set_group([], [sphere("ignored")])
        self.world.remove_group([])
        self.assertEqual(self.world.shape_count, 1)

    def test_unbounded_record_iterator_is_consumed_only_to_documented_limit(self):
        consumed = []

        def records():
            while True:
                consumed.append(None)
                yield sphere("same-id")

        self.world.set_group("bounded", records())
        self.assertEqual(len(consumed), 8192)
        self.assertEqual(self.world.shape_count, 1)

    def test_bad_motion_values_stay_finite_and_degenerate_rays_return_none(self):
        result = self.world.move_capsule((math.nan, math.inf, None),
                                         (math.inf, "bad", -math.inf),
                                         radius=math.nan, height=-1, step_height=math.inf)
        self.assertTrue(all(math.isfinite(x) for x in result.position))
        self.assertLess(result.position.length(), 1)
        result = self.world.move_sphere(None, (1e200, 0, 0), radius=-5, ignore=None)
        self.assertTrue(all(math.isfinite(x) for x in result.position))
        self.assertLessEqual(result.position.length(), 10000000)
        self.world.set_group("target", sphere("target"))
        for direction, maximum in (((0, 0, 0), 10), ((math.nan, 0, 0), 10),
                                   ((1, 0, 0), math.nan), ((1, 0, 0), -1)):
            self.assertIsNone(self.world.raycast((-5, 0, 0), direction, maximum))


if __name__ == "__main__":
    unittest.main()
