"""Original, data-driven content for Asterion Expedition.

This module deliberately has no engine dependencies. Colors are linear-looking
RGB triples in the range 0..1; quantities are individual cargo units.
"""


def _item(name, description, category, value, color):
    return dict(name=name, description=description, category=category,
                value=value, color=color)


ITEMS = {
    "ferrite": _item("Ferrite", "A sturdy structural mineral. Mine rust-colored stone deposits.", "Mineral", 9, (.63, .36, .23)),
    "carbon": _item("Carbon", "Fibrous material from alien plants. Used in cells and construction.", "Organic", 7, (.23, .70, .40)),
    "oxygen": _item("Oxygen", "Suit-compatible gas sealed by the harvester. Use to refill oxygen.", "Survival", 12, (.45, .84, .95)),
    "sodium": _item("Sodium", "Reactive grains that restore environmental protection when used.", "Survival", 14, (.98, .76, .21)),
    "copper": _item("Copper", "Conductive ore for ship systems and expedition equipment.", "Mineral", 22, (.89, .48, .24)),
    "crystal": _item("Prism Crystal", "A clean lattice that bends light into delicate ribbons.", "Mineral", 36, (.45, .71, 1.0)),
    "cobalt": _item("Cobalt", "Dense blue ore used by precision assemblies.", "Mineral", 30, (.24, .39, .93)),
    "gold": _item("Gold", "A rare, stable conductor. Valuable at orbital exchanges.", "Mineral", 65, (1.0, .77, .29)),
    "silicon": _item("Silicon", "A glass-forming mineral abundant on dry worlds.", "Mineral", 19, (.76, .78, .82)),
    "sulfur": _item("Sulfur", "Bright mineral clusters warmed by deep planetary vents.", "Mineral", 24, (.92, .80, .18)),
    "mycelium": _item("Lumen Fiber", "Soft luminous threads harvested from hardy fungal growth.", "Organic", 27, (.76, .40, .84)),
    "pearl": _item("Tidal Pearl", "Layered shell material that stores a faint electric charge.", "Organic", 58, (.68, .96, .90)),
    "relic": _item("Archive Fragment", "A durable memory tile recovered from an ancient signal site.", "Artifact", 220, (.98, .80, .47)),
    "alloy": _item("Structural Alloy", "Lightweight refined plate for habitats and durable equipment.", "Component", 44, (.63, .70, .77)),
    "glass": _item("Optical Glass", "Precisely cast glass for lenses and observatories.", "Component", 62, (.68, .89, .96)),
    "wire": _item("Conductive Wire", "A carefully insulated spool for electronics and power grids.", "Component", 56, (.89, .59, .35)),
    "circuit": _item("Logic Circuit", "A reliable controller assembled from copper and crystal.", "Component", 160, (.30, .92, .73)),
    "ceramic": _item("Thermal Ceramic", "A heat-resistant composite for harsh-environment installations.", "Component", 86, (.83, .72, .57)),
    "bio_polymer": _item("Bio Polymer", "A resilient organic sealant for advanced suit equipment.", "Component", 110, (.47, .83, .52)),
    "quantum_core": _item("Fold Core", "A stabilized lattice that keeps distant coordinates in agreement.", "Component", 530, (.79, .56, 1.0)),
    "atlas_key": _item("Meridian Key", "A reconstructed archive key, written in the language of star positions.", "Artifact", 920, (.96, .84, .49)),
    "fuel_cell": _item("Launch Cell", "Restores 45 ship fuel. Used by surface launches and engine boost.", "Consumable", 85, (.99, .61, .29)),
    "warp_cell": _item("Fold Cell", "Restores 85 ship fuel. Interstellar folding consumes one cell.", "Consumable", 170, (.70, .47, 1.0)),
    "life_gel": _item("Life Gel", "Restores 85 suit oxygen with a compact recycling membrane.", "Consumable", 58, (.40, .93, .82)),
    "ion_cell": _item("Ion Cell", "Restores 75 hazard protection and 55 equipment energy.", "Consumable", 76, (.44, .72, 1.0)),
    "repair_kit": _item("Repair Kit", "Restores 65 shield integrity using self-aligning plates.", "Consumable", 115, (.99, .74, .39)),
}


def _recipe(name, description, inputs, outputs=None, upgrade=None, tier=None):
    result = dict(name=name, description=description, inputs=inputs,
                  outputs=outputs or {})
    if upgrade:
        result.update(upgrade=upgrade, tier=tier or 1)
    return result


RECIPES = {
    "alloy": _recipe("Structural Alloy", "Refine raw ferrite into two construction plates.", {"ferrite": 6, "carbon": 2}, {"alloy": 2}),
    "glass": _recipe("Optical Glass", "Cast three clear optical panels.", {"silicon": 5, "crystal": 1}, {"glass": 3}),
    "wire": _recipe("Conductive Wire", "Draw copper into three insulated spools.", {"copper": 4, "carbon": 2}, {"wire": 3}),
    "circuit": _recipe("Logic Circuit", "Build a precision controller.", {"wire": 2, "crystal": 2, "cobalt": 1}, {"circuit": 1}),
    "ceramic": _recipe("Thermal Ceramic", "Fire a heat-resistant ceramic composite.", {"silicon": 4, "ferrite": 3, "sulfur": 1}, {"ceramic": 2}),
    "bio_polymer": _recipe("Bio Polymer", "Weave living fibers into three stable seals.", {"mycelium": 3, "carbon": 5, "oxygen": 2}, {"bio_polymer": 3}),
    "quantum_core": _recipe("Fold Core", "Align a precision lattice around a gold conductor.", {"crystal": 6, "gold": 3, "circuit": 2}, {"quantum_core": 1}),
    "atlas_key": _recipe("Meridian Key", "Reassemble archive fragments into a map of the listening network.", {"relic": 3, "quantum_core": 1, "glass": 2}, {"atlas_key": 1}),
    "fuel_cell": _recipe("Launch Cell", "Pack enough energy for several departures.", {"carbon": 5, "ferrite": 3}, {"fuel_cell": 1}),
    "warp_cell": _recipe("Fold Cell", "A charged lattice for passage between star systems.", {"copper": 5, "crystal": 3, "carbon": 4}, {"warp_cell": 1}),
    "life_gel": _recipe("Life Gel", "Store breathable air in a reusable gel membrane.", {"oxygen": 4, "carbon": 2}, {"life_gel": 1}),
    "ion_cell": _recipe("Ion Cell", "Charge an environmental protection battery.", {"sodium": 4, "cobalt": 1}, {"ion_cell": 1}),
    "repair_kit": _recipe("Repair Kit", "Assemble emergency shield repair plates.", {"alloy": 2, "sodium": 2}, {"repair_kit": 1}),
    "oxygen_recycle": _recipe("Oxygen Recovery", "Release stored oxygen from a life gel membrane.", {"life_gel": 1}, {"oxygen": 3}),
    "crystal_grow": _recipe("Crystal Cultivation", "Grow crystal from common mineral salts.", {"silicon": 5, "sodium": 3}, {"crystal": 2}),
    "cobalt_refine": _recipe("Cobalt Separation", "Recover trace cobalt from copper-rich minerals.", {"copper": 5, "ferrite": 4}, {"cobalt": 2}),
    "mining_1": _recipe("Survey Laser I", "Increase the extraction rate of your mining beam.", {"copper": 8, "crystal": 4, "alloy": 2}, upgrade="mining", tier=1),
    "mining_2": _recipe("Survey Laser II", "Install a faster adaptive mineral focus.", {"circuit": 2, "gold": 4, "alloy": 4}, upgrade="mining", tier=2),
    "mining_3": _recipe("Survey Laser III", "Fit a masterwork focus for rapid field collection.", {"quantum_core": 1, "circuit": 3, "crystal": 10}, upgrade="mining", tier=3),
    "jetpack_1": _recipe("Vector Pack I", "Improve jetpack efficiency and lift.", {"copper": 6, "carbon": 10, "alloy": 3}, upgrade="jetpack", tier=1),
    "jetpack_2": _recipe("Vector Pack II", "Stabilize extended climbs and traverses.", {"circuit": 2, "ceramic": 3, "cobalt": 5}, upgrade="jetpack", tier=2),
    "jetpack_3": _recipe("Vector Pack III", "Fit a precision inertial return system.", {"quantum_core": 1, "bio_polymer": 5, "gold": 6}, upgrade="jetpack", tier=3),
    "hazard_1": _recipe("Climate Weave I", "Reduce the cost of exploring hostile climates.", {"sodium": 10, "carbon": 8, "alloy": 2}, upgrade="hazard", tier=1),
    "hazard_2": _recipe("Climate Weave II", "Add reactive heat and toxin insulation.", {"ceramic": 4, "bio_polymer": 3, "circuit": 1}, upgrade="hazard", tier=2),
    "hazard_3": _recipe("Climate Weave III", "Build an expedition-grade sealed environment layer.", {"quantum_core": 1, "ceramic": 6, "pearl": 4}, upgrade="hazard", tier=3),
    "cargo_1": _recipe("Cargo Lattice I", "Expand cargo capacity by 120 units.", {"ferrite": 16, "copper": 6, "alloy": 3}, upgrade="cargo", tier=1),
    "cargo_2": _recipe("Cargo Lattice II", "Expand cargo capacity by another 120 units.", {"alloy": 8, "wire": 5, "circuit": 2}, upgrade="cargo", tier=2),
    "cargo_3": _recipe("Cargo Lattice III", "Expand cargo capacity to 600 units.", {"quantum_core": 1, "alloy": 12, "bio_polymer": 5}, upgrade="cargo", tier=3),
    "engine_1": _recipe("Helix Drive I", "Improve cruise thrust and reduce launch fuel use.", {"copper": 10, "crystal": 5, "alloy": 3}, upgrade="engine", tier=1),
    "engine_2": _recipe("Helix Drive II", "Install a higher-efficiency folded intake.", {"circuit": 3, "cobalt": 6, "ceramic": 3}, upgrade="engine", tier=2),
    "engine_3": _recipe("Helix Drive III", "Fit the expedition's finest compact drive.", {"quantum_core": 2, "gold": 8, "alloy": 8}, upgrade="engine", tier=3),
    "scanner_1": _recipe("Deep Scanner I", "Increase scan range and discovery rewards.", {"crystal": 6, "copper": 6, "carbon": 4}, upgrade="scanner", tier=1),
    "scanner_2": _recipe("Deep Scanner II", "Read distant life signatures and ancient materials.", {"glass": 4, "circuit": 2, "gold": 3}, upgrade="scanner", tier=2),
    "scanner_3": _recipe("Deep Scanner III", "Install a full-spectrum survey lens.", {"quantum_core": 1, "glass": 8, "pearl": 5}, upgrade="scanner", tier=3),
}

UPGRADE_NAMES = {
    "mining": "Survey Laser", "jetpack": "Vector Pack", "hazard": "Climate Weave",
    "cargo": "Cargo Lattice", "engine": "Helix Drive", "scanner": "Deep Scanner",
}

BUILDINGS = {
    "beacon": {"name": "Survey Beacon", "description": "A permanent illuminated marker for your expedition outpost.", "cost": {"ferrite": 10, "copper": 4}},
    "habitat": {"name": "Field Habitat", "description": "A sealed shelter. Approach to restore oxygen and weather protection.", "cost": {"alloy": 6, "carbon": 12, "glass": 2}},
    "solar": {"name": "Solar Array", "description": "A silent collector. Nearby arrays help restore equipment energy.", "cost": {"alloy": 3, "copper": 8, "crystal": 4}},
    "extractor": {"name": "Mineral Extractor", "description": "Collects local ore over time. Claim stored ore from the construction panel.", "cost": {"alloy": 6, "copper": 10, "circuit": 1}},
    "storage": {"name": "Cargo Vault", "description": "A durable supply landmark and home for your expedition's spare equipment.", "cost": {"alloy": 5, "ferrite": 10}},
    "landing_pad": {"name": "Landing Platform", "description": "A clear, illuminated platform for your ship and survey team.", "cost": {"alloy": 10, "copper": 8, "wire": 3}},
    "observatory": {"name": "Sky Observatory", "description": "An optical survey dome dedicated to the planet's changing sky.", "cost": {"alloy": 8, "glass": 6, "circuit": 2}},
    "garden": {"name": "Lumen Garden", "description": "Cultivates carbon-bearing plants. Harvest from the construction panel.", "cost": {"carbon": 15, "oxygen": 5, "alloy": 2}},
    "relay": {"name": "Meridian Relay", "description": "Reconnect a small part of the ancient listening network.", "cost": {"alloy": 6, "circuit": 3, "crystal": 8, "relic": 1}},
    "lamp": {"name": "Path Light", "description": "A warm light for the paths between buildings.", "cost": {"ferrite": 4, "sodium": 2}},
}


def _biome(name, description, sky, ground, accent, flora, water, temperature,
           hazard, resources, water_level):
    return dict(name=name, description=description, sky=sky, ground=ground,
                accent=accent, flora=flora, water=water, temperature=temperature,
                hazard=hazard, resources=resources, water_level=water_level)


BIOMES = {
    "verdant": _biome("Emerald Expanse", "Rolling jade uplands and copper-leaf forests beneath a broad blue sky.", (.32, .62, .79), (.20, .37, .25), (.96, .64, .31), (.24, .66, .41), (.10, .39, .52), 23, .08, ["ferrite", "carbon", "oxygen", "sodium", "copper", "crystal"], 4),
    "desert": _biome("Saffron Dunes", "Layered ochre ridges surround bright mineral flats and resilient brush.", (.70, .45, .32), (.62, .37, .18), (1.0, .76, .37), (.46, .55, .30), (.23, .40, .38), 54, .36, ["ferrite", "carbon", "oxygen", "sodium", "silicon", "copper", "gold"], -8),
    "frozen": _biome("Glacial Silence", "Silver plains carry slow blue shadows beneath a pale, luminous sky.", (.35, .57, .74), (.63, .75, .81), (.47, .83, 1.0), (.41, .60, .70), (.16, .36, .55), -48, .47, ["ferrite", "carbon", "oxygen", "sodium", "cobalt", "crystal"], 2),
    "volcanic": _biome("Ember Reach", "Dark basalt islands and amber vents reveal an energetic young world.", (.33, .21, .23), (.19, .17, .20), (1.0, .38, .10), (.62, .32, .25), (.48, .13, .08), 92, .64, ["ferrite", "carbon", "oxygen", "sodium", "sulfur", "gold", "copper"], -12),
    "toxic": _biome("Viridian Haze", "Chartreuse mists drift through sprawling, unusually geometric plant life.", (.49, .58, .29), (.32, .32, .20), (.81, .96, .31), (.53, .63, .27), (.32, .46, .19), 39, .56, ["ferrite", "carbon", "oxygen", "sodium", "sulfur", "mycelium", "cobalt"], 3),
    "oceanic": _biome("Tidal Mosaic", "Turquoise lagoons separate wind-shaped islands and pale coral growths.", (.25, .65, .76), (.43, .52, .38), (.93, .80, .58), (.25, .69, .59), (.06, .43, .56), 18, .15, ["ferrite", "carbon", "oxygen", "sodium", "pearl", "silicon", "copper"], 11),
    "crystalline": _biome("Prism Barrens", "Violet stone supports transparent spires that hum in the planetary wind.", (.35, .28, .56), (.33, .27, .43), (.55, .82, 1.0), (.65, .43, .81), (.25, .27, .54), 7, .28, ["ferrite", "carbon", "oxygen", "sodium", "crystal", "cobalt", "gold"], 0),
    "fungal": _biome("Lumen Canopy", "Great parasol groves glow above a soft carpet of luminous fibers.", (.29, .30, .48), (.28, .25, .37), (.95, .50, .77), (.65, .36, .71), (.23, .28, .44), 29, .24, ["ferrite", "carbon", "oxygen", "sodium", "mycelium", "crystal", "copper"], 5),
}

STORY = [
    {"title": "01 / A signal in the grass", "description": "Mine 20 resource units. The courier ship Asterion has found a repeating signal where the charts show silence.", "event": "mined", "target": 20, "reward": 100, "lore": "Your receiver repeats three notes. A fourth arrives from somewhere beyond the next horizon."},
    {"title": "02 / Learn the living world", "description": "Scan 3 plants, minerals, or creatures with your survey scanner.", "event": "scanned", "target": 3, "reward": 150, "lore": "The signal adjusts whenever a new lifeform enters your catalog. Someone built this network to listen."},
    {"title": "03 / Hands of the expedition", "description": "Craft any recipe in the Fabricator. Launch Cells use carbon and ferrite.", "event": "crafted", "target": 1, "reward": 150, "lore": "An old maker's mark appears in your fabricator. It resembles an open door."},
    {"title": "04 / The sky opens", "description": "Board your ship and launch from the surface. Climb toward the stars.", "event": "launched", "target": 1, "reward": 200, "lore": "The planet falls away. From above, the signal forms a perfect arc toward a neighboring world."},
    {"title": "05 / Two points make a path", "description": "Visit 2 different planets using the system navigation chart.", "event": "visited", "target": 2, "reward": 250, "lore": "Two worlds share the same phrase: We kept a place for those still traveling."},
    {"title": "06 / The patient archive", "description": "Explore a Signal Ruin and recover an archive fragment.", "event": "ruins", "target": 1, "reward": 350, "lore": "The Meridian builders measured distance in stories carried from one world to another."},
    {"title": "07 / Fold the distance", "description": "Travel to another star system. A Fold Cell powers the jump.", "event": "warped", "target": 1, "reward": 400, "lore": "Between the stars, your receiver is briefly quiet. Then a thousand quiet notes find each other."},
    {"title": "08 / Equipment for tomorrow", "description": "Install any suit, tool, cargo, scanner, or engine upgrade in the Fabricator.", "event": "upgrades", "target": 1, "reward": 400, "lore": "A workshop record describes the Asterion as a promise: every traveler should be able to return."},
    {"title": "09 / Names of the wandering", "description": "Scan 3 different alien creatures. Look for moving life signatures.", "event": "fauna", "target": 3, "reward": 450, "lore": "The network's oldest records are careful drawings of small creatures. Curiosity came before the starships."},
    {"title": "10 / A light to return to", "description": "Construct a building on any planet. A Survey Beacon needs ferrite and copper.", "event": "built", "target": 1, "reward": 450, "lore": "Your beacon adds one bright point to a map that has waited a very long time."},
    {"title": "11 / The makers' route", "description": "Craft 8 recipes in total and learn the rhythm of the expedition economy.", "event": "crafted", "target": 8, "reward": 500, "lore": "Every station keeps different materials. The builders relied on exchanges, not a single capital."},
    {"title": "12 / A wider constellation", "description": "Visit 5 distinct planets. Every biome brings different materials and weather.", "event": "visited", "target": 5, "reward": 600, "lore": "Five coordinates describe a spiral. Its center is not a location. It is a meeting."},
    {"title": "13 / The shared archive", "description": "Recover records from 3 separate Signal Ruins.", "event": "ruins", "target": 3, "reward": 650, "lore": "The Meridian network was never abandoned. Its builders left it open for whoever came next."},
    {"title": "14 / Farther than the chart", "description": "Complete 3 interstellar folds in total.", "event": "warped", "target": 3, "reward": 750, "lore": "New coordinates bloom around familiar ones. There is room on the map for every journey."},
    {"title": "15 / A home among the signals", "description": "Construct 4 buildings in total. Habitats, gardens, and extractors support longer journeys.", "event": "built", "target": 4, "reward": 800, "lore": "The archive calls a settlement a conversation with a place. Yours has begun."},
    {"title": "16 / Forty little wonders", "description": "Catalog 40 unique plants, minerals, and creatures.", "event": "scanned", "target": 40, "reward": 1000, "lore": "The missing fourth note was never missing. It is the sound of a new observer joining in."},
    {"title": "17 / The Meridian key", "description": "Craft a Meridian Key using archive fragments, a Fold Core, and optical glass.", "event": "crafted_atlas_key", "target": 1, "reward": 1500, "lore": "The key opens no vault. It grants your ship permission to add its own stories to the network."},
    {"title": "18 / The expedition continues", "description": "Visit 12 different planets and leave a lasting survey of the Asterion Reach.", "event": "visited", "target": 12, "reward": 2500, "lore": "Your first message is simple: The path is open. Come see what we found. Beyond it, ninety-six worlds keep turning."},
]

CONTRACT_TEMPLATES = [
    {"id": "ore", "name": "Mineral Survey", "description": "Collect fresh resource units for the station's field survey.", "event": "mined", "target": 55, "reward": 440},
    {"id": "catalog", "name": "Local Curiosities", "description": "Scan previously uncataloged plants, minerals, or creatures.", "event": "scanned", "target": 5, "reward": 550},
    {"id": "ecology", "name": "A Living Census", "description": "Catalog new creatures for the ecological atlas.", "event": "fauna", "target": 2, "reward": 620},
    {"id": "maker", "name": "Workshop Practice", "description": "Complete fabricator recipes and record their field performance.", "event": "crafted", "target": 4, "reward": 420},
    {"id": "archive", "name": "Open Memory", "description": "Investigate a new Signal Ruin and retrieve its record.", "event": "ruins", "target": 1, "reward": 700},
    {"id": "route", "name": "Chart Another Shore", "description": "Land on a previously unvisited planet.", "event": "visited", "target": 1, "reward": 380},
]

FAUNA_PREFIXES = ("Ribbon", "Glass", "Amber", "Crown", "Pebble", "Velvet", "Lantern", "Dusky", "Opal", "Cloud", "Needle", "Moss")
FAUNA_SUFFIXES = ("strider", "grazer", "hopper", "drifter", "trotter", "glider", "forager", "shellback")
RUIN_LORE = [
    "A wall of shallow bowls collected the rain. Each was labeled with the name of a distant sea.",
    "The archive contains a recipe for a festival bread and a map to seven kitchens among the stars.",
    "An astronomer corrected a star chart in the margin: Allow for the worlds we have not found yet.",
    "A tiny museum preserves impressions of leaves from worlds with very different suns.",
    "The station's final log records a successful launch. A new destination was added in a different hand.",
    "An instrument measures no known unit. When the wind crosses it, the receiver's three notes return.",
    "A child once drew a ship with an impossible number of windows. The archive kept the drawing.",
    "Rows of empty nameplates await new entries. The oldest is engraved: Traveler.",
    "A maintenance manual ends with a promise to leave the lights on for the next expedition.",
    "The relay's stored message is addressed to a world that had not yet been discovered when it was sent.",
    "A garden plan marks where shade will fall a hundred years after the trees are planted.",
    "A circle of listening posts points outward. This place was built to welcome new signals.",
]
