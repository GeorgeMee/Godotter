# Godotter Planning Quality Rules

Godotter plans must produce executable tasks for the execution agent, not a transcript of the planner's reasoning.

## Task Shape

Each PlanPack task should be a closed loop:

- make the required code/content change;
- include related tests in the same task when the touched area is `game/features` or `game/systems`;
- define acceptance criteria;
- define automated verification commands.

## Non-Executable Work

The planner may investigate internally, but these should not appear as executable tasks:

- pure discovery: `locate`, `find`, `identify`, `inspect`;
- pure decision: `determine`, `decide`, `choose`;
- pure analysis: `investigate`, `analyze`, `research`;
- manual-only smoke testing.

Discovery notes belong in the Plan context, not in the task list.

## Size Guidance

- Small bug or concrete runtime error: prefer 1-3 tasks.
- Feature work touching independent systems may use more tasks.
- Verification should be attached to the implementation task instead of being split into a final manual task.

## Dependency Rules

Dependencies should reference task IDs such as `t1`, `t2`. Numeric references like `1`, `2` are normalized to `t1`, `t2` for compatibility.

## Scene Wiring Checks

When a task edits `.tscn` scenes, exported `NodePath` fields, or `res://` file references, include:

- `uv run godotter runtime validate-paths`

This catches scenes where a UI/control/feature node keeps a stale relative path after being moved under a different parent, and catches missing scene/script/resource references.

If validation output includes a single safe `suggested=...` value, an executor may run:

- `uv run godotter runtime validate-paths --fix`

The fix mode only rewrites paths with unique suggestions; ambiguous paths remain errors for the agent/user to decide.
