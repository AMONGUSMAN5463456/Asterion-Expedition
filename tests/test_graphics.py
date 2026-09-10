"""Planet colour and renderer/collider regressions, including TinyDisplay.

The CPU checks cover map continuity and the actual habitat triangles. The
software render catches the white-planet failure after render-state inheritance.
"""
import hashlib
import math
import random
from types import SimpleNamespace
import unittest

from panda3d.core import (
    AmbientLight, LightAttrib, Material, MaterialAttrib, NodePath,
    OrthographicLens, PNMImage, Point3, SamplerState, ShaderAttrib,
    TextureStage, Vec3, loadPrcFileData,
)

from asterion.collision import CollisionWorld
from asterion.geometry import building_mesh
from asterion.planet_visuals import PlanetPaint, planet_mesh, planet_texture
from asterion.universe import generate_system, get_planet
from asterion.world import (
    CHUNK_SIZE, WorldRenderer, _flora_shapes, _placed_shapes, _structure_shapes,
)


def planets():
    return [p for i in (0, 1) for p in generate_system(i)["planets"]]


def state():
    return SimpleNamespace(settings={"quality": "low"}, depleted={}, bases={},
                           position=(0, 0, 22), elapsed=0)


class SceneHost:
    def __init__(self):
        self.render = NodePath("graphics-test-render")

    def setBackgroundColor(self, *color):
        pass


def mesh_ray(meshes, origin, direction, maximum):
    """Ray-test rendered triangles, independently of the collision proxies."""
    origin, direction = Vec3(*origin), Vec3(*direction)
    direction.normalize()
    nearest = None
    for mesh in meshes:
        for i in range(0, len(mesh.vertices), 3):
            a, b, c = (Vec3(*p) for p in mesh.vertices[i:i + 3])
            ab, ac = b - a, c - a
            h = direction.cross(ac)
            determinant = ab.dot(h)
            if abs(determinant) < 1e-8:
                continue
            inverse = 1.0 / determinant
            offset = origin - a
            u = inverse * offset.dot(h)
            q = offset.cross(ab)
            v = inverse * direction.dot(q)
            distance = inverse * ac.dot(q)
            if (-1e-6 <= u <= 1 + 1e-6 and v >= -1e-6 and u + v <= 1 + 1e-6
                    and 0 <= distance <= maximum):
                nearest = distance if nearest is None else min(nearest, distance)
    return nearest


class PlanetMapTests(unittest.TestCase):
    def test_every_biome_has_varied_coloured_reproducible_maps(self):
        before = random.getstate()
        signatures = set()
        catalog = planets()
        self.assertEqual(len({p["biome"] for p in catalog}), 8)
        for planet in catalog:
            with self.subTest(biome=planet["biome"]):
                texture = planet_texture(planet, width=64, height=32)
                raw = bytes(texture.getRamImageAs("RGB"))
                self.assertEqual(len(raw), 64 * 32 * 3)
                colors = list(zip(raw[0::3], raw[1::3], raw[2::3]))
                self.assertGreater(len(set(colors)), 300)
                chroma = sum(max(c) - min(c) for c in colors) / len(colors) / 255
                self.assertGreater(chroma, .04)
                luminance = [sum(c) / 765 for c in colors]
                self.assertGreater(max(luminance) - min(luminance), .25)
                self.assertFalse(any(min(c) > 235 for c in colors))
                self.assertEqual(raw, bytes(planet_texture(dict(planet), width=64,
                                                          height=32).getRamImageAs("RGB")))
                signatures.add(hashlib.sha256(raw).digest())
        self.assertEqual(len(signatures), 8)
        self.assertEqual(random.getstate(), before)

    def test_longitude_seam_is_continuous_for_all_biomes(self):
        for planet in planets():
            paint = PlanetPaint(planet)
            for latitude in (-1.4, -.7, 0, .7, 1.4):
                with self.subTest(biome=planet["biome"], latitude=latitude):
                    r, z = math.cos(latitude), math.sin(latitude)
                    a = paint.color((r * math.cos(-1e-8), r * math.sin(-1e-8), z))
                    b = paint.color((r * math.cos(1e-8), r * math.sin(1e-8), z))
                    self.assertLess(max(abs(x - y) for x, y in zip(a, b)), 1e-5)

    def test_texture_wrap_and_poles_do_not_form_a_seam_or_pinwheel(self):
        for planet in planets():
            with self.subTest(biome=planet["biome"]):
                texture = planet_texture(planet, width=64, height=32)
                self.assertEqual(texture.getWrapU(), SamplerState.WMRepeat)
                self.assertEqual(texture.getWrapV(), SamplerState.WMClamp)
                self.assertGreater(texture.getNumRamMipmapImages(), 1)
                raw = bytes(texture.getRamImageAs("RGB"))
                for row in (raw[:64 * 3], raw[-64 * 3:]):
                    self.assertEqual(row, row[:3] * 64)

    def test_cloud_cover_changes_albedo_without_a_second_surface(self):
        planet = get_planet(0, 0)
        clear = bytes(planet_texture(planet, False, 64, 32).getRamImageAs("RGB"))
        cloudy = bytes(planet_texture(planet, True, 64, 32).getRamImageAs("RGB"))
        self.assertGreater(sum(a != b for a, b in zip(clear, cloudy)), len(clear) * .05)

    def test_sphere_uv_seam_and_baked_terminator_are_explicit(self):
        mesh = planet_mesh(20, segments=32, rings=16)
        self.assertEqual(len(mesh.vertices), len(mesh.texcoords))
        self.assertEqual(len(mesh.vertices), len(mesh.colors))
        self.assertTrue(all(math.isfinite(c) for uv in mesh.texcoords for c in uv))
        seam = {}
        for p, uv, color in zip(mesh.vertices, mesh.texcoords, mesh.colors):
            if uv[0] in (0, 1):
                seam.setdefault(uv[1], {})[uv[0]] = (p, color)
        self.assertEqual(len(seam), 17)
        for edges in seam.values():
            self.assertEqual(edges[0], edges[1])
        light = [sum(c[:3]) / 3 for c in mesh.colors]
        self.assertLess(min(light), .12)
        self.assertGreater(max(light), .90)
        self.assertLessEqual(max(max(c[:3]) for c in mesh.colors), 1)

    def test_planet_overrides_white_parent_material_and_automatic_shader(self):
        host = SceneHost()
        self.addCleanup(host.render.removeNode)
        world = WorldRenderer(host)
        self.addCleanup(world.destroy)
        world.state = state()
        parent = host.render.attachNewNode("inherited-white-material")
        material = Material("white")
        material.setDiffuse((1, 1, 1, 1))
        material.setAmbient((1, 1, 1, 1))
        parent.setMaterial(material)
        parent.setShaderAuto()
        light = AmbientLight("overbright")
        light.setColor((3, 3, 3, 1))
        parent.setLight(parent.attachNewNode(light))
        planet = world._planet_model(get_planet(0, 0), parent, 30, rings=True)
        surface = planet.find("**/continental-surface")
        net = surface.getNetState()
        self.assertTrue(net.getAttrib(LightAttrib).hasAllOff())
        self.assertTrue(net.getAttrib(MaterialAttrib).isOff())
        shader = net.getAttrib(ShaderAttrib)
        self.assertFalse(shader.autoShader())
        self.assertTrue(shader.hasShader())
        self.assertIsNone(shader.getShader())
        self.assertIsNotNone(surface.getTexture())
        self.assertEqual(surface.findTextureStage("default").getMode(), TextureStage.MModulate)
        self.assertEqual(planet.findAllMatches("**/continental-surface").getNumPaths(), 1)
        self.assertFalse(planet.find("**/atmospheric-limb").getDepthWrite())
        self.assertFalse(planet.find("**/mineral-rings").getDepthWrite())


class HabitatGeometryTests(unittest.TestCase):
    def setUp(self):
        self.meshes = building_mesh("habitat")
        self.collision = CollisionWorld()
        self.collision.set_group("habitat", _structure_shapes("habitat"))

    def test_visible_door_and_collision_both_leave_a_standing_passage(self):
        for x in (-.65, 0, .65):
            for z in (.45, 1.4, 2.2):
                with self.subTest(x=x, z=z):
                    self.assertIsNone(mesh_ray(self.meshes, (x, -5, z), (0, 1, 0), 5))
                    self.assertIsNone(self.collision.raycast((x, -5, z), (0, 1, 0), 5))

    def test_solid_walls_floor_and_roof_match_the_visible_shell(self):
        rays = (
            ((2, -5, 1.4), (0, 1, 0), 5),
            ((0, 0, 1.4), (1, 0, 0), 5),
            ((0, 0, 1.4), (0, 1, 0), 5),
            ((0, 0, 1.4), (0, 0, 1), 5),
            ((0, 0, 1.4), (0, 0, -1), 5),
            ((0, 0, 5), (0, 0, -1), 5),
        )
        for origin, direction, distance in rays:
            with self.subTest(origin=origin, direction=direction):
                visible = mesh_ray(self.meshes, origin, direction, distance)
                hit = self.collision.raycast(origin, direction, distance)
                self.assertIsNotNone(visible)
                self.assertIsNotNone(hit)
                self.assertAlmostEqual(hit.distance, visible, delta=.025)

    def test_rotated_habitat_allows_entry_and_supports_floor_roof_and_ceiling(self):
        for heading in (0, 37, 90, 207):
            with self.subTest(heading=heading):
                node = NodePath("placed-habitat")
                node.setPos(31, -18, 20)
                node.setH(heading)
                matrix = node.getMat()
                self.collision.set_group("habitat", _placed_shapes(
                    _structure_shapes("habitat"), "habitat", tuple(node.getPos()), heading))
                entry = matrix.xformPoint(Point3(0, -5, 1.8))
                delta = matrix.xformVec(Vec3(0, 5, 0))
                result = self.collision.move_capsule(entry, delta)
                expected = matrix.xformPoint(Point3(0, 0, 2.001))
                self.assertLess((result.position - expected).length(), .015)
                self.assertFalse(self.collision.overlaps_capsule(result.position))
                ceiling = self.collision.move_capsule(result.position, (0, 0, 3))
                self.assertTrue(ceiling.ceiling)
                self.assertAlmostEqual(ceiling.position.z, 23, delta=.005)
                roof = self.collision.move_capsule((31, -18, 27), (0, 0, -8))
                self.assertTrue(roof.grounded)
                self.assertAlmostEqual(roof.position.z - 1.8, 23.32, delta=.005)
                node.removeNode()

    def test_tree_proxies_remain_trunk_sized(self):
        for style in ("fan", "coral", "mushroom", "succulent"):
            with self.subTest(style=style):
                world = CollisionWorld()
                shapes = _flora_shapes(style, 1234)
                world.set_group(style, shapes)
                self.assertTrue(all(shape["radius"] <= .53 for shape in shapes))
                self.assertIsNotNone(world.raycast((-2, 0, .8), (1, 0, 0), 3))
                self.assertFalse(world.overlaps_capsule((1, -1.2, 1.8)))


class WorldCollisionBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = SceneHost()
        cls.world = WorldRenderer(cls.host)
        cls.world.load_surface(get_planet(0, 0), state())

    @classmethod
    def tearDownClass(cls):
        cls.world.destroy()
        cls.host.render.removeNode()

    def test_depletion_unload_and_return_remove_owned_collision(self):
        world = self.world
        candidate = next(e for e in world.interactables()
                         if ":c" in e["id"] and e["kind"] == "mineral")
        target = candidate["id"]
        self.assertIn(target, world.collisions._groups)
        count = world.collisions.shape_count
        world.set_depleted(target)
        self.assertNotIn(target, world.collisions._groups)
        self.assertLess(world.collisions.shape_count, count)
        old_groups = {chunk["collision_group"] for chunk in world.chunks.values()}
        old_resources = {entity_id for chunk in world.chunks.values() for entity_id in chunk["ids"]}
        old_nodes = [chunk["root"] for chunk in world.chunks.values()]
        world._stream((CHUNK_SIZE * 12, CHUNK_SIZE * 11, 22), initial=True)
        self.assertTrue(old_groups.isdisjoint(world.collisions._groups))
        self.assertTrue(old_resources.isdisjoint(world.collisions._groups))
        self.assertTrue(all(node.isEmpty() for node in old_nodes))
        world._stream((0, 0, 22), initial=True)
        self.assertNotIn(target, world.collisions._groups)
        self.assertNotIn(target, {e["id"] for e in world.interactables()})
        self.assertTrue(all(e["id"] not in world.collisions._groups
                            for e in world.interactables() if e["kind"] == "fauna"))

    def test_constructed_buildings_register_immediately_and_remove_cleanly(self):
        world = self.world
        baseline = world.collisions.shape_count
        record = dict(id="graphics-habitat", kind="habitat", pos=(0, -20, 500), heading=37)
        world.add_building(record)
        node = world._buildings[record["id"]]
        self.assertGreater(world.collisions.shape_count, baseline)
        self.assertIn(record["id"], world.collisions._groups)
        world.add_building(record)
        self.assertEqual(len(world.collisions._groups[record["id"]]), 8)
        world.set_depleted(record["id"])
        self.assertEqual(world.collisions.shape_count, baseline)
        self.assertTrue(node.isEmpty())

    def test_terrain_detail_uvs_and_colours_join_at_chunk_boundaries(self):
        world = self.world
        left = world._terrain_mesh(-CHUNK_SIZE, 0)
        right = world._terrain_mesh(0, 0)
        def edge(mesh):
            return {p: (n, c, uv) for p, n, c, uv in
                    zip(mesh.vertices, mesh.normals, mesh.colors, mesh.texcoords) if p[0] == 0}
        self.assertGreater(len(edge(left)), 1)
        self.assertEqual(edge(left), edge(right))
        material = world.root.getMaterial()
        self.assertFalse(material.hasDiffuse())
        self.assertFalse(material.hasAmbient())

    def test_orbit_asteroid_station_and_mode_destroy_cleanup(self):
        host = SceneHost()
        world = WorldRenderer(host)
        self.addCleanup(host.render.removeNode)
        self.addCleanup(world.destroy)
        world.load_orbit(generate_system(2), state())
        target = next(e for e in world.interactables() if e["kind"] == "asteroid")
        self.assertIn(target["id"], world.collisions._groups)
        self.assertIn("s2:station", world.collisions._groups)
        before = world.collisions.shape_count
        world.set_depleted(target["id"])
        self.assertEqual(world.collisions.shape_count, before - 1)
        self.assertNotIn(target["id"], world.collisions._groups)
        self.assertTrue(target["node"].isEmpty())
        old_root = world.root
        world._start("surface")
        self.assertTrue(old_root.isEmpty())
        self.assertEqual(world.collisions.shape_count, 0)
        world.destroy()
        world.destroy()
        self.assertEqual(world.collisions.shape_count, 0)


class SoftwarePlanetTests(unittest.TestCase):
    def test_all_eight_biomes_render_coloured_with_an_overbright_parent(self):
        loadPrcFileData("asterion-graphics-tests", "\n".join((
            "load-display p3tinydisplay", "aux-display p3tinydisplay",
            "window-type offscreen", "win-size 160 160", "audio-library-name null",
            "textures-power-2 up", "sync-video false", "notify-level error", "model-cache-dir",
        )))
        from direct.showbase.ShowBase import ShowBase
        app = ShowBase(windowType="offscreen")
        self.addCleanup(app.destroy)
        app.disableMouse()
        app.setBackgroundColor(0, 0, 0, 1)
        lens = OrthographicLens()
        lens.setFilmSize(2.4, 2.4)
        lens.setNearFar(.1, 20)
        app.cam.node().setLens(lens)
        app.camera.setPos(0, -4, 0)
        app.camera.lookAt(0, 0, 0)
        parent = app.render.attachNewNode("overbright-parent")
        white = Material("white-material-regression")
        white.setDiffuse((1, 1, 1, 1))
        white.setAmbient((1, 1, 1, 1))
        parent.setMaterial(white)
        parent.setShaderAuto()
        light = AmbientLight("overbright-parent-light")
        light.setColor((4, 4, 4, 1))
        parent.setLight(parent.attachNewNode(light))
        world = WorldRenderer(app)
        world.state = state()
        world.state.settings["quality"] = "medium"
        world.mode = "orbit"
        signatures = set()
        for planet in planets():
            with self.subTest(biome=planet["biome"]):
                node = world._planet_model(planet, parent, 200)
                node.setScale(.005)
                texture = node.find("**/continental-surface").getTexture()
                self.assertEqual((texture.getXSize(), texture.getYSize()), (512, 256))
                app.graphicsEngine.renderFrame()
                app.graphicsEngine.renderFrame()
                frame = PNMImage()
                self.assertTrue(app.win.getScreenshot(frame))
                pixels = [tuple(frame.getXel(x, y)) for y in range(36, 124, 3)
                          for x in range(36, 124, 3) if (x - 80) ** 2 + (y - 80) ** 2 < 44 ** 2]
                signatures.add(tuple(round(c, 3) for p in pixels for c in p))
                node.removeNode()
                chroma = sum(max(p) - min(p) for p in pixels) / len(pixels)
                # Basalt has a deliberately dark, restrained palette; even it
                # must retain visible channel separation after vertex lighting.
                self.assertGreater(chroma, .02)
                brightness = [sum(p) / 3 for p in pixels]
                self.assertGreater(max(brightness) - min(brightness), .10)
                self.assertLess(sum(min(p) > .92 for p in pixels) / len(pixels), .02)
        self.assertEqual(len(signatures), 8)


if __name__ == "__main__":
    unittest.main()
