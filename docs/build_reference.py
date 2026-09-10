"""Rebuild the human-readable reference from the same data used by the game."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from asterion.content import BIOMES, BUILDINGS, ITEMS, RECIPES, UPGRADE_NAMES


def clean(text):
    return str(text).replace("|", "\\|").replace("\n", " ")


def materials(values):
    return "; ".join(f"{amount} {ITEMS[item]['name']}" for item, amount in values.items()) or "—"


def table(headers, rows):
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(clean(value) for value in row) + " |" for row in rows),
    ])


def build():
    sections = [
        "# Asterion Expedition · content reference",
        "This reference is generated from the shipped game data. The in-game panel "
        "shows current affordability, cargo capacity, installed tiers, and actual "
        "trade prices. Item values below are baseline economy values, not a quoted "
        "buy or sell price at a particular station.",
        f"The expedition includes {len(ITEMS)} cargo items, {len(RECIPES)} recipes "
        f"(including equipment tiers), {len(BUILDINGS)} structures, and {len(BIOMES)} biome families.",
        "## Cargo materials and supplies",
        table(["Item", "Category", "Base value", "Purpose"],
              [(v["name"], v["category"], v["value"], v["description"]) for v in ITEMS.values()]),
        "## Fabrication",
        "Input materials are consumed only on a successful craft. Upgrade recipes "
        "install equipment directly instead of adding a cargo item.",
        table(["Recipe", "Inputs", "Output or installation"],
              [(v["name"], materials(v["inputs"]),
                f"{UPGRADE_NAMES[v['upgrade']]} tier {v['tier']}" if v.get("upgrade") else materials(v["outputs"]))
               for v in RECIPES.values()]),
        "## Construction",
        "Structures are fixed prefabs placed on the current planet. Some are "
        "functional support equipment; others provide visual landmarks. A Cargo "
        "Vault does not add a second inventory, and there is no power-cable editor.",
        table(["Structure", "Cost", "Description"],
              [(v["name"], materials(v["cost"]), v["description"]) for v in BUILDINGS.values()]),
        "## Biome families",
        "Individual worlds vary from these base temperature and hazard values. "
        "The active world's HUD and map describe its actual conditions.",
        table(["Biome", "Base temperature", "Base hazard", "Resources", "Landscape"],
              [(v["name"], f"{v['temperature']} °C", f"{round(v['hazard'] * 100)}%",
                ", ".join(ITEMS[item]["name"] for item in v["resources"]), v["description"])
               for v in BIOMES.values()]),
        "[Return to the field guide](FIELD_GUIDE.md) · [Setup and controls](../README.md)",
    ]
    path = ROOT / "docs" / "REFERENCE.md"
    path.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    print(path)


if __name__ == "__main__":
    build()
