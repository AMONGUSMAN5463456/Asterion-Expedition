"""Invariants for the deterministic world, atomic economy, and save recovery."""

import copy
from collections import Counter
import json
import math
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from asterion.content import BIOMES, BUILDINGS, CONTRACT_TEMPLATES, ITEMS, RECIPES, STORY
from asterion.state import GameState, MAX_CREDITS
from asterion.universe import galaxy_catalog, generate_system, get_planet, planet_from_id, terrain_height


class UniverseTests(unittest.TestCase):
    def test_complete_deterministic_unique_galaxy(self):
        planets = [planet for i in range(24) for planet in generate_system(i)["planets"]]
        self.assertEqual(len(galaxy_catalog()), 24)
        self.assertEqual(len(planets), 96)
        self.assertEqual(len({p["id"] for p in planets}), 96)
        self.assertEqual(len({p["name"] for p in planets}), 96)
        self.assertEqual(len({p["seed"] for p in planets}), 96)
        self.assertEqual(Counter(p["biome"] for p in planets), {key: 12 for key in BIOMES})
        before = generate_system(12)
        random.seed(987)
        for _ in range(100):
            random.random()
        self.assertEqual(before, generate_system(12))
        before["planets"][0]["resources"].append("unobtainium")
        self.assertNotIn("unobtainium", generate_system(12)["planets"][0]["resources"])

    def test_all_planets_have_survival_resources_and_dry_landing(self):
        for system in range(24):
            for index in range(4):
                planet = get_planet(system, index)
                self.assertTrue(set(planet["resources"]) <= ITEMS.keys())
                self.assertTrue({"ferrite", "carbon", "oxygen", "sodium"} <= set(planet["resources"]))
                self.assertTrue(0 <= planet["hazard"] <= 1)
                for x, y in ((0, 0), (7, 6), (0, -12), (30, 0), (0, 35)):
                    self.assertGreater(terrain_height(planet["seed"], x, y), planet["water_level"] + 3)
                    self.assertEqual(terrain_height(planet["seed"], x, y), 20.0)
                for key in ("sky", "ground", "accent", "flora", "water"):
                    self.assertEqual(len(planet[key]), 3)
                    self.assertTrue(all(0 <= value <= 1 for value in planet[key]))

    def test_terrain_is_finite_smooth_and_reproducible(self):
        for seed in (73129, 1, 951321):
            for x in range(-600, 601, 47):
                for y in range(-600, 601, 59):
                    height = terrain_height(seed, x, y)
                    self.assertTrue(-3 < height < 42)
                    self.assertLess(abs(height - terrain_height(seed, x + .01, y)), .02)
                    self.assertEqual(height, terrain_height(seed, x, y))
        for x in (float("nan"), float("inf"), "bad", None):
            self.assertTrue(math.isfinite(terrain_height(73129, x, 0)))

    def test_invalid_chart_addresses_reject_instead_of_aliasing(self):
        for value in (-1, 24, 1.5, True, "0", None):
            with self.assertRaises(ValueError):
                generate_system(value)
        for value in ("s24-p0", "s-1-p0", "s0-p4", "s00-p0", "x0-p0", None):
            with self.assertRaises(ValueError):
                planet_from_id(value)

    def test_content_references_are_complete(self):
        self.assertGreaterEqual(len(RECIPES), 30)
        self.assertGreaterEqual(len(STORY), 12)
        for recipe in RECIPES.values():
            self.assertTrue(set(recipe["inputs"]) <= ITEMS.keys())
            self.assertTrue(set(recipe["outputs"]) <= ITEMS.keys())
            self.assertTrue(all(isinstance(n, int) and n > 0 for n in recipe["inputs"].values()))
        for building in BUILDINGS.values():
            self.assertTrue(set(building["cost"]) <= ITEMS.keys())


class EconomyTests(unittest.TestCase):
    def setUp(self):
        self.state = GameState()

    def test_capacity_and_invalid_quantities_are_atomic(self):
        state = self.state
        state.inventory = {"ferrite": state.capacity() - 2}
        self.assertEqual(state.add_item("carbon", 10), 2)
        self.assertEqual(state.cargo_used(), state.capacity())
        before = state.to_dict()
        for amount in (-10, 0, 1.2, True, None, "4", float("inf"), 10**30):
            self.assertEqual(state.add_item("gold", amount), 0)
            self.assertFalse(state.remove_item("ferrite", amount))
            self.assertFalse(state.trade("ferrite", amount, False)[0])
        self.assertEqual(state.to_dict(), before)
        self.assertFalse(state.remove_item("carbon", 3))
        self.assertTrue(state.remove_item("carbon", 2))
        self.assertNotIn("carbon", state.inventory)

    def test_failed_crafting_never_spends_partial_inputs(self):
        state = self.state
        state.inventory = {"ferrite": 100}
        before = state.to_dict()
        self.assertFalse(state.craft("alloy")[0])
        self.assertFalse(state.craft("missing")[0])
        self.assertFalse(state.craft([])[0])
        self.assertEqual(state.to_dict(), before)

    def test_each_recipe_transforms_exactly_and_rejects_missing_inputs(self):
        for recipe_id, recipe in RECIPES.items():
            state = GameState()
            state.inventory = dict(recipe["inputs"])
            if recipe.get("upgrade"):
                state.upgrades[recipe["upgrade"]] = recipe.get("tier", 1) - 1
            self.assertTrue(state.craft(recipe_id)[0], recipe_id)
            self.assertEqual(state.inventory, recipe["outputs"], recipe_id)
            if recipe.get("upgrade"):
                self.assertEqual(state.upgrades[recipe["upgrade"]], recipe["tier"])
            before = state.to_dict()
            self.assertFalse(state.craft(recipe_id)[0], recipe_id)
            self.assertEqual(state.to_dict(), before, recipe_id)

    def test_upgrade_tiers_cannot_skip_or_repeat(self):
        state = self.state
        state.inventory = {"ferrite": 100, "copper": 40, "alloy": 50, "wire": 10, "circuit": 10}
        before = state.to_dict()
        self.assertFalse(state.craft("cargo_2")[0])
        self.assertEqual(state.to_dict(), before)
        self.assertTrue(state.craft("cargo_1")[0])
        self.assertEqual(state.capacity(), 360)
        before = state.to_dict()
        self.assertFalse(state.craft("cargo_1")[0])
        self.assertEqual(state.to_dict(), before)
        self.assertTrue(state.craft("cargo_2")[0])
        self.assertEqual(state.capacity(), 480)

    def test_output_space_is_calculated_after_consuming_inputs(self):
        state = self.state
        state.inventory = {"ferrite": 238, "carbon": 2}
        self.assertTrue(state.craft("alloy")[0])
        self.assertEqual(state.cargo_used(), 234)
        # Inject a hypothetical high-yield recipe to verify the generic guard.
        with patch.dict(RECIPES, {"expansion_test": dict(name="Test", inputs={"carbon": 1}, outputs={"gold": 4})}):
            state.inventory = {"ferrite": 238, "carbon": 1}
            before = state.to_dict()
            self.assertFalse(state.craft("expansion_test")[0])
            self.assertEqual(state.to_dict(), before)

    def test_trade_spreads_and_transactions(self):
        state = self.state
        for system_id in range(24):
            for item in ITEMS:
                self.assertLess(state.price(item, False, system_id), state.price(item, True, system_id))
        state.inventory = {}
        state.credits = 100000
        self.assertTrue(state.trade("gold", 5, True)[0])
        self.assertTrue(state.trade("gold", 5, False)[0])
        self.assertLess(state.credits, 100000)
        self.assertEqual(state.inventory, {})
        for item, amount, buy, system in (("gold", 1, False, None), ("gold", 100000, True, None), ("unknown", 1, True, None), ("gold", 1, True, -1), ("gold", 1, "yes", None)):
            before = state.to_dict()
            self.assertFalse(state.trade(item, amount, buy, system)[0])
            self.assertEqual(state.to_dict(), before)
        state.inventory = {"ferrite": state.capacity()}
        before = state.to_dict()
        self.assertFalse(state.trade("oxygen", 1, True)[0])
        self.assertEqual(state.to_dict(), before)
        state.credits = MAX_CREDITS
        self.assertFalse(state.trade("ferrite", 1, False)[0])

    def test_consumables_restore_correct_systems_and_refuse_waste(self):
        state = self.state
        before = state.to_dict()
        self.assertFalse(state.consume("life_gel")[0])
        self.assertEqual(state.to_dict(), before)
        state.vitals["oxygen"] = 5
        self.assertTrue(state.consume("life_gel")[0])
        self.assertEqual(state.vitals["oxygen"], 90)
        state.vitals["hazard"], state.vitals["energy"] = 95, 20
        self.assertTrue(state.consume("ion_cell")[0])
        self.assertEqual(state.vitals["hazard"], 100)
        self.assertEqual(state.vitals["energy"], 75)
        self.assertFalse(state.consume("gold")[0])

    def test_discoveries_are_unique_and_skip_renderer_handles(self):
        state = self.state
        record = dict(name="Ribbon grazer", kind="fauna", planet="Talora", node=object(), reward=999999)
        self.assertTrue(state.discover("s0-p0:fauna:0", record)[0])
        self.assertEqual(state.stats["fauna"], 1)
        self.assertEqual(state.stats["scanned"], 1)
        self.assertEqual(state.credits, 940)
        self.assertEqual(state.discoveries["s0-p0:fauna:0"]["planet"], "Talora")
        self.assertNotIn("node", state.discoveries["s0-p0:fauna:0"])
        before = state.to_dict()
        self.assertFalse(state.discover("s0-p0:fauna:0", record)[0])
        self.assertEqual(state.to_dict(), before)

    def test_planet_visit_and_recall_cannot_mint_tradable_goods(self):
        state = self.state
        self.assertTrue(state.visit(get_planet(0, 0))[0])
        before = state.to_dict()
        self.assertFalse(state.visit(get_planet(0, 0))[0])
        self.assertEqual(state.to_dict(), before)
        state.inventory = {}
        state.vitals = {key: 0 for key in state.vitals}
        state.position = [1000, -900, -1000]
        for _ in range(3):
            self.assertTrue(state.rescue()[0])
        self.assertEqual(state.inventory, {})
        self.assertEqual(state.credits, before["credits"])
        self.assertTrue(all(v == 100 for v in state.vitals.values()))
        self.assertGreater(state.position[2], 20)

    def test_building_spend_placement_and_storage(self):
        state = self.state
        state.inventory = dict(BUILDINGS["extractor"]["cost"])
        ok, _, record = state.build("extractor", "s0-p0", [30, 20, 20])
        self.assertTrue(ok)
        self.assertEqual(state.inventory, {})
        self.assertEqual(len(state.bases["s0-p0"]), 1)
        record["pos"][0] = 999
        self.assertEqual(state.bases["s0-p0"][0]["pos"][0], 30)
        state.inventory = dict(BUILDINGS["extractor"]["cost"])
        before = state.to_dict()
        self.assertFalse(state.build("extractor", "s0-p0", [31, 20, 20])[0])
        self.assertFalse(state.build("extractor", "s24-p0", [80, 20, 20])[0])
        self.assertFalse(state.build("extractor", "s0-p0", [float("nan"), 20, 20])[0])
        self.assertEqual(state.to_dict(), before)
        state.inventory = {"ferrite": state.capacity()}
        state.elapsed = 150
        self.assertFalse(state.collect_base_yield("s0-p0")[0])
        self.assertEqual(state.bases["s0-p0"][0]["stored"], 5)
        state.remove_item("ferrite", 3)
        self.assertTrue(state.collect_base_yield("s0-p0")[0])
        self.assertEqual(state.inventory["copper"], 3)
        self.assertEqual(state.bases["s0-p0"][0]["stored"], 2)
        state.remove_item("ferrite", 3)
        self.assertTrue(state.collect_base_yield("s0-p0")[0])
        self.assertEqual(state.inventory["copper"], 5)
        self.assertFalse(state.collect_base_yield("s0-p0")[0])


class ProgressionTests(unittest.TestCase):
    def test_all_story_stages_complete_once_and_preserve_rewards(self):
        state = GameState()
        expected = state.credits + sum(goal["reward"] for goal in STORY)
        for index, goal in enumerate(STORY):
            state.record(goal["event"], goal["target"])
            self.assertGreaterEqual(state.story_stage, index + 1)
        self.assertTrue(state.objective()["complete"])
        self.assertEqual(state.credits, expected)
        state.record("mined", 10000)
        self.assertEqual(state.credits, expected)

    def test_invalid_event_amounts_never_advance_story(self):
        state = GameState()
        before = state.to_dict()
        for amount in (-1, 0, True, "10", float("nan"), float("inf"), 10**1000):
            state.record("mined", amount)
        self.assertEqual(state.to_dict(), before)
        state.record("mine", 20)
        self.assertEqual(state.story_stage, 1)

    def test_signal_and_structure_discoveries_do_not_count_as_life_scans(self):
        state = GameState()
        for kind in ("signal", "ruin", "outpost", "beacon", "planet"):
            self.assertTrue(state.discover(kind, dict(name=kind, kind=kind))[0])
        self.assertEqual(state.stats["scanned"], 0)
        self.assertEqual(state.stats["discoveries"], 5)

    def test_complete_planet_catalog_does_not_offer_impossible_travel_contracts(self):
        state = GameState()
        state.visited = [f"s{sid}-p{pid}" for sid in range(24) for pid in range(4)]
        for sid in range(24):
            offers = state.available_contracts(sid)
            self.assertEqual(len(offers), 3)
            self.assertTrue(all(offer["event"] != "visited" for offer in offers))

    def test_contract_baseline_completion_repeatability_and_no_double_claim(self):
        state = GameState()
        offer = state.available_contracts()[0]
        state.record(offer["event"], 100)
        self.assertTrue(state.accept_contract(offer["id"])[0])
        self.assertFalse(state.accept_contract(offer["id"])[0])
        contract = state.contracts[0]
        self.assertEqual(state.contract_progress(contract)["current"], 0)
        self.assertFalse(state.claim_contract(contract["id"])[0])
        state.record(contract["event"], contract["target"])
        self.assertTrue(state.contract_progress(contract)["complete"])
        credits = state.credits
        self.assertTrue(state.claim_contract(contract["id"])[0])
        self.assertEqual(state.credits, credits + contract["reward"])
        before = state.to_dict()
        self.assertFalse(state.claim_contract(contract["id"])[0])
        self.assertEqual(state.to_dict(), before)
        self.assertTrue(state.available_contracts())
        self.assertTrue(all(c["id"] != contract["id"] for c in state.available_contracts()))


class SaveTests(unittest.TestCase):
    def test_full_roundtrip_and_previous_backup_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "save.json"
            state = GameState()
            state.visit(get_planet(3, 2))
            state.record("mined", 23)
            state.depleted = {"s3-p2": ["stone-1", "plant-2"], "orbit:3": ["asteroid-4"], "orbit:23": ["asteroid-7"]}
            state.settings.update(quality="high", invert_y=True, volume=.1)
            state.position = [320, -150, 38.2]
            state.mode = "orbit"
            state.discover("s3-p2:flora:7", dict(name="Opal branch", kind="flora", planet="Arune 4c"))
            self.assertTrue(state.accept_contract(state.available_contracts()[0]["id"])[0])
            self.assertTrue(state.save(path)[0])
            first = state.to_dict()
            self.assertEqual(GameState.load(path).to_dict(), first)
            state.credits += 17
            self.assertTrue(state.save(path)[0])
            self.assertEqual(GameState.load(path).credits, state.credits)
            self.assertTrue(Path(str(path) + ".bak").exists())
            path.write_text('{"inventory":', encoding="utf-8")
            recovered = GameState.load(path)
            self.assertIn("backup", recovered.load_warning)
            self.assertEqual(recovered.to_dict(), first)
            self.assertEqual(recovered.depleted["orbit:3"], ["asteroid-4"])
            self.assertTrue(recovered.save(path)[0])
            self.assertEqual(GameState.load(path).to_dict(), first)
            self.assertEqual(json.loads(Path(str(path) + ".bak").read_text()), first)

    def test_save_after_nested_json_corruption_preserves_recovered_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "save.json"
            backup = Path(str(path) + ".bak")
            state = GameState()
            state.credits = 1234
            self.assertTrue(state.save(path)[0])
            self.assertTrue(state.save(path)[0])
            original_backup = backup.read_bytes()
            path.write_text('{"version":3,"nested":' + '[' * 100000
                            + '0' + ']' * 100000 + '}', encoding="utf-8")
            recovered = GameState.load(path)
            self.assertEqual(recovered.credits, 1234)
            recovered.credits += 100
            success, message = recovered.save(path)
            self.assertTrue(success, message)
            self.assertEqual(GameState.load(path).credits, 1334)
            self.assertEqual(backup.read_bytes(), original_backup)

    def test_optional_malformed_values_are_sanitized(self):
        bad = dict(version=3, inventory={"gold": -4, "carbon": "20", "oxygen": True, "ferrite": 999999, "invalid": 3},
                   credits=-300, upgrades={"cargo": 100, "engine": -1, "scanner": "yes"},
                   system_id=999, planet_index=-1, position=[float("nan"), 3, float("inf")],
                   vitals={"oxygen": -9, "hazard": 200, "fuel": "oops"},
                   stats={"mined": float("inf"), "scanned": -20},
                   settings={"sensitivity": float("nan"), "volume": 20, "invert_y": "yes", "quality": []},
                   discoveries={"e1": {"name": "Test", "kind": "flora", "node": object()}},
                   bases={"s0-p0": [{"kind": [], "pos": [0, 0, 0]}, {"kind": "beacon", "pos": [0, 0, float("inf")]}]},
                   depleted={"orbit:3": ["a", "a", "b", None], "orbit:24": ["c"], "s0-p0": ["x", "x"]},
                   visited=["s0-p0", "s0-p0", "s24-p0", {}, None],
                   contracts=[{"id": "bad", "template": []}], elapsed=float("inf"))
        state, changed = GameState._from_dict(bad)
        self.assertTrue(changed)
        self.assertEqual(state.cargo_used(), state.capacity())
        self.assertEqual(state.inventory, {"ferrite": 600})
        self.assertEqual(state.credits, 0)
        self.assertEqual(state.vitals["oxygen"], 0)
        self.assertEqual(state.vitals["hazard"], 100)
        self.assertEqual(state.vitals["fuel"], 100)
        self.assertEqual(state.settings["quality"], "medium")
        self.assertEqual(state.settings["sensitivity"], .16)
        self.assertEqual(state.visited, ["s0-p0"])
        self.assertEqual(state.depleted, {"orbit:3": ["a", "b"], "s0-p0": ["x"]})
        self.assertEqual(state.contracts, [])
        self.assertTrue(all(math.isfinite(n) for n in state.position))
        json.dumps(state.to_dict(), allow_nan=False)

    def test_corrupt_primary_and_backup_return_safe_new_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "save.json"
            for payload in ("[]", "null", "{", '{"version":999}', '{"version":3,"elapsed":NaN}'):
                path.write_text(payload)
                Path(str(path) + ".bak").write_text("garbage")
                state = GameState.load(path)
                self.assertTrue(state.load_warning)
                self.assertEqual(state.system_id, 0)
                self.assertEqual(state.credits, 800)

    def test_failed_primary_replace_preserves_complete_previous_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "save.json"
            state = GameState()
            self.assertTrue(state.save(path)[0])
            original = path.read_bytes()
            state.credits += 100
            import os
            real_replace = os.replace

            def fail_primary(source, target):
                if Path(target) == path:
                    raise OSError("Simulated interrupted commit")
                return real_replace(source, target)

            with patch("asterion.state.os.replace", side_effect=fail_primary):
                self.assertFalse(state.save(path)[0])
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(GameState.load(path).credits, 800)
            self.assertFalse(any(".writing-" in file.name or ".backup-" in file.name for file in path.parent.iterdir()))

    def test_old_sparse_save_gets_tolerant_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "save.json"
            path.write_text(json.dumps(dict(version=1, credits=1200, system_id=2)))
            state = GameState.load(path)
            self.assertEqual(state.credits, 1200)
            self.assertEqual(state.system_id, 2)
            self.assertEqual(state.settings["volume"], .45)
            self.assertTrue(state.inventory["fuel_cell"])
            self.assertEqual(state.story_stage, 0)

    def test_every_optional_field_handles_wrong_json_types(self):
        baseline = GameState().to_dict()
        wrong_types = (None, True, "bad", [], {}, [None], -10, 1.5, float("nan"), float("inf"))
        for key in baseline:
            if key == "version":
                continue
            for value in wrong_types:
                with self.subTest(field=key, value=str(value)):
                    candidate = copy.deepcopy(baseline)
                    candidate[key] = value
                    state, _ = GameState._from_dict(candidate)
                    output = state.to_dict()
                    json.dumps(output, allow_nan=False)
                    self.assertLessEqual(state.cargo_used(), state.capacity())
                    self.assertTrue(all(0 <= n <= 100 for n in state.vitals.values()))


if __name__ == "__main__":
    unittest.main()
