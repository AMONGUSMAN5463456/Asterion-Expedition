# Validation report — Asterion Expedition 1.1.0

Validated on 9 September 2026 using Linux, CPython 3.12.14, Panda3D 1.10.16,
and the bundled offscreen software renderer. Tests use temporary expedition
saves and do not read or modify a normal player save.

**182 automated tests passed.** A separate smoke run from a freshly extracted
release ZIP passed all **25 checks**. The ZIP entries and every SHA-256 entry in
`MANIFEST.sha256` were verified. Documentation was then updated with these
results and the final ZIP and manifest regenerated; gameplay code was unchanged.

| Suite | Passing tests | Coverage |
| --- | ---: | --- |
| Collision | 34 | Continuous capsule/sphere sweeps, rotated boxes, cylinders, thin walls, sliding, acute corners, steps, ceilings, roofs, overlap recovery, spatial groups and raycasts |
| Controller | 17 | Keyboard/mouse input, frame-rate consistency, walking/sprint, jumps, jetpack, flight, terrain, fuel, focus loss and cleanup |
| Movement upgrade | 20 | Solid-world integration, low steps, roofs, fast ship collision, assist/drift, air brake/hover, jump buffering and camera comfort settings |
| Graphics | 15 | Eight biome palettes and actual 512×256 orbital texture renders, white-parent material resistance, seams/poles, UV joins, habitat door alignment and collision lifetime |
| Native integration | 24 | Real app/UI callbacks, mining occlusion, ship collision, obstructed construction, safe spawn/landing recovery, travel, trade, save compatibility and settings |
| Interface/equipment | 9 | HUD bounds, tool visibility, movement feedback, menu callbacks, scrolling, camera-motion-off and lifecycle |
| Route planning | 14 | Clear segments around multiple/overlapping spheres, tangent endpoints, deterministic routes, malformed input and bounded work |
| Packaging | 16 | Dependency setup, offline relaunch, launch scripts, argument forwarding, audio decoding and font license |
| State/universe | 26 | All 96 worlds, content references, economy, cargo, progression, contracts, validated saves and backup recovery |
| World/geometry | 7 | Stable generation, heightfield seams, interaction identities, ship bounds, streaming, depletion and scene cleanup |

## Issues corrected in this update

- Explicit white ambient/diffuse materials could replace mesh colours, while
  excessive combined illumination washed out the remaining contrast.
- Procedural non-power-of-two planet textures failed to load in TinyDisplay;
  orbital maps now use supported 512×256 or 256×128 dimensions.
- Acute corner contacts could create sideways movement and leave a player stuck.
- Short movement steps could stall on a low tread or incorrectly turn walking
  speed into upward velocity.
- Old saves embedded in new solid geometry could recover beneath the terrain.
- Failed parking could reuse an unrelated old ship location or allow boarding
  an absent ship.
- Visual habitat openings needed to match the real passable collision opening.
- Solid objects needed to block extraction; depleted/unloaded objects needed
  their collision removed with their rendered geometry.

## Repeating the checks

Run from the extracted folder with Panda3D installed:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python main.py --smoke-test --software --no-audio
```

On Windows use `.venv\Scripts\python.exe` in place of `.venv/bin/python`.
The built-in smoke check creates a fresh expedition, constructs twelve menu
panels, surveys/mines/crafts, boards, opens orbit and a route, lands, saves,
restores, and trades. Dedicated integration tests also fly complete automatic
approaches and verify collision-related gameplay changes.

## Visual verification and limits

Actual game frames are included for the title, surface, orbit/cockpit,
fabrication, journal, construction, navigation, settings, and all eight biome
families in `screenshots/`. The surface and orbital views were visually
inspected. Automated rendered checks verify colour survives an intentionally
white and excessively lit parent scene, including the dark volcanic palette.

The environment has no desktop display or hardware audio. macOS/Windows live
input, Retina scaling, hardware GPU output, driver compatibility and desktop
frame rates were not tested. Software-rendered images have limited filtering
and antialiasing. These checks establish the exercised behavior, not a promise
that every possible interaction is bug-free.

Collision uses simple approximations of visible solids. Light foliage and
fauna remain non-solid; terrain is a heightfield with no caves or overhangs.
The route planner is bounded and can report no route in a crowded arrangement.
Surface worlds and orbit remain separate scenes. See README.md for the game's
content scope and platform setup.
