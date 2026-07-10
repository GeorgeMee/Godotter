from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import secrets
from pathlib import Path
from typing import Any

from godotter.agent import Agent
from godotter.config import Settings, get_settings
from godotter.context import Memory, build_chat_scout_context, build_project_summary, render_project_summary
from godotter.llm import create_brain
from godotter.operations import build_default_operations
from godotter.services.project.patches import PatchService
from godotter.services.chat.session_types import ChatSession
from godotter.services.chat.session_repository import ChatSessionRepository


@dataclass(slots=True)
class ChatReplyResult:
    reply_text: str
    conversation: list[dict[str, Any]]
    workspace_root: Path
    brain_name: str
    mode: str
    user_message: str
    enriched_message: str


class ChatSessionService:
    def __init__(self, settings: Settings | None = None, repository: ChatSessionRepository | None = None) -> None:
        self._base_settings = settings or get_settings()
        self._repository = repository

    def _copy_settings(self, base: Settings, **overrides: Any) -> Settings:
        try:
            return base.model_copy(update=overrides)
        except AttributeError:
            return base

    def build_agent(
        self,
        *,
        workspace_root: Path,
        brain_name: str | None = None,
        fallback_brain_name: str | None = None,
        mode: str = 'plan',
        operation_recorder: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[Agent, Settings, str]:
        settings = self._copy_settings(self._base_settings, workspace_root=workspace_root.resolve())
        default_brain = getattr(settings, 'resolved_chat_brain', getattr(settings, 'default_brain', 'stub'))
        selected_brain = brain_name or fallback_brain_name or default_brain
        memory = Memory(settings.resolved_memory_path)
        registry = build_default_operations()

        summary = build_project_summary(workspace_root)
        summary_text = render_project_summary(summary) if summary else None

        agent = Agent(
            brain=create_brain(settings, selected_brain, model_override=getattr(settings, 'chat_model', None)),
            settings=settings,
            registry=registry,
            memory=memory,
            mode=mode,
            brain_name=selected_brain,
            project_summary=summary_text,
            operation_recorder=operation_recorder,
        )
        return agent, settings, selected_brain

    def generate_reply(
        self,
        *,
        workspace_root: Path,
        messages: list[dict[str, object]],
        brain_name: str | None = None,
        fallback_brain_name: str | None = None,
        mode: str = 'plan',
        no_scout: bool = False,
        expose_tool_output: bool = True,
        history_limit: int = 20,
        operation_recorder: Callable[[dict[str, Any]], None] | None = None,
    ) -> ChatReplyResult:
        agent, _settings, selected_brain = self.build_agent(
            workspace_root=workspace_root,
            brain_name=brain_name,
            fallback_brain_name=fallback_brain_name,
            mode=mode,
            operation_recorder=operation_recorder,
        )
        agent.expose_tool_output = expose_tool_output
        if hasattr(agent.brain, 'tool_choice'):
            setattr(agent.brain, 'tool_choice', 'auto')

        last_user_index = self._find_last_user_message_index(messages)
        if last_user_index is None:
            raise ValueError('No user message found in chat history.')

        for msg in self._iter_history_messages(messages, last_user_index, history_limit):
            agent.conversation.append(msg)

        user_message = str(messages[last_user_index].get('content', '')).strip()
        enriched_message = user_message
        if not no_scout:
            scout_context = build_chat_scout_context(workspace_root, user_message)
            if scout_context:
                enriched_message = f'{user_message}\n\n--- Relevant project context (auto-scanned) ---\n{scout_context}'

        agent.conversation.append({'role': 'user', 'content': enriched_message})
        reply_text = agent._agentic_loop()
        return ChatReplyResult(
            reply_text=reply_text,
            conversation=list(agent.conversation),
            workspace_root=workspace_root,
            brain_name=selected_brain,
            mode=mode,
            user_message=user_message,
            enriched_message=enriched_message,
        )

    def generate_reply_for_session(
        self,
        session: ChatSession,
        *,
        brain_name: str | None = None,
        fallback_brain_name: str | None = None,
        no_scout: bool = False,
        expose_tool_output: bool = True,
        history_limit: int = 20,
    ) -> ChatReplyResult:
        result = self.generate_reply(
            workspace_root=session.workspace_root,
            messages=session.messages,
            brain_name=brain_name or session.brain_name,
            fallback_brain_name=fallback_brain_name,
            mode=session.mode,
            no_scout=no_scout,
            expose_tool_output=expose_tool_output,
            history_limit=history_limit,
            operation_recorder=self._build_operation_recorder(session),
        )
        session.messages = list(result.conversation)
        session.conversation_cursor = len(session.messages)
        session.updated_at = _now_iso()
        session.brain_name = result.brain_name
        session.mode = result.mode
        return result

    def _build_operation_recorder(self, session: ChatSession) -> Callable[[dict[str, Any]], None]:
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
        if self._repository is not None:
            self._repository.save_session(session)
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
        if self._repository is not None:
            self._repository.save_session(session)
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

        if self._repository is not None:
            self._repository.save_session(session)
        return rollback_record

    def _find_last_user_message_index(self, messages: list[dict[str, object]]) -> int | None:
        for index in range(len(messages) - 1, -1, -1):
            if str(messages[index].get('role', '')).strip() == 'user' and str(messages[index].get('content', '')).strip():
                return index
        return None

    def _find_last_rollbackable_operation_index(self, operation_history: list[dict[str, object]]) -> int | None:
        for index in range(len(operation_history) - 1, -1, -1):
            operation = operation_history[index]
            if str(operation.get('status') or 'applied') != 'applied':
                continue
            if isinstance(operation.get('inverse_patch'), str) and str(operation['inverse_patch']).strip():
                return index
        return None

    def _iter_history_messages(
        self,
        messages: list[dict[str, object]],
        last_user_index: int,
        history_limit: int,
    ) -> list[dict[str, object]]:
        history: list[dict[str, object]] = []
        for msg in messages[max(0, last_user_index - history_limit) : last_user_index]:
            role = str(msg.get('role', '')).strip()
            content = str(msg.get('content', '')).strip()
            if not content or role not in {'user', 'assistant', 'tool'}:
                continue
            item: dict[str, object] = {'role': role, 'content': content}
            tool_call_id = msg.get('tool_call_id')
            if role == 'tool' and isinstance(tool_call_id, str) and tool_call_id.strip():
                item['tool_call_id'] = tool_call_id.strip()
            reasoning_content = msg.get('reasoning_content')
            if isinstance(reasoning_content, str) and reasoning_content.strip():
                item['reasoning_content'] = reasoning_content.strip()
            history.append(item)
        return history


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec='seconds')


def _new_id(prefix: str) -> str:
    return f'{prefix}_{secrets.token_hex(8)}'
