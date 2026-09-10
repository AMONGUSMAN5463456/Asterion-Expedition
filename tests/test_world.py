"""World continuity and geometry checks; no window or graphics driver required."""
import math
from types import SimpleNamespace
import unittest

from panda3d.core import NodePath

from asterion.geometry import Mesh, make_ship
from asterion.universe import generate_system, get_planet, terrain_height
from asterion.world import CHUNK_SIZE, WorldRenderer


class SceneHost:
    def __init__(self):
        self.render=NodePath("test-render")
        self.background=None

    def setBackgroundColor(self,*color):
        self.background=color


def state():
    return SimpleNamespace(settings={"quality":"low"},depleted={},bases={},position=(0,0,22),elapsed=0)


class GeometryTests(unittest.TestCase):
    def test_ship_has_grounded_bounds_and_forward_nose(self):
        parent=NodePath("ship-test")
        ship=make_ship(parent)
        low,high=ship.getTightBounds()
        self.assertAlmostEqual(low.z,0,places=5)
        self.assertGreater(high.y,3.8)
        self.assertLess(low.y,-3.5)
        self.assertGreater(high.x-low.x,6.5)
        self.assertGreater(high.z,2.8)
        self.assertGreater(ship.findAllMatches("**/+GeomNode").getNumPaths(),1)
        parent.removeNode()

    def test_generated_normals_are_finite_and_unit_length(self):
        mesh=Mesh()
        mesh.box((0,0,0),(2,3,4),(.5,.7,.9))
        mesh.tube((1,2,3),(-2,4,7),.6,(.7,.8,.9),7,end_radius=.1)
        mesh.sphere((0,0,0),(2,2,2),(.6,.8,.4),12,8,smooth=True)
        self.assertEqual(len(mesh.vertices),len(mesh.normals))
        self.assertEqual(len(mesh.vertices)%3,0)
        for p,n in zip(mesh.vertices,mesh.normals):
            self.assertTrue(all(math.isfinite(c) for c in (*p,*n)))
            self.assertAlmostEqual(sum(c*c for c in n),1,places=5)


class WorldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=SceneHost()
        cls.state=state()
        cls.planet=get_planet(0,0)
        cls.world=WorldRenderer(cls.app)
        cls.world.load_surface(cls.planet,cls.state)

    @classmethod
    def tearDownClass(cls):
        cls.world.destroy()
        cls.app.render.removeNode()

    def test_landing_is_dry_and_mesh_collision_is_continuous(self):
        world=self.world
        self.assertGreater(world.height(0,0),self.planet["water_level"])
        self.assertEqual(world.height(0,0),20)
        # Height agrees exactly at mesh vertices, including negative tiles.
        for x,y in ((-64,-68),(0,0),(64,128),(508,-272)):
            self.assertAlmostEqual(world.height(x,y),terrain_height(self.planet["seed"],x,y),places=10)
            self.assertAlmostEqual(world.height(x-1e-7,y+.37),world.height(x+1e-7,y+.37),places=5)
        self.assertTrue(math.isfinite(world.height(float("nan"),0)))

    def test_interaction_contract_and_first_resources(self):
        entities=self.world.interactables()
        self.assertEqual(len(entities),len({e["id"] for e in entities}))
        self.assertTrue({"outpost","ruin","flora","mineral","fauna"}<={e["kind"] for e in entities})
        nearby=[e for e in entities if e["id"].startswith(self.planet["id"]+":landing:r")]
        self.assertTrue({"ferrite","carbon","oxygen","sodium"}<={e["resource"] for e in nearby})
        for entity in entities:
            self.assertTrue({"id","kind","name","pos","radius","node"}<=set(entity))
            self.assertFalse(entity["node"].isEmpty())
            self.assertGreater(entity["radius"],0)

    def test_streaming_retains_depletion_and_has_bounded_nodes(self):
        world=self.world
        candidate=next(e for e in world.interactables() if ":c" in e["id"] and e["kind"]=="mineral")
        candidate_id=candidate["id"]
        world.set_depleted(candidate_id)
        self.assertTrue(candidate["node"].isEmpty())
        world._stream((CHUNK_SIZE*12,CHUNK_SIZE*11,22),initial=True)
        self.assertLessEqual(len(world.chunks),25)
        self.assertTrue(all(not e["node"].isEmpty() for e in world.interactables()))
        world._stream((0,0,22),initial=True)
        self.assertNotIn(candidate_id,{e["id"] for e in world.interactables()})
        self.assertLessEqual(len(world.chunks),25)

    def test_building_adding_is_idempotent(self):
        record={"id":"test-beacon","kind":"beacon","pos":[13,5,999],"heading":75}
        self.world.add_building(record)
        self.world.add_building(record)
        found=[e for e in self.world.interactables() if e["id"]==record["id"]]
        self.assertEqual(len(found),1)
        self.assertAlmostEqual(found[0]["node"].getZ(),self.world.height(13,5))

    def test_orbit_depletion_and_mode_cleanup(self):
        host=SceneHost()
        s=state()
        world=WorldRenderer(host)
        system=generate_system(2)
        world.load_orbit(system,s)
        first={e["id"] for e in world.interactables() if e["kind"]=="asteroid"}
        target=sorted(first)[0]
        s.depleted["orbit:2"]=[target]
        old_root=world.root
        world.load_orbit(system,s)
        second={e["id"] for e in world.interactables() if e["kind"]=="asteroid"}
        self.assertEqual(second,first-{target})
        self.assertTrue(old_root.isEmpty())
        self.assertEqual(host.render.getNumChildren(),1)
        world.destroy()
        world.destroy()
        self.assertEqual(host.render.getNumChildren(),0)
        self.assertEqual(world.interactables(),[])
        host.render.removeNode()


if __name__=="__main__":
    unittest.main()
