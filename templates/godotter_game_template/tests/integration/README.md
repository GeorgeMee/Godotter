# Integration Tests

Integration tests combine multiple real systems/features in a focused harness.

Rules:

- Prefer public APIs and EventBus over private method calls.
- Keep the scene minimal; do not load a full level unless the scenario needs it.
- Always quit with `0` on PASS and `1` on FAIL.
