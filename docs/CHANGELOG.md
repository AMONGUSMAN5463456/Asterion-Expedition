# Unreleased

## Graphics

- Added optional screen-space ambient occlusion for surface contact shading,
  with a saved, immediate on/off control in Settings.
- Kept existing material colours and HUD rendering unchanged. AO is disabled
  in orbit and falls back to normal rendering when shaders are unavailable.

## Save recovery

- Fixed saving after recovery from an excessively nested JSON save. The corrupt
  primary is now replaced without overwriting the previous valid backup.

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
