"""Cloud depth, physical anchoring, continuous mist and software rendering."""
import math
import unittest

from panda3d.core import (GeomVertexReader, NodePath, PNMImage, Vec3,
                          loadPrcFileData)

from asterion.geometry import Mesh
from asterion.planet_weather import PlanetWeather, _visibility, fog_profile
from asterion.planetary import PlanetField
from asterion.universe import get_planet


class CloudVolumeTests(unittest.TestCase):
    def setUp(self):
        self.field = PlanetField(get_planet(0, 0))
        self.body = NodePath("test-physical-planet")
        self.weather = PlanetWeather(self.field, self.body)
        self.addCleanup(self.body.removeNode)
        self.addCleanup(self.weather.destroy)

    def point(self, altitude, x=0, y=0):
        return self.field.center + Vec3(x, y, self.field.radius + 20 + altitude)

    def test_clouds_occupy_finite_depth_above_the_physical_surface(self):
        puffs = self.weather.puffs
        self.assertEqual(len(puffs), 679)
        self.assertEqual(len(self.weather.banks), 97)
        heights = []
        for puff in puffs:
            heights.append(self.field.altitude(self.field.center + Vec3(*puff.center)))
            self.assertGreaterEqual(puff.radii[2], 80)
            self.assertLessEqual(puff.radii[2], 145)
        self.assertGreater(max(heights) - min(heights), 150)
        self.assertGreater(min(heights), 390)
        self.assertLess(max(heights), 560)
        samples = [self.weather.local_density(self.point(altitude))
                   for altitude in range(0, 1001, 2)]
        self.assertGreater(max(samples), .50)
        self.assertTrue(all(value == 0 for value in samples[:140]))
        self.assertTrue(all(value == 0 for value in samples[351:]))
        self.assertLess(max(abs(a - b) for a, b in zip(samples, samples[1:])), .025)

    def test_puff_centers_stay_fixed_while_projected_billows_have_parallax(self):
        centers = tuple(puff.center for puff in self.weather.puffs)
        count = self.weather.root.findAllMatches("**").getNumPaths()
        self.assertEqual(count, 100)
        data = self.weather._billows[-1]["data"]

        def vertices():
            reader = GeomVertexReader(data, "vertex")
            result = []
            while not reader.isAtEnd():
                result.append(tuple(reader.getData3f()))
            return result

        self.weather.update(self.point(1200, y=-800))
        first = vertices()
        for offset in range(0, 1200, 80):
            self.weather.update(self.point(490, x=offset, y=-150))
        self.assertNotEqual(first, vertices())
        self.assertEqual(centers, tuple(puff.center for puff in self.weather.puffs))
        self.assertEqual(count, self.weather.root.findAllMatches("**").getNumPaths())
        self.assertEqual(data.getNumRows(), 84)
        self.weather.update(self.point(490, x=1120, y=-150))
        frozen = bytes(data.getArray(0).getHandle().getData())
        self.weather.update(self.point(490, x=1120, y=-150))
        self.assertEqual(frozen, bytes(data.getArray(0).getHandle().getData()))

    def test_far_hemisphere_is_occluded_despite_software_depth_bias(self):
        observer = Vec3(0, 0, self.field.radius + 7000)
        front = self.weather.puffs[-7]
        back = min(self.weather.puffs, key=lambda puff: puff.center[2])
        self.assertEqual(_visibility(self.field.radius, observer, front), 1.)
        self.assertEqual(_visibility(self.field.radius, observer, back), 0.)
        self.weather.update(self.field.center + observer)
        hidden = sum(node.isHidden() for node, _ in self.weather.banks)
        self.assertGreater(hidden, 30)
        self.assertLess(hidden, 97)

    def test_mist_is_local_and_keeps_nearby_terrain_readable(self):
        data = self.field.atmosphere(self.point(490))
        tone = self.field.planet["sky"]
        clear_color, clear = fog_profile(data, tone, 0)
        cloud_color, cloud = fog_profile(data, tone, .8)
        self.assertGreater(cloud, clear)
        self.assertNotEqual(clear_color, cloud_color)
        # Altitude alone cannot fill a clear gap between banks with grey fog.
        self.assertEqual(fog_profile(dict(data, cloud=1), tone, 0),
                         fog_profile(dict(data, cloud=0), tone, 0))
        worst_calm = fog_profile(dict(density=1), tone, 1)[1]
        self.assertGreater(math.exp(-worst_calm * 650), .5)
        self.assertEqual(fog_profile(dict(density=0), tone, 1)[1], 0)

    def test_cleanup_is_idempotent_and_leaves_no_geometry_or_mist(self):
        self.weather.destroy()
        self.weather.destroy()
        self.weather.update(self.point(490))
        self.assertEqual(self.weather.local_density(self.point(490)), 0)
        self.assertTrue(self.weather.root.isEmpty())
        self.assertEqual(self.body.getNumChildren(), 0)
        self.assertFalse(self.weather._billows)


class SoftwareCloudRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loadPrcFileData("physical-cloud-software-test", "\n".join((
            "load-display p3tinydisplay", "aux-display p3tinydisplay",
            "window-type offscreen", "win-size 480 270", "audio-library-name null",
            "textures-power-2 up", "sync-video false", "notify-level error", "model-cache-dir")))
        from direct.showbase.ShowBase import ShowBase
        cls.app = ShowBase(windowType="offscreen")
        cls.app.disableMouse()
        cls.app.camLens.setNearFar(.12, 1000000)
        cls.app.camLens.setFov(78)

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def test_billows_render_above_and_below_without_a_spherical_shell(self):
        planet = get_planet(0, 0)
        planet["position"] = (0, 0, 0)
        field = PlanetField(planet)
        body = self.app.render.attachNewNode("cloud-render-reference")
        weather = PlanetWeather(field, body)
        self.addCleanup(body.removeNode)
        self.addCleanup(weather.destroy)
        backdrop = Mesh()
        backdrop.quad((-4000, -4000, field.radius + 20), (4000, -4000, field.radius + 20),
                      (4000, 4000, field.radius + 20), (-4000, 4000, field.radius + 20),
                      (.10, .23, .14))
        backdrop.node("physical-ground-reference", body, two_sided=True, unlit=True)
        self.app.setBackgroundColor(.10, .23, .36, 1)

        def render(hidden):
            weather.clouds.hide() if hidden else weather.clouds.show()
            self.app.graphicsEngine.renderFrame()
            self.app.graphicsEngine.renderFrame()
            frame = PNMImage()
            self.assertTrue(self.app.win.getScreenshot(frame))
            return frame

        for altitude, y, target in ((1050, -700, 450), (130, -450, 500)):
            position = Vec3(0, y, field.radius + altitude)
            self.app.camera.setPos(position)
            self.app.camera.lookAt(0, 200, field.radius + target)
            weather.update(position)
            clear, cloudy = render(True), render(False)
            changed = []
            for x in range(0, clear.getXSize(), 4):
                for y in range(0, clear.getYSize(), 4):
                    a, b = clear.getXel(x, y), cloudy.getXel(x, y)
                    changed.append(sum(abs(a[i] - b[i]) for i in range(3)))
            self.assertGreater(sum(changed) / len(changed), .025)
            self.assertGreater(sum(value < .01 for value in changed), len(changed) * .25)


if __name__ == "__main__":
    unittest.main()
