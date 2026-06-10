from __future__ import annotations

from pathlib import Path

from godotter.tasks.scout import scout_workspace

_BLOCKED_PREFIXES = ('.godotter/', '.godot/')


def build_chat_scout_context(
    workspace_root: Path, message: str, max_files: int = 8, max_file_lines: int = 80,
) -> str:
    project_file = workspace_root / 'project.godot'
    always_include: list[str] = []
    if project_file.exists():
        always_include.append('project.godot')

    scout = scout_workspace(workspace_root, message, max_files=max_files + len(always_include))
    relevant_paths = list(dict.fromkeys(
        always_include + [ref.path for ref in scout.relevant_files[:max_files + len(always_include)]]
    ))
    relevant_paths = [p for p in relevant_paths if not any(p.startswith(pre) for pre in _BLOCKED_PREFIXES)][:max_files]

    parts: list[str] = []
    for rel_path in relevant_paths:
        full_path = workspace_root / rel_path
        if not full_path.is_file():
            continue
        try:
            content = full_path.read_text(encoding='utf-8-sig')
        except (OSError, UnicodeDecodeError):
            continue
        lines = content.splitlines()
        if len(lines) > max_file_lines:
            shown = '\n'.join(lines[:max_file_lines]) + f'\n... ({len(lines) - max_file_lines} more lines)'
        else:
            shown = content
        parts.append(f'### {rel_path}\n{shown}')

    return '\n\n'.join(parts)
