from __future__ import annotations

from pathlib import Path
from typing import Any

from godotter.tools.base import Tool, ToolContext


class ValidateProject(Tool):
    name = 'validate_project'
    description = 'Run lightweight workspace validation for the current project structure.'
    input_schema = {
        'type': 'object',
        'properties': {},
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        checks: list[str] = []
        root = context.workspace_root

        checks.append(_check_exists(root / 'pyproject.toml', 'pyproject.toml'))
        checks.append(_check_exists(root / 'README.md', 'README.md'))
        checks.append(_check_exists(root / 'src' / 'godotter', 'src/godotter'))
        checks.append(_check_exists(root / 'tests', 'tests/'))

        project_file = root / 'project.godot'
        if project_file.exists():
            checks.append('OK   project.godot detected')
        else:
            checks.append('INFO project.godot not present in workspace root')

        venv_python = root / '.venv' / 'Scripts' / 'python.exe'
        checks.append('OK   .venv detected' if venv_python.exists() else 'WARN .venv not detected')

        return '\n'.join(checks)


def _check_exists(path: Path, label: str) -> str:
    return f'OK   {label} present' if path.exists() else f'WARN {label} missing'