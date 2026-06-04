# Chat Backend Design

This document defines the backend model for the web conversation workflow.

The goal is not to build a browser TUI. The goal is to support a mobile-friendly, plan-first workflow:

1. User selects a registered project as the current workspace.
2. User sends a short requirement.
3. Planner returns a draft plan.
4. User reviews plan items one by one.
5. Executor runs only approved work.
6. Verification failures trigger bounded repair loops.
7. Final results are summarized into the same session.

## Core Decision

`WorkPack` is not the conversation model.

- `ChatSession` is the durable user conversation.
- `PlanReview` is the reviewable plan surface shown in the UI.
- `PlanPack` is the persisted implementation plan artifact.
- `WorkPack` is the compact execution context for one small task.
- `RunJob` is the background execution record.

The web UI should never pass arbitrary paths from the browser. A session must bind to a project registered in `config/projects.toml`.

## Storage Layout

Chat sessions should live inside the selected project's `.godotter` directory.

Reason:

- The conversation is project-specific.
- Moving or archiving a project should keep its plans, workpacks, sessions, and run history together.
- The Godotter repo should not become the central storage for every game project's chat history.

For each registered project:

```text
<project>/.godotter/
  sessions/
    cs_<id>.json
    cs_<id>/
      messages.jsonl
      reviews/
        pr_<id>.json
      runs/
        rj_<id>.json
        rj_<id>.events.jsonl
  plans/
    latest.json
    <timestamp>_plan_<id>.json
    <timestamp>_plan_<id>.state.json
  workpacks/
    latest.json
    <timestamp>_<slug>_<id>.json
```

`config/projects.toml` remains the registry that tells the web server which project roots are allowed.

The session stores relative references to project artifacts when possible, not copies of every artifact.

## Data Models

### ChatSession

```json
{
  "session_id": "cs_...",
  "created_at": "2026-06-02T12:00:00",
  "updated_at": "2026-06-02T12:30:00",
  "title": "Add Tetris scoring",
  "project_name": "tetris3",
  "workspace_root": "D:/Godots/Engines/Godotter/tmp/tetris3",
  "planner": "alibaba",
  "executor": "alibaba",
  "status": "drafting|reviewing|ready_to_run|running|blocked|completed|archived",
  "latest_review_id": "pr_...",
  "latest_run_id": "rj_..."
}
```

Rules:

- `project_name` must exist in `config/projects.toml`.
- `workspace_root` is resolved server-side from the registry.
- Session files are stored under `<workspace_root>/.godotter/sessions`.
- Provider fields are optional; defaults come from `.env`.
- The browser may choose `project_name`, but may not submit `workspace_root`.

### ChatMessage

Stored as JSONL in `messages.jsonl`.

```json
{
  "message_id": "msg_...",
  "created_at": "2026-06-02T12:01:00",
  "role": "user|assistant|system|tool",
  "kind": "text|plan_summary|run_log|approval_notice|final_summary",
  "content": "User-visible text",
  "refs": [
    {"type": "plan_review", "id": "pr_..."},
    {"type": "run_job", "id": "rj_..."}
  ]
}
```

Rules:

- Tool output should be summarized in messages and linked to event logs.
- Raw long logs go into `RunJob` events, not into the main chat thread.
- Assistant messages must state whether they are draft-only or execution-related.

### PlanReview

`PlanReview` is the UI-facing review object. It points to a `PlanPack`.

```json
{
  "review_id": "pr_...",
  "session_id": "cs_...",
  "created_at": "2026-06-02T12:05:00",
  "status": "draft|in_review|partially_approved|approved|needs_revision|archived",
  "planpack_path": "D:/.../tmp/tetris3/.godotter/plans/20260602_plan_abcd12.json",
  "items": [
    {
      "item_id": "t1",
      "title": "Add scoring manager",
      "status": "needs_review|approved|rejected|needs_revision|running|passed|failed",
      "comment": "",
      "approved_at": null,
      "run_job_id": null
    }
  ]
}
```

Rules:

- Plan item approval is explicit.
- `needs_revision` requires a comment.
- `approved` does not mean executed.
- A revised plan should preserve comments and indicate replaced items.

### RunJob

```json
{
  "run_id": "rj_...",
  "session_id": "cs_...",
  "review_id": "pr_...",
  "project_name": "tetris3",
  "status": "queued|running|blocked_for_approval|passed|failed|canceled",
  "task_ids": ["t1", "t2"],
  "created_at": "2026-06-02T12:10:00",
  "started_at": null,
  "finished_at": null,
  "max_attempts": 3,
  "same_failure_limit": 2,
  "artifacts": {
    "planpack_path": "...",
    "workpack_paths": []
  }
}
```

Rules:

- A `RunJob` can run all approved items or a chosen subset.
- It creates one `WorkPack` per plan item before execution.
- Verification failures should loop: execute -> verify -> repair -> verify.
- If the same failure repeats past the limit, mark the run `failed`.
- Dangerous operations create approval requests before execution continues.

### RunEvent

Stored as JSONL in `rj_<id>.events.jsonl`.

```json
{
  "created_at": "2026-06-02T12:11:00",
  "type": "status|stdout|stderr|verification|artifact|approval_required|summary",
  "task_id": "t1",
  "message": "runtime lint passed",
  "payload": {}
}
```

Rules:

- SSE streams `RunEvent` records to the browser.
- Main chat receives compact summaries only.
- Long stdout/stderr belongs in event logs.

### ApprovalRequest

```json
{
  "approval_id": "ar_...",
  "session_id": "cs_...",
  "run_id": "rj_...",
  "kind": "plan_item_execute|delete|overwrite|external_command|dangerous_change",
  "status": "pending|approved|rejected",
  "summary": "Delete 3 files",
  "payload": {
    "paths": [],
    "command": null,
    "diff_summary": null
  },
  "user_comment": "",
  "created_at": "2026-06-02T12:12:00",
  "resolved_at": null
}
```

Rules:

- Execution of plan items requires approval.
- Destructive actions always require approval even during an approved run.
- User comments are passed back to the planner/executor as constraints.

## API Design

### Projects

Already started:

- `GET /api/projects`
- `POST /api/projects`
  - body: `{ "name": "tetris4", "no_git": true, "set_default": true }`
  - creates a new Godot project under the server-controlled default parent, currently `<godotter-repo>/tmp/<name>`.
  - registers it in `config/projects.toml`.
  - does not accept arbitrary browser-provided workspace paths in the first implementation.
- `GET /api/projects/{name}/summary`
- `GET /api/projects/{name}/plans`
- `GET /api/projects/{name}/workpacks`

Future:

- `POST /api/workspace/select`
  - body: `{ "project_name": "tetris3" }`
  - optional if selection remains browser-local, required if sessions are server-side.

### Sessions

- `POST /api/sessions`
  - body: `{ "project_name": "tetris3", "title": "optional" }`
  - returns: `ChatSession`

- `GET /api/sessions`
  - returns recent sessions with project, status, title, updated time.

- `GET /api/sessions/{session_id}`
  - returns session metadata, messages, latest review summary, latest run summary.

- `POST /api/sessions/{session_id}/messages`
  - body: `{ "content": "short requirement" }`
  - appends user message.
  - does not execute code.

### Planning

- `POST /api/sessions/{session_id}/plan`
  - body: `{ "mode": "create|revise", "comment": "optional", "item_ids": [] }`
  - creates or revises a PlanPack under the selected project.
  - creates a PlanReview linked to the session.

- `GET /api/sessions/{session_id}/reviews/{review_id}`
  - returns review, plan items, approval states.

- `POST /api/sessions/{session_id}/reviews/{review_id}/items/{item_id}/approval`
  - body: `{ "status": "approved|rejected|needs_revision", "comment": "optional" }`

### Execution

- `POST /api/sessions/{session_id}/runs`
  - body: `{ "review_id": "pr_...", "item_ids": ["t1"], "max_attempts": 3 }`
  - only approved items can run.
  - returns `RunJob`.

- `GET /api/runs/{run_id}`
  - returns run status and artifact refs.

- `GET /api/runs/{run_id}/events`
  - SSE stream.

- `POST /api/runs/{run_id}/cancel`
  - cancels queued/running jobs if possible.

### Approvals

- `GET /api/approvals?session_id=...`
- `POST /api/approvals/{approval_id}/approve`
- `POST /api/approvals/{approval_id}/reject`

## State Machine

### Session

```text
drafting -> reviewing -> ready_to_run -> running -> completed
                     \-> blocked
                     \-> archived
```

### PlanReview

```text
draft -> in_review -> partially_approved -> approved
                  \-> needs_revision -> draft
                  \-> archived
```

### Plan Item

```text
needs_review -> approved -> running -> passed
            \-> rejected
            \-> needs_revision
                         \-> needs_review
running -> failed
```

### RunJob

```text
queued -> running -> passed
                \-> failed
                \-> blocked_for_approval -> running
                \-> canceled
```

## Security and Boundaries

- Browser chooses `project_name`; server resolves paths from `config/projects.toml`.
- Browser must never send arbitrary `workspace_root` for file reads.
- `.env` editing stays a system configuration function.
- `projects.toml` is a registry, not the active session state.
- Chat sessions are project-local under `<project>/.godotter/sessions`.
- All destructive operations require explicit approval.
- Execution logs should be scoped to the selected registered project.

## First Implementation Slice

Implement in this order:

1. `ChatSession` storage with `POST /api/sessions` and `GET /api/sessions`.
2. Message append/list using JSONL.
3. Bind session to registered `project_name`.
4. Store sessions under `<project>/.godotter/sessions`.
5. Add "New Chat" and session list in the web UI.
6. Add draft plan generation endpoint, but no execution.
7. Add PlanReview item approval UI.
8. Add RunJob execution for approved items.
9. Add SSE event stream for runs.

This keeps the web product usable before execution automation becomes complex.
