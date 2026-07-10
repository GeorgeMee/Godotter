from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import difflib
from hashlib import sha256
from typing import Any

from godotter.config import Settings
from godotter.context import ExecutionContext, Memory
from godotter.llm import Brain, Thought
from godotter.operations import OperationRegistry
from godotter.operations.specs import operation_result_text
from unidiff import PatchSet


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
        registry: OperationRegistry,
        memory: Memory | None = None,
        mode: str = 'plan',
        brain_name: str = 'stub',
        project_summary: str | None = None,
        operation_recorder: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.brain = brain
        self.settings = settings
        self.registry = registry
        self.memory = memory
        self.project_summary = project_summary
        self.operation_recorder = operation_recorder
        self.expose_tool_output = True
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
                if not thought.text and result and self.expose_tool_output:
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
        operation = self.registry.get(name)
        if operation is None:
            return f"Error: Tool '{name}' not found"
        if self.mode != 'act' and not operation.permissions.isdisjoint({'write', 'execute'}):
            return f"Error: Tool '{name}' is not available in plan mode"
        context = ExecutionContext(
            settings=self.settings,
            workspace_root=self.settings.workspace_root.resolve(),
            memory=self.memory,
        )
        try:
            affected_paths = self._resolve_operation_paths(name, args, context)
            before_contents = self._capture_contents(context, affected_paths)
            before_hash = self._capture_hashes(before_contents)
            result = operation.execute(context, args)
            result_text = operation_result_text(result)
            after_contents = self._capture_contents(context, affected_paths)
            after_hash = self._capture_hashes(after_contents)
            if self.operation_recorder is not None and 'write' in operation.permissions:
                self.operation_recorder(
                    {
                        'tool_name': name,
                        'args': dict(args),
                        'result_text': result_text,
                        'affected_paths': affected_paths,
                        'before_hash': before_hash,
                        'after_hash': after_hash,
                        'forward_patch': self._build_patch(affected_paths, before_contents, after_contents),
                        'inverse_patch': self._build_patch(affected_paths, after_contents, before_contents),
                        'status': 'applied',
                    }
                )
            return result_text
        except Exception as exc:
            return f'Error: {exc}'

    def _resolve_operation_paths(self, name: str, args: dict[str, Any], context: ExecutionContext) -> list[str]:
        direct_path = args.get('path')
        if isinstance(direct_path, str) and direct_path.strip():
            if name in {
                'replace_file',
                'replace_text',
                'generate_file_patch',
                'generate_text_replace_patch',
                'scene_create',
                'scene_new',
                'scene_validate',
            }:
                return [self._normalize_workspace_path(direct_path)]

        patch_text = self._extract_patch_text(name, args, context)
        if patch_text:
            return self._extract_patch_paths(patch_text, context)
        return []

    def _extract_patch_text(self, name: str, args: dict[str, Any], context: ExecutionContext) -> str | None:
        if isinstance(args.get('patch'), str) and str(args['patch']).strip():
            return str(args['patch'])
        patch_path = args.get('patch_path')
        if isinstance(patch_path, str) and patch_path.strip():
            try:
                return context.resolve_path(patch_path).read_text(encoding='utf-8')
            except Exception:
                return None
        if name == 'apply_patch_file' and isinstance(args.get('path'), str):
            try:
                return context.resolve_path(str(args['path'])).read_text(encoding='utf-8')
            except Exception:
                return None
        return None

    def _extract_patch_paths(self, patch_text: str, context: ExecutionContext) -> list[str]:
        try:
            patch_set = PatchSet(patch_text.splitlines(True))
        except Exception:
            return []
        paths: list[str] = []
        for patched_file in patch_set:
            if patched_file.is_added_file:
                raw_path = _normalize_diff_path(patched_file.target_file)
            else:
                raw_path = _normalize_diff_path(patched_file.source_file)
            try:
                paths.append(self._normalize_workspace_path(raw_path))
            except Exception:
                continue
        return list(dict.fromkeys(paths))

    def _capture_contents(self, context: ExecutionContext, paths: list[str]) -> dict[str, str | None]:
        contents: dict[str, str | None] = {}
        for raw_path in paths:
            try:
                path = context.resolve_path(raw_path)
            except Exception:
                contents[raw_path] = None
                continue
            if not path.exists() or not path.is_file():
                contents[raw_path] = None
                continue
            contents[raw_path] = path.read_text(encoding='utf-8')
        return contents

    def _capture_hashes(self, contents: dict[str, str | None]) -> dict[str, str | None]:
        hashes: dict[str, str | None] = {}
        for raw_path, content in contents.items():
            if content is None:
                hashes[raw_path] = None
                continue
            hashes[raw_path] = sha256(content.encode('utf-8')).hexdigest()
        return hashes

    def _normalize_workspace_path(self, raw_path: str) -> str:
        resolved = self.settings.workspace_root.joinpath(raw_path).resolve()
        return resolved.relative_to(self.settings.workspace_root.resolve()).as_posix()

    def _build_patch(
        self,
        paths: list[str],
        before_contents: dict[str, str | None],
        after_contents: dict[str, str | None],
    ) -> str | None:
        diffs: list[str] = []
        for raw_path in paths:
            before_text = self._normalize_patch_text(before_contents.get(raw_path))
            after_text = self._normalize_patch_text(after_contents.get(raw_path))
            diff = ''.join(
                difflib.unified_diff(
                    before_text.splitlines(True),
                    after_text.splitlines(True),
                    fromfile=f'a/{raw_path}',
                    tofile=f'b/{raw_path}',
                )
            )
            if diff.strip():
                diffs.append(diff)
        rendered = ''.join(diffs).strip()
        return rendered or None

    def _normalize_patch_text(self, content: str | None) -> str:
        if content is None:
            return ''
        return content.replace('\r\n', '\n').replace('\r', '\n')
    def _refresh_brain_context(self) -> None:
        operations = self.registry.list(audience='agent')
        if self.mode != 'act':
            operations = [op for op in operations if op.permissions.isdisjoint({'write', 'execute'})]
        self.brain.tools = [operation.tool_definition() for operation in operations]
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
                'Act mode rules: make real workspace changes using replace_text, replace_file, '
                'or apply_unified_patch. '
                'Before writing any file, use tools to understand the project structure and conventions. '
                'Read Docs/Conventions.md if it exists in the workspace. '
                'Game scenes under game/levels/ or game/features/ must NEVER contain auto-quit, '
                'get_tree().quit(), or --scene detection. Those belong only in test harnesses under tests/. '
                'Game systems should self-initialize their tick loops; do not rely on external callers to start them. '
                'Use EventBus for cross-system communication; never call methods across systems directly. '
                'Never guess command names, file paths, or directory structures 鈥?verify with tools.'
            )
        else:
            parts.append(
                'Plan mode rules: analyze and plan, do not modify files. '
                'When you need to diagnose or plan, ALWAYS use tools first '
                '(project_info, scene_inspect, read_file, list_files, search_code, etc.) '
                'to inspect the actual code and project state. '
                'Never guess command names, file paths, or directory structures 鈥?verify with tools.'
            )
        return '\n\n'.join(parts)


def _normalize_diff_path(path: str) -> str:
    if path.startswith('a/') or path.startswith('b/'):
        return path[2:]
    return path
