# Godotter Web Console

This directory stores product and engineering design notes for the Godotter web console.

The target workflow is:

1. Select a registered project as the current workspace.
2. Chat about a requirement.
3. Generate a draft plan without execution.
4. Review and approve plan items.
5. Execute approved items.
6. Stream logs and verification results.
7. Summarize the outcome in the same session.

## Documents

- `architecture.md`: high-level system architecture.
- `api.md`: early API draft.
- `chat_backend.md`: concrete backend model for sessions, messages, reviews, runs, and approvals.
- `conversation_flow.md`: product-level conversation workflow and current gaps.
- `dev_and_deploy.md`: development and deployment notes.
- `notifications.md`: notification strategy.
- `state_machine.md`: early state machine draft.

## Current Priority

The next backend milestone should be `ChatSession` persistence.

Do not wire the homepage prompt directly to `plan prepare` yet. The web product needs durable session state first, otherwise plan discussion, approval comments, and execution history cannot be represented correctly.
