"""Exercise the real native app, renderer, menu callbacks, and local saves.

These tests use Panda3D's bundled software renderer and temporary save folders.
They neither open a desktop window nor need an audio device or network access.
Run after installing requirements: python -m unittest discover -s tests -v
"""

import copy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from direct.gui import DirectGuiGlobals as DGG
from direct.gui.DirectGui import DirectButton
from panda3d.core import Vec3, loadPrcFileData

loadPrcFileData("asterion-integration-tests", "\n".join((
    "load-display p3tinydisplay", "aux-display p3tinydisplay",
    "window-type offscreen", "win-size 640 360", "audio-library-name null",
    "sync-video false", "notify-level error", "model-cache-dir",
)))

from asterion.app import ExpeditionApp, distance
from asterion.content import BUILDINGS, RECIPES
from asterion.state import GameState


class NativeIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="asterion-integration-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.app = ExpeditionApp(save_dir=Path(cls.directory.name), offscreen=True, no_audio=True)
        cls.initial_title = (cls.app.current_panel, cls.app.started, cls.app.ui.panel_open)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.cleanup()
            cls.app.cleanup()  # Window-close and CLI-finally may both clean up.
        finally:
            cls.directory.cleanup()

    def setUp(self):
        self.app = type(self).app
        self.app.transition = None
        self.app.autopilot = None
        self.app.fade.hide()
        self.app.game.settings["quality"] = "low"
        self.app.new_game()
        for path in self.app.save_dir.iterdir():
            if path.is_file():
                path.unlink()
        self.app.step(0)

    def panel(self, kind, context=None):
        """Capture the model while allowing actual DirectGUI creation to run."""
        with patch.object(self.app.ui, "show_panel", wraps=self.app.ui.show_panel) as show:
            self.app.open_panel(kind, context)
        self.assertTrue(self.app.ui.panel_open)
        self.assertEqual(self.app.current_panel, kind)
        return show.call_args.args[0]

    def buttons(self, panel, action=None):
        buttons = list(panel.get("buttons", []))
        for row in panel.get("rows", []):
            buttons.extend(row.get("buttons", []))
        return [button for button in buttons if action is None or button["action"] == action]

    def press(self, button):
        self.assertTrue(button.get("enabled", True), button)
        widget = self.widget_for(button)
        self.assertEqual(widget["state"], DGG.NORMAL)
        widget["command"](*widget["extraArgs"])

    def widget_for(self, button):
        """Find the actual DirectButton represented by this menu model."""
        for widget in self.app.ui._menu_widgets:
            if not isinstance(widget, DirectButton):
                continue
            arguments = widget["extraArgs"]
            if arguments[:2] == [button["action"], button.get("payload")]:
                return widget
        self.fail("No rendered callback button for " + repr(button))

    def advance_until(self, predicate, frames=1200):
        for _ in range(frames):
            if predicate():
                return
            self.app.step(.1)
        self.fail("Gameplay did not reach the expected state within 120 simulated seconds")

    def assert_position_safe(self):
        position = self.app.controller.position
        self.assertTrue(all(math.isfinite(value) for value in position))
        if self.app.controller.mode == "surface":
            self.assertGreaterEqual(position.z, self.app.world.height(position.x, position.y) + 1.79)

    def aim_at(self, entity):
        """Aim a real camera at a real rendered target, without faking a hit."""
        center = Vec3(*entity["pos"])
        radius = float(entity.get("radius", 2))
        orbital = self.app.controller.mode == "orbit"
        if not orbital:
            center.z += min(radius * .6, 4)
        reach = max(10, radius * 2 + 3)
        for dx, dy in ((0, -reach), (reach, 0), (-reach, 0), (0, reach)):
            eye = center + Vec3(dx, dy, 1)
            if not orbital:
                eye.z = max(eye.z, self.app.world.height(eye.x, eye.y) + 1.8)
            delta = center - eye
            heading = math.degrees(math.atan2(-delta.x, delta.y))
            pitch = math.degrees(math.asin(delta.z / delta.length()))
            self.app.controller.set_mode(self.app.controller.mode, eye, heading, pitch)
            self.app._find_target()
            if self.app.target and self.app.target["id"] == entity["id"]:
                self.app.controller.set_enabled(True)
                return
        self.fail("Could not acquire real target " + entity["id"])

    def resource(self, orbital=False):
        kinds = ("asteroid",) if orbital else ("mineral", "flora")
        resources = [entity for entity in self.app.world.interactables() if entity["kind"] in kinds]
        self.assertTrue(resources)
        return min(resources, key=lambda entity: distance(entity["pos"], self.app.controller.position))

    def extract(self, entity):
        self.aim_at(entity)
        self.app.controller.mouse_down = True
        self.app._mine(float(entity.get("hardness", 1)) + 2)
        self.app.controller.mouse_down = False

    def test_all_main_panels_render_and_pause_movement_and_survival(self):
        for kind in ("inventory", "craft", "upgrades", "map", "galaxy", "journal",
                     "discoveries", "contracts", "build", "help", "settings", "pause"):
            with self.subTest(panel=kind):
                self.app.controller.keys["w"] = True
                self.app.controller.mouse_down = True
                self.panel(kind)
                self.assertFalse(any(self.app.controller.keys.values()))
                self.assertFalse(self.app.controller.mouse_down)
                position = Vec3(self.app.controller.position)
                elapsed = self.app.game.elapsed
                vitals = dict(self.app.game.vitals)
                for _ in range(4):
                    self.app.step(.1)
                self.assertEqual(self.app.controller.position, position)
                self.assertEqual(self.app.game.elapsed, elapsed)
                self.assertEqual(self.app.game.vitals, vitals)
                self.app.graphicsEngine.renderFrame()
                self.app.action("close")
                self.assertTrue(self.app.playing)

    def test_title_settings_and_new_game_confirmation_preserve_user_choices(self):
        self.assertEqual(type(self).initial_title, ("title", False, True))
        self.app.started = False
        self.app.ui.show_title(False)
        self.app.current_panel = "title"
        self.app.action("help")
        self.assertEqual(self.app.current_panel, "help")
        self.app.action("close")
        self.assertEqual(self.app.current_panel, "title")
        self.app.action("settings")
        self.app.action("setting", {"key": "sensitivity", "value": .28})
        self.app.action("setting", {"key": "invert_y", "value": True})
        self.app.action("setting", {"key": "volume", "value": .2})
        self.app.action("close")
        self.app.action("new_game")
        self.assertTrue(self.app.playing)
        self.assertEqual(self.app.game.settings["sensitivity"], .28)
        self.assertTrue(self.app.game.settings["invert_y"])
        self.assertEqual(self.app.game.settings["volume"], .2)
        self.app.game.credits = 4321
        self.assertTrue(self.app.save_game(False))
        self.app.action("new_game")
        self.assertEqual(self.app.current_panel, "new_confirm")
        self.app.action("close")
        self.assertEqual(self.app.game.credits, 4321)

    def test_scan_and_mine_share_target_and_persist_one_time_discovery(self):
        entity = self.resource()
        self.aim_at(entity)
        credits = self.app.game.credits
        self.app.scan()
        self.assertIn(entity["id"], self.app.game.discoveries)
        self.assertGreater(self.app.game.credits, credits)
        after_discovery = self.app.game.credits
        self.app.scan()
        self.assertEqual(self.app.game.credits, after_discovery)
        item = entity["resource"]
        amount = entity["amount"]
        initial = self.app.game.inventory.get(item, 0)
        self.extract(entity)
        self.assertEqual(self.app.game.inventory.get(item, 0), initial + amount)
        self.assertIn(entity["id"], self.app.game.depleted[self.app.planet["id"]])
        self.assertNotIn(entity["id"], {e["id"] for e in self.app.world.interactables()})
        self.assertTrue(entity["node"].isEmpty())
        self.assertTrue(self.app.save_game(False))
        self.app.continue_game()
        self.assertIn(entity["id"], self.app.game.discoveries)
        self.assertNotIn(entity["id"], {e["id"] for e in self.app.world.interactables()})
        self.assertEqual(self.app.game.inventory.get(item, 0), initial + amount)

    def test_full_cargo_mining_preserves_rendered_deposit_until_space_is_available(self):
        entity = self.resource()
        self.app.game.inventory = {"ferrite": self.app.game.capacity()}
        before = dict(self.app.game.inventory)
        self.extract(entity)
        self.assertEqual(self.app.game.inventory, before)
        self.assertNotIn(entity["id"], self.app.game.depleted.get(self.app.planet["id"], []))
        self.assertIn(entity["id"], {e["id"] for e in self.app.world.interactables()})
        self.assertFalse(entity["node"].isEmpty())
        self.app.game.remove_item("ferrite", entity["amount"])
        self.extract(entity)
        self.assertEqual(self.app.game.cargo_used(), self.app.game.capacity())
        self.assertNotIn(entity["id"], {e["id"] for e in self.app.world.interactables()})

    def test_full_cargo_ruin_can_be_retried_without_losing_its_unique_fragment(self):
        ruin = next(entity for entity in self.app.world.interactables() if entity["kind"] == "ruin")
        x, y, _ = ruin["pos"]
        self.app.controller.set_mode("surface", (x, y - 5, self.app.world.height(x, y - 5) + 1.8))
        self.app.game.inventory = {"ferrite": self.app.game.capacity()}
        archive_id = "signal:" + ruin["id"]
        credits = self.app.game.credits
        self.app.interact()
        self.assertNotIn(archive_id, self.app.game.discoveries)
        self.assertEqual(self.app.game.stats["ruins"], 0)
        self.assertEqual(self.app.game.credits, credits)
        self.assertFalse(self.app.ui.panel_open)
        self.app.game.remove_item("ferrite", 1)
        self.app.interact()
        self.assertIn(archive_id, self.app.game.discoveries)
        self.assertEqual(self.app.game.inventory["relic"], 1)
        self.assertEqual(self.app.game.stats["ruins"], 1)
        self.assertEqual(self.app.current_panel, "signal")
        credits = self.app.game.credits
        self.app.action("close")
        self.app.interact()
        self.assertEqual(self.app.game.inventory["relic"], 1)
        self.assertEqual(self.app.game.stats["ruins"], 1)
        self.assertEqual(self.app.game.credits, credits)

    def test_fabricator_callbacks_charge_exact_ingredients_and_refresh_affordability(self):
        model = self.panel("craft")
        recipe = RECIPES["fuel_cell"]
        before = dict(self.app.game.inventory)
        button = next(button for button in self.buttons(model, "craft") if button["payload"] == "fuel_cell")
        self.press(button)
        expected = dict(before)
        for item, amount in recipe["inputs"].items():
            expected[item] -= amount
            if not expected[item]:
                del expected[item]
        for item, amount in recipe["outputs"].items():
            expected[item] = expected.get(item, 0) + amount
        self.assertEqual(self.app.game.inventory, expected)
        self.assertEqual(self.app.current_panel, "craft")
        self.app.game.inventory.clear()
        model = self.panel("craft")
        self.assertFalse(any(button.get("enabled", True) for button in self.buttons(model, "craft")))
        self.app.action("craft", "fuel_cell")
        self.assertEqual(self.app.game.inventory, {})
        self.assertIn("Missing", self.app.notice)

    def test_market_actions_enforce_prices_capacity_and_menu_context(self):
        self.app.game.credits = 0
        model = self.panel("trade", {"name": "Test exchange"})
        self.assertFalse(any(button["enabled"] for button in self.buttons(model, "buy")))
        before = copy.deepcopy(self.app.game.to_dict())
        disabled = self.widget_for(self.buttons(model, "buy")[0])
        self.assertEqual(disabled["state"], DGG.DISABLED)
        disabled["command"](*disabled["extraArgs"])
        self.assertEqual(self.app.game.to_dict(), before)
        self.app.action("buy", {"item": "carbon", "amount": 1})
        self.assertEqual(self.app.game.to_dict(), before)
        self.app.game.credits = 10000
        owned = self.app.game.inventory["carbon"]
        cost = self.app.game.price("carbon", True) * 5
        self.app.action("buy", {"item": "carbon", "amount": 5})
        self.assertEqual(self.app.game.inventory["carbon"], owned + 5)
        self.assertEqual(self.app.game.credits, 10000 - cost)
        self.app.action("close")
        before = self.app.game.to_dict()
        self.app.action("buy", {"item": "carbon", "amount": 1})
        self.assertEqual(self.app.game.to_dict(), before)

    def test_construction_materials_and_world_nodes_survive_travel_and_reload(self):
        planet_id = self.app.planet["id"]
        before = dict(self.app.game.inventory)
        model = self.panel("build")
        self.press(next(button for button in self.buttons(model, "build") if button["payload"] == "beacon"))
        record = self.app.game.bases[planet_id][0]
        self.assertEqual(record["kind"], "beacon")
        for item, amount in BUILDINGS["beacon"]["cost"].items():
            self.assertEqual(self.app.game.inventory.get(item, 0), before[item] - amount)
        self.assertIn(record["id"], {e["id"] for e in self.app.world.interactables()})
        self.app.enter_orbit()
        self.assertNotIn(record["id"], {e["id"] for e in self.app.world.interactables()})
        self.app.land_on_planet(0)
        self.app.continue_game()
        self.assertEqual(len(self.app.game.bases[planet_id]), 1)
        self.assertIn(record["id"], {e["id"] for e in self.app.world.interactables()})
        self.assertEqual(self.app.game.bases[planet_id][0]["pos"], record["pos"])

    def test_constructed_habitat_actually_shelters_the_player(self):
        self.app.game.inventory = dict(BUILDINGS["habitat"]["cost"])
        self.app.controller.set_mode("surface", (200, 200, self.app.world.height(200, 200) + 1.8))
        self.app.action("build", "habitat")
        record = self.app.game.bases[self.app.planet["id"]][0]
        x, y, _ = record["pos"]
        self.app.controller.set_mode("surface", (x, y - 5, self.app.world.height(x, y - 5) + 1.8))
        self.assertGreater(distance(self.app.controller.position, self.app.game.ship_position), 30)
        self.app.game.vitals.update(oxygen=40, hazard=35)
        self.app.step(.1)
        self.assertGreater(self.app.game.vitals["oxygen"], 40)
        self.assertGreater(self.app.game.vitals["hazard"], 35)

    def test_surface_flight_save_restores_player_and_parked_ship_safely(self):
        self.app.recall_ship()
        fuel = self.app.game.vitals["fuel"]
        self.app.flight_action()
        self.assertEqual(self.app.controller.mode, "flight")
        self.assertIsNone(self.app.ship)
        self.assertLess(self.app.game.vitals["fuel"], fuel)
        self.app.controller.position = Vec3(154, -89, 300)
        self.assertTrue(self.app.save_game(False))
        self.app.continue_game()
        # Flight resumes airborne in place instead of collapsing to a foot
        # spawn beside the ship, so altitude survives the reload.
        self.assertEqual(self.app.controller.mode, "flight")
        self.assert_position_safe()
        saved = Vec3(self.app.controller.position)
        self.assertGreater(saved.z, 65)
        self.assertGreaterEqual(saved.z, self.app.world.height(saved.x, saved.y) + 4.0)
        # Manual landing still parks the ship beside the touchdown site.
        site = self.app._find_landing_site(saved.x, saved.y) or self.app._find_landing_site(0, 0)
        self.assertIsNotNone(site)
        self.app.controller.set_mode("flight", [site[0], site[1], self.app.world.height(*site) + 10],
                                      self.app.controller.heading, -10)
        self.app.controller.velocity = Vec3(0)
        self.app.controller.speed = 0.0
        self.app.flight_action()
        self.advance_until(lambda: self.app.transition is None)
        self.assertEqual(self.app.controller.mode, "surface")
        self.assertIsNotNone(self.app.ship)
        self.assertLess(distance(self.app.controller.position, self.app.game.ship_position), 22)
        self.app.flight_action()
        self.assertEqual(self.app.controller.mode, "flight")

    def test_altitude_transition_and_landing_replace_world_and_ship_exactly_once(self):
        self.app.recall_ship()
        self.app.launch()
        previous = self.app.world.root
        p = self.app.controller.position
        p.z = self.app.world.height(p.x, p.y) + 425
        self.app.step(.1)
        self.assertIsNotNone(self.app.transition)
        self.advance_until(lambda: self.app.transition is None)
        self.assertEqual(self.app.controller.mode, "orbit")
        self.assertTrue(previous.isEmpty())
        self.assertIsNone(self.app.ship)
        orbit_root = self.app.world.root
        self.app.flight_action()
        self.advance_until(lambda: self.app.transition is None)
        # Orbit F is atmospheric entry now: flight at altitude, then F lands.
        self.assertEqual(self.app.controller.mode, "flight")
        self.assertTrue(orbit_root.isEmpty())
        self.assertEqual(self.app.render.findAllMatches("**/asterion-surface").getNumPaths(), 1)
        self.assertEqual(self.app.render.findAllMatches("**/asterion-orbit").getNumPaths(), 0)
        entry = self.app.controller.position
        entry_alt = entry.z - self.app.world.height(entry.x, entry.y)
        self.assertGreaterEqual(entry_alt, 250)
        self.assertLessEqual(entry_alt, 350)
        site = self.app._find_landing_site(entry.x, entry.y) or self.app._find_landing_site(0, 0)
        self.assertIsNotNone(site)
        self.app.controller.set_mode("flight", [site[0], site[1], self.app.world.height(*site) + 10],
                                      self.app.controller.heading, -10)
        self.app.controller.velocity = Vec3(0)
        self.app.controller.speed = 0.0
        self.app.flight_action()
        self.advance_until(lambda: self.app.transition is None)
        self.assertEqual(self.app.controller.mode, "surface")
        self.assertIsNotNone(self.app.ship)
        self.assert_position_safe()

    def test_automatic_planet_route_reaches_landfall_and_saves_new_location(self):
        self.app.enter_orbit()
        model = self.panel("map")
        self.press(next(button for button in self.buttons(model, "travel") if button["payload"] == 1))
        self.assertIsNotNone(self.app.autopilot)
        self.assertTrue(self.app.playing)
        self.advance_until(lambda: self.app.controller.mode == "flight" and self.app.transition is None)
        self.assertEqual(self.app.game.planet_index, 1)
        self.assertIn("s0-p1", self.app.game.visited)
        self.assertIsNone(self.app.autopilot)
        self.assert_position_safe()
        restored = GameState.load(self.app.save_path)
        self.assertEqual(restored.planet_index, 1)
        self.assertEqual(restored.mode, "flight")
        # The pilot then lands manually via the existing flight F path.
        entry = self.app.controller.position
        site = self.app._find_landing_site(entry.x, entry.y) or self.app._find_landing_site(0, 0)
        self.assertIsNotNone(site)
        self.app.controller.set_mode("flight", [site[0], site[1], self.app.world.height(*site) + 10],
                                      self.app.controller.heading, -10)
        self.app.controller.velocity = Vec3(0)
        self.app.controller.speed = 0.0
        self.app.flight_action()
        self.advance_until(lambda: self.app.transition is None)
        self.assertEqual(self.app.controller.mode, "surface")
        self.assert_position_safe()

    def test_station_approach_pauses_cancels_and_opens_a_real_market(self):
        self.app.enter_orbit()
        self.app.action("approach_station")
        self.app.pause()
        position = Vec3(self.app.controller.position)
        for _ in range(10):
            self.app.step(.1)
        self.assertEqual(self.app.controller.position, position)
        self.app.action("close")
        self.app.interact()
        self.assertIsNone(self.app.autopilot)
        self.app.game.vitals["oxygen"] = 30
        self.app.action("approach_station")
        self.advance_until(lambda: self.app.current_panel == "trade")
        self.assertEqual(self.app.game.vitals["oxygen"], 100)
        self.assertLess(distance(self.app.controller.position, self.app.system["station"]), 320)
        self.assertFalse(self.app.playing)

    def test_warp_transition_debits_one_cell_and_persists_destination(self):
        self.app.enter_orbit()
        original_root = self.app.world.root
        cells = self.app.game.inventory["warp_cell"]
        model = self.panel("galaxy")
        self.press(next(button for button in self.buttons(model, "warp") if button["payload"] == 3))
        self.app.action("warp", 4)
        self.app.open_panel("inventory")
        self.assertFalse(self.app.ui.panel_open)
        self.advance_until(lambda: self.app.transition is None)
        self.assertEqual(self.app.game.system_id, 3)
        self.assertEqual(self.app.game.inventory["warp_cell"], cells - 1)
        self.assertEqual(self.app.game.stats["warped"], 1)
        self.assertTrue(original_root.isEmpty())
        self.assertEqual(self.app.render.findAllMatches("**/asterion-orbit").getNumPaths(), 1)
        self.app.continue_game()
        self.assertEqual(self.app.game.system_id, 3)
        self.assertEqual(self.app.controller.mode, "orbit")
        self.assertEqual(self.app.game.inventory["warp_cell"], cells - 1)
        self.assert_position_safe()

    def test_orbital_asteroid_depletion_survives_save_and_orbit_rebuild(self):
        self.app.enter_orbit()
        entity = self.resource(orbital=True)
        self.extract(entity)
        key = "orbit:0"
        self.assertIn(entity["id"], self.app.game.depleted[key])
        self.assertTrue(self.app.save_game(False))
        self.app.continue_game()
        self.assertIn(entity["id"], self.app.game.depleted.get(key, []))
        self.assertNotIn(entity["id"], {e["id"] for e in self.app.world.interactables()})

    def test_rescue_with_no_supplies_restores_safe_play_without_losing_discoveries(self):
        self.app.scan()
        discoveries = copy.deepcopy(self.app.game.discoveries)
        self.app.game.inventory.clear()
        self.app.game.credits = 0
        self.app.game.vitals.update({key: 0 for key in self.app.game.vitals})
        self.app.controller.position = Vec3(10000, -8000, 300)
        self.app.open_panel("rescue")
        self.app.action("rescue")
        self.assertTrue(self.app.playing)
        self.assert_position_safe()
        self.assertEqual(self.app.game.discoveries, discoveries)
        self.assertEqual(self.app.game.credits, 0)
        self.assertGreaterEqual(min(self.app.game.vitals.values()), 99)
        self.assertIsNotNone(self.app.ship)
        self.app.recall_ship()
        self.app.launch()
        self.assertEqual(self.app.controller.mode, "flight")

    def test_continue_recovers_backup_and_restores_settings_position_and_progress(self):
        self.app.game.credits = 5555
        self.app.game.settings.update(sensitivity=.24, invert_y=True)
        self.app.controller.set_mode("surface", (73, -39, 80), 126, -15)
        self.assertTrue(self.app.save_game(False))
        self.app.game.credits = 9999
        self.assertTrue(self.app.save_game(False))
        self.app.save_path.write_text("{broken save", encoding="utf-8")
        self.app.continue_game()
        self.assertEqual(self.app.game.credits, 5555)
        self.assertEqual(self.app.game.settings["sensitivity"], .24)
        self.assertTrue(self.app.game.settings["invert_y"])
        self.assertEqual(self.app.controller.heading, 126)
        self.assertEqual(self.app.controller.pitch, -15)
        self.assertIn("backup", self.app.notice.lower())
        self.assert_position_safe()
        self.assertTrue(self.app.save_game(False))
        self.assertEqual(json.loads(self.app.save_path.read_text(encoding="utf-8"))["credits"], 5555)

    def test_new_movement_preferences_round_trip_and_reject_invalid_values(self):
        choices = dict(fov=92, camera_motion=0, mouse_smoothing=.3,
                       flight_assist=False, mouse_capture=False)
        self.panel("settings")
        for key, value in choices.items():
            self.app.action("setting", {"key": key, "value": value})
        self.app.action("setting", {"key": "fov", "value": float("nan")})
        self.app.action("setting", {"key": "flight_assist", "value": "false"})
        self.app.action("close")
        self.assertTrue(self.app.save_game(False))
        self.app.continue_game()
        for key, value in choices.items():
            self.assertEqual(self.app.game.settings[key], value)
        legacy, _ = GameState._from_dict({"version": 1, "settings": {"sensitivity": .2}})
        self.assertEqual(legacy.settings["fov"], 78)
        self.assertTrue(legacy.settings["flight_assist"])

    def test_parked_ship_blocks_walking_and_launch_removes_its_collision(self):
        center = Vec3(*self.app.game.ship_position)
        start = center + Vec3(0, -10, 1.8)
        movement = Vec3(0, 20, 0)
        result = self.app.world.collisions.move_capsule(start, movement)
        self.assertTrue(any(name.startswith("player-ship:") for name in result.hit_ids))
        self.assertLess(result.position.y, center.y + 4)
        self.app.controller.set_mode("surface", self.app._safe_surface_position(start))
        self.app.launch()
        self.assertEqual(self.app.controller.mode, "flight")
        result = self.app.world.collisions.move_capsule(start, movement)
        self.assertFalse(any(name.startswith("player-ship:") for name in result.hit_ids))

    def test_loading_inside_new_solid_recovers_to_clear_walking_position(self):
        center = Vec3(*self.app.game.ship_position)
        self.app.controller.set_mode("surface", center + Vec3(0, 0, 1.8))
        self.assertTrue(self.app.save_game(False))
        self.app.continue_game()
        self.assertFalse(self.app.world.collisions.overlaps_capsule(self.app.controller.position))
        self.assert_position_safe()

    def test_solid_wall_occludes_mining_target_and_removal_restores_visibility(self):
        entity = self.resource()
        self.aim_at(entity)
        center = Vec3(*entity["pos"])
        center.z += min(entity.get("radius", 2) * .6, 4)
        midpoint = (self.app.controller.position + center) * .5
        self.app.world.collisions.set_group("test-occlusion", [
            {"id": "test-wall", "type": "box", "center": midpoint, "half": (2, 2, 4)}])
        self.app._find_target()
        self.assertTrue(self.app.target is None or self.app.target["id"] != entity["id"])
        self.app.world.collisions.remove_group("test-occlusion")
        self.app._find_target()
        self.assertEqual(self.app.target["id"], entity["id"])

    def test_obstructed_construction_does_not_charge_materials(self):
        direction = self.app.controller.forward()
        direction.z = 0
        direction.normalize()
        center = self.app.controller.position + direction * 12
        center.z = self.app.world.height(center.x, center.y) + 2
        self.app.world.collisions.set_group("test-construction", [
            {"id": "test-solid", "type": "box", "center": center, "half": (4, 4, 3)}])
        before = copy.deepcopy(self.app.game.to_dict())
        self.app.action("build", "beacon")
        self.assertEqual(self.app.game.inventory, before["inventory"])
        self.assertEqual(self.app.game.bases, before["bases"])
        self.assertIn("obstructed", self.app.notice)

    def test_broad_spawn_overlap_recovers_above_ground_without_floor_escape(self):
        self.app.world.collisions.set_group("test-spawn-block", [
            {"id": "large-room", "type": "box", "center": (0, 0, 5), "half": (50, 50, 5)}])
        with patch.object(self.app.world, "height", return_value=0):
            recovered = self.app._safe_surface_position((0, 0, 1.8))
        self.assertGreaterEqual(recovered.z, 1.8)
        self.assertFalse(self.app.world.collisions.overlaps_capsule(recovered))

    def test_failed_parking_keeps_landing_local_and_cannot_launch_absent_ship(self):
        self.app.game.ship_position = [-9000, -9000, 0]
        with patch.object(self.app, "_park_ship", return_value=False):
            self.app._load_surface(landing=(400, 500))
        self.assertLess(math.hypot(self.app.controller.position.x - 400,
                                   self.app.controller.position.y - 500), 35)
        self.assertIsNone(self.app.ship)
        fuel = self.app.game.vitals["fuel"]
        self.app.launch()
        self.assertEqual(self.app.controller.mode, "surface")
        self.assertEqual(self.app.game.vitals["fuel"], fuel)


if __name__ == "__main__":
    unittest.main()
