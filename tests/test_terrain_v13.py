"""Geometric and streaming contracts for the large physical planet renderer."""
import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from panda3d.core import NodePath

from asterion.planetary import PlanetField, direction_chart
from asterion.seamless_world import SeamlessWorld, _direction_components
from asterion.universe import generate_system


class ContinuousTerrainTests(unittest.TestCase):
    def setUp(self):
        self.planet = generate_system(0)['planets'][0]
        self.field = PlanetField(self.planet)
        self.world = SeamlessWorld(SimpleNamespace(render=NodePath('test-render')))
        self.world.planet = self.planet
        self.world.fields[self.field.planet_id] = self.field
        self.body = NodePath('test-body')
        self.world.body_roots[self.field.planet_id] = self.body
        self.addCleanup(self.body.removeNode)
        self.addCleanup(self.world.destroy)

    def globe(self):
        self.world._make_globe(self.field, self.body)
        return self.world._lod[self.field.planet_id]

    def test_all_mesh_vertices_match_the_collision_envelope(self):
        field, world = self.field, self.world
        for face, x, y in ((0, 90., 70.), (1, -4100., 7200.), (4, 1800., -2700.)):
            normal = _direction_components(face, x, y, field.radius)
            origin = tuple(c * field.radius for c in normal)
            mesh = world._terrain_mesh(field, face, x, y, x + 24, y + 24,
                                       resolution=12, origin=origin)
            for vertex in mesh.vertices[:12 * 12 * 6:7]:
                point = tuple(vertex[k] + origin[k] for k in range(3))
                radius = math.sqrt(sum(c * c for c in point))
                self.assertAlmostEqual(radius - field.radius, field.elevation(point), places=7)
                self.assertGreaterEqual(radius - field.radius, field.water_level - 1e-7)

    def test_neighbour_edges_share_positions_normals_and_lighting(self):
        left = self.world._terrain_mesh(self.field, 0, 96, 48, 120, 72, resolution=12)
        right = self.world._terrain_mesh(self.field, 0, 120, 48, 144, 72, resolution=12)
        def vertices(mesh):
            return {p: (n, c, uv) for p, n, c, uv in
                    zip(mesh.vertices[:864], mesh.normals[:864],
                        mesh.colors[:864], mesh.texcoords[:864])}
        a, b = vertices(left), vertices(right)
        common = a.keys() & b.keys()
        self.assertEqual(len(common), 13)
        for point in common:
            self.assertEqual(a[point], b[point])
        # A radial-only normal hid physical relief even when the mesh had hills.
        self.assertTrue(any(abs(sum(p[k] * n[k] for k in range(3)) /
                                    math.sqrt(sum(c * c for c in p))) < .9999
                            for p, (n, _, _) in a.items()))

    def test_cube_face_edge_has_no_geographic_crack(self):
        radius = self.field.radius
        for y in (-radius, -321.5, 0., 912.3, radius):
            first = self.world._sample(self.field, 0, radius, y)
            second = self.world._sample(self.field, 1, -radius, y)
            self.assertEqual(first, second)

    def test_parent_stays_visible_until_all_four_children_are_ready(self):
        records = self.globe()
        self.world._stream_lod((0, 0, 1), budget=3, altitude=2000)
        pending = next(record for record in records.values() if record['pending'])
        self.assertEqual(len(pending['pending']), 3)
        self.assertFalse(pending['node'].isHidden())
        self.assertFalse(pending['children'])
        self.assertTrue(all(records[key]['node'].isHidden() for key in pending['pending']))
        self.world._stream_lod((0, 0, 1), budget=1, altitude=2000)
        self.assertEqual(len(pending['children']), 4)
        self.assertTrue(pending['node'].isHidden())
        self.assertTrue(all(not records[key]['node'].isHidden() for key in pending['children']))

    def test_orbit_refines_without_a_surface_frame(self):
        records = self.globe()
        self.world.planet = None
        self.assertIsNone(self.world.frame)
        observer = tuple(self.field._center[k] + (self.field.radius + 2500) * (k == 2)
                         for k in range(3))
        self.world._update_terrain(observer, self.field)
        self.assertGreater(len(records), 6)
        self.assertIsNone(self.world.frame)

    def test_settled_lod_skips_same_view_and_rechecks_after_movement(self):
        self.globe()
        direction = (0., 0., 1.)
        self.world._stream_lod(direction, budget=0, altitude=1e9)
        self.assertNotIn(self.field.planet_id, self.world._lod_stable)
        self.world._stream_lod(direction, budget=1, altitude=1e9)
        with patch('asterion.seamless_world.math.acos',
                   side_effect=AssertionError('LOD traversal ran')):
            self.world._stream_lod(direction, budget=1, altitude=1e9)
            with self.assertRaisesRegex(AssertionError, 'LOD traversal ran'):
                self.world._stream_lod(direction, budget=1, altitude=1e9 - 1)
            with self.assertRaisesRegex(AssertionError, 'LOD traversal ran'):
                self.world._stream_lod((.001, 0., 1.), budget=1, altitude=1e9)

    def test_runtime_lod_separates_selection_from_patch_building(self):
        records = self.globe()
        direction = (0., 0., 1.)
        self.world._stream_lod(direction, budget=100, altitude=3)
        self.assertGreater(len(records), 100)
        self.assertNotIn(self.field.planet_id, self.world._lod_pending)
        before = len(records)
        self.world._stream_lod(direction, budget=1, altitude=3)
        self.assertEqual(len(records), before)
        self.assertIn(self.field.planet_id, self.world._lod_ready)
        self.world._stream_lod(direction, budget=1, altitude=3)
        self.assertEqual(len(records), before + 1)
        self.assertIn(self.field.planet_id, self.world._lod_pending)
        for _ in range(3):
            self.world._stream_lod(direction, budget=1, altitude=3)
        self.world._stream_lod(direction, budget=1, altitude=3)
        self.assertIn(self.field.planet_id, self.world._lod_ready)
        before = len(records)
        # A fast-moving observer must not keep postponing every new patch.
        self.world._stream_lod((.0001, 0., .999999995), budget=1, altitude=3)
        self.assertEqual(len(records), before + 1)

    def test_ground_footprint_reaches_two_metre_spacing_and_retreat_releases_it(self):
        records = self.globe()
        direction = _direction_components(0, 312., -137., self.field.radius)
        self.world._stream_lod(direction, budget=512, altitude=3)
        containing = [record for key, record in records.items()
                      if key[0] == 0 and not record['children'] and not record['node'].isHidden()
                      and record['bounds'][0] <= 312 <= record['bounds'][2]
                      and record['bounds'][1] <= -137 <= record['bounds'][3]]
        self.assertTrue(containing)
        self.assertLessEqual(min((r['bounds'][2] - r['bounds'][0]) / 12 for r in containing), 2)
        self.assertLessEqual(len(records), 1022)
        self.world._stream_lod(direction, budget=1, altitude=1e9)
        self.assertEqual(len(records), 6)
        self.assertTrue(all(not r['node'].isHidden() for r in records.values()))

    def test_partial_stream_cannot_leave_a_tall_mixed_lod_curtain(self):
        records = self.globe()
        direction = _direction_components(0, self.field.radius, self.field.radius,
                                          self.field.radius)
        for budget in (4, 12, 40, 72):
            self.world._stream_lod(direction, budget=budget, altitude=3)
            for key, record in records.items():
                if record['node'].isHidden():
                    continue
                for neighbour in self.world._lod_neighbours(records, self.field, key):
                    self.assertLessEqual(abs(key[1] - neighbour[1]), 1,
                                         (key, neighbour))

    def test_saved_seabed_base_sits_on_current_surface_without_mutating_record(self):
        field = self.field
        direction = next(_direction_components(face, x * field.radius / 3,
                                                y * field.radius / 3, field.radius)
                         for face in range(6) for x in range(-3, 4) for y in range(-3, 4)
                         if field.seabed_elevation(_direction_components(face, x * field.radius / 3,
                                                                        y * field.radius / 3,
                                                                        field.radius)) < field.water_level - 2)
        u, v = direction_chart(direction, field.radius)
        record = dict(id='legacy-seabed-base', kind='beacon',
                      pos=[u, v, field.water_level - 5], heading=17)
        original_position = list(record['pos'])
        self.world.add_building(record)
        entity = self.world._entities[record['id']]
        self.assertAlmostEqual(entity['geo'][2], field.water_level)
        self.assertAlmostEqual(entity['node'].getPos().length() - field.radius,
                               field.water_level, delta=.01)
        self.assertEqual(record['pos'], original_position)


if __name__ == '__main__':
    unittest.main()
