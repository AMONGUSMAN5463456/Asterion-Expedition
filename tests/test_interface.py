"""Equipment ownership and native HUD feedback without a desktop window."""

import copy
import math
from types import SimpleNamespace
import unittest

from direct.gui.DirectGui import DirectButton
from panda3d.core import GeomVertexReader, NodePath, Point2, loadPrcFileData

from asterion.effects import PlayerEffects
from asterion.ui import GameUI


class EquipmentTests(unittest.TestCase):
    def setUp(self):
        self.camera = NodePath("test-camera")
        self.camera.setPosHpr(5, 7, 11, 15, -12, 3)
        self.effects = PlayerEffects(SimpleNamespace(camera=self.camera))

    def tearDown(self):
        self.effects.destroy()
        self.camera.removeNode()

    def test_mode_visibility_and_idempotent_cleanup(self):
        self.assertTrue(self.effects.tool.isHidden())
        self.assertTrue(self.effects.cockpit.isHidden())
        for mode in ("surface", "flight", "orbit", "menu"):
            self.effects.update(.1, mode, True, .8, True)
            self.assertEqual(self.effects.tool.isHidden(), mode != "surface")
            self.assertEqual(self.effects.cockpit.isHidden(), mode not in ("flight", "orbit"))
        self.effects.destroy()
        self.effects.destroy()
        self.effects.update(.1, "surface")
        self.assertTrue(self.effects.root.isEmpty())
        self.assertEqual(self.camera.getNumChildren(), 0)

    def test_motion_off_stabilizes_equipment_without_touching_camera(self):
        camera_transform = self.camera.getTransform()
        for mode, node in (("surface", self.effects.tool), ("orbit", self.effects.cockpit)):
            self.effects.update(.1, mode, thrust=1, camera_motion=0)
            stationary = node.getTransform()
            for index in range(30):
                self.effects.update(1 / 60, mode, thrust=index % 2,
                                    camera_motion=0, boosting=True,
                                    braking=True, collision_feedback=.8)
                self.assertEqual(node.getTransform(), stationary)
            self.effects.update(.1, mode, thrust=1, camera_motion=1)
            self.assertNotEqual(node.getTransform(), stationary)
        self.assertEqual(self.camera.getTransform(), camera_transform)

    def test_indicators_acknowledge_braking_and_contact_without_shake(self):
        self.effects.update(.25, "flight", thrust=1, camera_motion=0)
        normal = self.effects.indicators.getColorScale()
        self.effects.update(.1, "flight", thrust=1, camera_motion=0, braking=True)
        braking = self.effects.indicators.getColorScale()
        self.assertNotEqual(normal, braking)
        self.effects.update(.1, "flight", camera_motion=0, collision_feedback=1)
        self.assertEqual(self.effects.indicators.getColorScale(), braking)
        self.assertEqual(self.effects.cockpit.getPos().lengthSquared(), 0)
        for _ in range(12):
            self.effects.update(.1, "flight", camera_motion=0)
        self.assertEqual(self.effects.indicators.getColorScale(), normal)
        for value in (float("nan"), float("inf"), None, "invalid"):
            self.effects.update(value, "surface", thrust=value, camera_motion=value,
                                collision_feedback=value)
            self.assertTrue(all(math.isfinite(v) for v in self.effects.tool.getPos()))


class InterfaceModelTests(unittest.TestCase):
    def test_minimal_application_keeps_public_model_and_lifecycle(self):
        calls = []
        ui = GameUI(SimpleNamespace(), lambda *args: calls.append(args))
        self.addCleanup(ui.destroy)
        ui.update({"mode": "surface"})
        ui.show_title(False)
        self.assertTrue(ui.panel_open)
        model = {"title": "Settings", "rows": [{"title": "Camera motion", "meta": "Off"}]}
        ui.show_panel(model)
        model["rows"][0]["meta"] = "Changed outside UI"
        self.assertEqual(ui._panel["rows"][0]["meta"], "Off")
        ui.set_visible(False)
        ui.hide_panel()
        self.assertFalse(ui.panel_open)
        ui.destroy()
        ui.destroy()
        ui._emit_button("setting", {"key": "camera_motion", "value": 0})
        self.assertEqual(calls, [])


class NativeInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loadPrcFileData("asterion-interface-tests", "\n".join((
            "load-display p3tinydisplay", "aux-display p3tinydisplay",
            "window-type offscreen", "win-size 1280 720", "audio-library-name null",
            "textures-power-2 up", "sync-video false", "notify-level error", "model-cache-dir",
        )))
        from direct.showbase.ShowBase import ShowBase
        cls.app = ShowBase(windowType="offscreen")
        cls.app.camLens.setFov(78)
        cls.app.camLens.setNearFar(.12, 10000)

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def setUp(self):
        self.calls = []
        self.ui = GameUI(self.app, lambda *args: self.calls.append(args))
        self.addCleanup(self.ui.destroy)

    def read(self, key):
        return self.ui._hud_labels[key].node.getText()

    def test_flat_movement_feedback_and_assist_setting(self):
        self.ui.update({"mode": "surface"})
        self.assertEqual(self.read("movement_status"), "ON FOOT")
        self.ui.update({"mode": "surface", "grounded": False, "vertical_speed": -6})
        self.assertIn("DESCENDING", self.read("movement_detail"))
        self.ui.update({"mode": "surface", "grounded": False, "jetpacking": True,
                        "braking": True, "vitals": {"energy": 25}})
        self.assertEqual(self.read("movement_status"), "AIR BRAKE ACTIVE")
        self.assertIn("CTRL", self.read("movement_detail"))
        self.assertAlmostEqual(self.ui._movement_bar._value, .25)
        view = {"mode": "orbit", "speed": 220, "flight_assist": False,
                "drifting": True, "throttle": 0, "vertical_speed": -14.5, "pitch": 30}
        self.ui.update(view)
        self.assertEqual(self.read("movement_status"), "INERTIAL DRIFT")
        self.assertIn("ASSIST OFF", self.read("movement_detail"))
        self.assertIn("-14.5", self.read("vertical_speed"))
        self.assertIn("+30", self.read("flight_pitch"))
        view["throttle"] = .65
        self.ui.update(view)
        self.assertEqual(self.read("movement_status"), "ENGINE THRUST")
        view["boosting"] = True
        self.ui.update(view)
        self.assertEqual(self.read("movement_status"), "BOOST ACTIVE")
        view["braking"] = True
        self.ui.update(view)
        self.assertEqual(self.read("movement_status"), "BRAKING")
        self.ui.update({"mode": "orbit", "flight_assist": True})
        self.assertIn("ASSIST ON", self.read("movement_detail"))
        self.assertIn("Brake/reverse", self.read("controls"))

    def test_context_visibility_and_modal_lifecycle(self):
        self.ui.update({"mode": "flight", "collision_feedback": 1, "mining_progress": .5,
                        "notice": "Discovery recorded"})
        for node in (self.ui._cockpit, self.ui._contact_group,
                     self.ui._mining_group, self.ui._notice_group):
            self.assertFalse(node.isHidden())
        self.ui.update({"mode": "surface"})
        for node in (self.ui._cockpit, self.ui._contact_group,
                     self.ui._mining_group, self.ui._notice_group):
            self.assertTrue(node.isHidden())
        self.ui.show_panel({"title": "Settings"})
        self.assertTrue(self.ui.hud.isHidden())
        self.assertFalse(self.ui.menu.isHidden())
        self.ui.set_visible(False)
        self.assertTrue(self.ui.menu.isHidden())
        self.ui.hide_panel()
        self.ui.set_visible(True)
        self.assertFalse(self.ui.hud.isHidden())
        root = self.ui.root
        self.ui.destroy()
        self.ui.update({"mode": "orbit"})
        self.assertTrue(root.isEmpty())
        self.assertFalse(self.ui.panel_open)

    def test_settings_callback_refresh_and_scroll_position(self):
        payload = {"key": "camera_motion", "value": 0}
        panel = {"title": "Settings", "id": "settings", "rows": [
            {"title": "Camera motion", "body": "Adjust view motion and equipment sway.",
             "meta": "35%", "buttons": [{"label": "OFF", "action": "setting", "payload": payload}]}
            for _ in range(9)]}
        self.ui.show_panel(panel)
        widget = next(widget for widget in self.ui._menu_widgets
                      if isinstance(widget, DirectButton) and widget["extraArgs"][0] == "setting")
        widget["command"](*widget["extraArgs"])
        self.assertEqual(self.calls, [("setting", payload)])
        self.ui._scroll.verticalScroll["value"] = .4
        refreshed = copy.deepcopy(panel)
        refreshed["rows"][0]["meta"] = "Off"
        self.ui.show_panel(refreshed)
        self.assertTrue(widget.isEmpty())
        self.assertAlmostEqual(self.ui._scroll.verticalScroll["value"], .4, places=4)
        texts = [path.node().getText() for path in self.ui.menu.findAllMatches("**/+TextNode")]
        self.assertIn("Off", texts)

    def test_720p_hud_text_stays_inside_view_and_status_strip(self):
        self.assertEqual((self.app.win.getXSize(), self.app.win.getYSize()), (1280, 720))
        for mode in ("surface", "flight", "orbit"):
            self.ui.update({"mode": mode, "location": "Velorum Prime",
                            "biome": "INTERPLANETARY SPACE", "speed": 1040, "altitude": -5723,
                            "credits": 234890, "coordinates": (12345, -89876, 301),
                            "grounded": False, "jetpacking": mode == "surface", "braking": True,
                            "target": "Ancient navigation terminal", "prompt": "E / Activate terminal",
                            "collision_feedback": .6, "notice": "Discovery added to your field journal.",
                            "objective": {"title": "Follow the ancient signal",
                                          "description": "Find a ruin and activate its terminal to decode the next star chart.",
                                          "progress": "1 / 3 discoveries"}})
            for key, label in self.ui._hud_labels.items():
                if not label.node.getText() or label.path.isHidden():
                    continue
                low, high = label.path.getTightBounds(self.ui.root)
                with self.subTest(mode=mode, label=key):
                    self.assertGreaterEqual(low.x, 0)
                    self.assertLessEqual(high.x, self.ui.width)
                    self.assertGreaterEqual(low.z, -self.ui.height)
                    self.assertLessEqual(high.z, 0)
                    if key.startswith("movement_"):
                        self.assertGreaterEqual(low.x, self.ui.width / 2 - 159)
                        self.assertLessEqual(high.x, self.ui.width / 2 + 159)
                        self.assertGreaterEqual(low.z, -847)
                        self.assertLessEqual(high.z, -789)

    def test_survey_tool_leaves_aim_region_clear_at_supported_fov(self):
        effects = PlayerEffects(self.app)
        self.addCleanup(effects.destroy)
        effects.update(.1, "surface", camera_motion=0)
        lens = self.app.camLens.makeCopy()
        for fov in (65, 78, 100):
            lens.setFov(fov)
            projected = []
            for path in effects.tool.findAllMatches("**/+GeomNode"):
                if path.isHidden():
                    continue
                for geom in path.node().getGeoms():
                    reader = GeomVertexReader(geom.getVertexData(), "vertex")
                    while not reader.isAtEnd():
                        point = self.app.camera.getRelativePoint(path, reader.getData3())
                        screen = Point2()
                        lens.project(point, screen)
                        projected.append((screen.x, screen.y))
            self.assertGreater(min(x for x, y in projected), .15)
            self.assertLess(max(y for x, y in projected), -.25)
            visible_left = max(-1, min(x for x, y in projected))
            visible_right = min(1, max(x for x, y in projected))
            visible_bottom = max(-1, min(y for x, y in projected))
            visible_top = min(1, max(y for x, y in projected))
            self.assertLess((visible_right - visible_left) * (visible_top - visible_bottom) / 4, .13)


if __name__ == "__main__":
    unittest.main()
