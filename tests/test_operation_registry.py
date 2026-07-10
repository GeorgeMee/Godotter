import json

from typer.testing import CliRunner

from godotter.config import Settings, get_settings
from godotter.context import ExecutionContext
from godotter.interfaces.machine_cli import app as machine_app
from godotter.operations import OperationRegistry, build_default_operations


runner = CliRunner()


def test_operation_registry_exposes_agent_tool_schema():
    registry = build_default_operations()
    definitions = registry.tool_definitions(audience='agent')
    names = {definition['name'] for definition in definitions}

    assert {
        'read_file',
        'list_files',
        'search_code',
        'git_status',
        'project_info',
        'validate_project',
        'runtime_doctor',
        'uid_scan',
        'uid_fix_apply',
        'headless_run',
        'replace_text',
        'apply_unified_patch',
    } <= names
    assert 'scene_create' not in names

    read_file = next(definition for definition in definitions if definition['name'] == 'read_file')
    assert read_file['input_schema']['properties']['path']['description']
    assert read_file['description'] == 'Read a UTF-8 text file from the workspace with line numbers.'

    apply_patch = next(definition for definition in definitions if definition['name'] == 'apply_unified_patch')
    assert list(apply_patch['input_schema']['properties']) == ['patch']


def test_default_operation_registry_uses_file_tools(tmp_path):
    sample = tmp_path / 'sample.txt'
    sample.write_text('alpha\nbeta\n', encoding='utf-8')
    settings = Settings(GODOTTER_WORKSPACE_ROOT=str(tmp_path))
    context = ExecutionContext(settings=settings, workspace_root=tmp_path)
    registry = build_default_operations()

    tool = registry.get('read_file')
    assert tool is not None
    assert '1 | alpha' in tool.execute(context, {'path': 'sample.txt'}).text


def test_machine_tool_schema_and_run(tmp_path, monkeypatch):
    sample = tmp_path / 'sample.txt'
    sample.write_text('alpha\nbeta\n', encoding='utf-8')
    monkeypatch.setenv('GODOTTER_DEFAULT_PROJECT', '')
    monkeypatch.setenv('GODOTTER_WORKSPACE_ROOT', str(tmp_path))
    get_settings.cache_clear()

    schema_result = runner.invoke(machine_app, ['tool', 'schema', 'read_file'])
    assert schema_result.exit_code == 0
    schema = json.loads(schema_result.stdout)
    assert schema['name'] == 'read_file'
    assert 'path' in schema['input_schema']['properties']

    run_result = runner.invoke(
        machine_app,
        [
            'tool',
            'run',
            'read_file',
            '--workspace',
            str(tmp_path),
            '--args',
            '{"path":"sample.txt"}',
        ],
    )
    assert run_result.exit_code == 0
    payload = json.loads(run_result.stdout)
    assert payload['ok'] is True
    assert payload['operation'] == 'read_file'
    assert '1 | alpha' in payload['data']['text']

    text_result = runner.invoke(
        machine_app,
        [
            'tool',
            'run',
            'read_file',
            '--workspace',
            str(tmp_path),
            '--args',
            '{"path":"sample.txt"}',
            '--text',
        ],
    )
    assert text_result.exit_code == 0
    assert '1 | alpha' in text_result.stdout
    get_settings.cache_clear()


def test_machine_tool_run_returns_error_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv('GODOTTER_DEFAULT_PROJECT', '')
    monkeypatch.setenv('GODOTTER_WORKSPACE_ROOT', str(tmp_path))
    get_settings.cache_clear()

    result = runner.invoke(
        machine_app,
        [
            'tool',
            'run',
            'read_file',
            '--workspace',
            str(tmp_path),
            '--args',
            '{"path":"missing.txt"}',
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload['ok'] is False
    assert payload['operation'] == 'read_file'
    assert payload['error']['code'] == 'file_not_found'
    get_settings.cache_clear()


def test_machine_tool_run_accepts_args_file(tmp_path, monkeypatch):
    sample = tmp_path / 'sample.txt'
    sample.write_text('alpha\nbeta\n', encoding='utf-8')
    args_path = tmp_path / 'args.json'
    args_path.write_text('\ufeff{"path":"sample.txt"}', encoding='utf-8')
    monkeypatch.setenv('GODOTTER_DEFAULT_PROJECT', '')
    monkeypatch.setenv('GODOTTER_WORKSPACE_ROOT', str(tmp_path))
    get_settings.cache_clear()

    result = runner.invoke(
        machine_app,
        [
            'tool',
            'run',
            'read_file',
            '--workspace',
            str(tmp_path),
            '--args-file',
            str(args_path),
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload['ok'] is True
    assert '1 | alpha' in payload['data']['text']
    get_settings.cache_clear()
