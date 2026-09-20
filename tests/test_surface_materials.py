"""Landscape material contracts independent of a particular graphics driver."""
import math
import random
from types import SimpleNamespace
import unittest

from panda3d.core import NodePath, SamplerState, TransparencyAttrib, Vec3

from asterion.geometry import Mesh
from asterion.planetary import PlanetField, PlanetFrame
from asterion.planet_visuals import terrain_detail_texture, terrain_relief_texture
from asterion.seamless_world import SeamlessWorld, _direction_components
from asterion.surface_materials import (configure_surface, terrain_albedo,
                                       update_surface_materials)
from asterion.universe import generate_system


class SurfaceMaterialTests(unittest.TestCase):
    def setUp(self):
        self.field = PlanetField(generate_system(0)["planets"][0])

    def test_ocean_material_uses_real_seabed_without_changing_contact_height(self):
        dry, wet = 0, 0
        before = random.getstate()
        field = self.field
        for face in range(6):
            for x in (-.75, 0., .75):
                for y in (-.75, 0., .75):
                    direction = _direction_components(face,x*field.radius,y*field.radius,field.radius)
                    contact = field.elevation(direction)
                    depth = field.seabed_elevation(direction)-field.water_level
                    color = terrain_albedo(field,direction)
                    self.assertTrue(all(math.isfinite(c) and 0 <= c <= 1 for c in color))
                    self.assertEqual(contact,field.elevation(direction))
                    if depth < -.5:
                        wet += 1
                        self.assertLess(color[3],.5)
                        self.assertEqual(contact,field.water_level)
                    elif depth > .5:
                        dry += 1
                        self.assertGreater(color[3],.5)
        self.assertGreater(dry,0)
        self.assertGreater(wet,0)
        self.assertEqual(before,random.getstate())

    def test_material_alpha_is_opaque_even_under_transparent_parent(self):
        parent = NodePath("transparent-parent")
        self.addCleanup(parent.removeNode)
        parent.setTransparency(TransparencyAttrib.MAlpha)
        mesh=Mesh()
        mesh.tri((0,0,0),(1,0,0),(0,1,0),(.2,.5,.4,.08))
        node=mesh.node("ocean-test",parent,unlit=True)
        configure_surface(node,self.field,(0.,0.,0.),gpu=False)
        transparency=node.getNetState().getAttrib(TransparencyAttrib)
        self.assertEqual(transparency.getMode(),TransparencyAttrib.MNone)
        self.assertIsNone(node.getShader())
        self.assertIsNotNone(node.getTexture())

    def test_triplanar_observer_stays_body_relative_during_a_reframe(self):
        root=NodePath("render")
        self.addCleanup(root.removeNode)
        world=SeamlessWorld(SimpleNamespace(render=root))
        world.root=root.attachNewNode("system")
        world.system=generate_system(0)
        self.addCleanup(world.destroy)
        body=world.root.attachNewNode("body")
        body.setPos(self.field.center)
        observer=self.field.center+Vec3(31.,-117.,self.field.radius+420.)
        update_surface_materials(body,self.field,observer,17.)
        shader_input=body.getShaderInput("ae_observer")
        before=tuple(shader_input.getVector())
        world.set_frame(PlanetFrame.from_planet(self.field.planet,(.3,.2,.91)))
        update_surface_materials(body,self.field,observer,17.)
        shader_input=body.getShaderInput("ae_observer")
        self.assertEqual(before,tuple(shader_input.getVector()))

    def test_soil_is_reproducible_mipmapped_and_has_no_hard_repeat_border(self):
        before=random.getstate()
        texture=terrain_detail_texture(self.field.seed)
        pixels=bytes(texture.getRamImageAs("RGB"))
        width=texture.getXSize()
        self.assertEqual(width,256)
        self.assertEqual(texture.getWrapU(),SamplerState.WMRepeat)
        self.assertEqual(texture.getWrapV(),SamplerState.WMRepeat)
        self.assertGreater(texture.getNumRamMipmapImages(),1)
        self.assertEqual(pixels,bytes(terrain_detail_texture(self.field.seed).getRamImageAs("RGB")))
        red=pixels[::3]
        self.assertGreater(max(red)-min(red),45)
        border=sum(abs(red[y*width]-red[y*width+width-1]) for y in range(width))/width
        self.assertLess(border,22)
        self.assertEqual(before,random.getstate())

    def test_batched_foliage_keeps_wind_weights_and_small_local_coordinates(self):
        root=NodePath("batch-test-render")
        self.addCleanup(root.removeNode)
        world=SeamlessWorld(SimpleNamespace(render=root))
        self.addCleanup(world.destroy)
        world.planet=self.field.planet
        world.fields[self.field.planet_id]=self.field
        source=Mesh()
        source.tri((0,0,0),(1,0,1),(0,1,1),(.3,.4,.2),
                   texcoords=((0,0),(.75,0),(1,0)))
        batch=Mesh()
        origin=(0.,0.,self.field.radius+20.)
        world._append_decor(batch,source,(0,0,20.),origin_offset=origin)
        self.assertEqual(batch.texcoords,source.texcoords)
        self.assertTrue(all(abs(c)<2 for vertex in batch.vertices for c in vertex))
        stone=Mesh()
        stone.tri((0,0,0),(1,0,0),(0,1,0),(.4,.35,.3))
        world._append_decor(batch,stone,(0,0,20.),origin_offset=origin)
        self.assertEqual(batch.texcoords[-3:],[(0.,0.)]*3)

    def test_baked_relief_is_soft_and_has_no_systematic_tangent_tilt(self):
        texture=terrain_relief_texture(self.field.seed)
        data=bytes(texture.getRamImageAs("RGB"))
        self.assertGreater(texture.getNumRamMipmapImages(),1)
        for component in (data[::3],data[1::3]):
            mean=sum(component)/len(component)
            self.assertAlmostEqual(mean,127.,delta=1.5)
            self.assertGreater(max(component)-min(component),15)


if __name__ == "__main__":
    unittest.main()
