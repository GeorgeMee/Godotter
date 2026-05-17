from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class ProjectInfo:
    name: str
    main_scene: str | None
    autoloads: list[str]
    script_count: int
    scene_count: int


def load_project_info(workspace_root: Path) -> ProjectInfo:
    project_file = workspace_root / 'project.godot'
    if not project_file.exists():
        raise FileNotFoundError(f'project.godot not found in {workspace_root}')

    content = project_file.read_text(encoding='utf-8')
    name = 'Unnamed'
    main_scene = None
    autoloads: list[str] = []

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line.startswith('config/name='):
            name = _extract_quoted_value(line)
        elif line.startswith('run/main_scene='):
            main_scene = _extract_quoted_value(line)
        elif line.startswith('autoload/') and '=' in line:
            autoloads.append(line.split('=', maxsplit=1)[0].split('/', maxsplit=1)[1])

    script_count = sum(1 for _ in workspace_root.rglob('*.gd'))
    scene_count = sum(1 for _ in workspace_root.rglob('*.tscn'))
    return ProjectInfo(name=name, main_scene=main_scene, autoloads=autoloads, script_count=script_count, scene_count=scene_count)


def _extract_quoted_value(line: str) -> str:
    _, raw_value = line.split('=', maxsplit=1)
    return raw_value.strip().strip('"')