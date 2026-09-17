"""Scale migration retains physical clearances and expedition data."""
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from panda3d.core import Vec3

from asterion.planetary import PlanetFrame, chart_direction, direction_chart
from asterion.state import GameState
from asterion.universe import SPACE_SCALE, generate_system, get_planet


class LargerWorldSaveTests(unittest.TestCase):
    def old_payload(self, **changes):
        payload = GameState().to_dict()
        payload.update(version=4)
        payload.pop('world_scale')
        payload.update(changes)
        return payload

    def test_framed_flight_retains_datum_altitude_offsets_and_momentum(self):
        for normal in ([0, 0, 1], [0, 1, 0], [0, 0, -1], [.6, -.8, 0]):
            payload = self.old_payload(system_id=19, planet_index=3, mode='flight',
                frame_up=normal, position=[127., -225., 730.], velocity=[21., -80., -105.],
                heading=271., pitch=-62., reference_roll=48.)
            state, _ = GameState._from_dict(payload)
            radius = get_planet(19, 3)['size']
            old_altitude = math.hypot(127, -225, radius / 4 + 730) - radius / 4
            new_altitude = math.hypot(*state.position[:2], radius + state.position[2]) - radius
            self.assertAlmostEqual(new_altitude, old_altitude, places=8)
            self.assertEqual(state.position[:2], [127, -225])
            for key in ('velocity', 'heading', 'pitch', 'reference_roll'):
                self.assertEqual(getattr(state, key), payload[key])
            for actual, expected in zip(state.frame_up, normal):
                self.assertAlmostEqual(actual, expected, places=14)
            self.assertEqual(state.world_scale, SPACE_SCALE)

    def test_unframed_entry_retains_clearance_instead_of_quadrupling_altitude(self):
        planet = get_planet(3, 2)
        normal = Vec3(.3, -.4, -.8).normalized()
        old_center = Vec3(*planet['position']) / 4
        position = old_center + normal * (planet['size'] / 4 + 1800)
        payload = self.old_payload(system_id=3, planet_index=2, mode='orbit',
                                   position=list(position), frame_up=None, velocity=[0, 0, -300])
        state, _ = GameState._from_dict(payload)
        relative = Vec3(*state.position) - Vec3(*planet['position'])
        self.assertAlmostEqual(relative.length() - planet['size'], 1800, delta=.05)
        self.assertGreater(relative.normalized().dot(normal), .999999)
        self.assertEqual(state.velocity, [0, 0, -300])

    def test_station_approach_keeps_docking_clearance(self):
        station = generate_system(4)['station']
        state, _ = GameState._from_dict(self.old_payload(system_id=4, mode='orbit',
            frame_up=None, position=[station[0] / 4, station[1] / 4 - 320, station[2] / 4]))
        self.assertEqual(state.position, [station[0], station[1] - 320, station[2]])

    def test_migration_keeps_far_base_near_player_and_home_base_in_metres(self):
        planet = get_planet(0, 0)
        old_planet = copy.deepcopy(planet)
        old_planet['size'] /= 4
        old_planet['position'] = tuple(c / 4 for c in planet['position'])
        normal = [0, 0, -1]
        frame = PlanetFrame.from_planet(old_planet, normal)
        point = frame.to_world((15, 6, 20)) - Vec3(*old_planet['position'])
        u, v = direction_chart(point, old_planet['size'])
        records = [dict(id='home', kind='beacon', pos=[20, 30, 20], heading=0),
                   dict(id='far', kind='habitat', pos=[u, v, 20], heading=90)]
        state, _ = GameState._from_dict(self.old_payload(frame_up=normal,
            position=[0, 0, 22], bases={'s0-p0': records}, depleted={'s0-p0': ['s0-p0:c1,2:r3']}))
        self.assertEqual(state.bases['s0-p0'][0]['pos'], [20, 30, 20])
        geo = state.bases['s0-p0'][1]['pos']
        new_point = Vec3(*planet['position']) + chart_direction(geo[0], geo[1], planet['size']) * (planet['size'] + geo[2])
        local = PlanetFrame.from_planet(planet, normal).to_local(new_point)
        self.assertLess(math.hypot(local.x - 15, local.y - 6), .05)
        self.assertEqual(state.depleted, {'s0-p0': ['s0-p0:c1,2:r3']})

    def test_opposite_hemisphere_base_is_not_mistaken_for_nearby_construction(self):
        planet = get_planet(0, 0)
        old_radius = planet['size']/4
        for normal in ((0, 0, 1), (0, 1, 0), (.6, -.8, 0)):
            with self.subTest(normal=normal):
                opposite = -Vec3(*normal)
                u, v = direction_chart(opposite, old_radius)
                record = dict(id='opposite-extractor', kind='extractor',
                              pos=[u, v, 20], heading=137, stored=41)
                state, _ = GameState._from_dict(self.old_payload(
                    frame_up=list(normal), position=[0, 0, 22],
                    bases={'s0-p0': [record]}))
                restored = state.bases['s0-p0'][0]
                direction = chart_direction(*restored['pos'][:2], planet['size'])
                self.assertGreater(direction.dot(opposite), .999999)
                self.assertAlmostEqual(restored['pos'][0], u*4, places=7)
                self.assertAlmostEqual(restored['pos'][1], v*4, places=7)
                self.assertEqual(restored['pos'][2], 20)
                for key in ('id', 'kind', 'heading', 'stored'):
                    self.assertEqual(restored[key], record[key])

    def test_distant_parked_ship_keeps_its_hemisphere_and_datum_clearance(self):
        radius = get_planet(0, 0)['size']
        old_radius = radius/4
        parked = [17., -11., -2*old_radius-20.]
        state, _ = GameState._from_dict(self.old_payload(
            frame_up=[0, 1, 0], position=[0, 0, 22], ship_position=parked))
        restored = state.ship_position
        self.assertLess(radius+restored[2], 0)
        self.assertEqual(restored[:2], parked[:2])
        old_altitude = math.hypot(*parked[:2], old_radius+parked[2])-old_radius
        new_altitude = math.hypot(*restored[:2], radius+restored[2])-radius
        self.assertAlmostEqual(new_altitude, old_altitude, places=8)

    def test_valid_distant_orbit_coordinate_is_not_clamped_after_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'distant-save.json'
            payload = self.old_payload(mode='orbit', frame_up=None,
                position=[8000000., -4000000., 3000000.], credits=8123,
                velocity=[70., -25., 40.])
            path.write_text(json.dumps(payload), encoding='utf-8')
            expected = [component*4 for component in payload['position']]
            state = GameState.load(path)
            self.assertEqual(state.position, expected)
            for _ in range(2):
                self.assertTrue(state.save(path)[0])
                state = GameState.load(path)
                self.assertEqual(state.position, expected)
                self.assertEqual(state.credits, payload['credits'])
                self.assertEqual(state.velocity, payload['velocity'])

    def test_failed_original_archive_keeps_the_legacy_primary_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'legacy-save.json'
            original = json.dumps(self.old_payload(credits=7123)).encode()
            path.write_bytes(original)
            state = GameState.load(path)
            state.credits += 100
            replace = os.replace

            def fail_archive(source, destination):
                if str(destination).endswith('.pre-v1.3.bak'):
                    raise OSError('archive destination unavailable')
                return replace(source, destination)

            with patch('asterion.state.os.replace', side_effect=fail_archive):
                success, _ = state.save(path)
            self.assertFalse(success)
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(any('.archive-' in entry.name for entry in path.parent.iterdir()))

    def test_original_save_archived_and_migration_never_reapplied(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'expedition.json'
            payload = self.old_payload(mode='orbit', frame_up=None,
                position=[150000, -120000, 100000], inventory={'ferrite': 100, 'carbon': 60},
                credits=12456, story_stage=8, discoveries={'sample': {'name': 'Kept sample'}},
                depleted={'s0-p0': ['s0-p0:c0,0:r2']})
            original = json.dumps(payload).encode()
            path.write_bytes(original)
            state = GameState.load(path)
            position = list(state.position)
            self.assertEqual(position, [600000, -480000, 400000])
            for _ in range(3):
                self.assertTrue(state.save(path)[0])
                state = GameState.load(path)
                self.assertEqual(state.position, position)
                for key in ('inventory', 'credits', 'story_stage', 'discoveries', 'depleted'):
                    self.assertEqual(getattr(state, key), payload[key])
            self.assertEqual(path.with_name(path.name + '.pre-v1.3.bak').read_bytes(), original)
            self.assertEqual(state.to_dict()['version'], 5)


if __name__ == '__main__':
    unittest.main()
