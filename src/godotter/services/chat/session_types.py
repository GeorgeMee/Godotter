from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ChatSession:
    session_id: str
    workspace_root: Path
    project_name: str
    title: str = 'New chat'
    status: str = 'drafting'
    created_at: str = ''
    updated_at: str = ''
    latest_review_id: str | None = None
    latest_run_id: str | None = None
    base_commit: str | None = None
    checkpoint_id: str | None = None
    brain_name: str | None = None
    mode: str = 'plan'
    messages: list[dict[str, Any]] = field(default_factory=list)
    operation_history: list[dict[str, Any]] = field(default_factory=list)
    conversation_cursor: int = 0
    operation_cursor: int = 0
    summary_state: str | None = None

    def meta_dict(self) -> dict[str, object]:
        return {
            'session_id': self.session_id,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'title': self.title,
            'project_name': self.project_name,
            'workspace_root': self.workspace_root.as_posix(),
            'status': self.status,
            'latest_review_id': self.latest_review_id,
            'latest_run_id': self.latest_run_id,
            'base_commit': self.base_commit,
            'checkpoint_id': self.checkpoint_id,
            'brain_name': self.brain_name,
            'mode': self.mode,
            'conversation_cursor': self.conversation_cursor,
            'operation_cursor': self.operation_cursor,
            'summary_state': self.summary_state,
        }

    def __getitem__(self, key: str) -> object:
        return self.meta_dict()[key]

    def get(self, key: str, default: object | None = None) -> object | None:
        return self.meta_dict().get(key, default)

    def __setitem__(self, key: str, value: object) -> None:
        if not hasattr(self, key):
            raise KeyError(key)
        setattr(self, key, value)

    def with_messages(self, messages: list[dict[str, Any]]) -> 'ChatSession':
        return ChatSession(
            session_id=self.session_id,
            workspace_root=self.workspace_root,
            project_name=self.project_name,
            title=self.title,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            latest_review_id=self.latest_review_id,
            latest_run_id=self.latest_run_id,
            base_commit=self.base_commit,
            checkpoint_id=self.checkpoint_id,
            brain_name=self.brain_name,
            mode=self.mode,
            messages=messages,
            operation_history=list(self.operation_history),
            conversation_cursor=self.conversation_cursor,
            operation_cursor=self.operation_cursor,
            summary_state=self.summary_state,
        )
