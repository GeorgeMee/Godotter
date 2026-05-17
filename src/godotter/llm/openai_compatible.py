from __future__ import annotations

import json
from typing import Any

import requests

from godotter.llm.providers import ProviderSpec
from godotter.llm.types import Brain, Thought, ToolCall


class OpenAICompatibleBrain(Brain):
    def __init__(self, provider: ProviderSpec, system_prompt: str | None = None, tools: list[dict[str, Any]] | None = None) -> None:
        super().__init__(system_prompt=system_prompt, tools=tools)
        self.provider = provider

    def think(self, conversation: list[dict[str, Any]]) -> Thought:
        payload: dict[str, Any] = {
            'model': self.provider.model,
            'messages': self._build_messages(conversation),
        }
        if self.tools:
            payload['tools'] = [
                {
                    'type': 'function',
                    'function': {
                        'name': tool['name'],
                        'description': tool['description'],
                        'parameters': tool['input_schema'],
                    },
                }
                for tool in self.tools
            ]
            payload['tool_choice'] = 'auto'

        response = requests.post(
            f'{self.provider.base_url}/chat/completions',
            headers={
                'Authorization': f'Bearer {self.provider.api_key}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        self.last_input_tokens = data.get('usage', {}).get('prompt_tokens', 0)
        message = data['choices'][0]['message']
        tool_calls = [self._parse_tool_call(tool_call) for tool_call in message.get('tool_calls', [])]
        return Thought(
            text=message.get('content'),
            tool_calls=tool_calls,
            raw_content=message,
        )

    def _build_messages(self, conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if self.system_prompt:
            messages.append({'role': 'system', 'content': self.system_prompt})
        for item in conversation:
            role = item['role']
            if role == 'assistant':
                entry: dict[str, Any] = {'role': 'assistant', 'content': item.get('content')}
                if item.get('tool_calls'):
                    entry['tool_calls'] = [
                        {
                            'id': tool_call['id'],
                            'type': 'function',
                            'function': {
                                'name': tool_call['name'],
                                'arguments': json.dumps(tool_call['args'], ensure_ascii=False),
                            },
                        }
                        for tool_call in item['tool_calls']
                    ]
                messages.append(entry)
                continue
            if role == 'tool':
                messages.append(
                    {
                        'role': 'tool',
                        'tool_call_id': item['tool_call_id'],
                        'content': item['content'],
                    }
                )
                continue
            messages.append({'role': role, 'content': item['content']})
        return messages

    def _parse_tool_call(self, tool_call: dict[str, Any]) -> ToolCall:
        function = tool_call['function']
        arguments = function.get('arguments') or '{}'
        return ToolCall(
            id=tool_call['id'],
            name=function['name'],
            args=json.loads(arguments),
        )