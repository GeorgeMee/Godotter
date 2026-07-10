from __future__ import annotations

from pathlib import Path

from godotter.services.godot import DoctorReport, run_doctor


class DiagnosticsService:
    def __init__(self, workspace_root: Path, godot_path: str | None = None) -> None:
        self.workspace_root = workspace_root.resolve()
        self.godot_path = godot_path

    def validate_project_text(self) -> str:
        root = self.workspace_root
        checks = [
            _check_exists(root / 'pyproject.toml', 'pyproject.toml'),
            _check_exists(root / 'README.md', 'README.md'),
            _check_exists(root / 'src' / 'godotter', 'src/godotter'),
            _check_exists(root / 'tests', 'tests/'),
        ]
        project_file = root / 'project.godot'
        if project_file.exists():
            checks.append('OK   project.godot detected')
        else:
            checks.append('INFO project.godot not present in workspace root')
        venv_python = root / '.venv' / 'Scripts' / 'python.exe'
        checks.append('OK   .venv detected' if venv_python.exists() else 'WARN .venv not detected')
        return '\n'.join(checks)

    def runtime_doctor_text(self, *, timeout: int = 15) -> str:
        report = run_doctor(self.workspace_root, self.godot_path, timeout=timeout)
        return render_doctor_report(report)


def render_doctor_report(report: DoctorReport) -> str:
    lines = [
        f'workspace_root={report.workspace_root}',
        f'project_exists={str(report.project_exists).lower()}',
        f'project_name={report.project_name or "(none)"}',
        f'main_scene={report.main_scene or "(none)"}',
        f'script_count={report.script_count}',
        f'scene_count={report.scene_count}',
        f'godot_configured={str(report.godot_configured).lower()}',
        f'godot_runnable={str(report.godot_runnable).lower()}',
        f'godot_version={report.godot_version or "(none)"}',
        f'godot_error={report.godot_error or "(none)"}',
    ]
    return '\n'.join(lines)


def _check_exists(path: Path, label: str) -> str:
    return f'OK   {label} present' if path.exists() else f'WARN {label} missing'

