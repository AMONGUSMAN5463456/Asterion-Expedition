"""Physical effect envelopes, lifecycle, audio continuity and native visuals."""
import math
from types import SimpleNamespace
import unittest

from panda3d.core import NodePath, PNMImage, Vec3, loadPrcFileData

from asterion.atmosphere_effects import AtmosphereEffects, atmosphere_envelope
from asterion.audio import AudioManager
from asterion.planetary import PlanetField
from asterion.ui import GameUI
from asterion.universe import get_planet


ENTRY = dict(density=.55, space_blend=.25, cloud=0.0, heat=1.0,
             entry=1.0, exit=0.0, radial_speed=-1100, altitude=600)
EXIT = dict(density=.55, space_blend=.25, cloud=0.0, heat=0.0,
            entry=0.0, exit=1.0, radial_speed=900, altitude=600)
CLOUD = dict(density=.5, space_blend=.12, cloud=1.0, heat=0.0,
             entry=.04, exit=0.0, radial_speed=-80, altitude=490)
VACUUM = dict(density=0.0, space_blend=1.0, cloud=0.0, heat=0.0,
              entry=0.0, exit=0.0, radial_speed=0.0, altitude=3000)


class AtmosphereEnvelopeTests(unittest.TestCase):
    def test_physical_entry_exit_hover_and_vacuum_have_distinct_signatures(self):
        entry, leaving = atmosphere_envelope(ENTRY), atmosphere_envelope(EXIT)
        self.assertGreater(entry["plasma"], .9)
        self.assertEqual(entry["exit"], 0)
        self.assertEqual(leaving["plasma"], 0)
        self.assertGreater(leaving["exit"], .9)
        # Even stale field hints cannot turn outward motion into reentry fire.
        self.assertEqual(atmosphere_envelope(dict(ENTRY, radial_speed=300))["plasma"], 0)
        hover = atmosphere_envelope(dict(CLOUD, radial_speed=0, heat=1, entry=1, exit=1))
        self.assertEqual((hover["plasma"], hover["exit"]), (0, 0))
        self.assertGreater(hover["cloud"], .4)
        self.assertTrue(all(v == 0 for v in atmosphere_envelope(VACUUM).values()))
        self.assertTrue(all(v == 0 for v in atmosphere_envelope(ENTRY, "surface").values()))

    def test_complete_real_field_crossing_is_continuous_in_both_directions(self):
        field = PlanetField(get_planet(0, 0))
        normal = Vec3(0, 0, 1)
        ground = field.center + normal * (field.radius + field.elevation(normal))
        for speed, active, absent in ((-1200, "plasma", "exit"), (950, "exit", "plasma")):
            samples = [atmosphere_envelope(field.atmosphere(
                ground + normal * altitude, normal * speed))
                for altitude in range(0, 2401, 5)]
            self.assertGreater(max(item[active] for item in samples), .9)
            self.assertTrue(all(item[absent] == 0 for item in samples))
            self.assertTrue(all(value == 0 for value in samples[-1].values()))
            for a, b in zip(samples, samples[1:]):
                self.assertLess(max(abs(a[key] - b[key]) for key in a), .08)

    def test_invalid_hints_stay_finite_and_bounded(self):
        for bad in (None, "invalid", float("nan"), float("inf"), -10000000):
            data = dict.fromkeys(ENTRY, bad)
            for value in atmosphere_envelope(data).values():
                self.assertTrue(math.isfinite(value))
                self.assertGreaterEqual(value, 0)
                self.assertLessEqual(value, 1)


class AtmosphereLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.camera = NodePath("atmosphere-test-camera")
        self.camera.setPosHpr(100, -300, 27, 51, -42, 13)
        self.screen = NodePath("atmosphere-test-screen")
        self.effects = AtmosphereEffects(SimpleNamespace(camera=self.camera, render2d=self.screen))

    def tearDown(self):
        self.effects.destroy()
        self.camera.removeNode()
        self.screen.removeNode()

    def snapshot(self):
        return [(node.getTransform(), node.getColorScale(), node.isHidden())
                for node in self.effects.root.findAllMatches("**")]

    def test_pause_does_not_advance_envelope_animation_or_visibility(self):
        self.effects.update(.1, ENTRY)
        before, levels, clock = self.snapshot(), dict(self.effects._levels), self.effects._time
        for data, mode in ((EXIT, "orbit"), (VACUUM, "menu"), (CLOUD, "flight")):
            self.effects.update(0, data, mode)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.effects._levels, levels)
        self.assertEqual(self.effects._time, clock)

    def test_zero_motion_freezes_geometry_and_never_moves_camera(self):
        pose = self.camera.getTransform()
        self.effects.update(.1, ENTRY, camera_motion=0)
        nodes = list(self.effects.root.findAllMatches("**"))
        transforms = [node.getTransform() for node in nodes]
        for _ in range(30):
            self.effects.update(1 / 60, EXIT, camera_motion=0)
        self.assertEqual([node.getTransform() for node in nodes], transforms)
        self.assertEqual(self.effects._time, 0)
        self.effects.update(.1, EXIT, camera_motion=1)
        self.assertNotEqual([node.getTransform() for node in nodes], transforms)
        self.assertEqual(self.camera.getTransform(), pose)

    def test_repeated_crossings_have_fixed_node_count_and_cleanup_is_idempotent(self):
        count = self.effects.root.findAllMatches("**").getNumPaths()
        self.assertLess(count, 80)
        for index in range(300):
            self.effects.update(.1, (ENTRY, EXIT, CLOUD, VACUUM)[index % 4])
        self.assertEqual(self.effects.root.findAllMatches("**").getNumPaths(), count)
        for _ in range(20):
            self.effects.update(.25, VACUUM)
        self.assertTrue(self.effects.root.isHidden())
        self.effects.update(.1, ENTRY)
        self.assertFalse(self.effects.root.isHidden())
        self.effects.update(.1, ENTRY, "surface")
        self.assertTrue(self.effects.root.isHidden())
        self.effects.destroy()
        self.effects.destroy()
        self.effects.update(.1, ENTRY)
        self.assertTrue(self.effects.root.isEmpty())
        self.assertEqual(self.screen.getNumChildren(), 0)


class AtmosphereAudioTests(unittest.TestCase):
    def test_ambience_uses_air_data_continuously_across_mode_labels(self):
        audio = AudioManager(SimpleNamespace())
        self.addCleanup(audio.destroy)
        audio.set_atmosphere(ENTRY)
        self.assertEqual(audio._targets("flight", .5), audio._targets("orbit", .5))
        entry = audio._targets("flight", .5)
        audio.set_atmosphere(EXIT)
        leaving = audio._targets("flight", .5)
        self.assertGreater(entry["engine"], leaving["engine"])
        audio.set_atmosphere(VACUUM)
        space = audio._targets("flight", .5)
        self.assertEqual(space["surface"], 0)
        self.assertEqual(space["space"], .4)
        self.assertGreater(entry["surface"], space["surface"])
        audio.set_atmosphere(None)
        self.assertEqual(audio._targets("flight", .5)["surface"], .31)
        audio.destroy()
        audio.set_atmosphere(ENTRY)
        audio.update(.1, "flight", 1)


class NativeAtmosphereTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loadPrcFileData("asterion-atmosphere-tests", "\n".join((
            "load-display p3tinydisplay", "aux-display p3tinydisplay",
            "window-type offscreen", "win-size 640 360", "audio-library-name null",
            "textures-power-2 up", "sync-video false", "notify-level error", "model-cache-dir",
        )))
        from direct.showbase.ShowBase import ShowBase
        cls.app = ShowBase(windowType="offscreen")
        cls.app.disableMouse()
        cls.app.setBackgroundColor(.025, .04, .065, 1)

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def render_effect(self, data):
        effects = AtmosphereEffects(self.app)
        try:
            for _ in range(20):
                effects.update(.1, data)
            self.app.graphicsEngine.renderFrame()
            self.app.graphicsEngine.renderFrame()
            frame = PNMImage()
            self.assertTrue(self.app.win.getScreenshot(frame))
            return frame
        finally:
            effects.destroy()

    def test_native_entry_exit_and_clouds_are_visible_and_center_is_untouched(self):
        baseline, entry, leaving, cloud = [self.render_effect(data)
                                           for data in (VACUUM, ENTRY, EXIT, CLOUD)]
        width, height = baseline.getXSize(), baseline.getYSize()
        for frame in (entry, leaving, cloud):
            for y in range(int(height * .32), int(height * .68), 9):
                for x in range(int(width * .32), int(width * .68), 9):
                    self.assertEqual(frame.getXel(x, y), baseline.getXel(x, y))
            difference = []
            for y in range(0, height, 5):
                for x in range(0, width, 5):
                    if x < width * .18 or x > width * .82 or y < height * .18 or y > height * .82:
                        pixel, old = frame.getXel(x, y), baseline.getXel(x, y)
                        difference.append(sum(abs(pixel[i] - old[i]) for i in range(3)))
                        self.assertGreaterEqual(sum(pixel), sum(old) - .015)
            self.assertGreater(sum(difference) / len(difference), .02)
        corners = [(x, y) for x in (8, width - 9) for y in range(15, height - 15, 13)]
        warmth = lambda frame: sum(frame.getXel(x, y).x - frame.getXel(x, y).z for x, y in corners) / len(corners)
        self.assertGreater(warmth(entry), .08)
        self.assertLess(warmth(leaving), -.07)

    def test_hud_uses_radial_altitude_and_descent_without_a_portal_hint(self):
        ui = GameUI(self.app, lambda *args: None)
        self.addCleanup(ui.destroy)
        ui.update({"mode": "flight", "altitude": 999999, "planet_altitude": 743,
                   "density": .41, "entry_heat": .72, "radial_speed": -317.5,
                   "vertical_speed": 99, "flight_phase": "atmospheric entry"})
        read = lambda key: ui._hud_labels[key].node.getText()
        self.assertEqual(read("altitude"), "743")
        self.assertIn("-317.5", read("vertical_speed"))
        self.assertIn("DESCENDING", read("location_detail"))
        self.assertIn("41%", read("location_detail"))
        self.assertIn("ATMOSPHERIC ENTRY", read("biome"))
        self.assertNotIn("420", read("controls"))


if __name__ == "__main__":
    unittest.main()
