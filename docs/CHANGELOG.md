# Unreleased

## Performance

- Mining beam reuses its flare quad and rebuilds line geometry only past a
  2 cm movement gate instead of every frame while mining.
- Player-effects colour updates are epsilon-gated and thrust bars update only
  on visible state change (steady cruise now issues no redundant updates).
- Broadphase collision queries cache per-cell order with a single-cell fast
  path (adversarial stacked-box bench: -34% capsule moves, -20% sweeps).
- Text truncation bisects the longest fitting prefix instead of removing one
  character per measure (~780x on a 5.2 KB string); redundant panel resends
  skip rebuild via compare-first (119 us down to 6 us).
- Save serialization trims per-field lookups and writes UTF-8 bytes once
  (20k-discovery to_dict -15%); orbit station colliders are cached per load.
- Cached repeated HUD and menu text measurements, bounded to 1,024 entries per
  interface, without changing fonts, wrapping, update frequency, or graphics quality.
- Reduced procedural mesh-transform overhead during chunk streaming by hoisting
  scale calculations and reusing a normal vector, preserving generated geometry.
- Spread terrain sampling, chunk decoration, and vertex-buffer construction
  across frames instead of building an entire chunk in one update.
- Kept the previous distant terrain visible while its replacement builds;
  cancelled unfinished work on streaming unload and world changes.

## Graphics

- Fixed ambient occlusion reconstructing surface positions incorrectly, which
  added false shading bands on smooth ground and bright unshaded rims around
  objects when looking down from a raised viewpoint.
- Added optional screen-space ambient occlusion for surface contact shading,
  with a saved, immediate on/off control in Settings.
- Kept existing material colours and HUD rendering unchanged. AO is disabled
  in orbit and falls back to normal rendering when shaders are unavailable.
- Per-biome surface sunlight, warm twilight horizons with blue nights, hazier
  skies with cirrus, varied stars with a milky-way band, altitude and biome
  ground tinting, shoreline foam with sun glint, desert/volcanic dust motes,
  and a richer orbit nebula with distant galaxies.
- Sharper continents with archipelagos, latitude ice caps, dune banding,
  volcanic ember veins, ocean depth shading, baked storm spirals, tinted soil
  grain, and a thicker atmospheric limb glow.
- Two-tone flora, grass, crystals, rocks, and fauna details; lit windows,
  railings, and beacon accents on structures, ship, and station.
- Scanner holo ring with charge lights and idle motion on the survey tool,
  canopy struts with HUD warmth in the cockpit, and a core/halo mining beam
  with an impact flare.

## Save recovery

- Fixed saving after recovery from an excessively nested JSON save. The corrupt
  primary is now replaced without overwriting the previous valid backup.

## Bugfixes

- Fixed crash when marking a region depleted before the first world load, and
  fixed overwritten buildings leaking their old scene node and colliders.
- Mesh triangles with invalid caller-supplied normals (NaN, zero-length,
  wrong count) now fall back to the computed face normal.
- Mute-then-unmute restores loop sounds; the AO shader no longer normalizes
  a degenerate zero cross product.

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
