"""Persistent solar rendering and combined collision regression coverage."""
import unittest
from panda3d.core import Vec3, loadPrcFileData
from direct.showbase.ShowBase import ShowBase
loadPrcFileData('seamless-world', 'load-display p3tinydisplay\nwindow-type offscreen\nwin-size 320 180\naudio-library-name null\nnotify-level error\nmodel-cache-dir')
from asterion.seamless_world import SeamlessWorld
from asterion.planetary import PlanetFrame, frame_to_world
from asterion.state import GameState
from asterion.universe import generate_system


class PersistentWorldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = ShowBase(windowType='offscreen')
        cls.app.disableMouse()

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.state = GameState()
        self.state.settings['quality'] = 'low'
        self.system = generate_system(0)
        self.planet = self.system['planets'][0]
        self.world = SeamlessWorld(self.app)
        self.addCleanup(self.world.destroy)
        self.world.ensure_system(self.system, self.state)
        self.frame = PlanetFrame.from_planet(self.planet)
        self.world.prepare_surface(self.planet, self.frame, self.state)

    def test_fixed_body_world_positions_across_full_sphere_reframes(self):
        roots = dict(self.world.body_roots)
        solar = self.world.root
        for normal in ((0,0,-1), (0,1,0), (.3,-.8,.2), (0,0,1)):
            frame = PlanetFrame.from_planet(self.planet, normal)
            self.world.set_frame(frame)
            for planet in self.system['planets']:
                p = self.world.body_roots[planet['id']].getPos(self.app.render)
                self.assertLess((frame_to_world(frame,p)-Vec3(*planet['position'])).length(), .10)
            self.assertEqual(roots, self.world.body_roots)
            self.assertEqual(solar, self.world.root)

    def test_combined_sweep_hits_thin_wall_before_the_ground(self):
        self.world.collisions.clear()
        self.world.collisions.set_group('test-wall',[{'id':'thin-wall','type':'box',
            'center':(10,-20,29),'half':(.10,8,10)}])
        result = self.world.move_sphere((-20,-20,32),(100,0,-15),3)
        self.assertIn('thin-wall',result.hit_ids)
        self.assertLess(result.position.x,7.01)
        self.assertGreater(result.position.z,self.world.height(result.position.x,result.position.y)+2.95)

    def test_ground_contact_does_not_stall_a_tangent_slide(self):
        self.world.collisions.clear()
        z = self.world.height(0,-20)+3.04
        result = self.world.move_sphere((0,-20,z),(12,0,-2),3)
        self.assertGreater(result.position.x,10.5)
        field = self.world.fields[self.planet['id']]
        self.assertGreater(field.altitude(self.frame.to_world(result.position)),2.96)

    def test_stream_and_frame_repetition_keep_bounded_objects_and_colliders(self):
        for _ in range(35):
            self.world.update(0,(0,-16,23),0)
        nodes = self.world.root.findAllMatches('**').getNumPaths()
        shapes = self.world.collisions.shape_count
        for _ in range(12):
            self.world.set_frame(None)
            self.world.set_frame(self.frame)
        self.assertEqual(self.world.root.findAllMatches('**').getNumPaths(),nodes)
        self.assertEqual(self.world.collisions.shape_count,shapes)
        for x in (600,1200,1800,2400,3000,0):
            normal = Vec3(x,0,self.planet['size']);normal.normalize()
            frame = PlanetFrame.from_planet(self.planet,normal)
            self.state.position=[0,0,22]
            self.world.prepare_surface(self.planet,frame,self.state)
            self.world.update(0,(0,0,22),0)
        self.assertLessEqual(len(self.world.chunks),49)
        self.assertLess(self.world.root.findAllMatches('**').getNumPaths(),nodes+400)
        self.assertLess(len(self.world._sample_cache),65001)

    def test_environment_keeps_stars_fixed_and_fog_changes_continuously(self):
        self.world.update(0,(0,0,1020),0)
        rotation = self.world.sky_root.getQuat(self.app.render)
        self.assertGreater(self.world.environment['density'],0)
        first = self.world.environment['density']
        self.world.update(0,(0,0,1020.1),0)
        self.assertLess(abs(self.world.environment['density']-first),.001)
        self.world.update(0,(0,0,2400),0)
        self.assertEqual(self.world.environment['density'],0)
        self.assertFalse(self.world.root.hasFog())
        self.assertAlmostEqual(abs(rotation.dot(self.world.sky_root.getQuat(self.app.render))),1,places=5)

    def test_returning_to_another_planet_retains_depletion_and_buildings(self):
        identifier = f"{self.planet['id']}:landing:r0"
        self.state.depleted[self.planet['id']]=[identifier]
        self.world.set_depleted(identifier)
        building = {'id':'persistent-building','kind':'beacon','pos':[0,-25,20],'heading':0}
        self.state.bases[self.planet['id']]=[building]
        self.world.add_building(building)
        root = self.world.root
        other = self.system['planets'][1]
        self.world.prepare_surface(other,PlanetFrame.from_planet(other),self.state)
        self.world.prepare_surface(self.planet,self.frame,self.state)
        entities = {e['id']:e for e in self.world.interactables()}
        self.assertNotIn(identifier,entities)
        self.assertIn(building['id'],entities)
        self.assertEqual(self.world.root,root)


if __name__ == '__main__':
    unittest.main()
