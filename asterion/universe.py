"""Stable procedural star charts and shared continuous terrain sampling."""

import copy
import math
import random
from functools import lru_cache

from .content import BIOMES

SYSTEM_COUNT = 24
PLANETS_PER_SYSTEM = 4
UNIVERSE_SEED = 73129

_SYSTEM_NAMES = (
    "Asterion", "Quiet Lantern", "Ilyra", "Copperwake", "Nacre Drift", "Vespera",
    "Pale Orchard", "Halcyon", "Cinder Choir", "Oriel", "Blue Meridian", "Tamaris",
    "Glass Harbor", "Aureline", "Morrow's Arc", "Nivalis", "Lumen Cross", "Saffron Veil",
    "Evershore", "Tesselar", "Far Willow", "Prism Hearth", "Silver Interval", "Open Horizon",
)
_FIRST = ("Ar", "Bel", "Cor", "Dae", "El", "Fen", "Gal", "Ily", "Ka", "Lor", "Myr", "Nae", "Or", "Pra", "Rhe", "Syl", "Tal", "Ul", "Va", "Yri")
_LAST = ("adia", "alis", "ara", "en", "essa", "ian", "ion", "ora", "une", "yth", "eva", "os", "arae", "eth", "ara", "elis")
_ECONOMIES = ("Botanical exchange", "Ore refining", "Survey technology", "Habitat fabrication", "Archive conservation", "Long-range logistics")
_STAR_COLORS = ((1.0, .84, .60), (.61, .81, 1.0), (1.0, .57, .34), (.87, .91, 1.0), (1.0, .91, .69), (.89, .67, 1.0))
_BIOME_IDS = tuple(BIOMES)


def _index(value, maximum, label):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < maximum:
        raise ValueError(f"{label} must be an integer from 0 to {maximum - 1}")
    return value


@lru_cache(maxsize=SYSTEM_COUNT)
def _system(index):
    rng = random.Random(UNIVERSE_SEED + index * 100003)
    planets = []
    for p in range(PLANETS_PER_SYSTEM):
        seed = UNIVERSE_SEED + index * 104729 + p * 15485863
        prng = random.Random(seed)
        # The multiplier is coprime to eight; every biome appears exactly 12 times.
        biome_id = _BIOME_IDS[((index * 4 + p) * 5) % len(_BIOME_IDS)]
        biome = BIOMES[biome_id]
        angle = p * math.tau / 4 + .23 + rng.uniform(-.14, .14)
        distance = 2100 + p * 740 + rng.uniform(-150, 150)
        name = "Talora" if index == p == 0 else prng.choice(_FIRST) + prng.choice(_LAST) + f" {index + 1}{'abcd'[p]}"
        temp = round(biome["temperature"] + prng.uniform(-9, 9), 1)
        hazard = max(.03, min(.85, biome["hazard"] + prng.uniform(-.04, .06)))
        if index == p == 0:
            temp, hazard = 22.4, .06
        planets.append(dict(
            id=f"s{index}-p{p}", index=p, system_id=index, name=name, seed=seed,
            biome=biome_id, description=biome["description"], temperature=temp,
            hazard=round(hazard, 3), gravity=round(prng.uniform(8.5, 14.8), 2),
            sky=biome["sky"], ground=biome["ground"], accent=biome["accent"],
            flora=biome["flora"], water=biome["water"],
            resources=list(biome["resources"]), day_length=round(prng.uniform(360, 720), 1),
            water_level=biome["water_level"],
            position=(round(math.sin(angle) * distance, 3), round(math.cos(angle) * distance, 3), round(rng.uniform(-320, 420), 3)),
            size=round(prng.uniform(190, 320), 1),
            atmosphere=("Calm" if hazard < .2 else "Variable" if hazard < .5 else "Severe"),
            sentinel_activity="Survey drones only", fauna_density=round(prng.uniform(.5, 1.5), 2),
        ))
    return dict(id=index, name=_SYSTEM_NAMES[index], star_color=_STAR_COLORS[index % len(_STAR_COLORS)],
                planets=planets, station=(0.0, 1150.0, 170.0),
                description=f"{_ECONOMIES[index % len(_ECONOMIES)]} · 4 charted worlds",
                economy=_ECONOMIES[index % len(_ECONOMIES)],
                coordinates=((index % 6) * 140 + 35 * math.sin(index), (index // 6) * 170 + 25 * math.cos(index)))


def generate_system(index):
    """Return an independent chart; callers cannot poison the cached universe."""
    return copy.deepcopy(_system(_index(index, SYSTEM_COUNT, "system index")))


def galaxy_catalog():
    return [dict(id=i, name=_SYSTEM_NAMES[i], description=f"{_ECONOMIES[i % len(_ECONOMIES)]} · 4 worlds",
                 coordinates=_system(i)["coordinates"], star_color=_system(i)["star_color"])
            for i in range(SYSTEM_COUNT)]


def get_planet(system_id, planet_index):
    _index(system_id, SYSTEM_COUNT, "system index")
    _index(planet_index, PLANETS_PER_SYSTEM, "planet index")
    return copy.deepcopy(_system(system_id)["planets"][planet_index])


def planet_from_id(planet_id):
    """Resolve a canonical planet ID, raising ValueError for malformed IDs."""
    if not isinstance(planet_id, str):
        raise ValueError("Invalid planet ID")
    try:
        left, right = planet_id.split("-p")
        system_id, planet_index = int(left[1:]), int(right)
        if planet_id != f"s{system_id}-p{planet_index}":
            raise ValueError
        return get_planet(system_id, planet_index)
    except (ValueError, TypeError, IndexError):
        raise ValueError("Invalid planet ID") from None


def terrain_height(seed, x, y):
    """Smooth bounded terrain, with a broad dry and nearly flat landing site.

    This exact function is shared by world meshing and movement. The central
    42-unit disk is 20 units above datum; all ocean water is below that height.
    No random state or Python hash seed participates in the sample.
    """
    try:
        x, y = float(x), float(y)
    except (TypeError, ValueError, OverflowError):
        return 20.0
    if not math.isfinite(x) or not math.isfinite(y):
        return 20.0
    # Limit enormous input before trigonometry; ordinary play is unaffected.
    x, y = max(-1e7, min(1e7, x)), max(-1e7, min(1e7, y))
    try:
        phase = (int(seed) % 99991) * .00173
    except (TypeError, ValueError, OverflowError):
        phase = 0.0
    broad = 9.2 * math.sin(x * .0062 + phase) * math.cos(y * .0071 - phase * .73)
    ridges = 6.0 * math.sin((x + y) * .012 + phase * 1.3)
    rolling = 3.0 * math.cos(x * .031 - y * .018 + phase * .5)
    detail = 1.1 * math.sin(x * .076 + phase) * math.cos(y * .063 + phase)
    natural = 18.0 + broad + ridges + rolling + detail
    radius = math.hypot(x, y)
    blend = max(0.0, min(1.0, (radius - 42.0) / 150.0))
    blend = blend * blend * (3.0 - 2.0 * blend)
    return 20.0 + (natural - 20.0) * blend
