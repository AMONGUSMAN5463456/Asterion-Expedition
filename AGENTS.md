# Repository Guidelines

## Project Overview

Single-player native 3D space-exploration game (Panda3D): surveying/mining,
fabrication, atmospheric flight, orbital trade, base-building, and an 18-stage
story across 24 systems / 96 planets / 8 biomes. Source-run, no build step.

## Architecture & Data Flow

`ExpeditionApp` (`asterion/app.py`, a `ShowBase` subclass) owns everything:

- `app.game: GameState` — authoritative mutable expedition state (inventory,
  credits, vitals, position, story). The only state that persists.
- `app.world: WorldRenderer` — owns all scene nodes plus the `CollisionWorld`.
- `app.controller: PlayerController` — sole input owner, updated once per frame.
- `app.ui: GameUI` — view-only; all actions route back through `app.action`.
- `app.audio: AudioManager`, `app.effects: PlayerEffects`.
- `app.system / app.planet` — regenerated from `universe.generate_system()`
  on every load/transition, never persisted.

Frame loop: `_frame(task)` → `step(dt)` with `dt` clamped to 0.1. When playing
(started, no panel, no transition): `controller.update` → `world.update` →
target/mine/survival checks → 60 s autosave. Always: `audio.update`,
`effects.update`, AO update, and `ui.update(self._view())` throttled to 20 Hz.
Headless runs drive `app.step(1/60)` directly (`main.py`).

Modes `surface | flight | orbit` change only via fade-guarded `transition_to`
(0.88 s); altitude ≥ 420 m auto-inserts orbit. Saves are atomic
(`expedition.json` + `.bak`, temp-file + fsync + `os.replace`); the backup is
written only from a still-valid primary, and load falls back primary → `.bak`.

Key modules (`asterion/`): `app` orchestration · `state` economy + saves
(`SAVE_VERSION=3`) · `world` streaming surface/orbit renderer (`CHUNK_SIZE=64`)
· `universe` deterministic galaxy + shared `terrain_height` · `navigation`
bounded `plan_route` (fail-closed `[]`) · `content` engine-free data tables
(items/recipes/buildings/biomes/story) · `controller` swept-capsule movement ·
`ui` HUD + data-driven menus · `geometry` batched `Mesh` builder + procedural
meshes · `collision` math-only colliders (`CollisionWorld`) · `occlusion`
half-res AO pass · `audio` cross-faded SFX/loops · `effects` survey-tool /
cockpit feedback · `planet_visuals` biome palettes + cached textures.

## Key Directories

- `asterion/` — game source (15 modules, ~8.9 kLOC). Edit here.
- `tests/` — stdlib `unittest` suite, one file per concern (see Testing & QA).
- `docs/` — `TECHNICAL_NOTES.md` (architecture authority), `REFERENCE.md`
  (**generated**, never hand-edit), `FIELD_GUIDE.md` (gameplay only),
  `CHANGELOG.md` (`# Unreleased` on top), `TEST_REPORT.md` (version-stamped
  proof), `screenshots/` (actual rendered frames), `build_reference.py`.
- `assets/` — `audio/` (13 committed WAVs), `fonts/` (DejaVu TTFs),
  `generate_audio.py` (stdlib synthesizer that produces the WAVs).
- Root — `main.py`, `bootstrap.py`, start shims, `requirements.txt`,
  `MANIFEST.sha256` (hash of tracked files, test-enforced).

## Development Commands

```bash
python3 bootstrap.py --setup-only   # one-time: private .venv + pinned Panda3D
python3 bootstrap.py                # play (forwards extra args to main.py)
python3 main.py                     # direct run once Panda3D is installed

# Diagnostics (never touch the normal save slot; always isolate)
python3 main.py --smoke-test --software --no-audio
python3 main.py --offscreen --software --autostart --no-audio --frames 12 \
  --save-dir ./diagnostic-save --screenshot ./surface-check.png
# Variants: --scene orbit | --panel inventory|craft|map|journal|build|help|settings

python3 -m unittest tests.test_collision tests.test_controller \
  tests.test_movement_v11 tests.test_navigation tests.test_systems
python3 -m unittest discover -s tests   # full suite, release gate (~110 s, ~1.5 GB)

python3 docs/build_reference.py     # REQUIRED after editing asterion/content.py
python3 assets/generate_audio.py    # REQUIRED after editing audio synthesis
```

No pytest, no linters/formatters/typecheckers configured — do not add any.
`--smoke-test` cannot combine with `--save-dir`; `--screenshot` requires
`--frames` or `--offscreen`. After changing any tracked file, rehash
`MANIFEST.sha256` (two-space `sha256sum` format, stable ordering) and run
`tests.test_packaging`.

## Code Conventions & Common Patterns

- `snake_case` modules/functions, `CapWords` classes, `_leading_underscore`
  privates, `UPPER_SNAKE` constants. Each module repeats tiny coercion helpers
  (`_finite`, `_number`, `_identifier`, …) — do not unify them.
- Guard-and-fallback everywhere: coerce bad/non-finite input to defaults,
  never raise on gameplay paths. Economy/UI actions return `(ok, message)`
  tuples. Lookups fail closed (`plan_route` → `[]`, bad records skipped).
  Raise `ValueError` (never `KeyError`) for bad planet/system IDs.
- All persistent state lives on `GameState`; renderer/controller hold only
  transient scene/input state rebuilt on load. `to_dict` round-trips through
  the `_from_dict` sanitizer so NaN/handles never reach disk.
- Perf idioms: `functools.lru_cache` (terrain sampler, shape tables,
  textures), last-value/dirty caches, fixed physics substeps (≤ 4), per-effect
  audio cooldowns, 4 ms chunk + 2 ms far-terrain streaming budgets, HUD at
  20 Hz, epsilon-gated redundant state updates. Optimize CPU/update-time;
  never cut visual quality for speed.
- Load-bearing, do not disturb: explicit render-state guards (`isEmpty()`
  checks, light/material/shader-off priorities, idempotent destroy/cleanup);
  AO enabled only on surface with shader support (`RuntimeError` disables it
  for the session); `terrain_height` is the exact formula shared by meshing
  and movement (landing-disk/water-level invariants); navigation budgets and
  endpoint rejection; depletion/chunk-unload must remove collision groups
  together with nodes.

## Important Files

- Entry: `main.py` (CLI + renderer selection) · `bootstrap.py` (installer,
  `PIN = "1.10.16"`, never auto-deletes a broken `.venv`).
- Config/packaging: `requirements.txt` (single pin, must equal
  `bootstrap.PIN`) · `MANIFEST.sha256` · `.gitignore` · `Start-Linux.sh` /
  `Start-Windows.bat` / `Start-Mac.command` (thin arg-forwarding shims).
- Core: `asterion/app.py` · `asterion/state.py` · `asterion/world.py` ·
  `asterion/controller.py` · `asterion/ui.py`.
- Data-as-code: `asterion/content.py` (keep IDs stable — saves reference
  them) · `asterion/universe.py` (`UNIVERSE_SEED = 73129`, shared with audio).
- Decisions: `docs/TECHNICAL_NOTES.md` (read before changing AO, collision,
  persistence, streaming, or audio behavior).

## Runtime/Tooling Preferences

- 64-bit CPython 3.10–3.14 (3.12 recommended); Apple system Python is
  unsuitable. Only third-party dep: `panda3d==1.10.16`, installed once into a
  private `.venv` by `bootstrap.py` (internet needed only for that step).
- Everything else is stdlib (`argparse`, `wave`, `math`, `hashlib`,
  `unittest`, …). No NumPy, no pytest, no CI config — keep it that way.
- Renderer: default windowed; offscreen picks headless-GL when EGL exists
  else `p3tinydisplay`; `--software` forces software (inspection only).
- Saves: per-user OS dir by default (`$XDG_DATA_HOME/asterion-expedition/`
  on Linux); `--save-dir` isolates experiments; never run two instances on
  one save dir. Audio is optional (`--no-audio`, null driver offscreen).

## Testing & QA

- Framework: stdlib `unittest` only (`if __name__ == "__main__": unittest.main()`
  per module; no conftest/pytest config). Never use `pytest` spellings.
- Fast subset for iteration: `test_collision`, `test_controller`,
  `test_movement_v11`, `test_navigation`, `test_systems`; add `test_world`
  for world/geometry changes, `test_graphics`/`test_interface` for visual/UI,
  `test_integration` (heaviest: full offscreen `ExpeditionApp`) for app flows,
  `test_packaging` after adds/renames/pins/assets/generated docs.
- Conventions: `*Tests(TestCase)` + `test_*` methods, `subTest`
  parametrization, `FakeWindow`/`SceneHost` fakes, `tempfile` save dirs,
  offscreen `p3tinydisplay` recipe in-module. Do not loosen numeric
  tolerances (`assertAlmostEqual` deltas, `places=` on heights/normals/HUD).
- NaN rule: sanitize-and-continue or fail closed — never raise, never persist.
- Pinned contracts (breaking these fails tests): legacy v1 save loads with
  tolerant defaults; failed economy ops leave `to_dict` bit-identical;
  `add_building` idempotent; depletion removes collision and never resurrects
  via streaming; ship bounds/normals/landing-height invariants; deterministic
  galaxy (24 systems, 96 unique IDs, stable seeds); `REFERENCE.md` byte-matches
  `build_reference.py` output; `MANIFEST.sha256` matches every listed file.
- Release gate: full discover green + `--smoke-test` JSON report
  (`status`, ~25 checks: panels, scan, mine, craft, flight, orbit, travel,
  landing, atomic save/restore, trade).
