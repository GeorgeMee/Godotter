# E2E Tests

E2E tests verify player-facing flows using real input or UI actions.

Rules:

- Load the real scene or entry flow being tested.
- Use `tests/core/input_sim.gd`, InputMap actions, or UI button signals.
- Do not call private gameplay methods directly.
- Always include timeout protection and auto-exit.
