import json

from typer.testing import CliRunner

from godotter.config import get_settings
from godotter.interfaces.machine_cli import app as machine_app
from godotter.operations import build_default_operations
from godotter.services.godot.lsp import detect_lsp_status


runner = CliRunner()


def test_analysis_status_is_registered_for_agent():
    registry = build_default_operations()
    names = {definition['name'] for definition in registry.tool_definitions(audience='agent')}

    assert 'analysis_status' in names
    assert 'scene_inspect' in names
    assert 'scene_validate' in names
    assert 'script_lint' in names


def test_lsp_status_reports_missing_project(tmp_path):
    status = detect_lsp_status(tmp_path, godot_path=None)

    assert status.configured is False
    assert status.available is False
    assert status.enabled is False
    assert status.reason == 'project_godot_not_found'


def test_machine_analysis_status_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv('GODOTTER_DEFAULT_PROJECT', '')
    monkeypatch.setenv('GODOTTER_WORKSPACE_ROOT', str(tmp_path))
    get_settings.cache_clear()

    result = runner.invoke(
        machine_app,
        [
            'tool',
            'run',
            'analysis_status',
            '--workspace',
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload['ok'] is True
    assert payload['operation'] == 'analysis_status'
    assert payload['data']['lsp']['reason'] == 'project_godot_not_found'
    assert 'scene_parser' in payload['data']['fallbacks']
    get_settings.cache_clear()


def test_machine_scene_inspect_envelope(tmp_path, monkeypatch):
    scene_path = tmp_path / 'scenes' / 'main_menu.tscn'
    scene_path.parent.mkdir()
    scene_path.write_text(
        '[gd_scene load_steps=2 format=3 uid="uid://abc"]\n\n'
        '[node name="MainMenu" type="Node2D"]\n',
        encoding='utf-8',
    )
    monkeypatch.setenv('GODOTTER_DEFAULT_PROJECT', '')
    monkeypatch.setenv('GODOTTER_WORKSPACE_ROOT', str(tmp_path))
    get_settings.cache_clear()

    result = runner.invoke(
        machine_app,
        [
            'tool',
            'run',
            'scene_inspect',
            '--workspace',
            str(tmp_path),
            '--args',
            '{"path":"scenes/main_menu.tscn"}',
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload['ok'] is True
    assert 'uid=uid://abc' in payload['data']['text']
    get_settings.cache_clear()
