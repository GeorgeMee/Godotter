# Web Conversation Flow

## Current state

Godotter already has useful execution primitives, but it does not yet have a real web conversation workflow.

- `Agent.conversation` is an in-memory list owned by one agent instance.
- `PlanPack` and `WorkPack` are persisted files, but they are not threaded back into a chat session.
- `plan prepare` is a one-shot command: it receives one goal, writes a plan, then exits.
- `plan run` executes a PlanPack and records task status, but it is not driven by approvals/comments from a conversation UI.
- The web console currently exposes status and `.env` editing, not real chat/session orchestration.

This means the website can look like a chat app, but without more backend work it is not yet a reliable "discuss first, approve later, execute after approval" product.

## Target workflow

The web workflow should be stateful and plan-first by default.

1. User submits a short requirement.
2. Planner creates a draft plan without executing code.
3. User approves, rejects, or comments on individual plan items.
4. Planner can regenerate only the rejected/commented items.
5. Executor runs approved items as small tasks.
6. Verification failures trigger bounded self-repair loops.
7. Final result is summarized back into the same thread.

## Required backend concepts

Add these concepts before wiring the UI to real execution:

- `ChatSession`: persistent thread metadata, workspace, provider, status, timestamps.
- `ChatMessage`: user/assistant/system/tool messages with optional attachments and command outputs.
- `PlanReview`: a PlanPack linked to one ChatSession with per-item approval state.
- `ApprovalComment`: comments attached to a specific plan item or dangerous operation.
- `RunJob`: background execution record linked to approved plan items.
- `NotificationEvent`: aggregated events for plan-ready, needs-approval, run-finished, and run-failed.

## Minimal API shape

The first real implementation should avoid a full TUI clone. Keep the API small:

- `POST /api/sessions`: create a session for a workspace.
- `GET /api/sessions`: list recent sessions.
- `GET /api/sessions/{id}`: fetch messages, latest plan, approvals, and run status.
- `POST /api/sessions/{id}/messages`: append a user message.
- `POST /api/sessions/{id}/plan`: generate or regenerate a draft PlanPack.
- `POST /api/sessions/{id}/plan/items/{item_id}/approval`: approve/reject/comment on one item.
- `POST /api/sessions/{id}/runs`: execute approved items.
- `GET /api/runs/{id}/events`: stream logs/status with SSE first; WebSocket can wait.

## Product rule

Do not rely on prompts alone for this workflow. Prompts can instruct the model, but approvals, execution gates, retry limits, and dangerous-operation checks must be represented as explicit server-side state and enforced by code.

## Detailed design

See `Docs/web_console/chat_backend.md` for the concrete backend model, storage layout, API shape, state machines, and implementation order.
