# Validation report — Asterion Expedition 1.4.0 / Celestial

Release validation: 20 September 2026. Native graphics were rendered on an
Apple M4, macOS 27.0, CPython 3.12.14 and Panda3D 1.10.16. Every gameplay
validation used a disposable save directory; the normal player save was not
opened or modified.

## Graphics and interface

The [native graphics capture](validation-v1.4/graphics-report.json) includes the
title, surface HUD, seven gameplay panels, cockpit/orbit and all eight biomes.
It uses the production OpenGL pipeline: physical-scale terrain detail, animated
ocean shading, filtered sun shadows, moving vegetation, atmospheric sky and
cloud banks, HDR scene buffer, selective bloom and edge smoothing. The shadow
comparison includes an otherwise identical unshadowed surface view.

The [detail gallery](validation-v1.4/details/) photographs the production courier,
outpost, ruin, field buildings, wildlife, mineral extraction, survey pulse and
orbital station. These use inspection camera poses in an isolated expedition.
None of the screenshots is retouched or composited.

The interface validator rendered **20 screens at each of 1280 × 720 and
960 × 540**, and checked native text bounds, including scrollable records.
It covers both title states, 15 panels, and surface/flight/orbit HUDs.
[1280 report](validation-v1.4/ui/validation-1280x720.json) ·
[960 report](validation-v1.4/ui/validation-960x540.json).
The final font check removed glyph-atlas artifacts on the Mac core profile.
UI typography renders after scene postprocessing.

## Gameplay and regression checks

All **295 regression tests passed**, with each module in its own process to
isolate Panda3D global state. The complete run took 340.1 seconds with three
workers. The affected interface, surface-material, geometry, graphics and packaging modules were
rerun after the last typography, palette and renderer-configuration corrections:
all **54 checks passed**. Results are in the
[regression report](validation-v1.4/regression-report.json).

Coverage includes collision and movement, high-speed planet sweeps, ocean
contact, cube seams and poles, terrain refinement, cloud depth/occlusion,
streaming, navigation, mining, crafting, construction, progression, save/load
and migration, UI actions, launchers, fonts and audio assets. Graphics-specific
checks include bounded effect lifetimes, exact preservation of procedural
resource IDs/positions/amounts, and reuse of prepared model buffers while
streaming. Terrain collision and saved-world scale remain unchanged.

The software-rendered gameplay smoke test passed **25 checks**, including
mining, scanning, crafting, boarding, a physical automatic interplanetary
approach, landing, saving, reloading and trading.
[Smoke result](validation-v1.4/smoke-report.json).

## Native physical descents

The final build completed **five physical descents from 7,000 m at 900 m/s**.
Only the initial camera/controller pose was assigned; the rest used ordinary
app updates, frame changes and swept collisions. Solar and planet roots stayed
unchanged, there was no entry fade, both flight/orbit modes were traversed, and
each scenario validated saved in-air momentum.

| Scenario | Surface | Hull clearance at contact | Result |
| --- | --- | --- | --- |
| Talora | Land | 3.0248 m | Normal F touchdown to walking |
| Deep ocean | Ocean | 3.0293 m | Hull contact |
| Far hemisphere | Land | 3.0262 m | Hull contact |
| North pole | Land | 3.0294 m | Hull contact |
| South pole | Land | 3.0235 m | Hull contact |

Every descent reached contact in 7.8 simulated seconds, with at most 30 m of
travel per tick, consistent with the controller's 3 m hull and 30 Hz update.
The [descent report](validation-v1.4/descents/descent-report.json) records
actual capture altitudes and frame numbers for 46 screenshots. The final
night-side inspection verifies that surface props receive their planet's
sun occlusion rather than remaining brightly lit over dark ground.

## Performance and limits

At 1280 × 720, the warm, fully populated surface measured **28.32 ms median**
and **31.96 ms p95** over 45 update-and-render frames (roughly 35 frames/s at
the median). This includes hardware effects, 81 loaded chunks and no queued
chunks. Median app update was 17.93 ms; median render submission was 10.2 ms.
These component medians need not sum to the total median. Startup to the
prepared title scene took 9.29 seconds.

Physical descents measured median app updates of 13–20 ms and p95 updates of
75–86 ms while new geography and props streamed. Those descent measurements
exclude most rendered frames and are not an FPS estimate. Streaming can
still cause brief stalls. This is a stylized procedural game, not a claim of
photorealism, ray tracing, or a locked 60 FPS.

Native OpenGL graphics were checked on this Mac. TinyDisplay retains the
improved geometry, textures and interface, with baked shading in place of
GPU effects. The 960 × 540 software capture took about 193 ms per warm frame;
it is a slow compatibility path. Other GPU/driver combinations and Windows/Linux desktop input
and hardware audio were not manually verified.

## Reproduction and release

Run from the extracted source folder with its prepared interpreter:

```sh
.venv/bin/python tools/run_regressions.py
.venv/bin/python tools/validate_graphics.py
.venv/bin/python tools/validate_interface.py --renderer gl --size 1280 720
.venv/bin/python tools/validate_interface.py --renderer gl --size 960 540
.venv/bin/python tools/validate_descents.py --hardware
.venv/bin/python tools/validate_art_details.py
.venv/bin/python main.py --smoke-test --software --no-audio
.venv/bin/python tools/build_release.py
```

The source archive has a single `asterion-expedition-v1.4.0/` root and includes
bundled font licenses. The builder verifies ZIP CRCs and each SHA-256 manifest
entry and writes an external archive checksum. Virtual environments, caches,
player saves, local logs and previous release archives are excluded. macOS and
Linux launcher executable permissions are preserved.
