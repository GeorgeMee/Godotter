from __future__ import annotations

from typing import Any

from godotter.tools.base import Tool, ToolContext


class SaveMemory(Tool):
    name = 'save_memory'
    description = 'Persist memory content for future runs.'
    input_schema = {
        'type': 'object',
        'properties': {
            'content': {'type': 'string', 'description': 'The full memory content to persist.'},
        },
        'required': ['content'],
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        if context.memory is None:
            return 'Error: Memory is not configured.'
        content = str(kwargs['content'])
        context.memory.save(content)
        return 'Memory updated.'