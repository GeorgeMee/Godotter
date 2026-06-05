# Godotter Testing Strategy

Godotter projects use layered tests so agents can choose the smallest useful verification scope.

## Test Kinds

| Kind | Directory | Purpose | Allowed style |
| --- | --- | --- | --- |
| `system` | `tests/systems/<name>/` | Verify one system manager or pure gameplay service. | Direct public method calls and EventBus assertions. |
| `feature` | `tests/features/<name>/` | Verify one feature in a minimal harness. | Public feature API, fake/minimal dependencies, EventBus assertions. |
| `integration` | `tests/integration/<scenario>/` | Verify several systems/features working together. | Real managers/features in a focused harness; prefer EventBus/public APIs. |
| `level-smoke` | `tests/levels/` | Verify a real level/scene starts and key nodes are wired. | Load the real scene, wait frames, assert key nodes/state. |
| `e2e` | `tests/e2e/<flow>/` | Verify a player-facing flow. | Use `InputSim`/InputMap/UI clicks; do not call private gameplay methods. |

## Rules

- All test scenes must run headless and exit automatically.
- PASS must call `get_tree().quit(0)`.
- FAIL must call `get_tree().quit(1)`.
- Use a timeout guard so a stuck scene fails instead of hanging.
- Unit tests may call public methods directly.
- E2E tests must simulate real player input or UI actions.
- Bug fixes should add or update a failing test first, then fix the implementation.

## Runtime Commands

- `uv run godotter runtime verify`
- `uv run godotter runtime test --project . --kind unit`
- `uv run godotter runtime test --project . --kind system`
- `uv run godotter runtime test --project . --kind feature`
- `uv run godotter runtime test --project . --kind integration`
- `uv run godotter runtime test --project . --kind level-smoke`
- `uv run godotter runtime test --project . --kind e2e`
- `uv run godotter runtime test --project . --kind all`

Use `runtime verify` as the canonical post-change gate. It runs structure validation, Managers/EventBus validation, path validation with safe `--fix`, path validation again, script lint, and all tests. Use `runtime test --scene` for a single explicit test scene.

## Verify Reports

`runtime verify` writes a machine-readable report under `.godotter/reports/verify/`.

- Default output: `.godotter/reports/verify/vr_<timestamp>_<id>.json`.
- Latest pointer: `.godotter/reports/verify/latest.json`.
- Explicit output: `uv run godotter runtime verify --json-output .godotter/reports/verify/vr_manual.json`.
- Plan/Task failures should attach the latest VerifyReport path to the failed task artifacts.

## Scaffolding

Use `godotter scaffold test` to generate convention-compliant test harnesses:

- `uv run godotter scaffold test inventory --kind system`
- `uv run godotter scaffold test item_pickup --kind feature`
- `uv run godotter scaffold test pickup_flow --kind integration`
- `uv run godotter scaffold test main --kind level-smoke`
- `uv run godotter scaffold test start_game_flow --kind e2e`
