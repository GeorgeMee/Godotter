from typer.testing import CliRunner

from godotter.interfaces.cli import app


runner = CliRunner()


def test_task_prepare_writes_workpack_and_list_show(monkeypatch, tmp_path):
    monkeypatch.setattr(
        'godotter.interfaces.cli.get_settings',
        lambda: type(
            'S',
            (),
            {
                'workspace_root': tmp_path,
                'resolved_memory_path': tmp_path / '.godotter' / 'memory.md',
                'default_brain': 'stub',
            },
        )(),
    )

    result = runner.invoke(app, ['task', 'prepare', 'do something'])
    assert result.exit_code == 0
    assert 'workpack=' in result.stdout

    list_result = runner.invoke(app, ['task', 'list'])
    assert list_result.exit_code == 0
    assert 'count=1' in list_result.stdout
    assert 'goal=do something' in list_result.stdout

    show_result = runner.invoke(app, ['task', 'show', '--latest'])
    assert show_result.exit_code == 0
    assert 'goal=do something' in show_result.stdout

