import json

from typer.testing import CliRunner

from godotter.config import get_settings
from godotter.interfaces.machine_cli import app as machine_app
from godotter.services.godot.run import RunService


runner = CliRunner()


class FakeRunResult:
    def __init__(self, exit_code: int = 0, stdout: str = 'ok', stderr: str = '', timed_out: bool = False, duration_ms: int = 12):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.duration_ms = duration_ms


class FakeGodotRunner:
    def __init__(self, godot_path: str, workspace_root) -> None:
        self.godot_path = godot_path
        self.workspace_root = workspace_root

    def run_project(self, timeout: int = 60, scene: str | None = None):
        return FakeRunResult(stdout=f'run:{scene or "(project)"}:{timeout}')


def test_run_service_requires_godot_path(tmp_path):
    service = RunService(tmp_path, godot_path=None)
    try:
        service.headless_run_text()
    except ValueError as exc:
        assert 'GODOT_PATH is not configured' in str(exc)
    else:
        raise AssertionError('expected ValueError')


def test_machine_headless_run_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv('GODOTTER_DEFAULT_PROJECT', '')
    monkeypatch.setenv('GODOTTER_WORKSPACE_ROOT', str(tmp_path))
    monkeypatch.setattr('godotter.services.godot.run.GodotRunner', FakeGodotRunner)
    get_settings.cache_clear()

    result = runner.invoke(
        machine_app,
        [
            'tool',
            'run',
            'headless_run',
            '--workspace',
            str(tmp_path),
            '--args',
            '{"scene":"res://scenes/main.tscn","timeout":15}',
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload['ok'] is True
    assert payload['operation'] == 'headless_run'
    assert 'stdout=run:res://scenes/main.tscn:15' in payload['data']['text']
    get_settings.cache_clear()

