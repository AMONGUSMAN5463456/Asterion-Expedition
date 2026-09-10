"""Engine-independent expedition state, economy, progression, and safe saves.

Mutating economy operations validate the whole transaction before changing any
inventory. Save files contain only bounded JSON values and are replaced
atomically after a complete write. A previous valid file remains available as
``<save path>.bak`` for recovery.
"""

import copy
import json
import math
import os
from pathlib import Path
import random
import re
import tempfile

from .content import BUILDINGS, CONTRACT_TEMPLATES, ITEMS, RECIPES, STORY, UPGRADE_NAMES
from .universe import get_planet, planet_from_id, terrain_height

SAVE_VERSION = 3
MAX_CREDITS = 2_000_000_000
MAX_COUNT = 1_000_000_000
MAX_SAVE_BYTES = 16 * 1024 * 1024
MAX_BASES_PER_PLANET = 48
_VITALS = ("oxygen", "hazard", "energy", "shield", "fuel")
_EVENT_ALIASES = {
    "mine": "mined", "mining": "mined", "scan": "scanned", "craft": "crafted",
    "launch": "launched", "visit": "visited", "warp": "warped", "ruin": "ruins",
    "build": "built", "trade": "traded", "upgrade": "upgrades", "creature": "fauna",
}
_DEFAULT_SETTINGS = dict(sensitivity=.16, invert_y=False, volume=.45, quality="medium",
                         fov=78.0, camera_motion=.35, mouse_smoothing=0.0,
                         flight_assist=True, mouse_capture=True, ambient_occlusion=True)
_CONSUMABLES = {
    "oxygen": {"oxygen": 9}, "sodium": {"hazard": 16},
    "life_gel": {"oxygen": 85}, "ion_cell": {"hazard": 75, "energy": 55},
    "fuel_cell": {"fuel": 45}, "warp_cell": {"fuel": 85},
    "repair_kit": {"shield": 65},
}


def _int(value, default=0, low=0, high=MAX_COUNT):
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return min(high, max(low, value))


def _number(value, default=0.0, low=0.0, high=1e12):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return default
    try:
        value = float(value)
    except (OverflowError, ValueError):
        return default
    return min(high, max(low, value)) if math.isfinite(value) else default


def _quantity(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_COUNT


def _identifier(value, maximum=160):
    return isinstance(value, str) and 0 < len(value) <= maximum and not any(ord(c) < 32 for c in value)


def _position(value, fallback):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return list(fallback)
    return [_number(value[i], float(fallback[i]), -1e7, 1e7) for i in range(3)]


def _valid_position(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        return False
    return all(isinstance(x, (float, int)) and not isinstance(x, bool)
               and -1e7 <= x <= 1e7 and math.isfinite(x) for x in value)


def _planet_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"s(?:[0-9]|1[0-9]|2[0-3])-p[0-3]", value):
        return False
    return True


def _depletion_location(value):
    return _planet_id(value) or (isinstance(value, str)
                                and re.fullmatch(r"orbit:(?:[0-9]|1[0-9]|2[0-3])", value) is not None)


def _small_record(record):
    """Drop renderer handles and arbitrary objects from discovery metadata."""
    result = {}
    if not isinstance(record, dict):
        return result
    for key in ("name", "kind", "description", "planet_id", "planet", "biome", "resource", "notes"):
        if isinstance(record.get(key), str):
            result[key] = record[key][:600]
    for key in ("system_id", "seed", "temperature", "reward", "elapsed"):
        if isinstance(record.get(key), (int, float)) and not isinstance(record[key], bool):
            result[key] = _number(record[key], 0, -1e9, 1e12)
    if _valid_position(record.get("pos")):
        result["pos"] = list(record["pos"])
    return result


class GameState:
    def __init__(self, seed=73129):
        self.seed = _int(seed, 73129, 0, 2**31 - 1)
        self.inventory = {
            "ferrite": 18, "carbon": 12, "oxygen": 20, "sodium": 12,
            "copper": 8, "crystal": 4, "alloy": 2, "fuel_cell": 3,
            "warp_cell": 2, "life_gel": 2, "ion_cell": 2,
        }
        self.credits = 800
        self.upgrades = {key: 0 for key in UPGRADE_NAMES}
        self.stats = {key: 0 for key in ("mined", "scanned", "crafted", "launched", "visited", "warped", "ruins", "built", "traded", "upgrades", "fauna", "discoveries", "contracts_completed", "rescues")}
        self.discoveries = {}
        self.visited = []
        self.depleted = {}
        self.bases = {}
        self.system_id = 0
        self.planet_index = 0
        self.mode = "surface"
        self.position = [0.0, -12.0, 21.8]
        self.heading = 0.0
        self.pitch = 0.0
        self.ship_position = [7.0, 6.0, 20.0]
        self.vitals = {key: 100.0 for key in _VITALS}
        self.settings = dict(_DEFAULT_SETTINGS)
        self.elapsed = 0.0
        self.story_stage = 0
        self.contracts = []
        self.load_warning = ""

    def capacity(self):
        return 240 + 120 * _int(self.upgrades.get("cargo"), 0, 0, 3)

    def cargo_used(self):
        return sum(_int(value) for value in self.inventory.values())

    def add_item(self, item, amount):
        if not isinstance(item, str) or item not in ITEMS or not _quantity(amount):
            return 0
        added = min(amount, max(0, self.capacity() - self.cargo_used()))
        if added:
            self.inventory[item] = _int(self.inventory.get(item)) + added
        return added

    def remove_item(self, item, amount):
        if not isinstance(item, str) or item not in ITEMS or not _quantity(amount):
            return False
        have = _int(self.inventory.get(item))
        if have < amount:
            return False
        if have == amount:
            self.inventory.pop(item, None)
        else:
            self.inventory[item] = have - amount
        return True

    def _can_pay(self, ingredients):
        return all(_int(self.inventory.get(item)) >= amount for item, amount in ingredients.items())

    def _pay(self, ingredients):
        for item, amount in ingredients.items():
            self.remove_item(item, amount)

    def _missing(self, ingredients):
        return ", ".join(f"{amount - _int(self.inventory.get(item))} {ITEMS[item]['name']}"
                         for item, amount in ingredients.items() if _int(self.inventory.get(item)) < amount)

    def craft(self, recipe_id):
        if not isinstance(recipe_id, str) or recipe_id not in RECIPES:
            return False, "That recipe is not in the fabricator."
        recipe = RECIPES[recipe_id]
        if any(item not in ITEMS for item in recipe.get("outputs", {})):
            return False, "That recipe's output is not in the cargo manifest."
        upgrade = recipe.get("upgrade")
        if upgrade:
            current = _int(self.upgrades.get(upgrade), 0, 0, 3)
            tier = recipe.get("tier", 1)
            if current >= tier:
                return False, f"{recipe['name']} is already installed."
            if current + 1 != tier:
                return False, f"Install {UPGRADE_NAMES[upgrade]} tier {tier - 1} first."
        if not self._can_pay(recipe["inputs"]):
            return False, "Missing " + self._missing(recipe["inputs"]) + "."
        remaining = self.cargo_used() - sum(recipe["inputs"].values())
        output_size = sum(recipe["outputs"].values())
        next_capacity = self.capacity() + (120 if upgrade == "cargo" else 0)
        if remaining + output_size > next_capacity:
            return False, "Cargo full. Make room for the complete recipe output."
        self._pay(recipe["inputs"])
        if upgrade:
            self.upgrades[upgrade] = recipe.get("tier", 1)
            self.record("upgrades")
        for item, amount in recipe["outputs"].items():
            self.inventory[item] = _int(self.inventory.get(item)) + amount
        self.record("crafted")
        self.record("crafted_" + recipe_id)
        return True, ("Installed " if upgrade else "Fabricated ") + recipe["name"] + "."

    def consume(self, item_id):
        if not isinstance(item_id, str) or item_id not in _CONSUMABLES:
            return False, "This material is used for crafting or trade."
        if _int(self.inventory.get(item_id)) < 1:
            return False, "No " + ITEMS[item_id]["name"] + " in cargo."
        effects = _CONSUMABLES[item_id]
        if all(_number(self.vitals.get(key), 100, 0, 100) >= 99.999 for key in effects):
            return False, "Those systems are already fully charged."
        self.remove_item(item_id, 1)
        for key, value in effects.items():
            self.vitals[key] = min(100.0, _number(self.vitals.get(key), 100, 0, 100) + value)
        return True, ITEMS[item_id]["name"] + " applied."

    def price(self, item, buy, system_id=None):
        if not isinstance(item, str) or item not in ITEMS or not isinstance(buy, bool):
            return 0
        sid = self.system_id if system_id is None else system_id
        if isinstance(sid, bool) or not isinstance(sid, int) or not 0 <= sid < 24:
            return 0
        # A fixed spread prevents buy/sell loops; local demand varies by system.
        item_number = tuple(ITEMS).index(item)
        demand = .83 + ((sid * 17 + item_number * 11 + sid * item_number * 3) % 35) / 100
        value = ITEMS[item]["value"] * demand
        return max(1, math.ceil(value * 1.20) if buy else math.floor(value * .74))

    def trade(self, item, amount, buy, system_id=None):
        unit_price = self.price(item, buy, system_id)
        if not _quantity(amount) or not unit_price or not isinstance(buy, bool):
            return False, "Choose a valid material and a positive whole quantity."
        total = unit_price * amount
        if buy:
            if total > self.credits:
                return False, "Insufficient credits for that purchase."
            if self.cargo_used() + amount > self.capacity():
                return False, "Cargo full. Reduce the purchase quantity."
            self.credits -= total
            self.inventory[item] = _int(self.inventory.get(item)) + amount
        else:
            if _int(self.inventory.get(item)) < amount:
                return False, "There is not enough of that material in cargo."
            if self.credits + total > MAX_CREDITS:
                return False, "The account has reached its credit limit."
            self.remove_item(item, amount)
            self.credits += total
        self.record("traded", amount)
        self.record("purchased" if buy else "sold", amount)
        return True, f"{'Bought' if buy else 'Sold'} {amount} {ITEMS[item]['name']} for {total:,} credits."

    def discover(self, entity_id, record):
        if not _identifier(entity_id) or not isinstance(record, dict):
            return False, "No readable survey signature."
        if entity_id in self.discoveries:
            return False, "Already recorded in your discovery atlas."
        if len(self.discoveries) >= 50000:
            return False, "The local discovery archive is full."
        clean = _small_record(record)
        kind = clean.get("kind", "mineral")
        base_reward = {"mineral": 65, "flora": 75, "fauna": 140, "ruin": 180,
                       "asteroid": 45, "planet": 250, "outpost": 100}.get(kind, 65)
        reward = round(base_reward * (1 + .4 * _int(self.upgrades.get("scanner"), 0, 0, 3)))
        clean.update(name=clean.get("name", "Unclassified discovery"), kind=kind,
                     reward=reward, elapsed=_number(self.elapsed))
        if not _planet_id(clean.get("planet_id")):
            clean["planet_id"] = f"s{_int(self.system_id, 0, 0, 23)}-p{_int(self.planet_index, 0, 0, 3)}"
        self.discoveries[entity_id] = clean
        self.credits = min(MAX_CREDITS, self.credits + reward)
        self.record("discoveries")
        if kind in ("mineral", "flora", "fauna", "asteroid"):
            self.record("scanned")
        if kind == "fauna":
            self.record("fauna")
        return True, f"Discovered {clean['name']}. +{reward:,} credits."

    def visit(self, planet):
        if not isinstance(planet, dict) or not _planet_id(planet.get("id")):
            return False, "No valid landing coordinates."
        canonical = planet_from_id(planet["id"])
        self.system_id, self.planet_index = canonical["system_id"], canonical["index"]
        planet_id = canonical["id"]
        if planet_id in self.visited:
            return False, "Returned to " + canonical["name"] + "."
        self.visited.append(planet_id)
        self.record("visited")
        # A planet uses its own archive key, so first landing rewards only once.
        discovered, _ = self.discover(planet_id, dict(name=canonical["name"], kind="planet", planet_id=planet_id,
                                                    planet=canonical["name"], biome=canonical["biome"],
                                                    description=canonical["description"]))
        reward = self.discoveries.get(planet_id, {}).get("reward", 250) if discovered else 0
        suffix = f" +{reward:g} survey credits." if reward else " Planet survey already archived."
        return True, f"First landing on {canonical['name']}." + suffix

    def build(self, kind, planet_id, pos, heading=0):
        if not isinstance(kind, str) or kind not in BUILDINGS or not _planet_id(planet_id):
            return False, "Select a valid building and planetary site.", None
        if not _valid_position(pos) or not isinstance(heading, (float, int)) or isinstance(heading, bool) or not -1e7 <= heading <= 1e7 or not math.isfinite(heading):
            return False, "The construction coordinates are invalid.", None
        records = self.bases.get(planet_id, [])
        if len(records) >= MAX_BASES_PER_PLANET:
            return False, f"This world already has {MAX_BASES_PER_PLANET} expedition structures.", None
        if any(math.hypot(pos[0] - record["pos"][0], pos[1] - record["pos"][1]) < (4 if kind == "lamp" else 8)
               for record in records):
            return False, "Move farther from the existing structure.", None
        building = BUILDINGS[kind]
        if not self._can_pay(building["cost"]):
            return False, "Missing " + self._missing(building["cost"]) + ".", None
        serial = _int(self.stats.get("built")) + 1
        used_ids = {record["id"] for record in records}
        while f"{planet_id}-base-{serial}" in used_ids:
            serial += 1
        record = dict(id=f"{planet_id}-base-{serial}", kind=kind, name=building["name"],
                      pos=[float(x) for x in pos], heading=float(heading) % 360,
                      created=_number(self.elapsed), last_collected=_number(self.elapsed), stored=0)
        self._pay(building["cost"])
        self.bases.setdefault(planet_id, []).append(record)
        self.record("built")
        self.record("built_" + kind)
        return True, building["name"] + " constructed.", copy.deepcopy(record)

    def collect_base_yield(self, planet_id):
        if not _planet_id(planet_id):
            return False, "Select a planetary outpost."
        records = self.bases.get(planet_id, [])
        planet = planet_from_id(planet_id)
        local_ore = next((x for x in planet["resources"] if x not in ("ferrite", "carbon", "oxygen", "sodium")), "ferrite")
        powered = any(record["kind"] == "solar" for record in records)
        now = _number(self.elapsed)
        collected = {}
        stored = 0
        for record in records:
            if record["kind"] not in ("extractor", "garden"):
                continue
            interval = 18 if record["kind"] == "garden" else 30
            rate = 2 if powered else 1
            last = min(now, _number(record.get("last_collected"), now))
            cycles = int(max(0, now - last) // interval)
            record["last_collected"] = last + cycles * interval
            record["stored"] = min(60, _int(record.get("stored"), 0, 0, 60) + cycles * rate)
            item = "carbon" if record["kind"] == "garden" else local_ore
            added = self.add_item(item, record["stored"])
            record["stored"] -= added
            stored += record["stored"]
            if added:
                collected[item] = collected.get(item, 0) + added
        if not collected:
            return False, ("Cargo full. Production remains stored at the outpost." if stored else "No production is ready. Extractors and gardens produce while you explore.")
        self.record("harvested", sum(collected.values()))
        return True, "Collected " + ", ".join(f"{amount} {ITEMS[item]['name']}" for item, amount in collected.items()) + "."

    def record(self, event, amount=1):
        if not isinstance(event, str) or not re.fullmatch(r"[A-Za-z0-9_:-]{1,80}", event):
            return
        if isinstance(amount, bool) or not isinstance(amount, (float, int)):
            return
        try:
            finite = math.isfinite(amount)
        except (TypeError, OverflowError):
            return
        if not finite or amount <= 0 or amount > MAX_COUNT:
            return
        event = _EVENT_ALIASES.get(event, event)
        if event not in self.stats and len(self.stats) >= 500:
            return
        self.stats[event] = min(1e12, _number(self.stats.get(event)) + amount)
        if isinstance(amount, int) and self.stats[event].is_integer():
            self.stats[event] = int(self.stats[event])
        self._advance_story()

    def _advance_story(self):
        self.story_stage = _int(self.story_stage, 0, 0, len(STORY))
        while self.story_stage < len(STORY):
            goal = STORY[self.story_stage]
            if _number(self.stats.get(goal["event"])) < goal["target"]:
                break
            self.credits = min(MAX_CREDITS, self.credits + goal["reward"])
            self.story_stage += 1

    def objective(self):
        stage = _int(self.story_stage, 0, 0, len(STORY))
        if stage >= len(STORY):
            return dict(title="The open horizon", description="Main expedition complete. Discover all 96 worlds, expand your outposts, and take new station contracts.", progress="18 / 18 chapters complete", fraction=1.0, complete=True, stage=stage, total=len(STORY))
        goal = STORY[stage]
        current = min(goal["target"], _number(self.stats.get(goal["event"])))
        return dict(title=goal["title"], description=goal["description"],
                    progress=f"{current:g} / {goal['target']}  ·  {goal['reward']:,} credits",
                    fraction=current / goal["target"], current=current, target=goal["target"],
                    complete=False, stage=stage, total=len(STORY), lore=goal["lore"])

    def available_contracts(self, system_id=None):
        sid = self.system_id if system_id is None else system_id
        if isinstance(sid, bool) or not isinstance(sid, int) or not 0 <= sid < 24:
            return []
        cycle = _int(self.stats.get("contracts_completed"))
        indexes = [index for index, template in enumerate(CONTRACT_TEMPLATES)
                   if template["event"] != "visited" or len(self.visited) < 96]
        random.Random(self.seed + sid * 101 + cycle * 919).shuffle(indexes)
        existing = {contract["id"] for contract in self.contracts}
        offers = []
        for index in indexes[:3]:
            template = CONTRACT_TEMPLATES[index]
            contract = copy.deepcopy(template)
            contract.update(id=f"c{sid}-{cycle}-{template['id']}", template=template["id"], system_id=sid)
            if contract["id"] not in existing:
                offers.append(contract)
        return offers

    def accept_contract(self, contract_id):
        if not _identifier(contract_id):
            return False, "Select an available station contract."
        if sum(not contract.get("claimed", False) for contract in self.contracts) >= 3:
            return False, "Finish one of your three active contracts first."
        offer = next((offer for offer in self.available_contracts() if offer["id"] == contract_id), None)
        if offer is None:
            return False, "That contract is no longer available or is already accepted."
        offer.update(baseline=_number(self.stats.get(offer["event"])), accepted=_number(self.elapsed), claimed=False)
        self.contracts.append(offer)
        return True, "Accepted: " + offer["name"] + "."

    def contract_progress(self, contract):
        if not isinstance(contract, dict):
            return dict(current=0, target=1, complete=False, progress="0 / 1")
        target = max(1, _int(contract.get("target"), 1))
        event = contract.get("event")
        current = min(target, max(0, _number(self.stats.get(event) if isinstance(event, str) else 0) - _number(contract.get("baseline"))))
        if contract.get("claimed"):
            current = target
        return dict(current=current, target=target, complete=current >= target,
                    progress=f"{current:g} / {target}")

    def claim_contract(self, contract_id):
        if not isinstance(contract_id, str):
            return False, "Select an active contract."
        contract = next((c for c in self.contracts if c["id"] == contract_id), None)
        if contract is None or contract.get("claimed"):
            return False, "That contract has already been claimed or was not accepted."
        if not self.contract_progress(contract)["complete"]:
            return False, "Complete the field objective before claiming this reward."
        reward = _int(contract.get("reward"), 0, 0, 10000)
        contract["claimed"] = True
        contract["completed"] = _number(self.elapsed)
        self.credits = min(MAX_CREDITS, self.credits + reward)
        self.record("contracts_completed")
        # Keep recent history and every active contract, bounding long saves.
        active = [c for c in self.contracts if not c.get("claimed")]
        history = [c for c in self.contracts if c.get("claimed")][-100:]
        self.contracts = history + active
        return True, f"Contract complete. +{reward:,} credits. New work is available."

    def rescue(self):
        """No-cost recall: restores traversal without creating tradable cargo."""
        self.system_id = _int(self.system_id, 0, 0, 23)
        self.planet_index = _int(self.planet_index, 0, 0, 3)
        planet = get_planet(self.system_id, self.planet_index)
        self.mode = "surface"
        self.position = [0.0, -12.0, terrain_height(planet["seed"], 0, -12) + 1.8]
        self.ship_position = [7.0, 6.0, terrain_height(planet["seed"], 7, 6)]
        self.heading, self.pitch = 0.0, 0.0
        self.vitals = {key: 100.0 for key in _VITALS}
        self.record("rescues")
        return True, "Expedition recall complete. You and your ship are safe at the landing site; all systems restored."

    def to_dict(self):
        # Round-trip through the sanitizer so external renderer objects, NaNs,
        # and malformed optional fields can never poison a new save file.
        raw = {key: getattr(self, key) for key in (
            "seed", "inventory", "credits", "upgrades", "stats", "discoveries", "visited", "depleted", "bases",
            "system_id", "planet_index", "mode", "position", "heading", "pitch", "ship_position", "vitals",
            "settings", "elapsed", "story_stage", "contracts")}
        clean, _ = self._from_dict(raw)
        result = {key: copy.deepcopy(getattr(clean, key)) for key in raw}
        result["version"] = SAVE_VERSION
        return result

    @classmethod
    def _from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError("Save root must be an object")
        state = cls(data.get("seed", 73129))
        changed = False
        upgrades = data.get("upgrades", {})
        if isinstance(upgrades, dict):
            state.upgrades = {key: _int(upgrades.get(key), 0, 0, 3) for key in UPGRADE_NAMES}
        if "inventory" in data:
            inventory = data["inventory"]
            if isinstance(inventory, dict):
                state.inventory = {}
                for item in ITEMS:
                    count = _int(inventory.get(item), 0, 0, state.capacity())
                    accepted = state.add_item(item, count)
                    if accepted != inventory.get(item, 0):
                        changed = True
                if any(item not in ITEMS for item in inventory):
                    changed = True
            else:
                changed = True
        state.credits = _int(data.get("credits", 800), 800, 0, MAX_CREDITS)
        state.system_id = _int(data.get("system_id", 0), 0, 0, 23)
        state.planet_index = _int(data.get("planet_index", 0), 0, 0, 3)
        state.mode = data.get("mode") if data.get("mode") in ("surface", "orbit", "flight") else "surface"
        state.position = _position(data.get("position"), state.position)
        state.ship_position = _position(data.get("ship_position"), state.ship_position)
        state.heading = _number(data.get("heading"), 0, -1e7, 1e7) % 360
        state.pitch = _number(data.get("pitch"), 0, -89, 89)
        state.elapsed = _number(data.get("elapsed"))
        state.story_stage = _int(data.get("story_stage"), 0, 0, len(STORY))
        vitals = data.get("vitals", {})
        if isinstance(vitals, dict):
            state.vitals = {key: _number(vitals.get(key), 100, 0, 100) for key in _VITALS}
        settings = data.get("settings", {})
        if isinstance(settings, dict):
            for key, low, high in (("sensitivity", .04, .5), ("volume", 0, 1),
                                   ("fov", 60, 100), ("camera_motion", 0, 1),
                                   ("mouse_smoothing", 0, 1)):
                state.settings[key] = _number(settings.get(key), _DEFAULT_SETTINGS[key], low, high)
            for key in ("invert_y", "flight_assist", "mouse_capture", "ambient_occlusion"):
                if isinstance(settings.get(key), bool):
                    state.settings[key] = settings[key]
            if settings.get("quality") in ("low", "medium", "high"):
                state.settings["quality"] = settings["quality"]
        stats = data.get("stats", {})
        if isinstance(stats, dict):
            if len(stats) > 500:
                changed = True
            for key, value in list(stats.items())[:500]:
                if isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9_:-]{1,80}", key):
                    number = _number(value)
                    state.stats[key] = int(number) if number.is_integer() else number
                else:
                    changed = True
        elif "stats" in data:
            changed = True
        visited = data.get("visited", [])
        if isinstance(visited, list):
            state.visited = list(dict.fromkeys(value for value in visited[:500] if _planet_id(value)))
            if len(state.visited) != len(visited):
                changed = True
        elif "visited" in data:
            changed = True
        discoveries = data.get("discoveries", {})
        if isinstance(discoveries, dict):
            if len(discoveries) > 50000:
                changed = True
            for key, value in list(discoveries.items())[:50000]:
                if _identifier(key) and isinstance(value, dict):
                    state.discoveries[key] = _small_record(value)
                else:
                    changed = True
        elif "discoveries" in data:
            changed = True
        depleted = data.get("depleted", {})
        if isinstance(depleted, dict):
            for key, values in depleted.items():
                if _depletion_location(key) and isinstance(values, list):
                    kept = list(dict.fromkeys(value for value in values[:25000] if _identifier(value)))
                    if len(kept) != len(values):
                        changed = True
                    state.depleted[key] = kept
                else:
                    changed = True
        elif "depleted" in data:
            changed = True
        bases = data.get("bases", {})
        if isinstance(bases, dict):
            for planet_id, records in bases.items():
                if not _planet_id(planet_id) or not isinstance(records, list):
                    changed = True
                    continue
                if len(records) > MAX_BASES_PER_PLANET:
                    changed = True
                clean_records = []
                seen_ids = set()
                for record in records[:MAX_BASES_PER_PLANET]:
                    if not isinstance(record, dict) or not isinstance(record.get("kind"), str) or record["kind"] not in BUILDINGS or not _valid_position(record.get("pos")):
                        changed = True
                        continue
                    identifier = record.get("id", f"{planet_id}-base-{len(clean_records) + 1}")
                    if not _identifier(identifier) or identifier in seen_ids:
                        changed = True
                        continue
                    seen_ids.add(identifier)
                    kind = record["kind"]
                    clean_records.append(dict(id=identifier, kind=kind, name=BUILDINGS[kind]["name"],
                                              pos=[float(x) for x in record["pos"]], heading=_number(record.get("heading"), 0, -1e7, 1e7) % 360,
                                              created=_number(record.get("created"), 0, 0, state.elapsed),
                                              last_collected=_number(record.get("last_collected"), state.elapsed, 0, state.elapsed),
                                              stored=_int(record.get("stored"), 0, 0, 60)))
                state.bases[planet_id] = clean_records
        elif "bases" in data:
            changed = True
        contracts = data.get("contracts", [])
        templates = {template["id"]: template for template in CONTRACT_TEMPLATES}
        if isinstance(contracts, list):
            seen = set()
            active_count = 0
            if len(contracts) > 150:
                changed = True
            for record in contracts[-150:]:
                if not isinstance(record, dict) or not isinstance(record.get("template"), str) or record["template"] not in templates or not _identifier(record.get("id")) or record["id"] in seen:
                    changed = True
                    continue
                claimed = record.get("claimed") is True
                if not claimed and active_count >= 3:
                    changed = True
                    continue
                clean = copy.deepcopy(templates[record["template"]])
                clean.update(id=record["id"], template=record["template"],
                             system_id=_int(record.get("system_id"), 0, 0, 23),
                             baseline=_number(record.get("baseline")), accepted=_number(record.get("accepted"), 0, 0, state.elapsed),
                             claimed=claimed)
                if claimed:
                    clean["completed"] = _number(record.get("completed"), 0, 0, state.elapsed)
                else:
                    active_count += 1
                seen.add(clean["id"])
                state.contracts.append(clean)
        elif "contracts" in data:
            changed = True
        return state, changed

    @staticmethod
    def _read_json(path):
        if path.stat().st_size > MAX_SAVE_BYTES:
            raise ValueError("Save file exceeds the supported size")
        try:
            with path.open("r", encoding="utf-8") as stream:
                data = json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Invalid numeric constant")))
        except RecursionError as exc:
            raise ValueError("Save JSON nesting exceeds the supported depth") from exc
        if not isinstance(data, dict):
            raise ValueError("Save root must be an object")
        if not any(key in data for key in ("version", "inventory", "system_id")):
            raise ValueError("This is not an expedition save")
        version = data.get("version", 1)
        if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= SAVE_VERSION:
            raise ValueError("Unsupported save version")
        return data

    def save(self, path):
        path = Path(path)
        temporary = None
        backup_temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2, allow_nan=False)
            if len(payload.encode("utf-8")) > MAX_SAVE_BYTES:
                return False, "Save is too large; the previous save remains untouched."
            # Only copy a valid prior primary into the backup. A corrupt primary
            # must never overwrite the good file recovered on the previous load.
            if path.exists():
                try:
                    self._read_json(path)
                    with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=path.name + ".backup-", delete=False) as stream:
                        backup_temporary = Path(stream.name)
                        stream.write(path.read_bytes())
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(backup_temporary, Path(str(path) + ".bak"))
                    backup_temporary = None
                except (OSError, ValueError, json.JSONDecodeError):
                    # A readable but invalid primary is intentionally skipped;
                    # backup I/O failure also does not damage the primary.
                    pass
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".writing-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            # Parse the complete temporary file before the only primary mutation.
            self._read_json(temporary)
            os.replace(temporary, path)
            temporary = None
            try:
                directory_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass  # Directory fsync is unavailable on some supported OSes.
            return True, "Expedition saved."
        except (OSError, ValueError, TypeError, OverflowError, RecursionError) as exc:
            return False, "Could not save the expedition: " + str(exc)[:160]
        finally:
            for leftover in (temporary, backup_temporary):
                if leftover is not None:
                    try:
                        leftover.unlink(missing_ok=True)
                    except OSError:
                        pass

    @classmethod
    def load(cls, path):
        path = Path(path)
        candidates = (path, Path(str(path) + ".bak"))
        failure = ""
        for index, candidate in enumerate(candidates):
            try:
                data = cls._read_json(candidate)
                state, changed = cls._from_dict(data)
                if index:
                    state.load_warning = "The main save could not be read. Your previous valid backup was recovered."
                elif changed:
                    state.load_warning = "Some invalid cargo entries were repaired while loading."
                return state
            except FileNotFoundError:
                continue
            except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
                failure = str(exc)[:120]
        state = cls()
        state.load_warning = ("No readable save was found. A new expedition is ready. " + failure).strip() if failure else "No existing save was found. A new expedition is ready."
        return state
