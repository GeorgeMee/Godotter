from pathlib import Path

from typer.testing import CliRunner

from godotter.interfaces.cli import app


class FakeRunResult:
    def __init__(self, stdout: str) -> None:
        self.exit_code = 0
        self.stdout = stdout
        self.stderr = ''
        self.timed_out = False
        self.duration_ms = 21


class FakeGodotRunner:
    def __init__(self, godot_path: str, workspace_root) -> None:
        self.godot_path = godot_path
        self.workspace_root = workspace_root

    def lint_script(self, file_path: str, timeout: int = 30):
        return FakeRunResult(f'lint:{file_path}:{timeout}')

    def lint_project(self, timeout: int = 60):
        return FakeRunResult(f'lint-project:{timeout}')

    def run_project(self, timeout: int = 60, scene: str | None = None, *, headless: bool = False):
        return FakeRunResult(f'run:{scene or "(project)"}:{timeout}:headless={str(headless).lower()}')


class FakeDoctorReport:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.project_exists = True
        self.project_name = 'Demo'
        self.main_scene = 'res://scenes/main.tscn'
        self.script_count = 2
        self.scene_count = 1
        self.godot_configured = True
        self.godot_runnable = True
        self.godot_version = 'Godot Engine v4.4.stable'
        self.godot_error = None


class FakeUidFixChange:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.uid = 'uid://player123'
        self.old_path = 'res://old/player.gd'
        self.new_path = 'res://scripts/player.gd'


class FakeUidFixResult:
    def __init__(self, file_path: Path) -> None:
        self.uid_entries = 1
        self.scanned_files = 2
        self.updated_files = 1
        self.changes = [FakeUidFixChange(file_path)]


class FakeRuntimeTarget:
    def __init__(self, workspace_root: Path, godot_path: str | None = '/usr/bin/godot') -> None:
        self.project_name = 'demo'
        self.workspace_root = workspace_root
        self.godot_path = godot_path
        self.main_scene = 'res://scenes/main.tscn'


runner = CliRunner()


def test_runtime_lint_command(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'godot_path': '/usr/bin/godot',
        'workspace_root': tmp_path,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.build_runner', lambda settings, project=None: FakeGodotRunner(settings.godot_path, settings.workspace_root))
    result = runner.invoke(app, ['runtime', 'lint', 'scripts/player.gd', '--timeout', '7'])
    assert result.exit_code == 0
    assert 'command=script_lint' in result.stdout
    assert 'stdout=lint:scripts/player.gd:7' in result.stdout


def test_runtime_run_command(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'godot_path': '/usr/bin/godot',
        'workspace_root': tmp_path,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.build_runner', lambda settings, project=None: FakeGodotRunner(settings.godot_path, settings.workspace_root))
    result = runner.invoke(app, ['runtime', 'run', '--scene', 'res://scenes/main.tscn', '--timeout', '11'])
    assert result.exit_code == 0
    assert 'command=headless_run' in result.stdout
    assert 'stdout=run:res://scenes/main.tscn:11:headless=false' in result.stdout


def test_runtime_command_requires_godot_path(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'godot_path': None,
        'workspace_root': tmp_path,
        'resolved_project_registry_path': tmp_path / 'projects.toml',
        'default_project_name': None,
    })())
    result = runner.invoke(app, ['runtime', 'lint'])
    assert result.exit_code != 0
    assert 'GODOT_PATH is not configured' in result.output


def test_runtime_doctor_command(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'godot_path': '/usr/bin/godot',
        'workspace_root': tmp_path,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.resolve_runtime_target', lambda settings, project=None: FakeRuntimeTarget(tmp_path))
    monkeypatch.setattr('godotter.interfaces.cli.run_doctor', lambda workspace_root, godot_path, timeout=15: FakeDoctorReport(workspace_root))
    result = runner.invoke(app, ['runtime', 'doctor', '--timeout', '5'])
    assert result.exit_code == 0
    assert 'project_exists=true' in result.stdout
    assert 'godot_version=Godot Engine v4.4.stable' in result.stdout


def test_runtime_uid_fix_command(monkeypatch, tmp_path):
    target = tmp_path / 'scenes' / 'main.tscn'
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'godot_path': '/usr/bin/godot',
        'workspace_root': tmp_path,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.resolve_runtime_target', lambda settings, project=None: FakeRuntimeTarget(tmp_path))
    monkeypatch.setattr('godotter.interfaces.cli.fix_uid_paths', lambda workspace_root, dry_run=True: FakeUidFixResult(target))
    result = runner.invoke(app, ['runtime', 'uid-fix', '--write'])
    assert result.exit_code == 0
    assert 'dry_run=false' in result.stdout
    assert 'updated_files=1' in result.stdout
    assert 'change file=scenes/main.tscn uid=uid://player123 old_path=res://old/player.gd new_path=res://scripts/player.gd' in result.stdout


def test_runtime_lint_command_accepts_project_option(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'godot_path': '/usr/bin/godot',
        'workspace_root': tmp_path,
    })())
    captured: dict[str, str | None] = {}

    def fake_build_runner(settings, project=None):
        captured['project'] = project
        return FakeGodotRunner(settings.godot_path, settings.workspace_root)

    monkeypatch.setattr('godotter.interfaces.cli.build_runner', fake_build_runner)
    result = runner.invoke(app, ['runtime', 'lint', '--project', 'demo'])
    assert result.exit_code == 0
    assert captured['project'] == 'demo'
