from __future__ import annotations

from pathlib import Path

from godotter.services.godot import load_project_info


class RuntimeInfoService:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def project_info_text(self) -> str:
        info = load_project_info(self.workspace_root)
        autoloads = ', '.join(info.autoloads) if info.autoloads else '(none)'
        lines = [
            f'name={info.name}',
            f'main_scene={info.main_scene or "(none)"}',
            f'autoloads={autoloads}',
            f'script_count={info.script_count}',
            f'scene_count={info.scene_count}',
        ]
        return '\n'.join(lines)

