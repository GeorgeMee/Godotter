from __future__ import annotations

from datetime import datetime
from pathlib import Path
import hashlib
import json
import re
import secrets
import subprocess
import sys

import typer

from godotter.agent import Agent
from godotter.config import Settings, get_settings
from godotter.config.logging import configure_logging
from godotter.context import ExecutionContext, Memory
from godotter.llm import create_brain
from godotter.operations import OperationRegistry, build_default_operations
from godotter.services.godot import latest_verify_report_path, run_verify
from godotter.services.godot.cli_helpers import resolve_runtime_target
from godotter.services.project.scaffolding import expected_test_dirs_for_paths, infer_test_kinds_for_paths
from godotter.tasks.planpack import (
    PlanPack,
    PlanState,
    PlanTask,
    load_planpack,
    load_planstate,
    new_plan_id,
    plan_state_path,
    write_planpack,
    write_planstate,
)
from godotter.tasks.planning import (
    ScoutPromptRef,
    build_plan_prompt,
    build_revise_prompt,
    normalize_plan_dependencies,
    validate_plan_tasks,
)
from godotter.tasks.scout import collect_changed_files, scout_workspace, write_task_run_baseline
from godotter.tasks.runstate import (
    append_attempt,
    create_runstate,
    finish_attempt,
    finish_runstate,
    write_runstate,
)
from godotter.tasks.workpack import WorkPack, WorkPackFileRef, load_workpack, write_workpack
from godotter.context.project_summary import build_project_summary, render_project_summary
from godotter.context.scout_context import build_chat_scout_context


task_app = typer.Typer(help='Prepare and run workpacks for agent tasks.')
plan_app = typer.Typer(help='Prepare and run multi-step plans (PlanPacks).')


def _copy_settings(base: Settings, **overrides) -> Settings:
    try:
        return base.model_copy(update=overrides)
    except AttributeError:
        return base


def _resolve_workspace_root(
    base_settings: Settings,
    workspace: Path | None = None,
    project: str | None = None,
) -> tuple[Path, Settings]:
    if workspace:
        effective = workspace.resolve()
        return effective, _copy_settings(base_settings, workspace_root=effective)
    try:
        target = resolve_runtime_target(base_settings, project=project)
        effective = target.workspace_root
        return effective, _copy_settings(base_settings, workspace_root=effective)
    except Exception:
        root = base_settings.workspace_root.resolve()
        return root, _copy_settings(base_settings, workspace_root=root)


def _normalize_cli_mode(raw_mode: str) -> tuple[str, str | None]:
    normalized = raw_mode.strip().lower()
    if normalized in {'plan', 'act'}:
        return normalized, None

    alias_map = {
        'review': 'plan',
        'code': 'act',
        'debug': 'act',
    }
    mapped = alias_map.get(normalized)
    if mapped:
        return mapped, f'note=mode_alias input={normalized} mapped_to={mapped}'

    raise typer.BadParameter('Unsupported mode. Use plan or act.')


def _audit_task_run_changes(
    workspace_root: Path,
    *,
    allow_no_changes: bool = False,
    strict: bool = True,
    scope_paths: list[str] | None = None,
) -> None:
    changed = [ref for ref in collect_changed_files(workspace_root) if not ref.path.startswith('.godotter/')]
    typer.echo(f'task_run_audit changed_files={len(changed)}')
    for ref in changed[:10]:
        typer.echo(f'task_run_change path={ref.path} reason={ref.reason}')

    if not changed:
        if allow_no_changes:
            typer.echo('note=task_run_audit_allow_no_workspace_changes')
            return
        typer.echo('task_run_audit_error=no_workspace_changes')
        raise typer.Exit(1)

    changed_paths = {ref.path for ref in changed}
    touches_feature_or_system = any(
        path.startswith('game/features/') or path.startswith('game/systems/')
        for path in changed_paths
    )
    touches_tests = any(path.startswith('tests/') for path in changed_paths)
    expected_test_dirs = expected_test_dirs_for_paths(changed_paths)
    missing_expected_tests = [
        test_dir for test_dir in expected_test_dirs if not any(path.startswith(test_dir) for path in changed_paths)
    ]

    if touches_feature_or_system and not touches_tests:
        if strict:
            typer.echo('task_run_audit_error=missing_tests_for_game_logic_changes')
            raise typer.Exit(1)
        typer.echo('task_run_audit_warn=missing_tests_for_game_logic_changes')

    if missing_expected_tests:
        message = ','.join(missing_expected_tests)
        if strict:
            typer.echo(f'task_run_audit_error=missing_expected_test_layer dirs={message}')
            raise typer.Exit(1)
        typer.echo(f'task_run_audit_warn=missing_expected_test_layer dirs={message}')

    if scope_paths:
        uncovered = [p for p in scope_paths if not any(p in cp for cp in changed_paths)]
        if uncovered:
            for p in uncovered:
                typer.echo(f'task_run_audit_warn=scope_file_not_covered path={p}')


def _task_changed_paths(workspace_root: Path) -> list[str]:
    return [ref.path for ref in collect_changed_files(workspace_root) if not ref.path.startswith('.godotter/')]


def _run_task_verification_commands(workspace_root: Path, commands: list[str]) -> tuple[bool, str | None, str]:
    for command in commands:
        command = _rewrite_verification_command(workspace_root, command)
        typer.echo(f'task_run_verify command={command}')
        if not command:
            typer.echo('task_run_verify exit_code=1 command_empty=true')
            return False, 'verify_empty_command', ''
        try:
            completed = subprocess.run(
                command,
                cwd=workspace_root,
                capture_output=True,
                timeout=300,
                shell=True,
            )
        except subprocess.TimeoutExpired as exc:
            out_b = exc.stdout or b''
            err_b = exc.stderr or b''
            stdout = out_b.decode('utf-8', errors='replace').strip() or '(empty)'
            stderr = err_b.decode('utf-8', errors='replace').strip() or '(empty)'
            typer.echo('task_run_verify exit_code=-1 timed_out=true')
            typer.echo(f'task_run_verify_stdout={stdout}')
            typer.echo(f'task_run_verify_stderr={stderr}')
            failure = f'verify_timeout command={command}\nstdout={stdout}\nstderr={stderr}'
            signature = hashlib.sha256(failure.encode('utf-8', errors='replace')).hexdigest()
            return False, failure, signature

        out_b = completed.stdout or b''
        err_b = completed.stderr or b''
        stdout = out_b.decode('utf-8', errors='replace').strip() or '(empty)'
        stderr = err_b.decode('utf-8', errors='replace').strip() or '(empty)'
        typer.echo(f'task_run_verify exit_code={completed.returncode} timed_out=false')
        typer.echo(f'task_run_verify_stdout={stdout}')
        typer.echo(f'task_run_verify_stderr={stderr}')
        if completed.returncode != 0:
            failure = f'verify_failed command={command} exit_code={completed.returncode}\nstdout={stdout}\nstderr={stderr}'
            signature = hashlib.sha256(failure.encode('utf-8', errors='replace')).hexdigest()
            return False, failure, signature

    signature = hashlib.sha256(b'OK').hexdigest()
    return True, None, signature


def _record_failure_verify_report(
    workspace_root: Path,
    *,
    source: dict[str, object],
    allow_existing: bool = False,
) -> Path | None:
    latest_path = latest_verify_report_path(workspace_root)
    if allow_existing and latest_path.exists():
        typer.echo(f'task_run_verify_report_stale_ignored={latest_path.as_posix()}')
    try:
        _report, path = run_verify(workspace_root, source=source)
    except Exception as exc:
        typer.echo(f'task_run_verify_report_error={type(exc).__name__}: {exc}')
        return None
    typer.echo(f'task_run_verify_report={path.as_posix()}')
    return path


def _latest_verify_report_artifact(workspace_root: Path, *, since: datetime | None = None) -> str | None:
    path = latest_verify_report_path(workspace_root)
    if not path.exists():
        return None
    if since is not None and path.stat().st_mtime < since.timestamp():
        return None
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    report_root = str(payload.get('workspace_root') or '')
    if report_root and Path(report_root).resolve() != workspace_root.resolve():
        return None
    return path.as_posix()


def _ensure_scope_test_verification(scope: list[str], commands: list[str]) -> list[str]:
    normalized = list(commands)
    existing = '\n'.join(normalized).lower()
    if 'runtime verify' in existing:
        return normalized
    for kind in infer_test_kinds_for_paths(scope):
        command = f'uv run godotter runtime test --project . --kind {kind}'
        if command.lower() not in existing:
            normalized.append(command)
    return normalized


def _rewrite_verification_command(workspace_root: Path, command: str) -> str:
    raw = (command or '').strip()
    if not raw:
        return raw

    for suffix in (' passes', ' succeeds', ' success', ' ok'):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)].rstrip()

    if raw == 'uv run godotter runtime lint --project . (project-wide)':
        return 'uv run godotter runtime lint --project . --headless'
    if raw == 'uv run godotter runtime lint --project . all':
        return 'uv run godotter runtime lint --project . --headless'
    if raw == 'uv run godotter runtime lint --project . warnings':
        return 'uv run godotter runtime lint --project . --headless'
    if raw == 'uv run godotter runtime lint --project . clean':
        return 'uv run godotter runtime lint --project . --headless'
    if raw in {'uv run godotter runtime verify', 'godotter runtime verify'}:
        return f'{raw} --project .'
    if raw.startswith('uv run godotter runtime verify ') and (' --kind ' in raw or ' --name ' in raw):
        return 'uv run godotter runtime verify --project .'
    if raw.startswith('godotter runtime verify ') and (' --kind ' in raw or ' --name ' in raw):
        return 'godotter runtime verify --project .'

    if 'run-scene' in raw:
        if ' --kind ' in raw:
            raw = raw.replace('run-scene', 'test')
            if '--project .' not in raw:
                raw = raw.replace('uv run godotter runtime test', 'uv run godotter runtime test --project .')
            raw = re.sub(r' --scene \S+', '', raw)
        else:
            raw = raw.replace('run-scene', 'run')
            if '--project .' not in raw:
                raw = raw.replace('uv run godotter runtime run', 'uv run godotter runtime run --project .')

    prefix = 'uv run godotter runtime lint --project . --headless '
    fallback_prefix = 'uv run godotter runtime lint --project . '
    if raw.startswith(prefix):
        path = raw[len(prefix) :].strip().strip('"').strip("'")
    elif raw.startswith(fallback_prefix):
        path = raw[len(fallback_prefix) :].strip().strip('"').strip("'")
    else:
        path = None
    if path is not None:
        if path.startswith('--path '):
            cleaned = path[len('--path ') :].strip().strip('"').strip("'")
            if cleaned:
                return f'uv run godotter runtime lint --project . --headless {cleaned}'
            return 'uv run godotter runtime lint --project . --headless'
        if path and ('/' not in path and '\\' not in path):
            candidate = workspace_root / path
            if not candidate.exists():
                matches = list(workspace_root.rglob(path))
                matches = [m for m in matches if m.is_file()]
                if len(matches) == 1:
                    rel = matches[0].relative_to(workspace_root).as_posix()
                    return f'uv run godotter runtime lint --project . --headless {rel}'
        return 'uv run godotter runtime lint --project . --headless'
    return raw


def _normalize_verification_commands(commands: list[str]) -> list[str]:
    normalized: list[str] = []
    for cmd in commands or []:
        raw = str(cmd).strip()
        if not raw:
            continue
        if raw.startswith('`') and raw.endswith('`') and len(raw) > 2:
            raw = raw[1:-1].strip()
        lower = raw.lower()

        if raw.startswith('uv ') or raw.startswith('godotter '):
            normalized.append(raw)
            continue

        if 'script_lint' in lower:
            path = None
            if 'target=' in raw:
                path = raw.split('target=', 1)[1].strip().split()[0].strip('"').strip("'")
            elif ' on ' in lower:
                path = raw.split(' on ', 1)[1].strip().split()[0].strip('"').strip("'")
            else:
                parts = raw.split()
                if len(parts) >= 2:
                    path = parts[-1].strip('"').strip("'")
            if path:
                if path.endswith('/') or path.endswith('\\'):
                    normalized.append('uv run godotter runtime lint --project . --headless')
                else:
                    normalized.append(f'uv run godotter runtime lint --project . --headless {path}')
            else:
                normalized.append('uv run godotter runtime lint --project . --headless')
            continue

        if 'headless_run' in lower or 'headless run' in lower:
            scene = None
            if 'target=' in raw:
                scene = raw.split('target=', 1)[1].strip().split()[0].strip('"').strip("'")
            else:
                for token in raw.split():
                    if token.startswith('res://'):
                        scene = token.strip('"').strip("'")
                        break
            if scene:
                normalized.append(f'uv run godotter runtime run --project . --scene {scene} --timeout 30')
            continue

    return normalized


def _maybe_apply_unified_diff(workspace_root: Path, text: str) -> None:
    stripped = (text or '').lstrip()
    if not stripped.startswith('diff --git '):
        return

    registry = build_default_operations()
    tool = registry.get('apply_unified_patch')
    if tool is None:
        typer.echo('task_run_patch_error=apply_unified_patch_tool_missing')
        raise typer.Exit(1)

    context = ExecutionContext(
        settings=get_settings(),
        workspace_root=workspace_root.resolve(),
        memory=None,
    )
    try:
        result = tool.execute(context, {'patch': text})
    except Exception as exc:
        typer.echo(f'task_run_patch_error={type(exc).__name__}: {exc}')
        raise typer.Exit(1) from exc
    typer.echo(f'task_run_patch_applied={result.model_dump_json(indent=2)}')


def _dump_task_debug(agent: Agent) -> None:
    try:
        tail = agent.conversation[-12:]
    except Exception:
        return
    typer.echo(f'task_run_debug_tail={len(tail)}')
    for msg in tail:
        role = msg.get('role')
        if role == 'assistant':
            tool_calls = msg.get('tool_calls') or []
            typer.echo(f'task_run_debug role=assistant has_tool_calls={bool(tool_calls)}')
        elif role == 'tool':
            tool_call_id = msg.get('tool_call_id')
            content = (msg.get('content') or '').strip()
            preview = ' '.join(content.split())[:200] if content else '(empty)'
            typer.echo(f'task_run_debug role=tool id={tool_call_id} preview={preview}')
        elif role == 'user':
            typer.echo('task_run_debug role=user')


def _ensure_utf8_stdio() -> None:
    # Avoid Windows cp936/gbk UnicodeEncodeError when LLM outputs emoji/symbols.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]
        except Exception:
            continue


_ensure_utf8_stdio()

app = typer.Typer(
    help='Godotter tasking interface for workflow automation.',
    no_args_is_help=True,
)
task_app = typer.Typer(help='Prepare and run workpacks for agent tasks.')
plan_app = typer.Typer(help='Prepare and run multi-step plans (PlanPacks).')

app.add_typer(task_app, name='task')
app.add_typer(plan_app, name='plan')


@app.callback()
def main() -> None:
    return None


@task_app.command('prepare', help='Create a WorkPack (compact task context) under .godotter/workpacks/.')
def task_prepare_command(
    goal: str = typer.Argument(..., help='Task goal / requirement, in one sentence.'),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    include: list[str] = typer.Option(
        [],
        '--include',
        help='Additional file paths to include as relevant files (repeatable).',
    ),
) -> None:
    base_settings = get_settings()
    root, settings = _resolve_workspace_root(base_settings, workspace=workspace)
    constraints = [
        'Obey Godotter dev-mode docs under Docs/.',
        'Levels must have a root Managers node and a Managers/EventBus child.',
        'Prefer structured events via EventBus; avoid implicit get-from-group lookups outside Managers.',
        'Run `godotter runtime verify` after changes; use lower-level runtime validators only for diagnosis.',
    ]
    relevant: list[WorkPackFileRef] = [
        WorkPackFileRef(path='Docs/godotter_dev_mode_project_structure.md', reason='Dev-mode conventions', priority=10),
        WorkPackFileRef(path='Docs/godotter_template_project.md', reason='Template conventions', priority=20),
        WorkPackFileRef(path='Docs/testing_strategy.md', reason='Testing strategy', priority=25),
    ]
    for extra in include:
        relevant.append(WorkPackFileRef(path=extra, reason='User-specified', priority=50))

    pack = WorkPack(
        task_id=f'wp_{secrets.token_hex(4)}',
        created_at=datetime.now().isoformat(timespec='seconds'),
        workspace_root=root.as_posix(),
        goal=goal,
        constraints=constraints,
        relevant_files=relevant,
        execution_plan=[
            'Scout: locate relevant files and constraints',
            'Execute: implement minimal changes within scope',
            'Verify: run validation commands and tests',
        ],
        verification=['uv run godotter runtime verify'],
    )
    out_path = write_workpack(root, pack)
    typer.echo(f'workpack={out_path.as_posix()}')


@task_app.command('scout', help='Scan the workspace to build a higher-signal WorkPack before execution.')
def task_scout_command(
    goal: str = typer.Argument(..., help='Task goal / requirement, in one sentence.'),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    max_files: int = typer.Option(40, '--max-files', help='Maximum number of relevant files to include.'),
) -> None:
    base_settings = get_settings()
    root, settings = _resolve_workspace_root(base_settings, workspace=workspace)
    scout = scout_workspace(root, goal, max_files=max_files)

    constraints = [
        'Obey Godotter dev-mode docs under Docs/.',
        'Levels must have a root Managers node and a Managers/EventBus child.',
        'Prefer structured events via EventBus; avoid implicit get-from-group lookups outside Managers.',
        'Run `godotter runtime verify` after changes; use lower-level runtime validators only for diagnosis.',
    ]
    relevant: list[WorkPackFileRef] = [
        WorkPackFileRef(path='Docs/godotter_dev_mode_project_structure.md', reason='Dev-mode conventions', priority=10),
        WorkPackFileRef(path='Docs/godotter_template_project.md', reason='Template conventions', priority=20),
        WorkPackFileRef(path='Docs/testing_strategy.md', reason='Testing strategy', priority=25),
    ]
    relevant.extend(scout.relevant_files)

    pack = WorkPack(
        task_id=f'wp_{secrets.token_hex(4)}',
        created_at=datetime.now().isoformat(timespec='seconds'),
        workspace_root=root.as_posix(),
        goal=goal,
        constraints=constraints,
        assumptions=[
            f'scout_keywords={",".join(scout.keywords)}',
            f'scout_changed_files={",".join(ref.path for ref in scout.changed_files)}',
        ],
        relevant_files=relevant,
        execution_plan=[
            'Execute: implement minimal changes within scope',
            'Verify: run validation commands and tests',
        ],
        verification=['uv run godotter runtime verify'],
    )
    out_path = write_workpack(root, pack)
    typer.echo(f'workpack={out_path.as_posix()}')


@task_app.command('run', help='Run an agent task using a WorkPack (defaults to .godotter/workpacks/latest.json).')
def task_run_command(
    workpack: Path | None = typer.Option(
        None,
        '--workpack',
        help='Path to a WorkPack JSON file (defaults to .godotter/workpacks/latest.json).',
    ),
    latest: bool = typer.Option(
        False,
        '--latest',
        help='Use .godotter/workpacks/latest.json (overrides --workpack).',
    ),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    mode: str = typer.Option(
        'plan',
        '--mode',
        help='Agent execution mode: plan or act. Deprecated aliases: review->plan, code/debug->act.',
    ),
    allow_no_changes: bool = typer.Option(
        False,
        '--allow-no-changes',
        help='Allow act-mode runs that only verify existing code without modifying files.',
    ),
    strict_audit: bool = typer.Option(
        True,
        '--strict-audit/--no-strict-audit',
        help='Fail fast on audit violations (default: strict). Plan runs may disable strict audit and rely on final verification.',
    ),
    max_attempts: int = typer.Option(
        1,
        '--max-attempts',
        min=1,
        help='Max agent attempts for this WorkPack when verification fails (act mode only).',
    ),
    stop_on_same_failure: bool = typer.Option(
        True,
        '--stop-on-same-failure/--no-stop-on-same-failure',
        help='Stop early if the same verification failure repeats (act mode only).',
    ),
    same_failure_limit: int = typer.Option(
        2,
        '--same-failure-limit',
        min=1,
        help='How many times the same failure may repeat before stopping early (act mode only).',
    ),
    brain: str | None = typer.Option(None, '--brain', help='Override the default AI brain/provider for this run.'),
) -> None:
    base_settings = get_settings()
    normalized_mode, mode_note = _normalize_cli_mode(mode)
    root, _ = _resolve_workspace_root(base_settings, workspace=workspace)
    workpack_path = (root / '.godotter' / 'workpacks' / 'latest.json') if latest else (
        workpack or (root / '.godotter' / 'workpacks' / 'latest.json')
    )
    if not workpack_path.exists():
        raise typer.BadParameter(f'WorkPack not found: {workpack_path}')

    pack = load_workpack(workpack_path)
    pack_root = Path(pack.workspace_root).resolve() if pack.workspace_root else root
    execution_root = (workspace or pack_root).resolve()
    settings = base_settings.model_copy(update={'workspace_root': execution_root})

    configure_logging(settings)
    memory = Memory(settings.resolved_memory_path)
    registry = build_default_operations()
    selected_brain = brain or settings.resolved_act_brain
    summary = build_project_summary(execution_root)
    summary_text = render_project_summary(summary) if summary else None
    agent = Agent(
        brain=create_brain(settings, selected_brain, model_override=getattr(settings, 'act_model', None)),
        settings=settings,
        registry=registry,
        memory=memory,
        mode=normalized_mode,
        brain_name=selected_brain,
        project_summary=summary_text,
    )

    prompt_lines = [
        'You are executing a Godotter WorkPack.',
        f'goal={pack.goal}',
        '',
        'Constraints:',
        *[f'- {c}' for c in pack.constraints],
        '',
        'Relevant files:',
        *[f'- {ref.path} (p{ref.priority}) {ref.reason}'.rstrip() for ref in pack.relevant_files],
        '',
        'Execution plan:',
        *[f'- {step}' for step in pack.execution_plan],
        '',
        'Verification:',
        *[f'- {cmd}' for cmd in pack.verification],
        '',
        'Proceed to implement the goal with minimal, testable changes.',
    ]
    if normalized_mode == 'act':
        prompt_lines.extend(
            [
                '',
                'Act-mode requirements:',
                '- If tool-calls are available, use replace_text, replace_file, apply_unified_patch, and other tools to make changes.',
                '- If tool-calls are not available, output a single unified diff patch (no commentary).',
                '- After changes, ensure verification commands pass.',
                '',
                'Before you finish, verify EVERY acceptance criterion below is met. If any is not done, continue working.',
                'Checklist (you MUST confirm each item):',
                *[f'  [ ] {step}' for step in pack.execution_plan],
                '',
                'Scope files that must be modified:',
                *[f'  - {p}' for p in pack.relevant_files if p.reason == 'scope'],
            ]
        )
    if mode_note:
        typer.echo(mode_note)

    if normalized_mode == 'act':
        # Only create baseline once per workspace so we can diff after multiple act runs.
        baseline_path = settings.workspace_root.resolve() / '.godotter' / '.task_run_baseline.json'
        if not baseline_path.exists():
            write_task_run_baseline(settings.workspace_root.resolve())

    last_signature: str | None = None
    same_failure_count = 0
    failure_report: str | None = None
    agent_output: str = ''
    run_state, run_state_path = create_runstate(
        workspace_root=settings.workspace_root.resolve(),
        workpack_path=workpack_path.resolve(),
        task_id=pack.task_id,
        goal=pack.goal,
        mode=normalized_mode,
    )
    typer.echo(f'runstate={run_state_path.as_posix()}')

    attempts = max_attempts if normalized_mode == 'act' else 1
    for attempt_index in range(1, attempts + 1):
        run_attempt = append_attempt(run_state, attempt_index)
        write_runstate(settings.workspace_root.resolve(), run_state)
        attempt_prompt = list(prompt_lines)
        if failure_report:
            attempt_prompt.extend(
                [
                    '',
                    f'Previous attempt {attempt_index - 1} FAILED. You MUST debug and fix the root cause.',
                    '',
                    'Required diagnostic process:',
                    '1. Read the verification output below carefully — identify what failed, what passed.',
                    '2. Form a NEW hypothesis about the root cause. If your last hypothesis was about logic, consider timing/async/signal-lifecycle issues.',
                    '3. If needed, add temporary debug prints (print("[DEBUG ...]")) to the failing code, then re-run the test to gather evidence.',
                    '4. Once you have evidence, apply the fix and verify.',
                    '',
                    'Full verification output from the last attempt:',
                    failure_report,
                    '',
                    'Your last analysis (do NOT repeat this; try a different approach):',
                    (agent_output or '')[-1200:],
                ]
            )

        agent_output = agent.handle_input('\n'.join(attempt_prompt))
        run_attempt.agent_output = agent_output
        write_runstate(settings.workspace_root.resolve(), run_state)
        typer.echo(agent_output)
        if normalized_mode != 'act':
            finish_attempt(run_state, run_attempt, status='pass')
            finish_runstate(run_state, status='pass')
            write_runstate(settings.workspace_root.resolve(), run_state)
            return

        _maybe_apply_unified_diff(settings.workspace_root.resolve(), agent_output)

        try:
            _audit_task_run_changes(
                settings.workspace_root.resolve(),
                allow_no_changes=allow_no_changes,
                strict=strict_audit,
                scope_paths=[ref.path for ref in pack.relevant_files if ref.reason == 'scope'],
            )
        except typer.Exit as exc:
            _dump_task_debug(agent)
            failure_report = f'audit_failed={type(exc).__name__}'
            run_attempt.changed_files = _task_changed_paths(settings.workspace_root.resolve())
            signature = hashlib.sha256(failure_report.encode('utf-8', errors='replace')).hexdigest()
            if stop_on_same_failure:
                agent_sig = hashlib.sha256(
                    ((agent_output or '')[-800:]).encode('utf-8', errors='replace')
                ).hexdigest()[:8]
                combined = f'{signature}:{agent_sig}'
                if combined == last_signature:
                    same_failure_count += 1
                else:
                    same_failure_count = 1
                    last_signature = combined
            if stop_on_same_failure and same_failure_count >= same_failure_limit:
                verify_report = _record_failure_verify_report(
                    settings.workspace_root.resolve(),
                    source={'command': 'task run', 'task_id': pack.task_id, 'reason': 'audit_failed'},
                )
                finish_attempt(
                    run_state,
                    run_attempt,
                    status='fail',
                    failure_report=failure_report,
                    verify_report=verify_report.as_posix() if verify_report else None,
                )
                finish_runstate(run_state, status='fail')
                write_runstate(settings.workspace_root.resolve(), run_state)
                raise
            if attempt_index >= attempts:
                verify_report = _record_failure_verify_report(
                    settings.workspace_root.resolve(),
                    source={'command': 'task run', 'task_id': pack.task_id, 'reason': 'audit_failed'},
                )
                finish_attempt(
                    run_state,
                    run_attempt,
                    status='fail',
                    failure_report=failure_report,
                    verify_report=verify_report.as_posix() if verify_report else None,
                )
                finish_runstate(run_state, status='fail')
                write_runstate(settings.workspace_root.resolve(), run_state)
                raise
            finish_attempt(run_state, run_attempt, status='retry', failure_report=failure_report)
            write_runstate(settings.workspace_root.resolve(), run_state)
            continue

        run_attempt.changed_files = _task_changed_paths(settings.workspace_root.resolve())
        write_runstate(settings.workspace_root.resolve(), run_state)
        ok, failure_report, signature = _run_task_verification_commands(settings.workspace_root.resolve(), pack.verification)
        if ok:
            finish_attempt(run_state, run_attempt, status='pass')
            finish_runstate(run_state, status='pass')
            write_runstate(settings.workspace_root.resolve(), run_state)
            return

        verify_report = _record_failure_verify_report(
            settings.workspace_root.resolve(),
            source={'command': 'task run', 'task_id': pack.task_id, 'reason': 'verification_failed'},
            allow_existing=any('runtime verify' in command.lower() for command in pack.verification),
        )

        if stop_on_same_failure:
            agent_sig = hashlib.sha256(
                ((agent_output or '')[-800:]).encode('utf-8', errors='replace')
            ).hexdigest()[:8]
            combined = f'{signature}:{agent_sig}'
            if combined == last_signature:
                same_failure_count += 1
            else:
                same_failure_count = 1
                last_signature = combined
        if stop_on_same_failure and same_failure_count >= same_failure_limit:
            typer.echo('task_run_retry_stop_reason=same_failure_repeated')
            finish_attempt(
                run_state,
                run_attempt,
                status='fail',
                failure_report=failure_report,
                verify_report=verify_report.as_posix() if verify_report else None,
            )
            finish_runstate(run_state, status='fail')
            write_runstate(settings.workspace_root.resolve(), run_state)
            raise typer.Exit(1)
        if attempt_index >= attempts:
            finish_attempt(
                run_state,
                run_attempt,
                status='fail',
                failure_report=failure_report,
                verify_report=verify_report.as_posix() if verify_report else None,
            )
            finish_runstate(run_state, status='fail')
            write_runstate(settings.workspace_root.resolve(), run_state)
            raise typer.Exit(1)
        finish_attempt(
            run_state,
            run_attempt,
            status='retry',
            failure_report=failure_report,
            verify_report=verify_report.as_posix() if verify_report else None,
        )
        write_runstate(settings.workspace_root.resolve(), run_state)


@task_app.command('list', help='List WorkPacks under .godotter/workpacks/.')
def task_list_command(
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    ) -> None:
    base_settings = get_settings()
    root, _ = _resolve_workspace_root(base_settings, workspace=workspace)
    workpack_dir = root / '.godotter' / 'workpacks'
    if not workpack_dir.exists():
        typer.echo(f'workpack_dir={workpack_dir.as_posix()}')
        typer.echo('count=0')
        return

    paths = sorted(workpack_dir.glob('*.json'), key=lambda p: p.name, reverse=True)
    paths = [p for p in paths if p.name != 'latest.json']
    typer.echo(f'workpack_dir={workpack_dir.as_posix()}')
    typer.echo(f'count={len(paths)}')
    for path in paths:
        try:
            pack = load_workpack(path)
            typer.echo(f'workpack={path.name} created_at={pack.created_at} task_id={pack.task_id} goal={pack.goal}')
        except Exception as exc:
            typer.echo(f'workpack={path.name} error={type(exc).__name__}: {exc}')


@task_app.command('show', help='Show a WorkPack summary.')
def task_show_command(
    workpack: Path | None = typer.Option(
        None,
        '--workpack',
        help='Path to a WorkPack JSON file (defaults to latest.json).',
    ),
    latest: bool = typer.Option(
        False,
        '--latest',
        help='Use .godotter/workpacks/latest.json (overrides --workpack).',
    ),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    ) -> None:
    base_settings = get_settings()
    root, _ = _resolve_workspace_root(base_settings, workspace=workspace)
    workpack_path = (root / '.godotter' / 'workpacks' / 'latest.json') if latest else (
        workpack or (root / '.godotter' / 'workpacks' / 'latest.json')
    )
    if not workpack_path.exists():
        raise typer.BadParameter(f'WorkPack not found: {workpack_path}')
    pack = load_workpack(workpack_path)
    typer.echo(f'workpack={workpack_path.as_posix()}')
    typer.echo(f'task_id={pack.task_id}')
    typer.echo(f'created_at={pack.created_at}')
    typer.echo(f'workspace_root={pack.workspace_root}')
    typer.echo(f'goal={pack.goal}')
    typer.echo(f'constraints={len(pack.constraints)}')
    for assumption in pack.assumptions:
        typer.echo(f'assumption={assumption}')
    typer.echo(f'relevant_files={len(pack.relevant_files)}')
    for ref in pack.relevant_files[:10]:
        suffix = f' {ref.reason}'.rstrip() if ref.reason else ''
        typer.echo(f'relevant_file path={ref.path} priority={ref.priority}{suffix}')
    typer.echo(f'execution_plan={len(pack.execution_plan)}')
    typer.echo(f'verification={len(pack.verification)}')


@plan_app.command('prepare', help='Create a PlanPack (multi-step task plan) under .godotter/plans/.')
def plan_prepare_command(
    goal: str = typer.Argument(..., help='High-level goal to split into smaller tasks.'),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    brain: str | None = typer.Option(None, '--brain', help='Override the default AI brain/provider for planning.'),
) -> None:
    base_settings = get_settings()
    root, settings = _resolve_workspace_root(base_settings, workspace=workspace)

    configure_logging(settings)
    memory = Memory(settings.resolved_memory_path)
    registry = build_default_operations()
    selected_brain = brain or settings.resolved_plan_brain
    summary = build_project_summary(root)
    summary_text = render_project_summary(summary) if summary else None
    agent = Agent(
        brain=create_brain(settings, selected_brain, model_override=getattr(settings, 'plan_model', None)),
        settings=settings,
        registry=registry,
        memory=memory,
        mode='plan',
        brain_name=selected_brain,
        project_summary=summary_text,
    )
    # PlanPack generation must be a pure text/JSON response. Tools would
    # cause the LLM to make changes instead of outputting a JSON plan.
    agent.brain.tools = []
    if hasattr(agent.brain, 'tool_choice'):
        setattr(agent.brain, 'tool_choice', 'none')

    scout = scout_workspace(root, goal, max_files=40)
    prompt, constraints = build_plan_prompt(
        goal,
        [ScoutPromptRef(path=ref.path, reason=ref.reason) for ref in scout.relevant_files],
    )
    raw = agent.handle_input(prompt)
    raw_stripped = raw.strip()
    parsed: dict
    try:
        parsed = json.loads(raw_stripped)
    except Exception:
        # Try to extract the JSON object from a mixed response.
        end = raw_stripped.rfind('}')
        if end == -1:
            debug_path = root / '.godotter' / 'plans' / 'last_planner_output.txt'
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(raw_stripped, encoding='utf-8', newline='\n')
            raise typer.BadParameter(
                f'Planner did not return JSON: could not find JSON object (saved raw to {debug_path.as_posix()})'
            )
        # Find "tasks" keyword first, then locate the root { before it.
        tasks_pos = raw_stripped.rfind('"tasks"', 0, end)
        if tasks_pos != -1:
            start = raw_stripped.rfind('{', 0, tasks_pos)
        else:
            start = raw_stripped.rfind('{', 0, end)
        if start != -1 and end > start:
            try:
                parsed = json.loads(raw_stripped[start : end + 1])
            except Exception as exc:
                debug_path = root / '.godotter' / 'plans' / 'last_planner_output.txt'
                debug_path.parent.mkdir(parents=True, exist_ok=True)
                debug_path.write_text(raw_stripped, encoding='utf-8', newline='\n')
                raise typer.BadParameter(
                    f'Planner did not return JSON: {exc} (saved raw to {debug_path.as_posix()})'
                ) from exc
        else:
            debug_path = root / '.godotter' / 'plans' / 'last_planner_output.txt'
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(raw_stripped, encoding='utf-8', newline='\n')
            raise typer.BadParameter(
                f'Planner did not return JSON: could not find JSON object (saved raw to {debug_path.as_posix()})'
            )

    raw_tasks = parsed.get('tasks', [])
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise typer.BadParameter('Planner JSON missing tasks[]')

    tasks: list[PlanTask] = []
    used_ids: set[str] = set()
    for index, item in enumerate(raw_tasks, start=1):
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get('id', '')).strip()
        task_id = raw_id or f't{index}'
        if task_id in used_ids:
            task_id = f'{task_id}_{index}'
        used_ids.add(task_id)
        tasks.append(
            PlanTask(
                id=task_id,
                title=str(item.get('title', '')).strip() or 'task',
                goal=str(item.get('goal', '')).strip() or '',
                depends_on=[str(x) for x in item.get('depends_on', []) if x],
                scope=[str(x) for x in item.get('scope', []) if x],
                acceptance=[str(x) for x in item.get('acceptance', []) if x],
                verification=[str(x) for x in item.get('verification', []) if x],
            )
        )

    try:
        normalize_plan_dependencies(tasks)
        validate_plan_tasks(tasks)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    pack = PlanPack(
        plan_id=new_plan_id(),
        created_at=datetime.now().isoformat(timespec='seconds'),
        workspace_root=root.as_posix(),
        goal=goal,
        name=str(parsed.get('name', '')).strip() or goal[:80],
        global_constraints=constraints,
        tasks=tasks,
    )
    out_path = write_planpack(root, pack)
    state = PlanState(
        plan_id=pack.plan_id,
        updated_at=datetime.now().isoformat(timespec='seconds'),
        task_status={task.id: 'pending' for task in tasks},
    )
    write_planstate(plan_state_path(out_path), state)
    latest_path = root / '.godotter' / 'plans' / 'latest.json'
    if latest_path.exists():
        write_planstate(plan_state_path(latest_path), state)
    typer.echo(f'plan={out_path.as_posix()}')
    typer.echo(f'tasks={len(tasks)}')


@plan_app.command('list', help='List PlanPacks under .godotter/plans/.')
def plan_list_command(
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    ) -> None:
    base_settings = get_settings()
    root, _ = _resolve_workspace_root(base_settings, workspace=workspace)
    plan_dir = root / '.godotter' / 'plans'
    if not plan_dir.exists():
        typer.echo(f'plan_dir={plan_dir.as_posix()}')
        typer.echo('count=0')
        return
    paths = sorted(plan_dir.glob('*.json'), key=lambda p: p.name, reverse=True)
    paths = [p for p in paths if p.name != 'latest.json' and not p.name.endswith('.state.json')]
    typer.echo(f'plan_dir={plan_dir.as_posix()}')
    typer.echo(f'count={len(paths)}')
    for path in paths:
        try:
            pack = load_planpack(path)
            typer.echo(f'plan={path.name} created_at={pack.created_at} plan_id={pack.plan_id} goal={pack.goal}')
        except Exception as exc:
            typer.echo(f'plan={path.name} error={type(exc).__name__}: {exc}')


@plan_app.command('show', help='Show PlanPack summary.')
def plan_show_command(
    plan: Path | None = typer.Option(None, '--plan', help='Path to PlanPack JSON file (defaults to latest.json).'),
    latest: bool = typer.Option(False, '--latest', help='Use .godotter/plans/latest.json.'),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    ) -> None:
    base_settings = get_settings()
    root, _ = _resolve_workspace_root(base_settings, workspace=workspace)
    plan_path = (root / '.godotter' / 'plans' / 'latest.json') if latest else (plan or (root / '.godotter' / 'plans' / 'latest.json'))
    if not plan_path.exists():
        raise typer.BadParameter(f'PlanPack not found: {plan_path}')
    pack = load_planpack(plan_path)
    typer.echo(f'plan={plan_path.as_posix()}')
    typer.echo(f'plan_id={pack.plan_id}')
    typer.echo(f'created_at={pack.created_at}')
    typer.echo(f'workspace_root={pack.workspace_root}')
    typer.echo(f'goal={pack.goal}')
    typer.echo(f'tasks={len(pack.tasks)}')
    for task in pack.tasks[:15]:
        typer.echo(f'task id={task.id} title={task.title}')


@plan_app.command('status', help='Show PlanPack run status (.state.json).')
def plan_status_command(
    plan: Path | None = typer.Option(None, '--plan', help='Path to PlanPack JSON file (defaults to latest.json).'),
    latest: bool = typer.Option(False, '--latest', help='Use .godotter/plans/latest.json.'),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
) -> None:
    base_settings = get_settings()
    root, _ = _resolve_workspace_root(base_settings, workspace=workspace)
    plan_path = (root / '.godotter' / 'plans' / 'latest.json') if latest else (plan or (root / '.godotter' / 'plans' / 'latest.json'))
    if not plan_path.exists():
        raise typer.BadParameter(f'PlanPack not found: {plan_path}')
    state_path = plan_state_path(plan_path)
    if not state_path.exists():
        typer.echo(f'state={state_path.as_posix()}')
        typer.echo('status=(missing)')
        return
    state = load_planstate(state_path)
    typer.echo(f'state={state_path.as_posix()}')
    typer.echo(f'updated_at={state.updated_at}')
    for task_id, status in state.task_status.items():
        typer.echo(f'task id={task_id} status={status}')
        for key, value in state.task_artifacts.get(task_id, {}).items():
            typer.echo(f'artifact task={task_id} {key}={value}')


@plan_app.command('run', help='Run tasks in a PlanPack sequentially (creates WorkPacks and executes them).')
def plan_run_command(
    plan: Path | None = typer.Option(None, '--plan', help='Path to PlanPack JSON file (defaults to latest.json).'),
    latest: bool = typer.Option(False, '--latest', help='Use .godotter/plans/latest.json.'),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    from_task: str | None = typer.Option(None, '--from', help='Start from this task id (inclusive).'),
    only_task: str | None = typer.Option(None, '--only', help='Run only this task id (ignores --from).'),
    rerun_passed: bool = typer.Option(False, '--rerun-passed', help='Re-run tasks already marked pass in state.'),
    continue_on_fail: bool = typer.Option(False, '--continue-on-fail', help='Continue running later tasks after a failure.'),
    max_attempts: int = typer.Option(
        3,
        '--max-attempts',
        min=1,
        help='Max agent attempts per task when verification fails.',
    ),
    stop_on_same_failure: bool = typer.Option(
        True,
        '--stop-on-same-failure/--no-stop-on-same-failure',
        help='Stop early if the same failure repeats across retries.',
    ),
    same_failure_limit: int = typer.Option(
        2,
        '--same-failure-limit',
        min=1,
        help='How many times the same failure may repeat before stopping early.',
    ),
    brain: str | None = typer.Option(None, '--brain', help='Override default brain/provider for execution.'),
) -> None:
    base_settings = get_settings()
    root, _ = _resolve_workspace_root(base_settings, workspace=workspace)
    plan_path = (root / '.godotter' / 'plans' / 'latest.json') if latest else (plan or (root / '.godotter' / 'plans' / 'latest.json'))
    if not plan_path.exists():
        raise typer.BadParameter(f'PlanPack not found: {plan_path}')
    pack = load_planpack(plan_path)
    state_path = plan_state_path(plan_path)
    state = load_planstate(state_path) if state_path.exists() else PlanState(plan_id=pack.plan_id, updated_at=datetime.now().isoformat(timespec='seconds'))

    # Determine task sequence.
    tasks = pack.tasks
    task_by_id = {t.id: t for t in tasks}
    if len(task_by_id) != len(tasks):
        raise typer.BadParameter('PlanPack contains duplicate task ids.')

    if only_task:
        if only_task not in task_by_id:
            raise typer.BadParameter(f'Unknown task id: {only_task}')
        tasks = [task_by_id[only_task]]
    elif from_task:
        idx = next((i for i, t in enumerate(tasks) if t.id == from_task), None)
        if idx is None:
            raise typer.BadParameter(f'Unknown task id: {from_task}')
        tasks = tasks[idx:]

    # Topologically order remaining tasks by depends_on where possible.
    if len(tasks) > 1:
        remaining_ids = {t.id for t in tasks}
        indegree: dict[str, int] = {t.id: 0 for t in tasks}
        edges: dict[str, list[str]] = {t.id: [] for t in tasks}
        for t in tasks:
            for dep in t.depends_on:
                if dep not in remaining_ids:
                    continue
                indegree[t.id] += 1
                edges.setdefault(dep, []).append(t.id)
        queue = [t.id for t in tasks if indegree[t.id] == 0]
        ordered_ids: list[str] = []
        while queue:
            current = queue.pop(0)
            ordered_ids.append(current)
            for nxt in edges.get(current, []):
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    queue.append(nxt)
        if len(ordered_ids) == len(tasks):
            tasks = [task_by_id[i] for i in ordered_ids]
        else:
            typer.echo('note=dependency_cycle_or_missing_deps_using_declared_order')

    for task in tasks:
        previous_status = state.task_status.get(task.id)
        if previous_status == 'pass' and not rerun_passed:
            state.task_status[task.id] = 'skipped'
            state.updated_at = datetime.now().isoformat(timespec='seconds')
            write_planstate(state_path, state)
            typer.echo(f'task_skipped id={task.id} title={task.title} reason=already_passed')
            continue

        state.task_status[task.id] = 'running'
        state.updated_at = datetime.now().isoformat(timespec='seconds')
        write_planstate(state_path, state)

        # Build a WorkPack from task details.
        settings = base_settings.model_copy(update={'workspace_root': root})
        selected_brain = brain or settings.resolved_act_brain

        scout_refs: list[WorkPackFileRef] = []
        if not task.scope:
            scoped_scout = scout_workspace(root, task.goal or task.title, max_files=25)
            scout_refs = [
                WorkPackFileRef(path=ref.path, reason=ref.reason or 'scout', priority=15) for ref in scoped_scout.relevant_files[:15]
            ]

        verification_commands = _normalize_verification_commands(task.verification) or ['uv run godotter runtime verify']
        verification_commands = _ensure_scope_test_verification(task.scope, verification_commands)

        wp = WorkPack(
            task_id=f'wp_{secrets.token_hex(4)}',
            created_at=datetime.now().isoformat(timespec='seconds'),
            workspace_root=root.as_posix(),
            goal=task.goal or task.title,
            constraints=pack.global_constraints,
            assumptions=[],
            relevant_files=[WorkPackFileRef(path=p, reason='scope', priority=30) for p in task.scope] + scout_refs,
            execution_plan=task.acceptance or [task.title],
            verification=verification_commands,
        )
        wp_path = write_workpack(root, wp)
        typer.echo(f'workpack={wp_path.as_posix()} task_id={task.id} title={task.title}')

        # Execute workpack using existing task runner logic (same process).
        task_started_at = datetime.now()
        try:
            task_run_command(
                workpack=wp_path,
                latest=False,
                workspace=root,
                mode='act',
                brain=selected_brain,
                allow_no_changes=True,
                strict_audit=False,
                max_attempts=max_attempts,
                stop_on_same_failure=stop_on_same_failure,
                same_failure_limit=same_failure_limit,
            )
        except Exception as exc:
            state.task_status[task.id] = 'fail'
            artifacts = {'error': f'{type(exc).__name__}: {exc}', 'workpack': wp_path.as_posix()}
            verify_report = _latest_verify_report_artifact(root, since=task_started_at)
            if verify_report:
                artifacts['verify_report'] = verify_report
            state.task_artifacts[task.id] = artifacts
            state.updated_at = datetime.now().isoformat(timespec='seconds')
            write_planstate(state_path, state)
            if continue_on_fail:
                typer.echo(f'task_failed_continue id={task.id} title={task.title}')
                continue
            raise

        state.task_status[task.id] = 'pass'
        state.task_artifacts[task.id] = {'workpack': wp_path.as_posix()}
        state.updated_at = datetime.now().isoformat(timespec='seconds')
        write_planstate(state_path, state)


@plan_app.command('revise', help='Revise a single task in a PlanPack based on reviewer feedback.')
def plan_revise_command(
    plan: Path = typer.Option(..., '--plan', help='Path to PlanPack JSON file.'),
    task: str = typer.Option(..., '--task', help='Task id to revise (e.g. t1).'),
    feedback: str = typer.Option(..., '--feedback', help='Reviewer feedback describing what went wrong and what to change.'),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    brain: str | None = typer.Option(None, '--brain', help='Override the default AI brain/provider for planning.'),
) -> None:
    base_settings = get_settings()
    root, settings = _resolve_workspace_root(base_settings, workspace=workspace)
    pack = load_planpack(plan)
    task_by_id = {t.id: t for t in pack.tasks}
    if task not in task_by_id:
        raise typer.BadParameter(f'Unknown task id: {task} (available: {", ".join(sorted(task_by_id.keys()))})')

    original = task_by_id[task]
    configure_logging(settings)
    memory = Memory(settings.resolved_memory_path)
    registry = build_default_operations()
    selected_brain = brain or settings.resolved_plan_brain
    summary = build_project_summary(root)
    summary_text = render_project_summary(summary) if summary else None
    agent = Agent(
        brain=create_brain(settings, selected_brain, model_override=getattr(settings, 'plan_model', None)),
        settings=settings,
        registry=registry,
        memory=memory,
        mode='plan',
        brain_name=selected_brain,
        project_summary=summary_text,
    )
    agent.brain.tools = []
    if hasattr(agent.brain, 'tool_choice'):
        setattr(agent.brain, 'tool_choice', 'none')

    prompt, _constraints = build_revise_prompt(original, feedback, pack.goal)
    raw = agent.handle_input(prompt)
    raw_stripped = raw.strip()

    try:
        parsed = json.loads(raw_stripped)
    except Exception:
        end = raw_stripped.rfind('}')
        if end == -1:
            raise typer.BadParameter('Planner did not return JSON for task revision.')
        start = raw_stripped.rfind('{', 0, end)
        if start == -1:
            raise typer.BadParameter('Planner did not return JSON for task revision.')
        try:
            parsed = json.loads(raw_stripped[start : end + 1])
        except Exception as exc:
            raise typer.BadParameter(f'Planner returned invalid JSON for task revision: {exc}') from exc

    revised = PlanTask(
        id=original.id,
        title=str(parsed.get('title', '')).strip() or original.title,
        goal=str(parsed.get('goal', '')).strip() or original.goal,
        depends_on=[str(x) for x in parsed.get('depends_on', original.depends_on) if x],
        scope=[str(x) for x in parsed.get('scope', original.scope) if x],
        acceptance=[str(x) for x in parsed.get('acceptance', original.acceptance) if x],
        verification=[str(x) for x in parsed.get('verification', original.verification) if x],
    )

    for i, t in enumerate(pack.tasks):
        if t.id == task:
            pack.tasks[i] = revised
            break

    write_planpack(root, pack)
    typer.echo(f'revised_task={task}')
    typer.echo(f'plan={plan.as_posix()}')


