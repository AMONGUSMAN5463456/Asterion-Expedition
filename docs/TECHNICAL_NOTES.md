# Technical notes

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
| `asterion/planet_visuals.py` | Deterministic CPU planet textures and explicit celestial shading |
| `asterion/effects.py` | Camera-mounted survey equipment and ship cockpit |
| `asterion/occlusion.py` | Optional depth-based surface ambient occlusion and compositing |
| `asterion/world.py`, `asterion/geometry.py` | Procedural scene construction and streaming |
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

## Ambient occlusion

Settings → Ambient occlusion enables soft contact shading on the surface.
It defaults to on, applies immediately, and is stored in the expedition save.
Orbit releases the effect's render targets; returning to a surface restores
the saved preference. The world-detail setting remains independent.

The GLSL 1.30 effect reconstructs positions and normals from scene depth,
samples a 1.5-metre neighbourhood at half resolution, and uses depth-aware
upsampling before compositing. Shading fades out between 80 and 120 metres.
It does not replace scene material shaders or process the 2D HUD. Texture
padding and lens changes are handled by the effect; Panda3D's filter manager
resizes its render targets with the window.

Positions are rebuilt from the lens field of view and depth planes rather than
by multiplying with an inverse projection matrix. A shader-input matrix reaches
GLSL in the opposite order from Panda3D's own matrix convention, so the former
reconstruction returned unusable positions and shaded the wrong pixels: on a
surface view from about seven metres up and pitched down, smooth ground gained
false horizontal shading bands and objects gained bright unshaded rims. The
lens parameters are plain float shader inputs and carry no such ambiguity.

A headless GL check confirms the reconstruction: at seven metres above level
ground, pitched down 45 degrees, the centre of the screen reconstructs the
ground at 9.9 metres, and neighbouring rows follow the expected distances.
With the fix, enabling occlusion on flat open ground leaves the frame unchanged,
and tight contacts still darken (measured at the base of a landing-pad deposit).

TinyDisplay runs without AO. Render-target or shader setup failure also leaves
normal rendering active and marks the setting unavailable for that session.
As a screen-space effect, AO cannot shade against geometry outside the camera
view or hidden behind the visible depth layer.

## HUD text layout

Text measurements are cached per interface by cleaned text, font, size, and
wrapping width. The cache clears at 1,024 entries and on interface destruction,
so changing telemetry cannot grow it indefinitely. HUD update frequency and
layout remain unchanged.

A 600-frame stationary surface profile (Python 3.14, headless GL) reduced
Python update time from 1.349 to 0.888 seconds, with HUD updates falling from
0.558 to 0.100 seconds. This is a CPU-side improvement, not an FPS guarantee:
the separate 640×360 rendered-frame sample remained approximately 36 ms/frame.
Cached and uncached 1280×720 HUD captures were pixel-identical.

## Streaming mesh transforms

`Mesh.add` calculates scale-dependent normal divisors once per mesh placement,
uses direct component arithmetic instead of per-vertex generators, and reuses
one temporary Panda3D normal vector. Vertex order, transforms, normal
normalization, colours, and UV padding are unchanged.

On Python 3.14, five unprofiled batches of 100 representative flora, rock, and
crystal transforms had median times of 279 ms before and 137 ms after. Across
36 placements, including non-uniform, negative, and zero scales, all generated
vertices, normals, colours, and UVs exactly matched the previous implementation.
A 240-frame headless surface traversal generating 35 chunks reduced profiled
chunk-generation time from 7.89 to 4.83 seconds before time slicing was added.
These CPU measurements include no GPU performance claim.

## Streaming frame pacing

Traversal uses cooperative generators with a 4 ms chunk-work budget and a
separate 2 ms distant-terrain budget per update. Terrain generation yields per
row, decoration placement per model, and vertex-buffer filling every 256 rows.
Budgets are checked between work units, not enforced as hard frame deadlines:
individual resource/landmark generation, collision registration, allocation,
unloading, rendering, and other gameplay work can still take additional time.

Initial surface loading remains synchronous. During traversal, a chunk becomes
visible in stages; each completed decoration/resource is registered with its
collision before the next yield. Geometry buffers attach only after completion.
The old horizon remains visible until its replacement is ready. Moving away
cancels unfinished chunk work before unloading its nodes and collision groups;
world destruction closes both pending generators. Depletion remains persistent.

A headless GL traversal over 900 updates (400 moving, then settling) reduced
CPU-update p99 from 71.7 to 11.4 ms and maximum from 159.5 to 13.8 ms. Updates
over 16.7 ms fell from 48 to zero; the median rose from 0.59 to 4.71 ms because
streaming work is distributed rather than concentrated. Rendering was excluded
from those timings. Settled chunk IDs and vertex-buffer hashes matched the
previous implementation exactly; this does not guarantee desktop GPU frame times.

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
```

## Graphics and performance

The normal window starts at 1280 × 720. The scene uses original procedural
geometry, vertex colors, lights, atmospheric fog, a changing sky, and streamed
terrain. The renderer draws a finite surrounding area, so distant objects and
terrain can appear as you move. Quality settings can reduce the local scene
workload. The orbital scene uses larger distance and speed scales than walking.

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
roof contact. Terrain remains a heightfield, so it cannot represent caves or
terrain overhangs. Fauna and light foliage are not solid.

Movement integrates at bounded small physics steps. Mouse smoothing is optional,
and turning is independent of the camera motion setting. Landings and ship recall
search for open, reasonably level ground. Automatic travel uses a bounded route
planner and still checks the actual swept ship movement. If no clear route can
be found, it stops and reports the obstruction.

Planet textures are generated in memory from stable seeds and cached; first
arrival in a new system can take several seconds. Their surface colours and
illumination do not depend on fixed-function white material defaults. Clouds
share the opaque surface to avoid overlapping transparent sphere artifacts.

## Persistence and determinism

Universe generation uses stable seeds. The catalog has 24 systems and 96 worlds,
with eight biome families. A surface is a generated heightfield around the
player, separate from its orbital planet model. Generation is reproducible;
it is not an online catalog or an astronomical simulation.

The save state stores plain JSON with validation and tolerant defaults.
Inventory transactions are handled in the state layer. Discovery identities,
mined deposit records, and construction are retained independently from the
rendered objects, which can be unloaded and recreated. Saves use a temporary
file and replacement, and preserve a previous valid file as a backup.

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

Playback cross-fades surface and space ambience, blends engine intensity with
motion, and rate-limits repeating effects. Master volume is stored in settings.
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

The project's Linux offscreen checks exercise the running renderer and gameplay
integration. Native macOS and Windows launchers are provided but have not been
executed on those operating systems in this environment. Hardware audio output,
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
