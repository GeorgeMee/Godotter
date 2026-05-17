from __future__ import annotations

from godotter.tools.base import Tool


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self, mode: str) -> list[dict[str, object]]:
        if mode == 'act':
            return [tool.definition() for tool in self._tools.values()]
        return [tool.definition() for tool in self._tools.values() if tool.plan_safe]

    def names(self, mode: str) -> list[str]:
        return [definition['name'] for definition in self.definitions(mode)]