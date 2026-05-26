from __future__ import annotations

from typing import Any

from godotter.tools.base import Tool, ToolContext
from godotter.utils.textio import read_text_utf8


IGNORED_PARTS = {'.git', '.venv', '__pycache__', 'References', '.godotter'}


def _normalize_newlines(text: str) -> str:
    return text.replace('\r\n', '\n').replace('\r', '\n')


class ReadFile(Tool):
    name = 'read_file'
    description = 'Read a UTF-8 text file from the workspace with line numbers.'
    input_schema = {
        'type': 'object',
        'properties': {
            'path': {'type': 'string', 'description': 'Path to the file relative to the workspace root.'},
        },
        'required': ['path'],
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        path = context.resolve_path(str(kwargs['path']))
        if not path.exists():
            return f'Error: File not found: {path.relative_to(context.workspace_root)}'
        content = _normalize_newlines(read_text_utf8(path))
        return ''.join(f'{index} | {line}\n' for index, line in enumerate(content.splitlines(), start=1))


class ListFiles(Tool):
    name = 'list_files'
    description = 'List files and directories under a workspace path.'
    input_schema = {
        'type': 'object',
        'properties': {
            'path': {'type': 'string', 'description': 'Directory to list, relative to the workspace root.', 'default': '.'},
        },
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        root = context.resolve_path(str(kwargs.get('path', '.')))
        if not root.exists():
            return f'Error: Path not found: {root.relative_to(context.workspace_root)}'
        entries: list[str] = []
        for path in sorted(root.rglob('*')):
            relative = path.relative_to(context.workspace_root)
            if any(part in IGNORED_PARTS for part in relative.parts):
                continue
            suffix = '/' if path.is_dir() else ''
            entries.append(f'{relative}{suffix}')
        return '\n'.join(entries) if entries else '(empty)'


class SearchCode(Tool):
    name = 'search_code'
    description = 'Search for a text string across workspace files.'
    input_schema = {
        'type': 'object',
        'properties': {
            'query': {'type': 'string', 'description': 'Search text.'},
            'path': {'type': 'string', 'description': 'Directory to search from.', 'default': '.'},
        },
        'required': ['query'],
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        query = str(kwargs['query']).lower()
        root = context.resolve_path(str(kwargs.get('path', '.')))
        matches: list[str] = []
        for path in sorted(root.rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(context.workspace_root)
            if any(part in IGNORED_PARTS for part in relative.parts):
                continue
            try:
                content = _normalize_newlines(read_text_utf8(path))
                lines = content.splitlines()
            except OSError:
                continue
            for index, line in enumerate(lines, start=1):
                if query in line.lower():
                    matches.append(f'{relative}:{index}: {line.strip()}')
        return '\n'.join(matches) if matches else 'No matches found.'
