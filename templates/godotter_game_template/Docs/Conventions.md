# Godotter Dev-Mode Conventions

This document defines the coding and project structure conventions for Godotter game projects.
AI agents should read this before creating or modifying any files.

## Directory Structure

```
cheat/             Developer tools (console, scene jumper, inspector, cheats)
game/core/         Reusable engine-level code (events, bootstrap, input)
game/systems/      Self-contained game systems, each in its own directory (grid, ghost, pacman)
game/features/     Game rule fragments that wire systems together, each in its own directory
game/gamemodes/    Reusable game mode controllers, one per level type
game/levels/       Player-facing level scenes
ui/                Player-facing UI (widgets, controllers, themes)
cheat/             Developer tools (console, scene jumper, inspector, cheats)

tests/core/        Core utility tests (event bus, input mapper)
tests/systems/     System-level harness tests
tests/features/    Feature-level harness tests
tests/levels/      Level smoke tests
tests/integration/ Cross-system tests
tests/e2e/         End-to-end playthrough tests
```

Do NOT create directories outside this structure (e.g., no `game/scenes/`).

## File Naming Rules

Every layer uses a distinct suffix in the main file name, so agents and tools can
identify the layer at a glance without checking the parent directory.

| Layer | Directory | Main file | Example |
|-------|-----------|-----------|---------|
| System | `game/systems/{name}/` | `{name}_system.gd` | `game/systems/pacman/pacman_system.gd` |
| Feature | `game/features/{name}/` | `{name}_feature.gd` | `game/features/eat_dot/eat_dot_feature.gd` |
| GameMode | `game/gamemodes/{name}/` | `{name}_mode.gd` | `game/gamemodes/pacman/pacman_mode.gd` |

### Rules

- Directory name is short (no suffix). The suffix is on the main file only.
- Visual prefabs (tscn + gd) live in the same directory as the system that owns them.
  Example: `game/systems/pacman/pacman.tscn` (visual) + `pacman_system.gd` (logic).
- A system with multiple sub-files may use subdirectories (e.g., `data/`, `actors/`).

### Examples

```
game/systems/pacman/
├── pacman_system.gd              ← main system file
├── pacman.tscn                    ← visual prefab
├── pacman.gd                      ← visual controller (animations, sprite direction)
└── pacman_system.gd.uid

game/systems/ghost/
├── ghost_system.gd                ← main system file
├── blinky.tscn / blinky.gd        ← individual ghost visuals
├── pinky.tscn / pinky.gd
├── inky.tscn / inky.gd
├── clyde.tscn / clyde.gd
└── ghost_system.gd.uid

game/features/eat_dot/
├── eat_dot_feature.gd
└── eat_dot_feature.gd.uid

game/gamemodes/pacman/
├── pacman_mode.gd
└── pacman_mode.gd.uid
```

## Dependency Hierarchy

References between layers MUST follow this strict hierarchy:

```
game/levels/       → game/gamemodes/
game/gamemodes/    → game/features/, game/systems/, game/core/
game/features/     → game/systems/, game/core/
game/systems/      → game/core/ ONLY
game/core/         → (no game/ dependencies)
ui/                → game/core/
cheat/             → any (debug only)
tests/             → any (direct instantiation)
```

### Rules

| Layer | Can reference | Cannot reference |
|-------|--------------|------------------|
| game/gamemodes/ | game/features/, game/systems/, game/core/ | game/levels/ |
| game/features/ | game/systems/, game/core/ | game/gamemodes/, game/levels/ |
| game/systems/ | game/core/ ONLY | game/features/, game/gamemodes/, game/levels/ |
| game/levels/ | game/gamemodes/ ONLY | game/systems/, game/features/ (must go through gamemode) |
| game/core/ | nothing in game/ | everything |

### Explanation

Each layer is a "ring" — inner rings know nothing about outer rings:
- **Systems** are self-contained logic units. They don't know about features, gamemodes, or levels.
- **Features** orchestrate systems into gameplay rules. They don't know about gamemodes or levels.
- **GameModes** bundle features and systems into a level type. They are mounted by level scenes.

## Scene Responsibilities

### game/levels/*.tscn — Player Entry Point

- This is what the player sees when they run the game
- Contains: a GameMode node (child), a Renderer (Node2D), and a UI (Control, group="ui")
- **NEVER contains auto-quit logic**
- **NEVER checks command-line arguments (`--scene`, `--headless`, etc.)**
- Runs in a continuous game loop until the player closes the window

### tests/**/harness — Test Entry Point

- Each test creates its own harness scene under `tests/<kind>/<name>/`
- Instantiate game systems directly in test scripts, do NOT load `game/levels/` scenes
- Tests control their own lifecycle: setup → exercise → assert → `get_tree().quit(0)`
- **Tests may auto-quit** because they are headless CI runs

## System

A system is a Node (never Node2D or Control) placed under a GameMode node.

### Contract

```gdscript
class_name ExampleSystem
extends Node

var event_bus = null

func configure(bus) -> void:
    event_bus = bus
    event_bus.subscribe(EventTypes.SOME_EVENT, _on_some_event)
```

### Rules

- Self-contained: owns ONE domain, publishes events, knows nothing about other systems
- Has its own Timer-driven tick loop (not _process)
- Uses EventBus to communicate outward: publish events on state changes
- Implements configure(bus) method to receive EventBus reference
- Node type: Node (not Node2D, not Control) — no visual representation
- Registered in group "mgr:..." for uniqueness validation
- Main file name: `{short_name}_system.gd` (e.g., `ghost_system.gd`)

## Feature

A feature wires systems together to implement a gameplay rule. It is a Node placed under a GameMode.

### Contract

```gdscript
class_name CombatFeature
extends Node

var event_bus = null

func configure(bus) -> void:
    event_bus = bus
    event_bus.subscribe(EventTypes.PLAYER_FIRED, _on_player_fired)
```

### Rules

- Subscribes to events from systems, calls methods on other systems (via EventBus)
- Can enable/disable game rules independently
- Does NOT own system instances — systems are owned by GameMode
- Main file name: `{short_name}_feature.gd` (e.g., `eat_dot_feature.gd`)

## GameMode

A GameMode is the single orchestrator for one type of level scene.

### Contract

```gdscript
class_name PacManMode
extends Node

@export var level_data_path: String = ""

func _ready() -> void:
    var bus = EventBus.new()
    bus.name = "EventBus"
    add_child(bus)

    var pacman = preload("res://game/systems/pacman/pacman_system.gd").new()
    pacman.name = "PacManSystem"
    pacman.add_to_group("mgr:pacman")
    add_child(pacman)
    pacman.configure(bus)

    var eat_dot = preload("res://game/features/eat_dot/eat_dot_feature.gd").new()
    eat_dot.name = "EatDotFeature"
    add_child(eat_dot)
    eat_dot.configure(bus)
```

### Rules

- Creates and owns all system and feature nodes as children in _ready()
- Receives exported parameters for level-specific configuration (e.g., level_data_path)
- Controls game lifecycle: start, pause, end, restart
- Multiple levels can share the same GameMode with different data
- A level scene contains exactly one GameMode node
- Main file name: `{short_name}_mode.gd` (e.g., `pacman_mode.gd`)

## Event-Driven Architecture

## Event-Driven Architecture

## Input System

## Event-Driven Architecture

- Communication between systems/features goes through EventBus, not direct method calls
- Publish events via `event_bus.publish(GameEvent.new(...))`
- Subscribe in configure(bus) or _ready()
- Use EventTypes constants, never raw strings

## UI Layers

| Layer | z-index | Directory | Examples |
|---|---|---|---|
| Cheat Overlay | 100 | `cheat/overlay/` | Floating trigger, console, scene jumper |
| Controller | 60 | `ui/controllers/` | Virtual gamepad boards |
| Game UI | 50 | `ui/views/` | Pause menu, game over, dialogs |
| HUD | 10 | `ui/views/` | HP, score, minimap |
| Game UI | 50 | `ui/views/` | Pause menu, game over, dialogs |
| HUD | 10 | `ui/views/` | HP, score, minimap |
| Game World | 0 | `game/` | Characters, effects, level geometry |

## Level UI Convention

- Each level must have a `UI` node (type=Control, group="ui") as a direct child of the level root
- Level-specific UI is stored as widgets under `ui/widgets/`
- `main_level.tscn` loads `main_widget.tscn` as child of the UI node
- `game_level.tscn` loads `game_widget.tscn` as child of the UI node
- Widgets are self-contained prefabs with their own scripts — levels do not need `.gd` files

- cheat/ UI stays inside `cheat/` directory, never in `ui/`
- ui/ is for player-facing game UI only
- cheat/ uses `CanvasLayer` with `layer = 100`; game-ui uses `layer = 50`
- cheat trigger is a floating button, draggable, semi-transparent, activated in debug builds

## Cheat System

- `cheat/autoload/cheat_bootstrap.gd` is registered as the last autoload
- Activated only when `OS.is_debug_build()` or `application/config/dev_mode=true`
- Floating trigger button (🔧) at bottom-right, click to open overlay
- Overlay contains: reload current scene, scene jumper (lists all .tscn under game/levels/)
- Scene jumper auto-scans `res://game/levels/` — no hardcoded path list needed
- All cheat UI is touch-friendly: big buttons, no keyboard input required

- Game systems start their own tick loops in configure() or _ready()
- Do NOT rely on an external "start" method being called
- Use Timer.new() inside the system, not _process() polling

Example:
```gdscript
func _ready():
    var timer := Timer.new()
    timer.wait_time = 0.15
    timer.timeout.connect(tick)
    add_child(timer)
    timer.start()
```

## Input System

- `game/core/input/input_actions.gd` — defines all InputMap actions
- `game/core/input/input_mapper.gd` — alias resolver (game action → base action)
- `ui/controllers/` — virtual controller boards for mobile/touch

## UI Layers

| Layer | z-index | Directory | Examples |
|---|---|---|---|
| Cheat Overlay | 100 | cheat/overlay/ | Floating trigger, console, scene jumper |
| Controller | 60 | ui/controllers/ | Virtual gamepad boards |
| Game UI | 50 | ui/views/ | Pause menu, game over, dialogs |
| HUD | 10 | ui/views/ | HP, score, minimap |
| Game World | 0 | game/ | Characters, effects, level geometry |

## Level UI Convention

- Each level must have a UI node (type=Control, group="ui") as a direct child of the level root
- Level-specific UI is stored as widgets under ui/widgets/
- cheat/ UI stays inside cheat/ directory, never in ui/
- ui/ is for player-facing game UI only

## Cheat System

- cheat/autoload/cheat_bootstrap.gd is registered as the last autoload
- Activated only when OS.is_debug_build() or application/config/dev_mode=true
- Floating trigger button (🔧), click to open overlay with scene jumper and reload
- All cheat UI is touch-friendly

## Forbidden Patterns

- ❌ get_tree().quit() in any file under game/
- ❌ OS.get_cmdline_args() or args.has("--scene") in any file under game/
- ❌ Auto-quit timers in game scenes
- ❌ Test harness logic (assertions, pass/fail, auto-quit) in game code
- ❌ Cross-system direct method calls (use EventBus instead)
- ❌ New directories outside the dev-mode structure
- ❌ System referencing another system, feature, gamemode, or level
- ❌ Feature referencing another feature, gamemode, or level

## Test / Player Separation

| Concern | Player (game/levels/) | Test (tests/) |
|---|---|---|
| Lifecycle | Continuous loop | Starts → asserts → quits |
| Auto-quit | NEVER | YES |
| Instantiate systems | Via GameMode | Script: System.new() |
| EventBus | GameMode creates it | System or injected |

## References

- Input system: Docs/Input.md
- Cheat tools: cheat/README.md
