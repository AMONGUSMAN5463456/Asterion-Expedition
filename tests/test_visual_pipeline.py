"""Presentation lifetimes and shader assets, independent of desktop drivers."""
from pathlib import Path
from types import SimpleNamespace
import unittest

from panda3d.core import NodePath, Vec3
from asterion.exploration_vfx import ExplorationVFX, mining_beam


class ExplorationEffectsTests(unittest.TestCase):
    def setUp(self):
        render=NodePath("render")
        world=render.attachNewNode("solar-system")
        self.app=SimpleNamespace(render=render,world=SimpleNamespace(root=world,height=lambda x,y:0),
                controller=SimpleNamespace(position=Vec3(0,0,1.8),mode="surface"))
        self.effects=ExplorationVFX(self.app)

    def tearDown(self):
        self.effects.destroy()
        self.app.render.removeNode()

    def test_repeated_scan_replaces_pulse_and_releases_it(self):
        self.effects.scan()
        first=self.effects.pulse
        self.effects.scan()
        self.assertTrue(first.isEmpty())
        self.assertEqual(self.app.world.root.getNumChildren(),1)
        self.effects.update(.1)
        transform=self.effects.pulse.getTransform()
        self.effects.update(0)
        self.assertEqual(self.effects.pulse.getTransform(),transform)
        for _ in range(30): self.effects.update(.1)
        self.assertIsNone(self.effects.pulse)
        self.assertEqual(self.app.world.root.getNumChildren(),0)

    def test_pulse_is_owned_by_physical_system_and_tolerates_unload(self):
        self.effects.scan()
        self.app.world.root.setPos(200,300,400)
        self.assertEqual(self.effects.pulse.getPos(),Vec3(0,0,.15))
        self.app.world.root.removeNode()
        self.effects.update(.1)
        self.effects.destroy()
        self.effects.destroy()

    def test_beam_and_contact_are_one_removable_node(self):
        beam=mining_beam(self.app.render,Vec3(0),Vec3(0,20,0),3.)
        self.assertGreaterEqual(beam.getNumChildren(),2)
        beam.removeNode()
        self.assertTrue(beam.isEmpty())
        point=mining_beam(self.app.render,Vec3(0),Vec3(0))
        point.removeNode()


if __name__=="__main__": unittest.main()
