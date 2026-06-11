# Cheat Tools

Developer tools for debugging and testing. All UI is touch-friendly (mobile-first).

## Structure

```
cheat/
├── autoload/          CheatBootstrap — autoload, conditional activation
├── overlay/            Floating trigger button + bottom slide-up panel
├── jump/               Scene jumper — list and jump to levels
├── inspector/          (future) Reflection-based Manager state viewer
└── cheats/             (future) One-click cheats (invincible, add money, etc.)
```

## Activation

- Automatically in debug builds (`OS.is_debug_build()`)
- In release builds: set `application/config/dev_mode=true` in project.godot
- Floating 🔧 button appears at bottom-right

## Controls

| Action | Gesture |
|---|---|
| Open overlay | Tap 🔧 |
| Close overlay | Tap ✕ or swipe down |
| Move trigger | Long-press + drag |
| Jump to scene | Tap scene name |
| Reload current | Tap 🔄 重载当前场景 |

## Adding New Cheats

Place cheat scripts in `cheat/cheats/`. Register them in `cheat_bootstrap.gd`.
All cheat UI must use `CanvasLayer` with `layer = 100`.
