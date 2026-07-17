from __future__ import annotations

from collections.abc import Callable
import secrets
from pathlib import Path
from typing import Any

from godotter.config import Settings, get_settings
from godotter.services.chat.session_repository import ChatSessionRepository
from godotter.services.chat.session_types import ChatSession
from godotter.services.project.patches import PatchService


class SessionService:
    def __init__(self, settings: Settings | None = None, repository: ChatSessionRepository | None = None) -> None:
        self._base_settings = settings or get_settings()
        self._repository = repository

    @property
    def has_repository(self) -> bool:
        return self._repository is not None

    def _require_repository(self) -> ChatSessionRepository:
        if self._repository is None:
            raise ValueError('ChatSessionRepository is required for persisted session operations.')
        return self._repository

    def _default_workspace_root(self, workspace_root: Path | None = None) -> Path:
        if workspace_root is not None:
            return workspace_root.resolve()
        return Path(getattr(self._base_settings, 'workspace_root', Path('.'))).resolve()

    def save_session(self, session: ChatSession) -> ChatSession:
        if self._repository is not None:
            self._repository.save_session(session)
        return session

    def create_session(
        self,
        project_name: str,
        *,
        title: str = 'Chat',
        session_id: str | None = None,
        workspace_root: Path | None = None,
        status: str = 'drafting',
        mode: str = 'plan',
        brain_name: str | None = None,
        summary_state: str | None = None,
    ) -> ChatSession:
        if self._repository is not None:
            session = self._repository.create_session(
                project_name,
                title=title,
                session_id=session_id,
                status=status,
                mode=mode,
                brain_name=brain_name,
                summary_state=summary_state,
            )
            session.workspace_root = self._repository.workspace_root
            session.project_name = project_name
            session.title = title.strip() or session.title
            session.status = status
            session.mode = mode
            session.brain_name = brain_name
            session.summary_state = summary_state
            self._repository.save_session(session)
            return session
        return ChatSession(
            session_id=session_id or _new_id('cs'),
            workspace_root=self._default_workspace_root(workspace_root),
            project_name=project_name,
            title=title.strip() or 'New chat',
            status=status,
            brain_name=brain_name,
            mode=mode,
            summary_state=summary_state,
        )

    def load_session(self, session_id: str) -> ChatSession:
        return self._require_repository().load_session(session_id)

    def list_sessions(
        self,
        *,
        project_name: str | None = None,
        status: str = 'all',
    ) -> list[ChatSession]:
        sessions = self._require_repository().list_sessions()
        if project_name:
            sessions = [session for session in sessions if session.project_name == project_name]
        normalized_status = status.strip().lower()
        if normalized_status == 'active':
            sessions = [session for session in sessions if session.status != 'archived']
        elif normalized_status == 'archived':
            sessions = [session for session in sessions if session.status == 'archived']
        elif normalized_status != 'all':
            raise ValueError('status must be active, archived, or all')
        return sessions

    def session_detail(self, session_id: str) -> dict[str, object]:
        session = self._require_repository().load_session(session_id)
        return {
            'ok': True,
            'session': session,
            'messages': list(session.messages),
            'operations': list(session.operation_history),
            'latest_review': None,
        }

    def set_status(self, session: ChatSession, status: str) -> ChatSession:
        session.status = status
        session.updated_at = _now_iso()
        return self.save_session(session)

    def archive_session(self, session: ChatSession) -> ChatSession:
        return self.set_status(session, 'archived')

    def set_latest_review(self, session: ChatSession, review_id: str | None, *, status: str | None = None) -> ChatSession:
        session.latest_review_id = review_id
        if status is not None:
            session.status = status
        session.updated_at = _now_iso()
        return self.save_session(session)

    def set_latest_run(self, session: ChatSession, run_id: str | None, *, status: str | None = None) -> ChatSession:
        session.latest_run_id = run_id
        if status is not None:
            session.status = status
        session.updated_at = _now_iso()
        return self.save_session(session)

    def load_or_create_session(
        self,
        project_name: str,
        *,
        session_id: str | None = None,
        title: str = 'Chat',
        workspace_root: Path | None = None,
        status: str = 'drafting',
        mode: str = 'plan',
        brain_name: str | None = None,
        summary_state: str | None = None,
    ) -> ChatSession:
        if self._repository is None:
            return self.create_session(
                project_name,
                title=title,
                session_id=session_id,
                workspace_root=workspace_root,
                status=status,
                mode=mode,
                brain_name=brain_name,
                summary_state=summary_state,
            )
        if session_id:
            try:
                session = self._repository.load_session(session_id)
            except FileNotFoundError:
                session = self.create_session(
                    project_name,
                    title=title,
                    session_id=session_id,
                    workspace_root=workspace_root,
                    status=status,
                    mode=mode,
                    brain_name=brain_name,
                    summary_state=summary_state,
                )
        else:
            session = self.create_session(
                project_name,
                title=title,
                workspace_root=workspace_root,
                status=status,
                mode=mode,
                brain_name=brain_name,
                summary_state=summary_state,
            )
        session.workspace_root = self._repository.workspace_root
        self._repository.save_session(session)
        return session

    def build_operation_recorder(self, session: ChatSession) -> Callable[[dict[str, Any]], None]:
        def _record(record: dict[str, Any]) -> None:
            self.record_operation(
                session,
                tool_name=str(record.get('tool_name') or ''),
                args=dict(record.get('args') or {}),
                result_text=record.get('result_text') if isinstance(record.get('result_text'), str) else None,
                affected_paths=[str(path) for path in record.get('affected_paths') or [] if str(path).strip()],
                before_hash=record.get('before_hash') if isinstance(record.get('before_hash'), dict) else None,
                after_hash=record.get('after_hash') if isinstance(record.get('after_hash'), dict) else None,
                forward_patch=record.get('forward_patch') if isinstance(record.get('forward_patch'), str) else None,
                inverse_patch=record.get('inverse_patch') if isinstance(record.get('inverse_patch'), str) else None,
                status=str(record.get('status') or 'applied'),
            )

        return _record

    def record_operation(
        self,
        session: ChatSession,
        *,
        tool_name: str,
        args: dict[str, object],
        result_text: str | None = None,
        before_hash: dict[str, str | None] | None = None,
        after_hash: dict[str, str | None] | None = None,
        forward_patch: str | None = None,
        inverse_patch: str | None = None,
        affected_paths: list[str] | None = None,
        status: str = 'applied',
        checkpoint_id: str | None = None,
        base_commit: str | None = None,
    ) -> dict[str, object]:
        record = {
            'operation_id': _new_id('op'),
            'created_at': _now_iso(),
            'tool_name': tool_name,
            'args': args,
            'before_hash': before_hash,
            'after_hash': after_hash,
            'forward_patch': forward_patch,
            'inverse_patch': inverse_patch,
            'affected_paths': affected_paths or [],
            'result_text': result_text,
            'status': status,
            'checkpoint_id': checkpoint_id or session.checkpoint_id,
            'base_commit': base_commit or session.base_commit,
        }
        session.operation_history.append(record)
        session.operation_cursor = len(session.operation_history)
        session.updated_at = _now_iso()
        self.save_session(session)
        return record

    def update_checkpoint(
        self,
        session: ChatSession,
        *,
        checkpoint_id: str | None = None,
        base_commit: str | None = None,
        summary_state: str | None = None,
    ) -> ChatSession:
        if checkpoint_id is not None:
            session.checkpoint_id = checkpoint_id
        if base_commit is not None:
            session.base_commit = base_commit
        if summary_state is not None:
            session.summary_state = summary_state
        session.updated_at = _now_iso()
        self.save_session(session)
        return session

    def rollback_last_operation(self, session: ChatSession) -> dict[str, object]:
        index = self._find_last_rollbackable_operation_index(session.operation_history)
        if index is None:
            raise ValueError('No rollbackable operation found.')

        operation = session.operation_history[index]
        inverse_patch = operation.get('inverse_patch')
        if not isinstance(inverse_patch, str) or not inverse_patch.strip():
            raise ValueError('The last operation does not have an inverse patch.')

        result = PatchService(session.workspace_root).apply_unified_patch(inverse_patch)
        operation['status'] = 'reverted'
        operation['reverted_at'] = _now_iso()

        rollback_record = self.record_operation(
            session,
            tool_name='rollback_operation',
            args={
                'target_operation_id': str(operation.get('operation_id') or ''),
                'target_tool_name': str(operation.get('tool_name') or ''),
            },
            result_text='Rolled back the last operation.',
            before_hash=operation.get('after_hash') if isinstance(operation.get('after_hash'), dict) else None,
            after_hash=operation.get('before_hash') if isinstance(operation.get('before_hash'), dict) else None,
            forward_patch=inverse_patch,
            inverse_patch=operation.get('forward_patch') if isinstance(operation.get('forward_patch'), str) else None,
            affected_paths=result.applied_paths,
            status='applied',
        )

        self.save_session(session)
        return rollback_record

    def append_message(
        self,
        session: ChatSession,
        *,
        role: str,
        content: str,
        kind: str = 'text',
        refs: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        repository = self._require_repository()
        message = repository.append_message(
            session.session_id,
            project_name=session.project_name,
            role=role,
            content=content,
            kind=kind,
            refs=refs,
        )
        updated = repository.load_session(session.session_id)
        session.messages = list(updated.messages)
        session.title = updated.title
        session.status = updated.status
        session.updated_at = updated.updated_at
        session.project_name = updated.project_name
        session.workspace_root = updated.workspace_root
        session.brain_name = updated.brain_name
        session.mode = updated.mode
        session.conversation_cursor = updated.conversation_cursor
        session.operation_cursor = updated.operation_cursor
        session.summary_state = updated.summary_state
        return message

    def _find_last_rollbackable_operation_index(self, operation_history: list[dict[str, object]]) -> int | None:
        for index in range(len(operation_history) - 1, -1, -1):
            operation = operation_history[index]
            if str(operation.get('status') or 'applied') != 'applied':
                continue
            if isinstance(operation.get('inverse_patch'), str) and str(operation['inverse_patch']).strip():
                return index
        return None


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec='seconds')


def _new_id(prefix: str) -> str:
    return f'{prefix}_{secrets.token_hex(8)}'

