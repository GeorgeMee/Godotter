from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from godotter.runtime.project_info import load_project_info


@dataclass(slots=True)
class DoctorReport:
    workspace_root: Path
    project_exists: bool
    project_name: str | None
    main_scene: str | None
    script_count: int
    scene_count: int
    godot_configured: bool
    godot_runnable: bool
    godot_version: str | None
    godot_error: str | None


def run_doctor(workspace_root: Path, godot_path: str | None, timeout: int = 15) -> DoctorReport:
    project_path = workspace_root / 'project.godot'
    project_exists = project_path.exists()
    project_name: str | None = None
    main_scene: str | None = None
    script_count = 0
    scene_count = 0

    if project_exists:
        info = load_project_info(workspace_root)
        project_name = info.name
        main_scene = info.main_scene
        script_count = info.script_count
        scene_count = info.scene_count

    godot_configured = bool(godot_path)
    godot_runnable = False
    godot_version: str | None = None
    godot_error: str | None = None

    if godot_path:
        try:
            completed = subprocess.run(
                [godot_path, '--version'],
                cwd=workspace_root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            stdout = completed.stdout.strip()
            stderr = completed.stderr.strip()
            version_text = stdout or stderr
            if completed.returncode == 0:
                godot_runnable = True
                godot_version = version_text.splitlines()[0] if version_text else '(unknown)'
            else:
                godot_error = version_text or f'process exited with code {completed.returncode}'
        except FileNotFoundError:
            godot_error = f'Godot binary not found: {godot_path}'
        except subprocess.TimeoutExpired:
            godot_error = f'Godot version check timed out after {timeout}s'
        except Exception as exc:
            godot_error = str(exc)

    return DoctorReport(
        workspace_root=workspace_root,
        project_exists=project_exists,
        project_name=project_name,
        main_scene=main_scene,
        script_count=script_count,
        scene_count=scene_count,
        godot_configured=godot_configured,
        godot_runnable=godot_runnable,
        godot_version=godot_version,
        godot_error=godot_error,
    )
