"""Real-app quantum journeys, controls, saves and safety dropouts.

Use Panda3D's software renderer and temporary saves. Routes cross the live
solar scene, including a solid inserted after engagement to test the swept
collision path independently of the route planner.
"""

import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from direct.gui import DirectGuiGlobals as DGG
from direct.gui.DirectGui import DirectButton
from direct.showbase.MessengerGlobal import messenger
from panda3d.core import Vec3, loadPrcFileData

loadPrcFileData("quantum-integration", "\n".join((
    "load-display p3tinydisplay", "aux-display p3tinydisplay",
    "window-type offscreen", "win-size 640 360", "audio-library-name null",
    "sync-video false", "notify-level error", "model-cache-dir",
)))

from asterion.app import ExpeditionApp


class QuantumIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saves = tempfile.TemporaryDirectory(prefix="asterion-quantum-")
        cls.app = ExpeditionApp(save_dir=Path(cls.saves.name), offscreen=True, no_audio=True)

    @classmethod
    def tearDownClass(cls):
        try:
            cls.app.cleanup()
        finally:
            cls.saves.cleanup()

    def setUp(self):
        a = self.app
        a.transition = a.autopilot = None
        a.fade.hide()
        a.game.settings.update(quality="low", camera_motion=0)
        a.new_game()
        for path in a.save_dir.iterdir():
            if path.is_file():
                path.unlink()

    def flying(self, altitude=4500):
        a = self.app
        field = a.world.fields[a.system["planets"][0]["id"]]
        normal = Vec3(0, 0, 1)
        point = field.center + normal * (field.radius + field.elevation(normal) + altitude)
        a.enter_orbit(point)
        a.controller.reset_keys()
        return field

    def advance_until(self, condition, frames=1200):
        a = self.app
        for _ in range(frames):
            if condition():
                return
            a.step(.1)
        self.fail(f"Quantum state did not settle: phase={a.quantum.phase}, "
                  f"position={a.world_position()}, notice={a.notice!r}")

    def ready(self, target=1):
        a = self.app
        self.assertTrue(a.select_quantum_target(target))
        self.assertTrue(a.quantum_action(), a.notice)
        self.assertEqual(a.quantum.phase, "spooling")
        self.advance_until(lambda: a.quantum.phase == "ready", frames=80)

    def engage(self, target=1):
        self.ready(target)
        self.assertTrue(self.app.quantum_action(), self.app.notice)
        self.assertTrue(self.app.quantum.travelling)

    def test_chart_callback_and_q_fly_to_safe_orbit_without_rebuilding_world(self):
        a = self.app
        self.flying()
        initial = Vec3(a.world_position())
        roots = a.world.root, dict(a.world.body_roots)
        fuel, fold_cells = a.game.vitals["fuel"], a.game.inventory["warp_cell"]
        with patch.object(a.ui, "show_panel", wraps=a.ui.show_panel) as show:
            messenger.send("q")
        self.assertEqual(a.current_panel, "map")
        panel = show.call_args.args[0]
        button = next(button for row in panel["rows"] for button in row.get("buttons", [])
                      if button["action"] == "quantum_target" and button["payload"] == 1)
        self.assertTrue(button.get("enabled", True))
        widget = next(widget for widget in a.ui._menu_widgets
                      if isinstance(widget, DirectButton)
                      and widget["extraArgs"][:2] == ["quantum_target", 1])
        self.assertEqual(widget["state"], DGG.NORMAL)
        widget["command"](*widget["extraArgs"])
        self.assertFalse(a.ui.panel_open)
        self.assertIsNone(a.autopilot)
        self.assertEqual(a.quantum.phase, "idle")
        self.assertEqual(a.world_position(), initial)

        messenger.send("q")
        self.advance_until(lambda: a.quantum.phase == "ready", frames=80)
        self.assertEqual(a.world_position(), initial)
        self.assertEqual(a.game.vitals["fuel"], fuel)
        self.assertGreater(a.camera.getQuat().getForward().dot(a.quantum.next_direction), .995)
        cost = a.quantum.cost
        messenger.send("q")
        self.assertTrue(a.quantum.travelling)
        self.assertAlmostEqual(a.game.vitals["fuel"], fuel - cost, places=6)
        maximum_speed = 0
        with patch.object(a, "transition_to", side_effect=AssertionError("quantum fade")), \
                patch.object(a, "_load_surface", side_effect=AssertionError("quantum scene reload")):
            for _ in range(1200):
                if not a.quantum.travelling:
                    break
                a.step(.1)
                maximum_speed = max(maximum_speed, a.controller.speed)
                self.assertEqual(a.world.root, roots[0])
                self.assertEqual(a.world.body_roots, roots[1])
                self.assertIsNone(a.transition)
                self.assertTrue(a.fade.isHidden())
                self.assertTrue(all(math.isfinite(value) for value in a.world_position()))
        self.assertEqual(a.quantum.phase, "cooldown", a.notice)
        self.assertGreater(maximum_speed, 5000)
        self.assertGreater((a.world_position() - initial).length(), 100000)
        self.assertEqual(a.game.planet_index, 1)
        field = a.world.fields[a.system["planets"][1]["id"]]
        self.assertAlmostEqual(field.altitude(a.world_position()), 3500, delta=.15)
        self.assertEqual(a.controller.mode, "orbit")
        self.assertIsNone(a.frame)
        self.assertIsNone(a.ship)
        self.assertEqual(a.controller.velocity, Vec3(0))
        self.assertEqual(a.controller.speed, 0)
        self.assertEqual(a.game.inventory["warp_cell"], fold_cells)
        self.assertAlmostEqual(a.game.vitals["fuel"], fuel - cost, places=6)
        self.assertTrue(a.save_path.exists(), "Quantum arrival should save the safe final position")

    def test_menus_pause_spooling_and_transit_then_resume_the_same_course(self):
        a = self.app
        self.flying()
        self.assertTrue(a.select_quantum_target(1))
        self.assertTrue(a.quantum_action())
        a.step(.1)
        for expected in ("spooling", "transit"):
            if expected == "transit":
                self.advance_until(lambda: a.quantum.phase == "ready", frames=80)
                self.assertTrue(a.quantum_action())
                a.step(.1)
            with self.subTest(phase=expected):
                snapshot = (Vec3(a.world_position()), a.quantum.progress,
                            a.quantum.spool_progress, a.game.elapsed, a.game.vitals["fuel"])
                a.open_panel("pause")
                for _ in range(45):
                    a.step(.1)
                self.assertEqual(a.quantum.phase, expected)
                self.assertEqual(snapshot, (Vec3(a.world_position()), a.quantum.progress,
                                           a.quantum.spool_progress, a.game.elapsed, a.game.vitals["fuel"]))
                a.close_panel()
                a.step(.1)
                self.assertGreater(a.game.elapsed, snapshot[3])
                if expected == "spooling":
                    self.assertGreater(a.quantum.spool_progress, snapshot[2])
                else:
                    self.assertGreater(a.quantum.progress, snapshot[1])

    def test_unexpected_solid_causes_safe_dropout_instead_of_tunnelling(self):
        a = self.app
        self.flying()
        self.engage()
        start = Vec3(a.world_position())
        direction = Vec3(a.quantum.next_direction)
        center = start + direction * 2000
        a.world._space_collisions.set_group("quantum-test-obstacle", [{
            "id": "late-route-obstruction", "type": "sphere",
            "center": tuple(center), "radius": 80,
        }])
        self.advance_until(lambda: not a.quantum.travelling, frames=100)
        self.assertEqual(a.quantum.phase, "cooldown")
        self.assertIn("obstruction", a.notice.lower())
        self.assertLess((a.world_position() - start).dot(direction), 1920)
        self.assertGreaterEqual((a.world_position() - center).length(), 82.9)
        self.assertEqual(a.controller.velocity, Vec3(0))
        self.assertEqual(a.controller.speed, 0)
        self.assertGreater(a.controller.collision_feedback, 0)

    def test_q_and_e_abort_without_teleporting_or_refunding_engagement(self):
        a = self.app
        for key in ("q", "e"):
            with self.subTest(key=key):
                self.flying()
                self.engage()
                for _ in range(10):
                    a.step(.1)
                before, fuel = Vec3(a.world_position()), a.game.vitals["fuel"]
                self.assertGreater(a.controller.speed, 0)
                messenger.send(key)
                self.assertEqual(a.quantum.phase, "cooldown")
                self.assertEqual(a.world_position(), before)
                self.assertEqual(a.controller.velocity, Vec3(0))
                self.assertEqual(a.game.vitals["fuel"], fuel)
                self.assertFalse(a.quantum_action(), "Cooldown must reject re-engagement")
                self.advance_until(lambda: a.quantum.phase == "idle", frames=60)
                self.assertTrue(a.quantum_action(), a.notice)
                a.interact()  # Cancel this spool before trying the other key.

    def test_surface_atmosphere_and_low_fuel_are_rejected_without_spending(self):
        a = self.app
        surface_position = Vec3(a.world_position())
        self.assertFalse(a.select_quantum_target(1))
        self.assertFalse(a.quantum_action())
        self.assertEqual(a.world_position(), surface_position)
        for altitude in (1000, 2990):
            with self.subTest(altitude=altitude):
                self.flying(altitude)
                self.assertTrue(a.select_quantum_target(1))
                position, fuel = Vec3(a.world_position()), a.game.vitals["fuel"]
                self.assertFalse(a.quantum_action())
                self.assertEqual(a.quantum.phase, "idle")
                self.assertEqual(a.world_position(), position)
                self.assertEqual(a.game.vitals["fuel"], fuel)
        self.flying()
        self.assertTrue(a.select_quantum_target(1))
        a.game.vitals["fuel"] = 0
        self.assertFalse(a.quantum_action())
        self.assertEqual(a.quantum.phase, "idle")
        a.game.vitals["fuel"] = 100
        self.ready()
        a.game.vitals["fuel"] = 0  # Recheck at engagement, after calibration.
        self.assertFalse(a.quantum_action())
        self.assertEqual(a.quantum.phase, "ready")
        self.assertEqual(a.game.vitals["fuel"], 0)

    def test_quantum_keys_work_while_flight_modifiers_are_held(self):
        a = self.app
        self.flying()
        self.assertTrue(a.select_quantum_target(1))
        for prefix in ("shift-", "control-", "shift-control-"):
            with self.subTest(modifiers=prefix):
                messenger.send(prefix + "q")
                self.assertEqual(a.quantum.phase, "spooling")
                a._update_quantum(3)
                self.assertEqual(a.quantum.phase, "ready")
                messenger.send(prefix + "q")
                self.assertEqual(a.quantum.phase, "transit")
                a.step(.1)
                position = Vec3(a.world_position())
                messenger.send(prefix + "e")
                self.assertEqual(a.quantum.phase, "cooldown")
                self.assertEqual(a.world_position(), position)
                self.assertEqual(a.controller.velocity, Vec3(0))
                a._update_quantum(4)
                self.assertEqual(a.quantum.phase, "idle")
                messenger.send(prefix + "q")
                self.assertEqual(a.quantum.phase, "spooling")
                messenger.send(prefix + "q")
                self.assertEqual(a.quantum.phase, "idle")

    def test_invalid_target_does_not_replace_the_selected_destination(self):
        a = self.app
        self.flying()
        self.assertTrue(a.select_quantum_target(1))
        expected = dict(a.quantum_target)
        for invalid in (-1, 4, True, 1.0, "1", None):
            with self.subTest(target=invalid):
                self.assertFalse(a.select_quantum_target(invalid))
                self.assertEqual(a.quantum_target, expected)
        self.assertTrue(a.quantum_action())
        self.assertFalse(a.select_quantum_target(2))
        self.assertEqual(a.quantum_target, expected)

    def test_active_drive_blocks_approach_and_fold_without_spending_cells(self):
        a = self.app
        self.flying()
        self.engage()
        a.open_panel("map")
        position, cells = Vec3(a.world_position()), a.game.inventory["warp_cell"]
        for action, payload in (("travel", 2), ("approach_station", None), ("warp", 1)):
            with self.subTest(action=action):
                a.action(action, payload)
                self.assertTrue(a.quantum.travelling)
                self.assertEqual(a.quantum_target["index"], 1)
                self.assertIsNone(a.autopilot)
                self.assertIsNone(a.transition)
                self.assertEqual(a.game.system_id, 0)
                self.assertEqual(a.game.inventory["warp_cell"], cells)
                self.assertEqual(a.world_position(), position)

    def test_transit_save_keeps_live_course_but_reload_stops_at_saved_position(self):
        a = self.app
        self.flying()
        self.engage()
        for _ in range(15):
            a.step(.1)
        position, velocity = Vec3(a.world_position()), Vec3(a.controller.velocity)
        fuel = a.game.vitals["fuel"]
        self.assertGreater(velocity.length(), 1000)
        self.assertTrue(a.save_game(False))
        self.assertTrue(a.quantum.travelling)
        self.assertEqual(a.controller.velocity, velocity)
        saved = json.loads(a.save_path.read_text())
        self.assertEqual(saved["velocity"], [0, 0, 0])
        self.assertLess((Vec3(*saved["position"]) - position).length(), .05)
        a.step(.1)
        self.assertGreater((a.world_position() - position).length(), 1)
        a.continue_game()
        self.assertEqual(a.quantum.phase, "idle")
        self.assertIsNone(a.quantum_target)
        self.assertIsNone(a.autopilot)
        self.assertEqual(a.controller.mode, "orbit")
        self.assertEqual(a.controller.velocity, Vec3(0))
        self.assertLess((a.world_position() - position).length(), .05)
        self.assertAlmostEqual(a.game.vitals["fuel"], fuel)
        a.step(.1)
        self.assertLess((a.world_position() - position).length(), .05)

    def test_new_game_and_rescue_clear_transient_quantum_state(self):
        a = self.app
        for reset in (a.new_game, a.rescue):
            with self.subTest(reset=reset.__name__):
                self.flying()
                self.engage()
                a.step(.1)
                reset()
                self.assertEqual(a.quantum.phase, "idle")
                self.assertIsNone(a.quantum_target)
                self.assertEqual(a.controller.mode, "surface")
                self.assertEqual(a.controller.velocity, Vec3(0))
                self.assertIsNotNone(a.ship)

    def test_shift_boost_and_automatic_approach_remain_available(self):
        a = self.app
        self.flying()
        a.controller.keys.update(w=True, shift=True)
        for _ in range(15):
            a.step(.1)
        self.assertTrue(a.controller.boosting)
        self.assertGreater(a.controller.speed, 500)
        self.assertEqual(a.quantum.phase, "idle")
        a.controller.reset_keys()
        a.action("travel", 1)
        self.assertIsNotNone(a.autopilot)
        self.assertEqual(a.quantum.phase, "idle")
        before = Vec3(a.world_position())
        for _ in range(5):
            a.step(.1)
        self.assertGreater((a.world_position() - before).length(), 1)
        a.interact()
        self.assertIsNone(a.autopilot)


if __name__ == "__main__":
    unittest.main()
