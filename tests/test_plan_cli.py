import json

from typer.testing import CliRunner

from godotter.interfaces.cli import app


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
                        'verification': ['echo ok'],
                    },
                    {
                        'id': 't2',
                        'title': 'Task B',
                        'goal': 'Do B',
                        'depends_on': ['t1'],
                        'scope': ['game/systems/b/'],
                        'acceptance': ['B done'],
                        'verification': ['echo ok'],
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
                        'verification': ['echo ok'],
                    },
                    {
                        'id': 't1',
                        'title': 'Task 1',
                        'goal': 'Do 1',
                        'depends_on': [],
                        'scope': ['game/systems/t1/'],
                        'acceptance': ['1 done'],
                        'verification': ['echo ok'],
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
