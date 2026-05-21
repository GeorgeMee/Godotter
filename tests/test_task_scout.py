from typer.testing import CliRunner

from godotter.interfaces.cli import app


runner = CliRunner()


def test_task_scout_finds_relevant_files(monkeypatch, tmp_path):
    # Create a minimal "project" with some files containing a keyword.
    (tmp_path / 'Docs').mkdir()
    (tmp_path / 'src').mkdir()
    (tmp_path / 'Docs' / 'godotter_dev_mode_project_structure.md').write_text('Managers EventBus', encoding='utf-8')
    (tmp_path / 'Docs' / 'godotter_template_project.md').write_text('template', encoding='utf-8')
    (tmp_path / 'src' / 'inventory.py').write_text('class InventoryMgr: pass', encoding='utf-8')

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

    result = runner.invoke(app, ['task', 'scout', 'add inventory system'])
    assert result.exit_code == 0
    assert 'workpack=' in result.stdout

    list_result = runner.invoke(app, ['task', 'list'])
    assert list_result.exit_code == 0
    assert 'count=1' in list_result.stdout

    show_result = runner.invoke(app, ['task', 'show', '--latest'])
    assert show_result.exit_code == 0
    assert 'goal=add inventory system' in show_result.stdout

