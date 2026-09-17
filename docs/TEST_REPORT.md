# Validation report — Asterion Expedition 1.3.0

Release validation: 17 September 2026. Host: Apple Silicon, macOS 26.3.1,
CPython 3.12.14 and the pinned Panda3D 1.10.16 runtime. Tests use disposable
save directories; the normal player expedition was not opened or modified.

## Actual hardware descents

Five descents drove the real ExpeditionApp controller and collision system
from **7,000 m above the surface at 900 m/s**, with native Cocoa/OpenGL
hardware rendering. After each scenario's starting pose, movement used normal
application ticks, reframing and swept collisions. No position teleport or
surface loader was used during the descent. Solar and planet scene roots
remained unchanged, no entry fade occurred, and both orbit and flight modes
were traversed. Each flight also saved and validated its in-air momentum.

| Scenario | Surface | Hull clearance at contact | Result |
| --- | --- | --- | --- |
| talora-land | land | 3.0248 m | F touchdown to walking |
| deep-ocean | ocean | 3.0293 m | Hull contact |
| far-hemisphere | land | 3.0262 m | Hull contact |
| north-pole | land | 3.0294 m | Hull contact |
| south-pole | land | 3.0235 m | Hull contact |

All five reached contact in 7.8 simulated seconds. The largest per-tick travel
was approximately 30 m, matching 900 m/s at a 30 Hz app tick. The ship collider
has a 3 m radius. Talora's normal F landing then parked the ship and returned
to walking; its final frame shows the on-foot tool and HUD.

[Machine-readable descent measurements](validation-v1.3/descent-report.json)
and 46 rendered images are included. Filename altitudes are capture thresholds;
the JSON records actual altitude and frame number. Representative views:

- [Talora from orbit](validation-v1.3/talora-land-6900m.png)
- [Inside the home cloud bank](validation-v1.3/talora-land-0650m.png)
- [Below the clouds](validation-v1.3/talora-land-0120m.png)
- [Deep-ocean hull contact](validation-v1.3/deep-ocean-0005m.png)
- [Completed on-foot touchdown](validation-v1.3/talora-touchdown.png)

Native startup images were also inspected. Balanced terrain refinement removed
a rectangular coarse/fine skirt visible at the ground horizon. A separate review
of 12 hardware captures confirmed that crossed cloud strips and orbital depth
speckling are absent. Cloud haze is localized to actual banks and clears below
them; oceans and far-side approaches have clear air outside those volumes.

## Regression and gameplay checks

All **276 tests passed** in the final release run (166.8 seconds wall time). The run executes every test module in a
fresh Python process, using three workers; this isolates Panda3D global font
and graphics state between modules. Per-module counts, times and pass/fail
results are in the [regression report](validation-v1.3/regression-report.json).
The equivalent standard discovery command is:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Coverage includes:

- Shared land/ocean elevation, high-speed sweeps, both poles, cube seams,
  steep coastal banks, underwater recovery and non-solid atmosphere.
- Complete globe coverage, balanced neighboring detail levels, atomic child
  swaps, orbital refinement before frame changes and near-ground mesh accuracy.
- Fixed 3D cloud positions, finite cloud depth, parallax, cloud-density continuity,
  planetary occlusion, software rendering and bounded object counts.
- Actual app ascent/descent, automatic approach and landing, flight cancellation,
  save/load, held-input/attitude preservation, mining depletion and construction.
- Cargo, crafting, trade, survival, story, contracts, navigation, controls,
  collision with props/buildings, interface, launchers, fonts and audio assets.
- Save versions 1–4 migrating into schema 5 once; flight altitude/momentum,
  nearby distant bases, antipodal bases, parked-ship hemispheres, station
  clearance, large coordinates and atomic backup failure behavior.
- Entry detail starts at the live ship position rather than a stale autosave
  position, avoiding ground-detail initialization during an orbital approach.

The built-in smoke test passed **25 checks**, including mining, crafting,
boarding, a physical automatic interplanetary approach, landing, saving,
reloading and trading. Its [JSON result](validation-v1.3/smoke-report.json)
is included. Reproduce with:

```sh
.venv/bin/python main.py --smoke-test --software --no-audio
.venv/bin/python tools/validate_descents.py --hardware
```

Omit `--hardware` to run descent capture through Panda3D TinyDisplay. Python
compilation and POSIX launcher syntax checks also passed.

## Save and release integrity

Schema 5 stores `world_scale: 96`. Original saves are retained once as
`expedition.json.pre-v1.3.bak`; if creating that archive fails, the original
primary is not replaced. The rolling `.bak` backup still operates normally.
Home bases retain metre coordinates; nearby remote construction stays near the
saved frame and all constructed nodes are placed on current land/water elevation.
Cargo, credits, upgrades, discoveries, depletion IDs and progression remain.
Expanded remote procedural scenery can change deposit placement/availability.

The source ZIP has a single `asterion-expedition-v1.3.0/` root. Its builder
verifies ZIP CRCs and every SHA-256 manifest entry and writes an external ZIP
checksum. Virtual environments, Python caches, player saves, local logs and
previous release ZIPs are excluded. Launch script executable permissions are
included. The extracted archive is checked independently before delivery.

## Measured limits

These are automated real-app flights and rendered frames, not a manual keyboard
playthrough. Native hardware graphics were checked on this Mac; Windows/Linux
desktop input, hardware audio and other GPU/driver combinations are unverified.

The native descent run measured median app update times of roughly 9–14 ms,
with 95th-percentile updates of roughly 68–154 ms while new detail/props streamed.
These numbers exclude most display frames and do not establish a desktop frame
rate. Streaming can still cause brief stalls. A fresh scene prepares terrain
before showing the player and can take several seconds.

The game remains a stylized source distribution requiring Python and a first-run
Panda3D download. Water supports surface contact; there is no swimming, seabed
exploration, caves, overhangs or ray-traced atmosphere. Cloud banks are textured
billboards arranged at fixed spatial depths with matching local-density samples.
