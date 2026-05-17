from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from godotter.tools.base import Tool, ToolContext


class GitStatusTool(Tool):
    name = 'git_status'
    description = 'Show a short git status for the current workspace repository.'
    input_schema = {
        'type': 'object',
        'properties': {},
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        return _run_git(context.workspace_root, ['status', '--short'])


class GitDiffTool(Tool):
    name = 'git_diff'
    description = 'Show git diff output for the current workspace repository.'
    input_schema = {
        'type': 'object',
        'properties': {
            'cached': {'type': 'boolean', 'description': 'Show staged changes if true.', 'default': False},
            'path': {'type': 'string', 'description': 'Optional path filter relative to the workspace root.'},
        },
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        args = ['diff']
        if _to_bool(kwargs.get('cached', False)):
            args.append('--cached')
        raw_path = kwargs.get('path')
        if raw_path:
            resolved = context.resolve_path(str(raw_path))
            args.extend(['--', resolved.relative_to(context.workspace_root).as_posix()])
        return _run_git(context.workspace_root, args)


class GitLogTool(Tool):
    name = 'git_log'
    description = 'Show recent git commit history for the current workspace repository.'
    input_schema = {
        'type': 'object',
        'properties': {
            'limit': {'type': 'integer', 'description': 'Maximum number of commits to show.', 'default': 5},
        },
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        limit = max(_to_int(kwargs.get('limit', 5)), 1)
        return _run_git(context.workspace_root, ['log', '--oneline', f'-n{limit}'])


class GitBranchTool(Tool):
    name = 'git_branch'
    description = 'Show local git branches for the current workspace repository.'
    input_schema = {
        'type': 'object',
        'properties': {},
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        return _run_git(context.workspace_root, ['branch', '--list'])


def _run_git(workspace_root: Path, args: list[str]) -> str:
    try:
        completed = subprocess.run(
            ['git', *args],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError:
        return 'Error: git executable not found'
    except subprocess.TimeoutExpired:
        return 'Error: git command timed out'

    stdout = completed.stdout.strip()
    stderr = completed.stderr.strip()
    if completed.returncode != 0:
        details = stderr or stdout or f'git exited with code {completed.returncode}'
        return f'Error: {details}'
    return stdout or '(empty)'


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def _to_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value.strip())
    return int(value)
