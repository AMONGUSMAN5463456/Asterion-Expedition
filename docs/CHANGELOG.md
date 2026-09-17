# Version 1.3.0 — living horizons

- Expanded physical diameters and system distances fourfold (planets about
  36–61 km across), retaining catalog seeds, IDs, names, economy and story.
- Terrain now refines according to observer distance from orbit onward, with
  complete globe coverage at every altitude and atomic coarse/fine patch swaps.
  Tile-relative geometry retains small relief on large planets; slope lighting
  reveals hills and radial skirts close mixed-detail edges.
- Added persistent cloud banks with finite vertical depth, translucent puffs,
  spatial parallax, local cloud-density fog and atmospheric limb haze.
- Unified visible water and solid collision elevations. Fast descents stop on
  water rather than its seabed; shoreline contacts test land and water separately
  so steep banks cannot overlap a ship hull.
- Kept walking, mining, crafting, bases, scanning, missions, manual flight,
  autopilot, comfort settings and the existing expedition save directory.
- Save schema 5 records world scale. One-time migration preserves flight datum
  altitude and momentum, station approach clearance, home base metre positions,
  and nearby remote construction. The original save is archived as
  `expedition.json.pre-v1.3.bak` before the first upgraded save.
- Added repeatable real-application descent validation with rendered altitude
  checkpoints and JSON results, plus terrain, weather, shoreline and save tests.

Resource IDs and depletion dictionaries remain stable. Enlarging the terrain
regenerates remote scenery; individual far-away deposits can change position or
availability. Constructed bases and expedition progress remain in the save.

See TEST_REPORT.md for measured checks and their limits.

---

# Version 1.2.0 — continuous horizons

- Replaced the ground/orbit scene swap with one persistent system of spherical
  planets. Ascent, re-entry and far-hemisphere exploration are physical flights.
- Increased planets and system distances 24× while keeping seeds, IDs, economies,
  names and content stable. Nearby terrain and distant globes share geography.
- Added progressive globe detail, curved near terrain, stable geographic resource
  addresses, persistent building positions, and fixed planetary orientations.
- Added cloud-band wisps, directional entry plasma, blue ascent/exit streaks,
  continuous atmospheric haze and star visibility, wind and rumble, and a flight
  phase/density/radial-altitude HUD. Camera-motion-off remains available.
- Removed the 420 m orbital teleport, solid atmosphere shells and F-entry portal.
  F now lands a slow, low craft; manual pilots can descend anywhere.
- Added rigid tangent reference frames that preserve world position, velocity,
  full camera attitude, held input, throttle and comfort effects while changing.
- Blended atmospheric/orbital thrust and damping continuously; full attitude
  steering and saves remain valid at the poles.
- Unified ship sweeps across real spherical terrain, surface props, asteroids and
  station solids. Ground collision and walking/jetpack controls remain active.
- Automatic approaches physically climb, route around bodies and descend to the
  survey district. They can be cancelled back to manual flight.
- Save schema 4 stores the active frame, flight velocity and roll. In-air saves
  resume in flight. Legacy orbital coordinates migrate once; old home saves,
  discoveries, materials, construction and progression remain supported.

See TEST_REPORT.md for exercised behavior and platform limitations.

---

# Version 1.1.0 — worlds in colour

## Graphics

- Fixed explicit white ambient/diffuse materials that could override vertex
  colours, and reduced excessive combined ambient/direct illumination.
- Added deterministic textured planets with biome palettes, connected land
  masses, ocean shelves, coastlines, ice, cloud fronts, and day/night shading.
- Added explicit material/light states for dependable celestial colour, with
  no mandatory custom GPU shader.
- Refined surface lighting, sky atmosphere, survey tool, cockpit, and HUD.
- Added an accessible habitat shell whose visible doorway matches collision.

## Collision

- Added continuous capsule and sphere collision against rotated boxes, finite
  cylinders, and spheres, with sliding, steps, ceilings, and overlap recovery.
- Registered trunks, rocks, resource deposits, structure walls/floors/roofs,
  parked ship hulls, asteroids, and station modules.
- Kept doors and pavilion openings passable; light foliage and fauna stay
  non-solid. Mining and chunk unloading remove obsolete collision shapes.
- Added safe spawn recovery, landing-site search, construction obstruction
  checks, mining occlusion, and orbital approach route planning.

## Movement and comfort

- Refined acceleration, deceleration, air control, and sprint transitions.
- Added buffered/coyote jumps, variable jump height, and jetpack air braking
  and hover control.
- Added assisted or inertial flight with active braking and swept hull movement.
- Added adjustable field of view, camera motion, mouse smoothing, and mouse
  capture, retained in saves. Camera motion can be switched off.
- Added movement, thrust, brake, and contact feedback to the HUD.

Existing version 1.0 expedition saves remain supported. The universe, material
economy, story, authored content, and save location are preserved.

See TEST_REPORT.md for verification and platform limitations.
