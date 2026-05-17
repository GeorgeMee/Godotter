from __future__ import annotations

import json
import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    args: dict[str, Any]


@dataclass(slots=True)
class Thought:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_content: Any = None
    thinking: str | None = None


class Brain(ABC):
    context_limit: int = 128_000
    last_input_tokens: int = 0

    def __init__(self, system_prompt: str | None = None, tools: list[dict[str, Any]] | None = None) -> None:
        self.system_prompt = system_prompt
        self.tools = tools or []

    @abstractmethod
    def think(self, conversation: list[dict[str, Any]]) -> Thought:
        raise NotImplementedError


class StubBrain(Brain):
    """Deterministic local brain for tool-loop testing."""

    def think(self, conversation: list[dict[str, Any]]) -> Thought:
        trailing_tools = _collect_trailing_tool_messages(conversation)
        if trailing_tools:
            rendered = [f"[{item['tool_call_id']}]\n{item['content']}" for item in trailing_tools]
            return Thought(text='\n\n'.join(rendered), raw_content={'type': 'tool-results'})

        last_user = next(
            (message for message in reversed(conversation) if message.get('role') == 'user'),
            None,
        )
        content = last_user.get('content') if last_user else 'No input received.'

        if isinstance(content, str) and content.startswith('tool '):
            _, remainder = content.split(' ', maxsplit=1)
            name, raw_args = remainder.split(' ', maxsplit=1)
            args = self._parse_tool_args(raw_args)
            return Thought(
                text=f'[stub] invoking {name}',
                tool_calls=[ToolCall(id='stub-tool-1', name=name, args=args)],
                raw_content={'type': 'tool_use', 'name': name, 'input': args},
            )

        if isinstance(content, str) and content.startswith('remember '):
            memory_content = content.removeprefix('remember ')
            return Thought(
                text='[stub] saving memory',
                tool_calls=[
                    ToolCall(
                        id='stub-tool-1',
                        name='save_memory',
                        args={'content': memory_content},
                    )
                ],
                raw_content={'type': 'tool_use', 'name': 'save_memory', 'input': {'content': memory_content}},
            )

        return Thought(
            text=f'[stub:{len(self.tools)} tools] {content}',
            raw_content={'type': 'text', 'text': content},
        )

    def _parse_tool_args(self, raw_args: str) -> dict[str, Any]:
        stripped = raw_args.strip()
        if stripped.startswith('{'):
            return json.loads(stripped)

        args: dict[str, Any] = {}
        for token in shlex.split(stripped, posix=True):
            if '=' not in token:
                raise ValueError(f'Unsupported tool argument format: {token}')
            key, value = token.split('=', maxsplit=1)
            args[key] = value
        return args


def _collect_trailing_tool_messages(conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
    trailing: list[dict[str, Any]] = []
    for item in reversed(conversation):
        if item.get('role') != 'tool':
            break
        trailing.append(item)
    trailing.reverse()
    return trailing