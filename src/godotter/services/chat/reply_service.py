from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from godotter.agent import Agent
from godotter.config import Settings, get_settings
from godotter.context import Memory, build_chat_scout_context, build_project_summary, render_project_summary
from godotter.llm import create_brain
from godotter.operations import build_default_operations
from godotter.services.chat.session_service import SessionService
from godotter.services.chat.session_types import ChatSession


@dataclass(slots=True)
class ChatReplyResult:
    reply_text: str
    conversation: list[dict[str, Any]]
    workspace_root: Path
    brain_name: str
    mode: str
    user_message: str
    enriched_message: str


class ReplyService:
    def __init__(self, settings: Settings | None = None, session_service: SessionService | None = None) -> None:
        self._base_settings = settings or get_settings()
        self._session_service = session_service

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
        operation_recorder = (
            self._session_service.build_operation_recorder(session) if self._session_service is not None else None
        )
        result = self.generate_reply(
            workspace_root=session.workspace_root,
            messages=session.messages,
            brain_name=brain_name or session.brain_name,
            fallback_brain_name=fallback_brain_name,
            mode=session.mode,
            no_scout=no_scout,
            expose_tool_output=expose_tool_output,
            history_limit=history_limit,
            operation_recorder=operation_recorder,
        )
        session.messages = list(result.conversation)
        session.conversation_cursor = len(session.messages)
        session.updated_at = _now_iso()
        session.brain_name = result.brain_name
        session.mode = result.mode
        if self._session_service is not None:
            self._session_service.save_session(session)
        return result

    def _find_last_user_message_index(self, messages: list[dict[str, object]]) -> int | None:
        for index in range(len(messages) - 1, -1, -1):
            if str(messages[index].get('role', '')).strip() == 'user' and str(messages[index].get('content', '')).strip():
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

