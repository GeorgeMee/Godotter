from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from godotter.config import Settings
from godotter.context import Memory
from godotter.llm import Brain, Thought
from godotter.tools import ToolContext, ToolRegistry


class AgentStop(Exception):
    """Raised when the interactive agent should stop."""


@dataclass(slots=True)
class AgentState:
    mode: str = 'plan'
    brain_name: str = 'stub'
    conversation: list[dict[str, Any]] = field(default_factory=list)


class Agent:
    def __init__(
        self,
        brain: Brain,
        settings: Settings,
        registry: ToolRegistry,
        memory: Memory | None = None,
        mode: str = 'plan',
        brain_name: str = 'stub',
        project_summary: str | None = None,
    ) -> None:
        self.brain = brain
        self.settings = settings
        self.registry = registry
        self.memory = memory
        self.project_summary = project_summary
        self.state = AgentState(mode=mode, brain_name=brain_name)
        self._refresh_brain_context()

    @property
    def conversation(self) -> list[dict[str, Any]]:
        return self.state.conversation

    @property
    def mode(self) -> str:
        return self.state.mode

    def switch_mode(self, mode: str) -> str:
        normalized = mode.strip().lower()
        if normalized not in {'plan', 'act'}:
            raise ValueError(f'Unsupported mode: {mode}')
        self.state.mode = normalized
        self._refresh_brain_context()
        return self.state.mode

    def handle_input(self, user_input: str) -> str:
        text = user_input.strip()
        if text == '/q':
            raise AgentStop()
        if not text:
            return ''
        if text.startswith('/mode '):
            return f'mode={self.switch_mode(text.split(maxsplit=1)[1])}'

        self.conversation.append({'role': 'user', 'content': user_input})
        return self._agentic_loop()

    def _agentic_loop(self) -> str:
        output_parts: list[str] = []
        for turn_index in range(10):
            if turn_index == 0 and self.mode == 'plan' and hasattr(self.brain, 'tool_choice'):
                setattr(self.brain, 'tool_choice', 'required')
            elif turn_index == 1 and self.mode == 'plan' and hasattr(self.brain, 'tool_choice'):
                setattr(self.brain, 'tool_choice', 'auto')

            thought = self.brain.think(self.conversation)
            self.conversation.append(self._assistant_message(thought))
            if thought.text:
                output_parts.append(thought.text)
            if not thought.tool_calls:
                break

            for tool_call in thought.tool_calls:
                result = self._execute_tool(tool_call.name, tool_call.args)
                self.conversation.append(
                    {
                        'role': 'tool',
                        'tool_call_id': tool_call.id,
                        'content': result,
                    }
                )
                if not thought.text and result:
                    output_parts.append(result)
        return '\n\n'.join(part for part in output_parts if part)

    def _assistant_message(self, thought: Thought) -> dict[str, Any]:
        message: dict[str, Any] = {
            'role': 'assistant',
            'content': thought.text,
        }
        reasoning_content = self._extract_reasoning_content(thought)
        if reasoning_content:
            message['reasoning_content'] = reasoning_content
        if thought.tool_calls:
            message['tool_calls'] = [
                {'id': tool_call.id, 'name': tool_call.name, 'args': tool_call.args}
                for tool_call in thought.tool_calls
            ]
        return message

    def _extract_reasoning_content(self, thought: Thought) -> str | None:
        if thought.thinking:
            return thought.thinking
        if isinstance(thought.raw_content, dict):
            raw_reasoning = thought.raw_content.get('reasoning_content')
            if isinstance(raw_reasoning, str) and raw_reasoning.strip():
                return raw_reasoning
        return None

    def _execute_tool(self, name: str, args: dict[str, Any]) -> str:
        if self.mode == 'act' and name == 'generate_patch':
            return "Error: Tool 'generate_patch' is not allowed in act mode. Use 'apply_patch' to modify files."
        tool = self.registry.get(name)
        if tool is None:
            return f"Error: Tool '{name}' not found"
        if self.mode != 'act' and not tool.plan_safe:
            return f"Error: Tool '{name}' is not available in plan mode"
        context = ToolContext(
            settings=self.settings,
            workspace_root=self.settings.workspace_root.resolve(),
            memory=self.memory,
        )
        try:
            return tool.execute(context, **args)
        except Exception as exc:
            return f'Error: {exc}'

    def _refresh_brain_context(self) -> None:
        self.brain.tools = self.registry.definitions(self.mode)
        self.brain.system_prompt = self._build_system_prompt()
        # Best-effort: force at least one tool call in act mode for OpenAI-compatible providers.
        if hasattr(self.brain, 'tool_choice'):
            setattr(self.brain, 'tool_choice', 'required' if self.mode == 'act' else 'auto')
        if hasattr(self.brain, 'request_timeout_s'):
            setattr(self.brain, 'request_timeout_s', 300 if self.mode == 'act' else 120)

    def _build_system_prompt(self) -> str:
        parts = []
        if self.project_summary:
            parts.append(self.project_summary)
        if self.memory is not None:
            parts.append(self.memory.content)
        parts.append(f'Current mode: {self.mode}.')
        if self.mode == 'act':
            parts.append(
                'Act mode rules: make real workspace changes using apply_patch. '
                'Before writing any file, use tools to understand the project structure and conventions. '
                'Read Docs/Conventions.md if it exists in the workspace. '
                'Game scenes under game/levels/ or game/features/ must NEVER contain auto-quit, '
                'get_tree().quit(), or --scene detection. Those belong only in test harnesses under tests/. '
                'Game systems should self-initialize their tick loops; do not rely on external callers to start them. '
                'Use EventBus for cross-system communication; never call methods across systems directly. '
                'Never guess command names, file paths, or directory structures — verify with tools.'
            )
        else:
            parts.append(
                'Plan mode rules: analyze and plan, do not modify files. '
                'When you need to diagnose or plan, ALWAYS use tools first '
                '(project_info, scene_inspect, read_file, list_files, search_code, etc.) '
                'to inspect the actual code and project state. '
                'Never guess command names, file paths, or directory structures — verify with tools.'
            )
        return '\n\n'.join(parts)
