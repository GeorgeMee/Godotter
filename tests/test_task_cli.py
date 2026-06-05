from pathlib import Path
import subprocess

from typer.testing import CliRunner

from godotter.interfaces.cli import app
from godotter.tasks.runstate import load_runstate
from godotter.tasks.workpack import WorkPack, WorkPackFileRef, write_workpack


runner = CliRunner()


def _init_git_repo(path: Path) -> None:
    subprocess.run(['git', 'init'], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.name', 'Godotter Test'], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.email', 'godotter@example.com'], cwd=path, check=True, capture_output=True, text=True)
    (path / '.gitignore').write_text('.godotter/\n', encoding='utf-8')
    subprocess.run(['git', 'add', '.'], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'commit', '-m', 'initial'], cwd=path, check=True, capture_output=True, text=True)


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


def test_task_run_uses_workpack_workspace_root(monkeypatch, tmp_path):
    default_root = tmp_path / 'default'
    pack_root = tmp_path / 'pack-root'
    default_root.mkdir()
    pack_root.mkdir()

    workpack = WorkPack(
        task_id='wp_test',
        created_at='2026-05-23T12:00:00',
        workspace_root=pack_root.as_posix(),
        goal='inspect workspace',
    )
    workpack_path = write_workpack(pack_root, workpack, filename='manual.json')

    monkeypatch.setattr(
        'godotter.interfaces.cli.get_settings',
        lambda: type(
            'S',
            (),
            {
                'workspace_root': default_root,
                'resolved_memory_path': default_root / '.godotter' / 'memory.md',
                'default_brain': 'stub',
                'model_copy': lambda self, update: type(
                    'S',
                    (),
                    {
                        'workspace_root': Path(update['workspace_root']),
                        'resolved_memory_path': Path(update['workspace_root']) / '.godotter' / 'memory.md',
                        'default_brain': 'stub',
                    },
                )(),
            },
        )(),
    )

    monkeypatch.setattr('godotter.interfaces.cli.configure_logging', lambda settings: None)
    monkeypatch.setattr('godotter.interfaces.cli.Memory', lambda path: object())
    monkeypatch.setattr('godotter.interfaces.cli.ToolRegistry', lambda tools: object())
    monkeypatch.setattr('godotter.interfaces.cli.build_default_tools', lambda: [])
    monkeypatch.setattr('godotter.interfaces.cli.create_brain', lambda settings, selected_brain: object())

    class FakeAgent:
        def __init__(self, **kwargs):
            self.settings = kwargs['settings']

        def handle_input(self, prompt):
            return f'workspace_root={self.settings.workspace_root.as_posix()}'

    monkeypatch.setattr('godotter.interfaces.cli.Agent', FakeAgent)

    result = runner.invoke(app, ['task', 'run', '--workpack', str(workpack_path)])
    assert result.exit_code == 0
    assert f'workspace_root={pack_root.as_posix()}' in result.stdout


def test_task_show_prints_assumptions_and_relevant_files(monkeypatch, tmp_path):
    workpack = WorkPack(
        task_id='wp_show',
        created_at='2026-05-23T12:00:00',
        workspace_root=tmp_path.as_posix(),
        goal='show details',
        assumptions=['scout_changed_files=src/inventory.py,src/notes.txt'],
        relevant_files=[
            WorkPackFileRef(path='src/inventory.py', reason='git:modified', priority=15),
            WorkPackFileRef(path='src/notes.txt', reason='git:untracked', priority=5),
        ],
    )
    write_workpack(tmp_path, workpack, filename='show.json')

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

    result = runner.invoke(app, ['task', 'show', '--latest'])
    assert result.exit_code == 0
    assert 'assumption=scout_changed_files=src/inventory.py,src/notes.txt' in result.stdout
    assert 'relevant_file path=src/inventory.py priority=15 git:modified' in result.stdout
    assert 'relevant_file path=src/notes.txt priority=5 git:untracked' in result.stdout


def test_task_run_maps_deprecated_code_mode_to_act(monkeypatch, tmp_path):
    _init_git_repo(tmp_path)
    workpack = WorkPack(
        task_id='wp_mode',
        created_at='2026-05-23T12:00:00',
        workspace_root=tmp_path.as_posix(),
        goal='implement feature',
    )
    workpack_path = write_workpack(tmp_path, workpack, filename='mode.json')

    monkeypatch.setattr(
        'godotter.interfaces.cli.get_settings',
        lambda: type(
            'S',
            (),
            {
                'workspace_root': tmp_path,
                'resolved_memory_path': tmp_path / '.godotter' / 'memory.md',
                'default_brain': 'stub',
                'model_copy': lambda self, update: type(
                    'S',
                    (),
                    {
                        'workspace_root': Path(update['workspace_root']),
                        'resolved_memory_path': Path(update['workspace_root']) / '.godotter' / 'memory.md',
                        'default_brain': 'stub',
                    },
                )(),
            },
        )(),
    )
    monkeypatch.setattr('godotter.interfaces.cli.configure_logging', lambda settings: None)
    monkeypatch.setattr('godotter.interfaces.cli.Memory', lambda path: object())
    monkeypatch.setattr('godotter.interfaces.cli.ToolRegistry', lambda tools: object())
    monkeypatch.setattr('godotter.interfaces.cli.build_default_tools', lambda: [])
    monkeypatch.setattr('godotter.interfaces.cli.create_brain', lambda settings, selected_brain: object())

    class FakeAgent:
        def __init__(self, **kwargs):
            self.settings = kwargs['settings']
            self.mode = kwargs['mode']

        def handle_input(self, prompt):
            (self.settings.workspace_root / 'notes.txt').write_text('changed\n', encoding='utf-8')
            return f'mode={self.mode}'

    monkeypatch.setattr('godotter.interfaces.cli.Agent', FakeAgent)

    result = runner.invoke(app, ['task', 'run', '--workpack', str(workpack_path), '--mode', 'code'])
    assert result.exit_code == 0
    assert 'note=mode_alias input=code mapped_to=act' in result.stdout
    assert 'mode=act' in result.stdout


def test_task_run_act_fails_without_workspace_changes(monkeypatch, tmp_path):
    _init_git_repo(tmp_path)
    workpack = WorkPack(
        task_id='wp_no_changes',
        created_at='2026-05-23T12:00:00',
        workspace_root=tmp_path.as_posix(),
        goal='implement feature',
    )
    workpack_path = write_workpack(tmp_path, workpack, filename='no_changes.json')

    monkeypatch.setattr(
        'godotter.interfaces.cli.get_settings',
        lambda: type(
            'S',
            (),
            {
                'workspace_root': tmp_path,
                'resolved_memory_path': tmp_path / '.godotter' / 'memory.md',
                'default_brain': 'stub',
                'model_copy': lambda self, update: type(
                    'S',
                    (),
                    {
                        'workspace_root': Path(update['workspace_root']),
                        'resolved_memory_path': Path(update['workspace_root']) / '.godotter' / 'memory.md',
                        'default_brain': 'stub',
                    },
                )(),
            },
        )(),
    )
    monkeypatch.setattr('godotter.interfaces.cli.configure_logging', lambda settings: None)
    monkeypatch.setattr('godotter.interfaces.cli.Memory', lambda path: object())
    monkeypatch.setattr('godotter.interfaces.cli.ToolRegistry', lambda tools: object())
    monkeypatch.setattr('godotter.interfaces.cli.build_default_tools', lambda: [])
    monkeypatch.setattr('godotter.interfaces.cli.create_brain', lambda settings, selected_brain: object())

    class FakeAgent:
        def __init__(self, **kwargs):
            pass

        def handle_input(self, prompt):
            return 'no changes'

    monkeypatch.setattr('godotter.interfaces.cli.Agent', FakeAgent)

    result = runner.invoke(app, ['task', 'run', '--workpack', str(workpack_path), '--mode', 'act'])
    assert result.exit_code == 1
    assert 'task_run_audit_error=no_workspace_changes' in result.stdout


def test_task_run_act_requires_tests_and_level_updates(monkeypatch, tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / 'game' / 'features').mkdir(parents=True)
    workpack = WorkPack(
        task_id='wp_partial',
        created_at='2026-05-23T12:00:00',
        workspace_root=tmp_path.as_posix(),
        goal='implement feature',
    )
    workpack_path = write_workpack(tmp_path, workpack, filename='partial.json')

    monkeypatch.setattr(
        'godotter.interfaces.cli.get_settings',
        lambda: type(
            'S',
            (),
            {
                'workspace_root': tmp_path,
                'resolved_memory_path': tmp_path / '.godotter' / 'memory.md',
                'default_brain': 'stub',
                'model_copy': lambda self, update: type(
                    'S',
                    (),
                    {
                        'workspace_root': Path(update['workspace_root']),
                        'resolved_memory_path': Path(update['workspace_root']) / '.godotter' / 'memory.md',
                        'default_brain': 'stub',
                    },
                )(),
            },
        )(),
    )
    monkeypatch.setattr('godotter.interfaces.cli.configure_logging', lambda settings: None)
    monkeypatch.setattr('godotter.interfaces.cli.Memory', lambda path: object())
    monkeypatch.setattr('godotter.interfaces.cli.ToolRegistry', lambda tools: object())
    monkeypatch.setattr('godotter.interfaces.cli.build_default_tools', lambda: [])
    monkeypatch.setattr('godotter.interfaces.cli.create_brain', lambda settings, selected_brain: object())

    class FakeAgent:
        def __init__(self, **kwargs):
            self.settings = kwargs['settings']

        def handle_input(self, prompt):
            feature_path = self.settings.workspace_root / 'game' / 'features' / 'snake.gd'
            feature_path.write_text('extends Node\n', encoding='utf-8')
            return 'partial changes'

    monkeypatch.setattr('godotter.interfaces.cli.Agent', FakeAgent)

    result = runner.invoke(app, ['task', 'run', '--workpack', str(workpack_path), '--mode', 'act'])
    assert result.exit_code == 1
    assert 'task_run_audit_error=missing_tests_for_game_logic_changes' in result.stdout


def test_task_run_act_passes_with_game_tests_and_level_updates(monkeypatch, tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / 'game' / 'features').mkdir(parents=True)
    (tmp_path / 'game' / 'levels').mkdir(parents=True)
    (tmp_path / 'tests' / 'features').mkdir(parents=True)
    workpack = WorkPack(
        task_id='wp_complete',
        created_at='2026-05-23T12:00:00',
        workspace_root=tmp_path.as_posix(),
        goal='implement feature',
    )
    workpack_path = write_workpack(tmp_path, workpack, filename='complete.json')

    monkeypatch.setattr(
        'godotter.interfaces.cli.get_settings',
        lambda: type(
            'S',
            (),
            {
                'workspace_root': tmp_path,
                'resolved_memory_path': tmp_path / '.godotter' / 'memory.md',
                'default_brain': 'stub',
                'model_copy': lambda self, update: type(
                    'S',
                    (),
                    {
                        'workspace_root': Path(update['workspace_root']),
                        'resolved_memory_path': Path(update['workspace_root']) / '.godotter' / 'memory.md',
                        'default_brain': 'stub',
                    },
                )(),
            },
        )(),
    )
    monkeypatch.setattr('godotter.interfaces.cli.configure_logging', lambda settings: None)
    monkeypatch.setattr('godotter.interfaces.cli.Memory', lambda path: object())
    monkeypatch.setattr('godotter.interfaces.cli.ToolRegistry', lambda tools: object())
    monkeypatch.setattr('godotter.interfaces.cli.build_default_tools', lambda: [])
    monkeypatch.setattr('godotter.interfaces.cli.create_brain', lambda settings, selected_brain: object())

    class FakeAgent:
        def __init__(self, **kwargs):
            self.settings = kwargs['settings']

        def handle_input(self, prompt):
            (self.settings.workspace_root / 'game' / 'features' / 'snake').mkdir(parents=True, exist_ok=True)
            (self.settings.workspace_root / 'game' / 'features' / 'snake' / 'snake.gd').write_text('extends Node\n', encoding='utf-8')
            (self.settings.workspace_root / 'game' / 'levels' / 'main.tscn').write_text('[gd_scene format=3]\n', encoding='utf-8')
            (self.settings.workspace_root / 'tests' / 'features' / 'snake').mkdir(parents=True, exist_ok=True)
            (self.settings.workspace_root / 'tests' / 'features' / 'snake' / 'test_snake.gd').write_text('extends Node\n', encoding='utf-8')
            (self.settings.workspace_root / 'tests' / 'levels').mkdir(parents=True, exist_ok=True)
            (self.settings.workspace_root / 'tests' / 'levels' / 'main_smoke.tscn').write_text('[gd_scene format=3]\n', encoding='utf-8')
            return 'complete changes'

    monkeypatch.setattr('godotter.interfaces.cli.Agent', FakeAgent)

    result = runner.invoke(app, ['task', 'run', '--workpack', str(workpack_path), '--mode', 'act'])
    assert result.exit_code == 0
    assert 'task_run_audit changed_files=4' in result.stdout
    assert 'runstate=' in result.stdout
    state = load_runstate(tmp_path / '.godotter' / 'runs' / 'latest.json')
    assert state.status == 'pass'
    assert state.task_id == 'wp_complete'
    assert len(state.attempts) == 1
    assert state.attempts[0].status == 'pass'
    assert 'game/features/snake/snake.gd' in state.attempts[0].changed_files


def test_task_run_act_executes_verification_commands(monkeypatch, tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / 'game' / 'features').mkdir(parents=True)
    (tmp_path / 'game' / 'levels').mkdir(parents=True)
    (tmp_path / 'tests' / 'features').mkdir(parents=True)
    workpack = WorkPack(
        task_id='wp_verify',
        created_at='2026-05-23T12:00:00',
        workspace_root=tmp_path.as_posix(),
        goal='implement feature',
        verification=['echo verify-one', 'echo verify-two'],
    )
    workpack_path = write_workpack(tmp_path, workpack, filename='verify.json')

    monkeypatch.setattr(
        'godotter.interfaces.cli.get_settings',
        lambda: type(
            'S',
            (),
            {
                'workspace_root': tmp_path,
                'resolved_memory_path': tmp_path / '.godotter' / 'memory.md',
                'default_brain': 'stub',
                'model_copy': lambda self, update: type(
                    'S',
                    (),
                    {
                        'workspace_root': Path(update['workspace_root']),
                        'resolved_memory_path': Path(update['workspace_root']) / '.godotter' / 'memory.md',
                        'default_brain': 'stub',
                    },
                )(),
            },
        )(),
    )
    monkeypatch.setattr('godotter.interfaces.cli.configure_logging', lambda settings: None)
    monkeypatch.setattr('godotter.interfaces.cli.Memory', lambda path: object())
    monkeypatch.setattr('godotter.interfaces.cli.ToolRegistry', lambda tools: object())
    monkeypatch.setattr('godotter.interfaces.cli.build_default_tools', lambda: [])
    monkeypatch.setattr('godotter.interfaces.cli.create_brain', lambda settings, selected_brain: object())

    class FakeAgent:
        def __init__(self, **kwargs):
            self.settings = kwargs['settings']

        def handle_input(self, prompt):
            (self.settings.workspace_root / 'game' / 'features' / 'snake').mkdir(parents=True, exist_ok=True)
            (self.settings.workspace_root / 'game' / 'features' / 'snake' / 'snake.gd').write_text('extends Node\n', encoding='utf-8')
            (self.settings.workspace_root / 'game' / 'levels' / 'main.tscn').write_text('[gd_scene format=3]\n', encoding='utf-8')
            (self.settings.workspace_root / 'tests' / 'features' / 'snake').mkdir(parents=True, exist_ok=True)
            (self.settings.workspace_root / 'tests' / 'features' / 'snake' / 'test_snake.gd').write_text('extends Node\n', encoding='utf-8')
            (self.settings.workspace_root / 'tests' / 'levels').mkdir(parents=True, exist_ok=True)
            (self.settings.workspace_root / 'tests' / 'levels' / 'main_smoke.tscn').write_text('[gd_scene format=3]\n', encoding='utf-8')
            return 'complete changes'

    monkeypatch.setattr('godotter.interfaces.cli.Agent', FakeAgent)

    result = runner.invoke(app, ['task', 'run', '--workpack', str(workpack_path), '--mode', 'act'])
    assert result.exit_code == 0
    assert 'task_run_verify command=echo verify-one' in result.stdout
    assert 'task_run_verify command=echo verify-two' in result.stdout


def test_task_run_act_fails_on_verification_command_error(monkeypatch, tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / 'game' / 'features').mkdir(parents=True)
    (tmp_path / 'game' / 'levels').mkdir(parents=True)
    (tmp_path / 'tests' / 'features').mkdir(parents=True)
    workpack = WorkPack(
        task_id='wp_verify_fail',
        created_at='2026-05-23T12:00:00',
        workspace_root=tmp_path.as_posix(),
        goal='implement feature',
        verification=['python -c "raise SystemExit(2)"'],
    )
    workpack_path = write_workpack(tmp_path, workpack, filename='verify_fail.json')

    monkeypatch.setattr(
        'godotter.interfaces.cli.get_settings',
        lambda: type(
            'S',
            (),
            {
                'workspace_root': tmp_path,
                'resolved_memory_path': tmp_path / '.godotter' / 'memory.md',
                'default_brain': 'stub',
                'model_copy': lambda self, update: type(
                    'S',
                    (),
                    {
                        'workspace_root': Path(update['workspace_root']),
                        'resolved_memory_path': Path(update['workspace_root']) / '.godotter' / 'memory.md',
                        'default_brain': 'stub',
                    },
                )(),
            },
        )(),
    )
    monkeypatch.setattr('godotter.interfaces.cli.configure_logging', lambda settings: None)
    monkeypatch.setattr('godotter.interfaces.cli.Memory', lambda path: object())
    monkeypatch.setattr('godotter.interfaces.cli.ToolRegistry', lambda tools: object())
    monkeypatch.setattr('godotter.interfaces.cli.build_default_tools', lambda: [])
    monkeypatch.setattr('godotter.interfaces.cli.create_brain', lambda settings, selected_brain: object())

    class FakeAgent:
        def __init__(self, **kwargs):
            self.settings = kwargs['settings']

        def handle_input(self, prompt):
            (self.settings.workspace_root / 'game' / 'features' / 'snake').mkdir(parents=True, exist_ok=True)
            (self.settings.workspace_root / 'game' / 'features' / 'snake' / 'snake.gd').write_text('extends Node\n', encoding='utf-8')
            (self.settings.workspace_root / 'game' / 'levels' / 'main.tscn').write_text('[gd_scene format=3]\n', encoding='utf-8')
            (self.settings.workspace_root / 'tests' / 'features' / 'snake').mkdir(parents=True, exist_ok=True)
            (self.settings.workspace_root / 'tests' / 'features' / 'snake' / 'test_snake.gd').write_text('extends Node\n', encoding='utf-8')
            (self.settings.workspace_root / 'tests' / 'levels').mkdir(parents=True, exist_ok=True)
            (self.settings.workspace_root / 'tests' / 'levels' / 'main_smoke.tscn').write_text('[gd_scene format=3]\n', encoding='utf-8')
            return 'complete changes'

    monkeypatch.setattr('godotter.interfaces.cli.Agent', FakeAgent)

    result = runner.invoke(app, ['task', 'run', '--workpack', str(workpack_path), '--mode', 'act'])
    assert result.exit_code == 1
    assert 'task_run_verify exit_code=2 timed_out=false' in result.stdout
    state = load_runstate(tmp_path / '.godotter' / 'runs' / 'latest.json')
    assert state.status == 'fail'
    assert state.task_id == 'wp_verify_fail'
    assert state.attempts[0].status == 'fail'
    assert state.attempts[0].failure_report is not None
    assert state.attempts[0].verify_report is not None

