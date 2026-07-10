from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
import secrets
from datetime import datetime

from godotter.services.chat.session_types import ChatSession


@dataclass(slots=True)
class ChatSessionRepository:
    workspace_root: Path

    def session_meta_path(self, session_id: str) -> Path:
        return self.sessions_dir / f'{session_id}.json'

    def session_data_dir(self, session_id: str) -> Path:
        return self.sessions_dir / session_id

    def session_messages_path(self, session_id: str) -> Path:
        return self.session_data_dir(session_id) / 'messages.jsonl'

    def session_operations_path(self, session_id: str) -> Path:
        return self.session_data_dir(session_id) / 'operations.jsonl'

    @property
    def sessions_dir(self) -> Path:
        return self.workspace_root / '.godotter' / 'sessions'

    def create_session(self, project_name: str, *, title: str = '', session_id: str | None = None, now_iso: str | None = None) -> ChatSession:
        resolved_session_id = session_id or _new_id('cs')
        timestamp = now_iso or _now_iso()
        session = ChatSession(
            session_id=resolved_session_id,
            workspace_root=self.workspace_root,
            project_name=project_name,
            title=title.strip() or 'New chat',
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._write_json(self.session_meta_path(resolved_session_id), session.meta_dict())
        self.session_data_dir(resolved_session_id).mkdir(parents=True, exist_ok=True)
        self.session_messages_path(resolved_session_id).write_text('', encoding='utf-8', newline='\n')
        self.session_operations_path(resolved_session_id).write_text('', encoding='utf-8', newline='\n')
        return session

    def list_sessions(self) -> list[ChatSession]:
        if not self.sessions_dir.exists():
            return []
        paths = [path for path in self.sessions_dir.glob('cs_*.json') if path.is_file()]
        sessions = []
        for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                sessions.append(self._session_from_dict(self._read_json(path)))
            except Exception:
                continue
        return sessions

    def load_session(self, session_id: str) -> ChatSession:
        path = self.session_meta_path(session_id)
        if not path.exists():
            raise FileNotFoundError(session_id)
        return self._session_from_dict(
            self._read_json(path),
            messages=self.read_messages(session_id),
            operation_history=self.read_operations(session_id),
        )

    def read_messages(self, session_id: str) -> list[dict[str, object]]:
        path = self.session_messages_path(session_id)
        if not path.exists():
            return []
        messages = []
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                messages.append(json.loads(line))
            except Exception:
                continue
        return messages

    def read_operations(self, session_id: str) -> list[dict[str, object]]:
        path = self.session_operations_path(session_id)
        if not path.exists():
            return []
        operations = []
        for line in path.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                operations.append(json.loads(line))
            except Exception:
                continue
        return operations

    def append_message(
        self,
        session_id: str,
        *,
        project_name: str,
        role: str,
        content: str,
        kind: str = 'text',
        refs: list[dict[str, object]] | None = None,
        now_iso: str | None = None,
    ) -> dict[str, object]:
        session = self.load_session(session_id)
        content = content.strip()
        if not content:
            raise ValueError('message_content_required')
        if role not in {'user', 'assistant', 'system', 'tool'}:
            raise ValueError('invalid_message_role')

        timestamp = now_iso or _now_iso()
        message = {
            'message_id': _new_id('msg'),
            'created_at': timestamp,
            'role': role,
            'kind': kind,
            'content': content,
            'refs': refs or [],
        }
        path = self.session_messages_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8', newline='\n') as handle:
            handle.write(json.dumps(message, ensure_ascii=False) + '\n')

        session.updated_at = timestamp
        if session.title == 'New chat' and role == 'user':
            session.title = content[:48]
        session.project_name = project_name
        session.workspace_root = self.workspace_root
        self._write_json(self.session_meta_path(session_id), session.meta_dict())
        return message

    def save_session(self, session: ChatSession) -> None:
        self._write_json(self.session_meta_path(session.session_id), session.meta_dict())
        self._write_jsonl(self.session_operations_path(session.session_id), session.operation_history)

    def append_operation(
        self,
        session_id: str,
        *,
        operation: dict[str, object],
        now_iso: str | None = None,
    ) -> dict[str, object]:
        session = self.load_session(session_id)
        timestamp = now_iso or _now_iso()
        record = dict(operation)
        record['created_at'] = timestamp
        if not record.get('operation_id'):
            record['operation_id'] = _new_id('op')
        session.operation_history.append(record)
        session.operation_cursor = len(session.operation_history)
        session.updated_at = timestamp
        self.save_session(session)
        return record

    def update_checkpoint(
        self,
        session_id: str,
        *,
        checkpoint_id: str | None = None,
        base_commit: str | None = None,
        summary_state: str | None = None,
        conversation_cursor: int | None = None,
        operation_cursor: int | None = None,
        now_iso: str | None = None,
    ) -> ChatSession:
        session = self.load_session(session_id)
        timestamp = now_iso or _now_iso()
        if checkpoint_id is not None:
            session.checkpoint_id = checkpoint_id
        if base_commit is not None:
            session.base_commit = base_commit
        if summary_state is not None:
            session.summary_state = summary_state
        if conversation_cursor is not None:
            session.conversation_cursor = conversation_cursor
        if operation_cursor is not None:
            session.operation_cursor = operation_cursor
        session.updated_at = timestamp
        self.save_session(session)
        return session

    def session_detail(self, session_id: str) -> dict[str, object]:
        session = self.load_session(session_id)
        return {
            'ok': True,
            'session': session.meta_dict(),
            'messages': self.read_messages(session_id),
            'latest_review': None,
        }

    def _write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')

    def _write_jsonl(self, path: Path, payloads: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('w', encoding='utf-8', newline='\n') as handle:
            for payload in payloads:
                handle.write(json.dumps(payload, ensure_ascii=False) + '\n')

    def _read_json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding='utf-8'))

    def _session_from_dict(
        self,
        payload: dict[str, object],
        *,
        messages: list[dict[str, object]] | None = None,
        operation_history: list[dict[str, object]] | None = None,
    ) -> ChatSession:
        workspace_root = Path(str(payload.get('workspace_root') or self.workspace_root))
        return ChatSession(
            session_id=str(payload.get('session_id') or _new_id('cs')),
            workspace_root=workspace_root,
            project_name=str(payload.get('project_name') or ''),
            title=str(payload.get('title') or 'New chat'),
            status=str(payload.get('status') or 'drafting'),
            created_at=str(payload.get('created_at') or ''),
            updated_at=str(payload.get('updated_at') or ''),
            latest_review_id=_optional_string(payload.get('latest_review_id')),
            latest_run_id=_optional_string(payload.get('latest_run_id')),
            base_commit=_optional_string(payload.get('base_commit')),
            checkpoint_id=_optional_string(payload.get('checkpoint_id')),
            brain_name=_optional_string(payload.get('brain_name')),
            mode=str(payload.get('mode') or 'plan'),
            conversation_cursor=int(payload.get('conversation_cursor') or 0),
            operation_cursor=int(payload.get('operation_cursor') or 0),
            summary_state=_optional_string(payload.get('summary_state')),
            messages=messages or [],
            operation_history=operation_history or [],
        )


def _new_id(prefix: str) -> str:
    return f'{prefix}_{secrets.token_hex(8)}'


def _now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
