# Technical notes

## Celestial rendering

`visual_pipeline.py` owns the hardware path: GLSL 1.50 material shaders,
a directional depth map with filtered shadow comparison, an HDR scene target,
a reduced-size bloom pass and an edge-smoothing final pass. The interface
renders afterward so text is not blurred by the scene filters. Buffers resize
with the game window and are released when the app closes.

`surface_materials.py` shades the existing physical terrain mesh in planet
coordinates. It adds soil detail, optical water normals and shoreline foam;
it never moves a terrain vertex. `celestial_sky.py` provides a continuous sky,
small stellar point sprites, a dust lane and a sun corona. Cloud billows remain
attached to physical world positions, with their original collision-free depth.

`geometry.py` contains all original procedural model geometry. Curved leaves,
bevelled industrial panels, crystal growth, animal bodies and mechanical detail
replace the earlier basic shapes. `effects.py` renders the camera equipment;
`exploration_vfx.py` owns short-lived survey and mining light.

The desktop launcher requests OpenGL 3.2. GLSL capability is detected separately
from Panda's legacy Cg shader flag, which is false on modern macOS. TinyDisplay
runs without GPU buffers/shaders and keeps the model, palette and UI upgrades.
No new runtime package or asset download is required beyond the existing pinned
Panda3D installation.

## Runtime and package structure

Asterion Expedition uses Python 3.10–3.14, Panda3D 1.10.16, and the Python
standard library. It renders a native desktop window. It does not run inside a
browser or use a hosted game server. The source distribution includes the game,
fonts with their license, synthesized WAV files, documentation, and launchers.
Python, Panda3D binaries, and a virtual environment are not bundled.

| File or directory | Responsibility |
| --- | --- |
| `main.py` | Command-line options, graphics configuration, launch and smoke checks |
| `bootstrap.py` | One-time local dependency setup and return-code-preserving launch |
| `asterion/app.py` | Gameplay orchestration, panels, travel, interactions, survival |
| `asterion/controller.py` | Mouse, keyboard, walking, jetpack, and flight |
| `asterion/collision.py` | Continuous capsule/sphere collision, spatial groups, ray queries |
| `asterion/navigation.py` | Bounded visibility route planner around orbital exclusion spheres |
| `asterion/planetary.py` | Shared spherical elevation/colour, tangent frames, atmosphere and swept terrain contact |
| `asterion/seamless_world.py` | Persistent solar scene, progressive globe/near detail, geographic objects and combined flight collision |
| `asterion/planet_weather.py` | Spatial cloud banks, depth fog and atmospheric haze |
| `asterion/planet_visuals.py` | Cached terrain detail textures and retained legacy visual helpers |
| `asterion/atmosphere_effects.py` | Continuous entry plasma, cloud wisps and blue exit effects |
| `asterion/effects.py` | Camera-mounted survey equipment and ship cockpit |
| `asterion/world.py`, `asterion/geometry.py` | Shared procedural meshes, solid-shape definitions and legacy renderer regression coverage |
| `asterion/state.py` | Inventory, economy, upgrades, missions, construction, JSON saves |
| `asterion/content.py`, `asterion/universe.py` | Original authored content and deterministic generation |
| `asterion/ui.py` | HUD, title, and scrollable panels |
| `asterion/audio.py` | Optional looping ambience and action sound playback |
| `assets/generate_audio.py` | Reproducible synthesis of all bundled sound files |
| `docs/build_reference.py` | Rebuild the material/recipe/building reference from game data |
| `tests/` | Automated tests |

The launcher checks the installed Panda3D package version locally. It contacts
PyPI only if the pinned version is missing from the private environment. It
does not upgrade pip, query for newer versions, alter global Python packages,
or choose an unpinned engine fallback. Only binary wheels are accepted. A broken
existing environment is reported, not automatically deleted.

The upstream [Panda3D 1.10.16 package](https://pypi.org/project/Panda3D/1.10.16/)
publishes CPython wheels for the project's supported Python versions, including
macOS universal2, Windows x86-64, and common Linux targets. Wheel availability
is not a guarantee that every GPU driver and desktop configuration will work.
The [official Python downloads](https://www.python.org/downloads/) page is the
source for installing Python separately.

## Useful commands

Run these from the extracted project directory. On Windows use `py -3` instead
of `python3`, or the private `.venv\Scripts\python.exe` after setup.

```sh
# Install dependencies once without opening the game.
python3 bootstrap.py --setup-only

# Launch through the reusable local environment.
python3 bootstrap.py

# Keep playing if an audio device is unavailable.
python3 bootstrap.py --no-audio

# Ask the launcher for its help without creating an environment.
python3 bootstrap.py --bootstrap-help
```

When your active interpreter already has the pinned Panda3D installed, launch
directly with `python3 main.py`. After bootstrap setup on Mac or Linux, the
explicit form is `.venv/bin/python main.py`.

For isolated diagnostics, pass a dedicated save directory so experiments do
not use your normal expedition slot:

```sh
python3 bootstrap.py --save-dir ./diagnostic-save

# Automated smoke checks use a temporary save directory by default.
python3 bootstrap.py --smoke-test --software --no-audio

# A standalone surface image from a fresh diagnostic expedition.
python3 bootstrap.py --offscreen --software --autostart --no-audio \
  --frames 12 --save-dir ./diagnostic-save --screenshot ./surface-check.png

# An orbital frame or a particular UI panel can also be inspected.
python3 bootstrap.py --offscreen --software --scene orbit --no-audio \
  --frames 12 --save-dir ./diagnostic-save --screenshot ./orbit-check.png

python3 bootstrap.py --offscreen --software --panel inventory --no-audio \
  --frames 12 --save-dir ./diagnostic-save --screenshot ./cargo-check.png
```

`--autostart`, `--scene orbit`, and `--panel` start a fresh diagnostic expedition;
use `--save-dir` with these options if you want to isolate all file activity.
Screenshots use the final rendered frame. Offscreen mode disables sound.
Software rendering is a fallback for inspection, with reduced performance and
some differences in visual features; it is not the preferred desktop mode.

Run the standard-library test suite using an interpreter with Panda3D installed:

```sh
.venv/bin/python -m unittest discover -s tests -v

# Actual physical descents, altitude screenshots and a JSON report.
.venv/bin/python tools/validate_descents.py

# Build the versioned source ZIP and verify every manifest entry.
.venv/bin/python tools/build_release.py
```

## Graphics and performance

The normal window starts at 1280 × 720. The scene uses original procedural
geometry, vertex colors, lights, atmospheric fog, a changing sky, and streamed
terrain. The entire terrain sphere is always present. Finer geometry and nearby objects stream as you move, with balanced neighboring terrain levels and complete child groups replacing coarse patches. Quality settings can reduce the local scene
workload. The persistent scene uses system metres throughout. Near a planet, a
rigid tangent frame keeps walking and near-object collision coordinates small;
changing the frame preserves physical position, velocity and full camera attitude.
Flight speed, damping, sky, fog, wind and effects blend continuously by altitude.

For Linux offscreen checks, the entry point chooses Panda3D's headless OpenGL
renderer when EGL is available. It falls back to the software renderer when EGL
is absent, and `--software` explicitly selects that renderer. Offscreen captures
check scene composition and UI, but do not measure live input latency, desktop
frame pacing, or how a hardware driver presents the final window.

## Collision and movement

Solid scenery uses simple compound boxes, vertical cylinders, and spheres,
indexed by spatial cells. The player uses a rounded upright capsule; ships use
a swept sphere. Resource depletion and chunk unloading remove their collision
groups. Compound building collision leaves real doorways open and supports
roof contact. Terrain is a spherical radial heightfield shared by the globe, near mesh and
ship sweeps. Near walking uses its tangent projection. It has no caves or overhangs. Fauna and light foliage are not solid.

Movement integrates at bounded small physics steps. Mouse smoothing is optional,
and turning is independent of the camera motion setting. Landings and ship recall
search for open, reasonably level ground. Automatic travel uses a bounded route
planner and still checks the actual swept ship movement. If no clear route can
be found, it stops and reports the obstruction.

Globe tiles and near terrain sample the same seeded spherical function. Detail
is generated with bounded work and a finite cache; a system load can take a few
seconds. Biome colours and illumination use explicit vertex and light states,
without a required custom GPU shader. Cloud banks use overlapping textured billboards at fixed 3D positions, with ellipsoidal density samples for local mist and analytic planetary occlusion. Procedural terrain detail textures use
power-of-two sizes for compatibility with the software fallback. Geography and
celestial directions remain fixed across the active reference-frame changes.

## Persistence and determinism

Universe generation uses stable seeds. The catalog has 24 systems and 96 worlds,
with eight biome families. Spherical terrain and orbit share one persistent
planet, and nearby resource tiles use stable cubed-sphere addresses. Construction
uses canonical geographic coordinates. Generation is reproducible; it is not an
online catalog or an astronomical simulation.

Save schema 5 stores plain JSON with validation and tolerant defaults. Coordinate
format 2 records the active tangent normal (or no frame in space), local position,
velocity, heading, pitch and physical roll. Old orbital positions are scaled once
to the current scale; old ground positions retain their home chart. Version 1.2 saves use a separate scale migration that retains datum altitude, momentum, nearby base offsets and station clearance. Each new save records `world_scale: 96` so it is never migrated twice. Flight saves restore in air.
Inventory transactions are handled in the state layer. Discovery identities,
mined deposit records, and construction are retained independently from the
rendered objects, which can be unloaded and recreated. Saves use a temporary
file and replacement, and preserve a previous valid file as a backup. An original pre-1.3 save is also archived once as `expedition.json.pre-v1.3.bak`.

The normal save directory is per user and separate from the source folder.
Deleting or recreating `.venv` does not remove an expedition. There is one normal
save slot; separate `--save-dir` values can maintain independent experiments.
Do not run two game instances against the same save directory at the same time.

## Audio provenance and behavior

All 13 audio assets are original mathematical synthesis: three ambience/engine
loops and ten action cues. They are 22,050 Hz mono 16-bit PCM WAV files. The
generator uses deterministic phases and a fixed random seed. Loops have
periodic waveforms, and individual cues have short attack and release envelopes
to avoid abrupt digital edges. No microphone recordings, commercial samples,
existing song melodies, or external audio files were used.

Playback cross-fades ambience with atmospheric density, blends engine intensity
with motion and atmospheric entry/exit, and rate-limits repeating action effects. Master volume is stored in settings.
Unavailable audio hardware or a missing optional audio file does not prevent
play. Regenerate the shipped assets with:

```sh
python3 assets/generate_audio.py
```

## Validation boundaries

Packaging validation checks Python syntax, shell syntax, pinned-dependency
selection, offline reuse, startup return codes, and optional-audio failure
handling. WAV checks confirm valid PCM format, non-silent output, peaks below
digital clipping, and quiet edges on one-shot effects.

Version 1.3 checks run on macOS with the real Panda3D application, native Cocoa/OpenGL hardware rendering, and software offscreen rendering. The automated descent tool captures physical orbit-to-contact flights with isolated saves. Windows and Linux desktop behavior has not been verified for this update. Hardware audio output,
desktop mouse capture on each window system, and prolonged play on a range of
computers remain areas for real-device testing. No unmeasured minimum hardware
specification or frame-rate guarantee is asserted.

## Extending the game

Edit data in `asterion/content.py` to add recipes, item descriptions, or objectives.
Keep IDs stable when changing content used by existing saves. World generation
and movement share terrain sampling so visual surfaces and ground collision
stay aligned. Changes to surface meshing should preserve that contract.

Run `python3 docs/build_reference.py` after editing content to update the
reference guide. Run the tests and smoke checks before distributing changes.
Keep the license files with redistributed fonts and source. A prebuilt desktop
release would additionally require a platform packaging process and testing on
each target operating system; the current source ZIP does not claim to provide
that work.
