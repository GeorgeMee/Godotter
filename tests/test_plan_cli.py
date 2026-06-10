import json
from pathlib import Path

from typer.testing import CliRunner

from godotter.interfaces.cli import app
from godotter.tasks.workpack import load_workpack


runner = CliRunner()


def test_plan_prepare_list_show_status(monkeypatch, tmp_path):
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
                    'log_level': 'INFO',
                'model_copy': lambda self, update=None: self,
            },
        )(),
    )

    # Make stub return valid plan JSON.
    monkeypatch.setattr(
        'godotter.interfaces.cli.Agent.handle_input',
        lambda self, prompt: json.dumps(
            {
                'tasks': [
                    {
                        'id': 't1',
                        'title': 'Task A',
                        'goal': 'Do A',
                        'depends_on': [],
                        'scope': ['game/systems/a/'],
                        'acceptance': ['A done'],
                        'verification': ['uv run godotter runtime lint --project .'],
                    },
                    {
                        'id': 't2',
                        'title': 'Task B',
                        'goal': 'Do B',
                        'depends_on': ['t1'],
                        'scope': ['game/systems/b/'],
                        'acceptance': ['B done'],
                        'verification': ['uv run godotter runtime lint --project .'],
                    },
                ]
            }
        ),
    )

    result = runner.invoke(app, ['plan', 'prepare', 'make something'])
    assert result.exit_code == 0
    assert 'plan=' in result.stdout
    assert 'tasks=2' in result.stdout

    list_result = runner.invoke(app, ['plan', 'list'])
    assert list_result.exit_code == 0
    assert 'count=1' in list_result.stdout

    show_result = runner.invoke(app, ['plan', 'show', '--latest'])
    assert show_result.exit_code == 0
    assert 'tasks=2' in show_result.stdout

    status_result = runner.invoke(app, ['plan', 'status', '--latest'])
    assert status_result.exit_code == 0
    assert 'status=pending' in status_result.stdout or 'task id=' in status_result.stdout


def test_plan_run_orders_by_dependencies(monkeypatch, tmp_path):
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
                    'log_level': 'INFO',
                'model_copy': lambda self, update=None: self,
            },
        )(),
    )

    # Prepare a plan with reversed order but dependency t1 -> t2.
    monkeypatch.setattr(
        'godotter.interfaces.cli.Agent.handle_input',
        lambda self, prompt: json.dumps(
            {
                'tasks': [
                    {
                        'id': 't2',
                        'title': 'Task 2',
                        'goal': 'Do 2',
                        'depends_on': ['t1'],
                        'scope': ['game/systems/t2/'],
                        'acceptance': ['2 done'],
                        'verification': ['uv run godotter runtime lint --project .'],
                    },
                    {
                        'id': 't1',
                        'title': 'Task 1',
                        'goal': 'Do 1',
                        'depends_on': [],
                        'scope': ['game/systems/t1/'],
                        'acceptance': ['1 done'],
                        'verification': ['uv run godotter runtime lint --project .'],
                    },
                ]
            }
        ),
    )

    result = runner.invoke(app, ['plan', 'prepare', 'make something'])
    assert result.exit_code == 0

    calls: list[str] = []

    def _fake_task_run_command(
        *,
        workpack,
        latest,
        workspace,
        mode,
        brain,
        allow_no_changes=False,
        strict_audit=True,
        max_attempts=1,
        stop_on_same_failure=True,
        same_failure_limit=2,
    ):
        calls.append(str(workpack))

    monkeypatch.setattr('godotter.interfaces.cli.task_run_command', _fake_task_run_command)

    run_result = runner.invoke(app, ['plan', 'run', '--latest'])
    assert run_result.exit_code == 0
    assert len(calls) == 2
    first = run_result.stdout.find('task_id=t1')
    second = run_result.stdout.find('task_id=t2')
    assert first != -1 and second != -1 and first < second


def test_plan_run_injects_scope_specific_test_verification(monkeypatch, tmp_path):
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
                    'log_level': 'INFO',
                'model_copy': lambda self, update=None: self,
            },
        )(),
    )
    monkeypatch.setattr(
        'godotter.interfaces.cli.Agent.handle_input',
        lambda self, prompt: json.dumps(
            {
                'tasks': [
                    {
                        'id': 't1',
                        'title': 'Update inventory system',
                        'goal': 'Update inventory system behavior',
                        'depends_on': [],
                        'scope': ['game/systems/inventory/scripts/inventory_manager.gd'],
                        'acceptance': ['Inventory behavior updated'],
                        'verification': ['uv run godotter runtime lint --project .'],
                    },
                ]
            }
        ),
    )

    result = runner.invoke(app, ['plan', 'prepare', 'update inventory'])
    assert result.exit_code == 0

    captured: list[str] = []

    def _fake_task_run_command(
        *,
        workpack,
        latest,
        workspace,
        mode,
        brain,
        allow_no_changes=False,
        strict_audit=True,
        max_attempts=1,
        stop_on_same_failure=True,
        same_failure_limit=2,
    ):
        captured.append(str(workpack))

    monkeypatch.setattr('godotter.interfaces.cli.task_run_command', _fake_task_run_command)

    run_result = runner.invoke(app, ['plan', 'run', '--latest'])

    assert run_result.exit_code == 0
    pack = load_workpack(Path(captured[0]))
    assert 'uv run godotter runtime test --project . --kind system' in pack.verification


def test_plan_run_failed_task_records_latest_verify_report(monkeypatch, tmp_path):
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
                    'log_level': 'INFO',
                'model_copy': lambda self, update=None: self,
            },
        )(),
    )
    monkeypatch.setattr(
        'godotter.interfaces.cli.Agent.handle_input',
        lambda self, prompt: json.dumps(
            {
                'tasks': [
                    {
                        'id': 't1',
                        'title': 'Failing task',
                        'goal': 'Fail task',
                        'depends_on': [],
                        'scope': ['game/systems/failing/'],
                        'acceptance': ['Failure recorded'],
                        'verification': ['uv run godotter runtime verify'],
                    },
                ]
            }
        ),
    )
    prepare_result = runner.invoke(app, ['plan', 'prepare', 'fail task'])
    assert prepare_result.exit_code == 0

    def _fake_task_run_command(
        *,
        workpack,
        latest,
        workspace,
        mode,
        brain,
        allow_no_changes=False,
        strict_audit=True,
        max_attempts=1,
        stop_on_same_failure=True,
        same_failure_limit=2,
    ):
        report_dir = tmp_path / '.godotter' / 'reports' / 'verify'
        report_dir.mkdir(parents=True)
        (report_dir / 'latest.json').write_text('{"report_id":"vr_test","result":"fail"}\n', encoding='utf-8')
        raise RuntimeError('verification failed')

    monkeypatch.setattr('godotter.interfaces.cli.task_run_command', _fake_task_run_command)

    run_result = runner.invoke(app, ['plan', 'run', '--latest'])
    assert run_result.exit_code != 0

    status_result = runner.invoke(app, ['plan', 'status', '--latest'])
    assert status_result.exit_code == 0
    assert 'task id=t1 status=fail' in status_result.stdout
    assert 'artifact task=t1 verify_report=' in status_result.stdout


def test_plan_prepare_rejects_non_executable_tasks(monkeypatch, tmp_path):
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
                    'log_level': 'INFO',
                'model_copy': lambda self, update=None: self,
            },
        )(),
    )

    monkeypatch.setattr(
        'godotter.interfaces.cli.Agent.handle_input',
        lambda self, prompt: json.dumps(
            {
                'tasks': [
                    {
                        'id': 't1',
                        'title': 'Locate all double_down references',
                        'goal': 'Find every file that mentions double_down',
                        'depends_on': [],
                        'scope': ['game/features/tetris_gameplay/'],
                        'acceptance': ['References are listed'],
                        'verification': ['grep double_down'],
                    },
                ]
            }
        ),
    )

    result = runner.invoke(app, ['plan', 'prepare', 'fix input error'])
    assert result.exit_code != 0
    assert 'planner_quality_gate_failed' in result.output
