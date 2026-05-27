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
        self.tool_choice: str | dict[str, Any] = 'auto'

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
            payload['tool_choice'] = self.tool_choice

        try:
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
        except requests.HTTPError as exc:
            # Some OpenAI-compatible providers do not support tool_choice="required".
            # Retry once with tool_choice="auto" to avoid hard failure.
            if self.tools and self.tool_choice == 'required' and exc.response is not None:
                try:
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
                except requests.RequestException as inner:
                    raise RuntimeError(self._format_http_error(getattr(inner, 'response', None))) from inner
            else:
                raise RuntimeError(self._format_http_error(exc.response)) from exc
        except requests.RequestException as exc:
            raise RuntimeError(
                f'LLM request failed: provider={self.provider.name} model={self.provider.model} error={type(exc).__name__}: {exc}'
            ) from exc

        data = response.json()
        self.last_input_tokens = data.get('usage', {}).get('prompt_tokens', 0)
        message = data['choices'][0]['message']
        tool_calls = [self._parse_tool_call(tool_call) for tool_call in message.get('tool_calls', [])]
        return Thought(
            text=message.get('content'),
            tool_calls=tool_calls,
            raw_content=message,
            thinking=message.get('reasoning_content'),
        )

    def _build_messages(self, conversation: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if self.system_prompt:
            messages.append({'role': 'system', 'content': self.system_prompt})
        for item in conversation:
            role = item['role']
            if role == 'assistant':
                entry: dict[str, Any] = {'role': 'assistant', 'content': item.get('content')}
                reasoning_content = item.get('reasoning_content')
                if reasoning_content:
                    entry['reasoning_content'] = reasoning_content
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

    def _format_http_error(self, response: requests.Response | None) -> str:
        if response is None:
            return f'LLM request failed: provider={self.provider.name} model={self.provider.model} error=http_error'

        return (
            f'LLM request failed: provider={self.provider.name} model={self.provider.model} '
            f'status_code={response.status_code} body={_safe_json_preview(response)}'
        )


def _safe_json_preview(response: requests.Response, max_len: int = 400) -> str:
    try:
        data = response.json()
        text = json.dumps(data, ensure_ascii=False)
    except ValueError:
        text = (response.text or '').strip()

    text = ' '.join(text.split())
    if len(text) > max_len:
        return f'{text[:max_len]}...'
    return text or '(empty)'
