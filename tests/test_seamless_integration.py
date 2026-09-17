"""Native end-to-end regression tests for continuous planet flight.

These drive the real application and shared collision field. No flight test
uses the explicit load initializer to cross an atmosphere boundary.
"""
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from panda3d.core import Vec3, loadPrcFileData
loadPrcFileData('seamless-integration', 'load-display p3tinydisplay\nwindow-type offscreen\nwin-size 640 360\naudio-library-name null\nnotify-level error\nmodel-cache-dir')
from asterion.app import ExpeditionApp
from asterion.planetary import PlanetFrame, frame_to_world, vector_to_world, vector_to_local


class SeamlessFlightIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saves = tempfile.TemporaryDirectory(prefix='asterion-continuity-')
        cls.app = ExpeditionApp(save_dir=cls.saves.name, offscreen=True, no_audio=True)

    @classmethod
    def tearDownClass(cls):
        cls.app.cleanup()
        cls.saves.cleanup()

    def setUp(self):
        a = self.app
        a.transition = a.autopilot = None
        a.fade.hide()
        a.game.settings.update(quality='low', camera_motion=0)
        a.new_game()
        a.game.settings['flight_assist'] = False

    def flying(self, planet_index=0, normal=(0, 0, 1), altitude=3500):
        a = self.app
        planet = a.system['planets'][planet_index]
        field = a.world.fields[planet['id']]
        direction = Vec3(*normal)
        direction.normalize()
        point = field.center + direction * (field.radius + field.elevation(direction) + altitude)
        a._remove_ship()
        a.frame = None
        a.world.set_frame(None)
        a.controller.set_mode('orbit', point, 27, -13)
        a.controller.set_flight_collision(a.world.move_sphere)
        a._update_flight_environment()
        return field, direction

    def physical_pose(self):
        a = self.app
        return (a.world_position(), vector_to_world(a.frame, a.controller.velocity),
                vector_to_world(a.frame, a.camera.getQuat().getForward()),
                vector_to_world(a.frame, a.camera.getQuat().getUp()))

    def assertPoseNear(self, before, after):
        for index, (x, y) in enumerate(zip(before, after)):
            self.assertLess((x-y).length(), .04 if index == 0 else .002, (index, x, y))

    def test_round_trip_crosses_clouds_and_space_without_loading_or_input_reset(self):
        a = self.app
        a.recall_ship()
        a.launch()
        a.controller.reset_keys()
        a.controller.velocity = Vec3(0, 0, 800)
        roots = (a.world.root, dict(a.world.body_roots))
        seen, maximum_exit = set(), 0
        # A held look key also survives, while inertial thrust carries the ship.
        a.controller.keys['arrow_right'] = True
        with patch.object(a, 'transition_to', side_effect=AssertionError('flight fade')):
            for _ in range(55):
                a.step(.1)
                seen.add(a.controller.mode)
                maximum_exit = max(maximum_exit, a.atmosphere_data['exit'])
                self.assertIsNone(a.transition)
                self.assertTrue(a.fade.isHidden())
                self.assertTrue(a.controller.keys['arrow_right'])
                self.assertEqual(a.world.root, roots[0])
                self.assertEqual(a.world.body_roots, roots[1])
            self.assertEqual(seen, {'flight', 'orbit'})
            self.assertIsNone(a.frame)
            field = a.world.fields[a.system['planets'][0]['id']]
            normal = a.world_position()-field.center
            normal.normalize()
            a.controller.velocity = vector_to_local(a.frame, -normal * 800)
            maximum_heat = 0
            for _ in range(70):
                a.step(.1)
                maximum_heat = max(maximum_heat, a.atmosphere_data['heat'])
                self.assertEqual(a.world.root, roots[0])
                if a.atmosphere_data['altitude'] < 50:
                    break
        self.assertGreater(maximum_exit, .3)
        self.assertGreater(maximum_heat, .3)
        self.assertEqual(a.controller.mode, 'flight')
        self.assertGreater(a.atmosphere_data['altitude'], 2.5)
        self.assertLess(a.atmosphere_data['altitude'], 80)

    def test_rebase_preserves_world_pose_velocity_and_all_held_keys(self):
        a = self.app
        field, normal = self.flying(2, (-.35, .84, -.7), 700)
        a.controller.velocity = Vec3(23, -61, 104)
        a.controller.reference_roll = 31
        a.camera.setHpr(67, -51, 31)
        a.controller.heading, a.controller.pitch = 67, -51
        a.controller.keys.update(w=True, shift=True, space=True)
        before = self.physical_pose()
        keys = dict(a.controller.keys)
        root = a.world.root
        other = Vec3(normal + Vec3(.04, -.015, .03))
        a._change_frame(PlanetFrame.from_planet(field.planet, other), field.planet)
        self.assertPoseNear(before, self.physical_pose())
        self.assertEqual(a.controller.keys, keys)
        self.assertEqual(a.world.root, root)
        a._change_frame(None)
        self.assertPoseNear(before, self.physical_pose())

    def test_far_hemisphere_and_polar_entry_keeps_same_location(self):
        a = self.app
        for normal in ((0, 0, -1), (0, 1, 0), (0, -1, 0), (.8, .1, -.7)):
            with self.subTest(normal=normal):
                field, direction = self.flying(1, normal, 2790)
                self.assertIsNotNone(a.frame)
                self.assertEqual(a.game.planet_index, 1)
                self.assertGreater(a.frame.normal.dot(direction), .99999)
                self.assertAlmostEqual(field.altitude(a.world_position()), 2790, delta=.04)
                self.assertIsNone(a.transition)
                a.controller.velocity = vector_to_local(a.frame, -direction * 400)
                for _ in range(8):
                    a.step(.1)
                self.assertLess(a.atmosphere_data['altitude'], 2500)
                self.assertGreater((a.world_position()-field.center).normalized().dot(direction), .999)

    def test_entry_streams_at_live_flight_pose_instead_of_stale_saved_ground_pose(self):
        a = self.app
        a.game.position = [0, 0, 22]  # Autosave still contains the previous surface.
        with patch.object(a.world, '_stream_lod', wraps=a.world._stream_lod) as stream:
            self.flying(1, (.4, .3, .8), 2790)
        preparation = stream.call_args_list[0]
        self.assertAlmostEqual(preparation.kwargs['altitude'], 2790, delta=.1)
        self.assertEqual(preparation.kwargs['budget'], 16)
        self.assertLess((a.world._last_position - a.controller.position).length(), .01)

    def test_atmosphere_is_not_solid_and_surface_stops_fast_descent(self):
        a = self.app
        field, direction = self.flying(3, (.6, .2, -.7), 2400)
        start = a.controller.position
        crossing = a.world.move_sphere(start, vector_to_local(a.frame, -direction * 800), 3)
        self.assertAlmostEqual(field.altitude(frame_to_world(a.frame, crossing.position)), 1600, delta=.05)
        self.assertFalse(any('atmosphere' in key for key in crossing.hit_ids))
        ground = a.world.move_sphere(start, vector_to_local(a.frame, -direction * 3000), 3)
        self.assertTrue(any('terrain' in key for key in ground.hit_ids), ground.hit_ids)
        self.assertGreater(field.altitude(frame_to_world(a.frame, ground.position)), 2.8)

    def test_midflight_save_restores_actual_pose_velocity_and_orientation(self):
        a = self.app
        field, direction = self.flying(2, (.5, -.7, -.2), 920)
        a.controller.velocity = Vec3(-27, 38, -83)
        a.controller.heading, a.controller.pitch, a.controller.reference_roll = 234, -90, 37
        a.camera.setHpr(234, -90, 37)
        before = self.physical_pose()
        self.assertTrue(a.save_game(False))
        a.continue_game()
        self.assertEqual(a.controller.mode, 'flight')
        self.assertIsNone(a.ship)
        self.assertEqual(a.game.planet_index, 2)
        self.assertPoseNear(before, self.physical_pose())
        a.step(.1)
        self.assertGreater((a.world_position()-before[0]).length(), 5)

    def test_autopilot_plans_without_moving_then_physically_lands(self):
        a = self.app
        self.flying(1, (0, 0, 1), 2850)
        start = self.physical_pose()
        root = a.world.root
        self.assertTrue(a.land_on_planet(1))
        self.assertPoseNear(start, self.physical_pose())
        with patch.object(a, 'transition_to', side_effect=AssertionError('autopilot fade')):
            for _ in range(1200):
                a.step(.1)
                self.assertEqual(a.world.root, root)
                if a.controller.mode == 'surface' or not a.autopilot:
                    break
        self.assertEqual(a.controller.mode, 'surface', (a.notice, a.atmosphere_data, a.autopilot))
        self.assertEqual(a.game.planet_index, 1)
        self.assertIsNotNone(a.ship)
        self.assertIn(a.planet['id'], a.game.visited)

    def test_depletion_and_building_remain_after_space_roundtrip(self):
        a = self.app
        resource = next(e for e in a.world.interactables() if e['kind'] in ('flora', 'mineral'))
        identifier = resource['id']
        a.game.depleted.setdefault(a.planet['id'], []).append(identifier)
        a.world.set_depleted(identifier)
        geo = a._geo_position(Vec3(0, -30, a.world.height(0, -30)))
        building = {'id': 'continuity-beacon', 'kind': 'beacon', 'pos': geo, 'heading': 17}
        a.game.bases.setdefault(a.planet['id'], []).append(building)
        a.world.add_building(building)
        before = next(e for e in a.world.interactables() if e['id'] == building['id'])
        world_pos = frame_to_world(a.frame, before['pos'])
        root = a.world.root
        a._remove_ship()
        a._change_frame(None)
        a._change_frame(PlanetFrame.from_planet(a.planet), a.planet)
        after = {e['id']:e for e in a.world.interactables()}
        self.assertNotIn(identifier, after)
        self.assertLess((frame_to_world(a.frame, after[building['id']]['pos'])-world_pos).length(), .04)
        self.assertEqual(a.world.root, root)


if __name__ == '__main__':
    unittest.main()
