"""Quantum travel feedback remains bounded, readable, and camera independent."""
import math
from types import SimpleNamespace
import unittest

from panda3d.core import GeomVertexReader, NodePath, loadPrcFileData

from asterion.effects import PlayerEffects
from asterion.ui import GameUI


class QuantumEquipmentTests(unittest.TestCase):
    def setUp(self):
        self.camera = NodePath("quantum-test-camera")
        self.camera.setPosHpr(8, 13, 21, 40, -7, 3)
        self.effects = PlayerEffects(SimpleNamespace(camera=self.camera))

    def tearDown(self):
        self.effects.destroy()
        self.camera.removeNode()

    def test_charge_transit_cancel_and_surface_visibility(self):
        self.assertTrue(self.effects.quantum.isHidden())
        for phase in ("spooling", "ready", "transit"):
            self.effects.update(.1, "orbit", quantum_phase=phase, quantum_progress=.5)
            self.assertFalse(self.effects.quantum.isHidden())
            self.assertEqual(self.effects.quantum_charge.isHidden(), phase == "transit")
            self.assertEqual(self.effects.quantum_tunnel.isHidden(), phase != "transit")
        for phase, mode in (("cooldown", "orbit"), ("idle", "flight"),
                            ("transit", "surface"), ("ready", "hidden")):
            self.effects.update(.1, mode, quantum_phase=phase, quantum_progress=.5)
            self.assertTrue(self.effects.quantum.isHidden())

    def test_transit_has_forward_motion_but_never_shakes_the_camera(self):
        camera_transform = self.camera.getTransform()
        self.effects.update(.1, "orbit", camera_motion=0,
                            quantum_phase="transit", quantum_progress=.4)
        cockpit_transform = self.effects.cockpit.getTransform()
        streak = self.effects._quantum_streaks[0][0]
        before = streak.getY()
        for _ in range(30):
            self.effects.update(1 / 60, "orbit", thrust=1, boosting=True,
                                camera_motion=0, quantum_phase="transit", quantum_progress=.4)
            self.assertEqual(self.camera.getTransform(), camera_transform)
            self.assertEqual(self.effects.cockpit.getTransform(), cockpit_transform)
            self.assertEqual(self.effects._quantum_filaments.getR(), 0)
        self.assertNotEqual(streak.getY(), before)

    def test_pause_freezes_retained_geometry_and_cleanup_is_complete(self):
        self.effects.update(.1, "flight", quantum_phase="transit", quantum_progress=.3)
        nodes = tuple(self.effects.quantum.findAllMatches("**"))
        snapshots = [(node.getTransform(), node.getColorScale()) for node in nodes]
        for _ in range(10):
            self.effects.update(0, "flight", quantum_phase="transit", quantum_progress=.3)
        self.assertEqual(snapshots, [(node.getTransform(), node.getColorScale()) for node in nodes])
        count = self.effects.root.findAllMatches("**").getNumPaths()
        for index in range(120):
            phase = ("idle", "spooling", "ready", "transit", "cooldown")[index % 5]
            self.effects.update(.1, "flight", quantum_phase=phase, quantum_progress=.8)
        self.assertEqual(self.effects.root.findAllMatches("**").getNumPaths(), count)
        self.effects.destroy()
        self.effects.destroy()
        self.effects.update(.1, "flight", quantum_phase="transit")
        self.assertTrue(self.effects.root.isEmpty())
        self.assertEqual(self.camera.getNumChildren(), 0)

    def test_malformed_progress_cannot_create_invalid_geometry(self):
        for invalid in (float("nan"), float("inf"), -2, 9, None, "invalid"):
            for phase in ("spooling", "transit"):
                self.effects.update(invalid, "flight", camera_motion=invalid,
                                    quantum_phase=phase, quantum_progress=invalid)
                for path in self.effects.quantum.findAllMatches("**/+GeomNode"):
                    self.assertTrue(all(math.isfinite(v) for v in path.getPos()))
                    for geom in path.node().getGeoms():
                        reader = GeomVertexReader(geom.getVertexData(), "vertex")
                        while not reader.isAtEnd():
                            self.assertTrue(all(math.isfinite(v) for v in reader.getData3()))


class QuantumInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loadPrcFileData("quantum-presentation-tests", "\n".join((
            "load-display p3tinydisplay", "aux-display p3tinydisplay",
            "window-type offscreen", "win-size 1280 720", "audio-library-name null",
            "textures-power-2 up", "sync-video false", "notify-level error", "model-cache-dir",
        )))
        from direct.showbase.ShowBase import ShowBase
        cls.app = ShowBase(windowType="offscreen")

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.ui = GameUI(self.app, lambda *_: None)
        self.addCleanup(self.ui.destroy)

    def read(self, key):
        return self.ui._hud_labels[key].node.getText()

    def view(self, phase, **kwargs):
        quantum = dict(phase=phase, target="Velorum Prime", progress=.4, spool_progress=.75,
                       remaining=253000, eta=41.2, cost=8.6, cooldown=2.7)
        quantum.update(kwargs)
        return dict(mode="orbit", quantum=quantum, speed=285000,
                    location="Deep space", flight_assist=True)

    def test_each_phase_gives_action_progress_and_destination(self):
        self.ui.update(self.view("spooling"))
        self.assertEqual(self.read("movement_status"), "QUANTUM SPOOLING")
        self.assertIn("75%", self.read("quantum_detail"))
        self.assertIn("Cancel", self.read("prompt"))
        self.assertEqual(self.read("target"), "Velorum Prime")
        self.assertAlmostEqual(self.ui._quantum_bar._value, .75)
        self.ui.update(self.view("ready"))
        self.assertIn("Q ENGAGE", self.read("movement_detail"))
        self.assertIn("8.6%", self.read("quantum_detail"))
        self.assertEqual(self.ui._quantum_bar._value, 1)
        self.ui.update(self.view("transit"))
        self.assertIn("40%", self.read("quantum_detail"))
        self.assertIn("253 KM", self.read("quantum_detail"))
        self.assertIn("ETA 0:42", self.read("quantum_detail"))
        self.assertIn("Q/E ABORT", self.read("movement_detail"))
        self.assertIn("Q / E Abort", self.read("controls"))
        self.assertAlmostEqual(self.ui._quantum_bar._value, .4)
        self.ui.update(self.view("cooldown"))
        self.assertIn("2.7S", self.read("quantum_detail"))
        self.assertTrue(self.ui._quantum_meter.isHidden())

    def test_idle_and_surface_restore_normal_hud(self):
        self.ui.update(self.view("idle"))
        self.assertIn("Q Quantum", self.read("controls"))
        self.assertIn("Spool quantum", self.read("prompt"))
        self.assertTrue(self.ui._quantum_group.isHidden())
        self.ui.update(self.view("transit"))
        self.ui.update(dict(mode="surface", quantum=self.view("transit")["quantum"]))
        self.assertEqual(self.read("movement_status"), "ON FOOT")
        self.assertTrue(self.ui._quantum_group.isHidden())
        self.assertEqual(self.read("quantum_detail"), "")
        self.assertNotIn("Quantum", self.read("controls"))
        self.ui.update(dict(mode="orbit"))
        self.assertEqual(self.read("movement_status"), "STATION KEEPING")
        self.assertTrue(self.ui._quantum_group.isHidden())

    def test_quantum_text_fits_normal_and_narrow_views(self):
        for aspect in (16 / 9, 4 / 3):
            self.ui._aspect = lambda: aspect
            self.ui._layout(force=True)
            for phase in ("idle", "spooling", "ready", "transit", "cooldown"):
                self.ui.update(self.view(phase, target="Ancient outer-system navigation destination " * 4))
                for key, label in self.ui._hud_labels.items():
                    if not label.node.getText() or label.path.isHidden():
                        continue
                    low, high = label.path.getTightBounds(self.ui.root)
                    with self.subTest(aspect=aspect, phase=phase, label=key):
                        self.assertGreaterEqual(low.x, 0)
                        self.assertLessEqual(high.x, self.ui.width)
                        self.assertGreaterEqual(low.z, -self.ui.height)
                        self.assertLessEqual(high.z, 0)
                        if key.startswith("movement_"):
                            self.assertGreaterEqual(low.x, self.ui.width / 2 - 159)
                            self.assertLessEqual(high.x, self.ui.width / 2 + 159)

    def test_missing_and_invalid_quantum_values_are_finite(self):
        for quantum in (None, [], "invalid", {"phase": "unknown"},
                        dict(phase="transit", progress=float("nan"), eta=float("inf"),
                             remaining=None, cost="invalid")):
            self.ui.update(dict(mode="orbit", quantum=quantum))
            self.assertTrue(math.isfinite(self.ui._movement_bar._value))
            for label in self.ui._hud_labels.values():
                self.assertNotIn("nan", label.node.getText())
                self.assertNotIn("inf", label.node.getText())


if __name__ == "__main__":
    unittest.main()
