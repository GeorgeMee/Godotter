# Godotter Dev-Mode Conventions

This document defines the coding and project structure conventions for Godotter game projects.
AI agents should read this before creating or modifying any files.

## Directory Structure

```
game/core/         Reusable engine-level code (events, bootstrap, input)
game/systems/      Self-contained game systems (snake, grid, inventory)
game/features/     Feature controllers that wire systems together
game/content/      Art, audio, prefabs (no logic)
game/levels/       Player-facing level scenes

tests/core/        Core utility tests (event bus, input mapper)
tests/systems/     System-level harness tests
tests/features/    Feature-level harness tests
tests/levels/      Level smoke tests
tests/integration/ Cross-system tests
tests/e2e/         End-to-end playthrough tests
```

Do NOT create directories outside this structure (e.g., no `game/scenes/`).

## Scene Responsibilities

### game/levels/*.tscn — Player Entry Point

- This is what the player sees when they run the game
- Contains game content: feature nodes, system managers, visual setup
- **NEVER contains auto-quit logic**
- **NEVER checks command-line arguments (`--scene`, `--headless`, etc.)**
- Runs in a continuous game loop until the player closes the window

### tests/**/harness — Test Entry Point

- Each test creates its own harness scene under `tests/<kind>/<name>/`
- Instantiate game systems directly in test scripts, do NOT load `game/levels/` scenes
- Tests control their own lifecycle: setup → exercise → assert → `get_tree().quit(0)`
- **Tests may auto-quit** because they are headless CI runs

## Event-Driven Architecture

- Communication between systems/features goes through `EventBus`, not direct method calls
- Publish events via `event_bus.publish(GameEvent.new(...))`
- Subscribe in `configure(bus)` or `_ready()`
- Use `EventTypes` constants, never raw strings

## Auto-Start Pattern

- Game systems (managers) should start their own tick loops in `_ready()` or `configure()`
- Do NOT rely on an external "start" method being called
- Use `Timer.new()` inside the system, not `_process()` polling

Example:
```gdscript
func _ready():
    var timer := Timer.new()
    timer.wait_time = 0.15
    timer.timeout.connect(tick)
    add_child(timer)
    timer.start()
```

## Managers/EventBus Convention

- Each level must have a root `Managers` node and a `Managers/EventBus` child
- Structured events via `EventBus`; avoid implicit get-from-group lookups outside Managers
- Bootstrap via `game/core/bootstrap/managers.gd` which calls `configure(event_bus)` on all children and siblings

## Export

- `export_presets.cfg` contains default presets: Windows Desktop + Android (unsigned)
- Android template (`android/build/`) is auto-installed on first build via `--install-android-build-template`
- Set `GODOTTER_ANDROID_KEYSTORE_PATH` to enable APK signing

## Forbidden Patterns

- ❌ `get_tree().quit()` in any file under `game/`
- ❌ `OS.get_cmdline_args()` or `args.has("--scene")` in any file under `game/`
- ❌ Auto-quit timers in game scenes
- ❌ Test harness logic (assertions, pass/fail, auto-quit) in game code
- ❌ Cross-system direct method calls (use EventBus instead)
- ❌ New directories outside the dev-mode structure

## Test / Player Separation

| Concern | Player (game/levels/) | Test (tests/) |
|---|---|---|
| Lifecycle | Continuous loop | Starts → asserts → quits |
| Auto-quit | NEVER | YES |
| Instantiate systems | Scene tree | Script: `Manager.new()` |
| EventBus | Real | Real or Fake (tests/core/fake_event_bus.gd) |

## References

- Input system: `Docs/Input.md`
- Template event flow example: `game/features/item_pickup/` + `game/systems/inventory/` + `tests/features/item_pickup/`
