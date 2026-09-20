"""Procedural art invariants that protect lighting, wind batching and collision."""
from collections import Counter
import math
import random
import unittest

from panda3d.core import Vec3

from asterion.collision import CollisionWorld
from asterion.geometry import (Mesh, building_mesh, crystal_mesh, fauna_mesh,
                               flora_mesh, grass_mesh, outpost_mesh, rock_mesh,
                               ruin_mesh, station_mesh)
from asterion.world import _structure_shapes
from tests.test_graphics import mesh_ray


LEAF = (.24, .66, .41)
ACCENT = (.96, .64, .31)


class GeometrySurfaceTests(unittest.TestCase):
    def test_chamfered_box_is_closed_outward_and_preserves_collision_bounds(self):
        mesh = Mesh()
        center, size = (4, -7, 2), (2, 3, 4)
        mesh.bevel_box(center, size, (.65, .72, .76), bevel=.21)
        for axis in range(3):
            self.assertAlmostEqual(min(p[axis] for p in mesh.vertices), center[axis] - size[axis] / 2)
            self.assertAlmostEqual(max(p[axis] for p in mesh.vertices), center[axis] + size[axis] / 2)
        edges = Counter()
        for i in range(0, len(mesh.vertices), 3):
            triangle = mesh.vertices[i:i + 3]
            a, b, c = map(lambda p: Vec3(*p), triangle)
            normal = (b - a).cross(c - a)
            self.assertGreater(normal.lengthSquared(), 1e-8)
            self.assertGreater(normal.dot((a + b + c) / 3 - Vec3(*center)), 0)
            welded = [tuple(round(v, 7) for v in p) for p in triangle]
            for j in range(3):
                edges[tuple(sorted((welded[j], welded[(j + 1) % 3])))] += 1
        self.assertEqual(set(edges.values()), {2}, "Every edge must belong to exactly two faces")

    def test_ellipsoid_normals_follow_the_surface_gradient(self):
        mesh = Mesh()
        center, size = (3, -1, 2), (2.8, .65, 1.2)
        mesh.sphere(center, size, (.6, .7, .8), segments=15, rings=9, smooth=True)
        for point, normal in zip(mesh.vertices, mesh.normals):
            gradient = Vec3(*((point[k] - center[k]) / (size[k] * size[k]) for k in range(3)))
            gradient.normalize()
            self.assertGreater(gradient.dot(Vec3(*normal)), .99999)
        for i in range(0, len(mesh.vertices), 3):
            a, b, c = (Vec3(*p) for p in mesh.vertices[i:i + 3])
            self.assertGreater((b - a).cross(c - a).lengthSquared(), 1e-10)

    def test_foliage_flexibility_survives_batching_without_bending_rigid_objects(self):
        for style in ("fan", "coral", "mushroom", "succulent"):
            with self.subTest(style=style):
                tree = flora_mesh(style, LEAF, ACCENT, 73129)
                self.assertEqual(len(tree.texcoords), len(tree.vertices))
                self.assertGreater(max(uv[0] for uv in tree.texcoords), .1)
                self.assertTrue(all(uv[1] == 0 and 0 <= uv[0] <= 1 for uv in tree.texcoords))
                self.assertTrue(all(uv[0] == 0 for point, uv in zip(tree.vertices, tree.texcoords)
                                    if point[2] < .15))
                stone = rock_mesh(seed=17)
                batch = Mesh()
                batch.add(stone)
                batch.add(tree, (17, 5, 2), scale=.7, heading=39)
                batch.add(stone, (3, -7, 0))
                self.assertEqual(batch.texcoords[:len(stone.vertices)], [(0, 0)] * len(stone.vertices))
                self.assertEqual(batch.texcoords[len(stone.vertices):len(stone.vertices) + len(tree.vertices)],
                                 tree.texcoords)
                self.assertEqual(batch.texcoords[-len(stone.vertices):], [(0, 0)] * len(stone.vertices))
        grass = grass_mesh(LEAF, ACCENT, 73)
        self.assertTrue(all(uv[0] < .01 for point, uv in zip(grass.vertices, grass.texcoords) if point[2] < .02))

    def test_three_cached_fan_variants_have_distinct_crowns(self):
        trees = [flora_mesh("fan", LEAF, ACCENT, 73128 + i) for i in range(3)]
        self.assertEqual(len({len(tree.vertices) for tree in trees}), 3)
        # Collision-bearing trunk stays in the same envelope for every crown.
        for tree in trees:
            root_points = [p for p in tree.vertices if .60 < p[2] < 1.25]
            self.assertTrue(root_points)
            self.assertLess(max(math.hypot(p[0] - .04, p[1]) for p in root_points), .27)

    def test_asset_arrays_are_finite_deterministic_and_have_bounded_cost(self):
        state = random.getstate()
        factories = {
            "fan": lambda: flora_mesh("fan", LEAF, ACCENT, 73130),
            "mushroom": lambda: flora_mesh("mushroom", LEAF, ACCENT, 73129),
            "coral": lambda: flora_mesh("coral", LEAF, ACCENT, 73129),
            "succulent": lambda: flora_mesh("succulent", LEAF, ACCENT, 73129),
            "grass": lambda: grass_mesh(LEAF, ACCENT, 19),
            "rock": lambda: rock_mesh(seed=21),
            "crystal": lambda: crystal_mesh(seed=17),
            **{f"fauna-{i}": lambda i=i: fauna_mesh(LEAF, ACCENT, i) for i in range(3)},
        }
        for name, factory in factories.items():
            with self.subTest(asset=name):
                a, b = factory(), factory()
                self.assertEqual(a.vertices, b.vertices)
                self.assertEqual(a.colors, b.colors)
                self.assertLess(len(a.vertices), 18000)
                self.assert_mesh_valid(a)
        for factory in (outpost_mesh, ruin_mesh, station_mesh,
                        *[lambda kind=kind: building_mesh(kind) for kind in ("habitat", "solar", "extractor", "beacon")]):
            solid, glow = factory()
            self.assertLess(len(solid.vertices) + len(glow.vertices), 45000)
            self.assert_mesh_valid(solid)
            self.assert_mesh_valid(glow)
        self.assertEqual(random.getstate(), state)

    def assert_mesh_valid(self, mesh):
        self.assertEqual(len(mesh.vertices), len(mesh.normals))
        self.assertEqual(len(mesh.vertices), len(mesh.colors))
        self.assertEqual(len(mesh.vertices) % 3, 0)
        self.assertTrue(not mesh.texcoords or len(mesh.texcoords) == len(mesh.vertices))
        for point, normal, color in zip(mesh.vertices, mesh.normals, mesh.colors):
            self.assertTrue(all(math.isfinite(value) for value in (*point, *normal)))
            self.assertAlmostEqual(sum(value * value for value in normal), 1, places=5)
            self.assertTrue(all(0 <= value <= 1 for value in color))


class HabitatCladdingTests(unittest.TestCase):
    def test_new_windows_grilles_ceiling_and_roof_stay_flush_to_collision_shell(self):
        meshes = building_mesh("habitat")
        collisions = CollisionWorld()
        collisions.set_group("habitat", _structure_shapes("habitat"))
        rays = [
            ((5, 0, 1.85), (-1, 0, 0)), ((-5, 0, 1.85), (1, 0, 0)),
            ((5, -.98, .62), (-1, 0, 0)), ((2.12, -5, 1.85), (0, 1, 0)),
            ((0, 0, 1.96), (0, 1, 0)), ((2.83, 0, 1.4), (0, 0, 1)),
            ((2.13, 1.67, 5), (0, 0, -1)), ((-2.13, 1.67, 5), (0, 0, -1)),
        ]
        for origin, direction in rays:
            with self.subTest(origin=origin, direction=direction):
                visible = mesh_ray(meshes, origin, direction, 6)
                collision = collisions.raycast(origin, direction, 6)
                self.assertIsNotNone(visible)
                self.assertIsNotNone(collision)
                self.assertAlmostEqual(visible, collision.distance, delta=.0251)
        for x in (-.8, 0, .8):
            for height in (.40, 1.8, 2.26):
                self.assertIsNone(mesh_ray(meshes, (x, -5, height), (0, 1, 0), 5))
                self.assertIsNone(collisions.raycast((x, -5, height), (0, 1, 0), 5))


if __name__ == "__main__":
    unittest.main()
