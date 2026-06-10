import json
import subprocess

from typer.testing import CliRunner

from godotter.interfaces.cli import app


runner = CliRunner()


def _init_git_repo(path):
    subprocess.run(['git', 'init'], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.name', 'Godotter Test'], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.email', 'godotter@example.com'], cwd=path, check=True, capture_output=True, text=True)


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
                'resolved_chat_brain': 'stub',
                'resolved_plan_brain': 'stub',
                'resolved_act_brain': 'stub',
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


def test_task_scout_prioritizes_git_changed_files(monkeypatch, tmp_path):
    (tmp_path / 'Docs').mkdir()
    (tmp_path / 'src').mkdir()
    (tmp_path / 'Docs' / 'godotter_dev_mode_project_structure.md').write_text('Managers EventBus', encoding='utf-8')
    (tmp_path / 'Docs' / 'godotter_template_project.md').write_text('template', encoding='utf-8')
    tracked = tmp_path / 'src' / 'inventory.py'
    tracked.write_text('class InventoryMgr: pass\n', encoding='utf-8')
    _init_git_repo(tmp_path)
    subprocess.run(['git', 'add', '.'], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'commit', '-m', 'initial'], cwd=tmp_path, check=True, capture_output=True, text=True)
    tracked.write_text('class InventoryMgr:\n    pass\n', encoding='utf-8')
    (tmp_path / 'src' / 'notes.txt').write_text('todo\n', encoding='utf-8')

    monkeypatch.setattr(
        'godotter.interfaces.cli.get_settings',
        lambda: type(
            'S',
            (),
            {
                'workspace_root': tmp_path,
                'resolved_memory_path': tmp_path / '.godotter' / 'memory.md',
                'default_brain': 'stub',
                'resolved_chat_brain': 'stub',
                'resolved_plan_brain': 'stub',
                'resolved_act_brain': 'stub',
            },
        )(),
    )

    result = runner.invoke(app, ['task', 'scout', 'inventory changes'])
    assert result.exit_code == 0

    latest = json.loads((tmp_path / '.godotter' / 'workpacks' / 'latest.json').read_text(encoding='utf-8'))
    relevant = latest['relevant_files']
    reasons = {entry['path']: entry['reason'] for entry in relevant}
    assert reasons['src/inventory.py'] == 'git:modified'
    assert reasons['src/notes.txt'] == 'git:untracked'
    assert 'scout_changed_files=src/inventory.py,src/notes.txt' in latest['assumptions']

