from __future__ import annotations

from datetime import datetime
from pathlib import Path
import hashlib
import secrets
import subprocess
import json
import sys

import typer

from godotter.agent import Agent
from godotter.config import get_settings
from godotter.context import Memory
from godotter.llm import SUPPORTED_PROVIDERS, create_brain
from godotter.logging import configure_logging, get_logger
from godotter.operations import (
    build_runner,
    check_provider_connectivity,
    fetch_model_rows,
    format_doctor_report,
    format_provider_key_status,
    format_provider_rows,
    format_runtime_result,
    format_uid_fix_result,
    normalize_provider_name,
    render_project_scaffold_summary,
    resolve_runtime_target,
    scaffold_scene_only,
    scaffold_scene_with_script,
    scaffold_godot_project,
    set_default_provider,
    set_model_for_provider,
    set_provider_key,
)
from godotter.runtime import fix_uid_paths, run_doctor
from godotter.runtime.validators import validate_managers, validate_nodepaths, validate_paths, validate_structure
from godotter.tasks.planpack import (
    PlanPack,
    PlanState,
    PlanTask,
    load_planpack,
    load_planstate,
    new_plan_id,
    new_task_id,
    plan_state_path,
    write_planpack,
    write_planstate,
)
from godotter.tasks.planning import (
    ScoutPromptRef,
    build_plan_prompt,
    normalize_plan_dependencies,
    validate_plan_tasks,
)
from godotter.tasks.scout import collect_changed_files, scout_workspace, write_task_run_baseline
from godotter.tasks.workpack import WorkPack, WorkPackFileRef, load_workpack, write_workpack
from godotter.tools import ToolRegistry, build_default_tools


def _ensure_utf8_stdio() -> None:
    # Avoid Windows cp936/gbk UnicodeEncodeError when LLM outputs emoji/symbols.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')  # type: ignore[attr-defined]
        except Exception:
            continue


_ensure_utf8_stdio()

app = typer.Typer(
    help='Godotter CLI - AI-assisted Godot development tool.',
    epilog='Use "godotter COMMAND --help" for more information on a command.',
)
provider_app = typer.Typer(help='Manage AI providers (add, list, switch, configure API keys).')
provider_key_app = typer.Typer(help='Manage API keys for AI providers.')
model_app = typer.Typer(help='Manage AI models (list available, set default).')
runtime_app = typer.Typer(help='Godot runtime operations (run, lint, diagnose, fix UID issues).')
project_app = typer.Typer(help='Manage Godot projects and scaffolding.')
task_app = typer.Typer(help='Prepare and run workpacks for agent tasks.')
plan_app = typer.Typer(help='Prepare and run multi-step plans (PlanPacks).')
scene_app = typer.Typer(help='Create scenes and paired scripts (tscn + gd).')

app.add_typer(provider_app, name='provider')
provider_app.add_typer(provider_key_app, name='key')
app.add_typer(model_app, name='model')
app.add_typer(runtime_app, name='runtime')
app.add_typer(project_app, name='project')
app.add_typer(task_app, name='task')
app.add_typer(plan_app, name='plan')
app.add_typer(scene_app, name='scene')


@app.callback()
def main() -> None:
    return None


@app.command('info', help='Display project information and configuration.')
def info_command() -> None:
    settings = get_settings()
    configure_logging(settings)
    logger = get_logger('godotter.cli')
    logger.info(
        'godotter_info',
        app_env=settings.app_env,
        workspace_root=str(settings.workspace_root),
        default_mode=settings.default_mode,
        default_brain=settings.default_brain,
        supported_providers=list(SUPPORTED_PROVIDERS),
    )
    typer.echo('Godotter scaffold is initialized.')


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
    settings = get_settings()
    root = (workspace or settings.workspace_root).resolve()
    constraints = [
        'Obey Godotter dev-mode docs under Docs/.',
        'Levels must have a root Managers node and a Managers/EventBus child.',
        'Prefer structured events via EventBus; avoid implicit get-from-group lookups outside Managers.',
        'Run `godotter runtime validate-structure`, `godotter runtime validate-managers`, and `godotter runtime validate-paths` after changes.',
    ]
    relevant: list[WorkPackFileRef] = [
        WorkPackFileRef(path='Docs/godotter_dev_mode_project_structure.md', reason='Dev-mode conventions', priority=10),
        WorkPackFileRef(path='Docs/godotter_template_project.md', reason='Template conventions', priority=20),
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
        verification=[
            'uv run godotter runtime validate-structure',
            'uv run godotter runtime validate-managers',
            'uv run godotter runtime validate-paths',
            'uv run godotter runtime lint --project .',
            'uv run godotter runtime test --project . --pattern "*_smoke.tscn;*_harness.tscn" --timeout 30',
        ],
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
    settings = get_settings()
    root = (workspace or settings.workspace_root).resolve()
    scout = scout_workspace(root, goal, max_files=max_files)

    constraints = [
        'Obey Godotter dev-mode docs under Docs/.',
        'Levels must have a root Managers node and a Managers/EventBus child.',
        'Prefer structured events via EventBus; avoid implicit get-from-group lookups outside Managers.',
        'Run `godotter runtime validate-structure`, `godotter runtime validate-managers`, and `godotter runtime validate-paths` after changes.',
    ]
    relevant: list[WorkPackFileRef] = [
        WorkPackFileRef(path='Docs/godotter_dev_mode_project_structure.md', reason='Dev-mode conventions', priority=10),
        WorkPackFileRef(path='Docs/godotter_template_project.md', reason='Template conventions', priority=20),
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
        verification=[
            'uv run godotter runtime validate-structure',
            'uv run godotter runtime validate-managers',
            'uv run godotter runtime validate-paths',
            'uv run godotter runtime lint --project .',
            'uv run godotter runtime test --project . --pattern "*_smoke.tscn;*_harness.tscn" --timeout 30',
        ],
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
    root = (workspace or base_settings.workspace_root).resolve()
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
    registry = ToolRegistry(build_default_tools())
    selected_brain = brain or settings.default_brain
    agent = Agent(
        brain=create_brain(settings, selected_brain),
        settings=settings,
        registry=registry,
        memory=memory,
        mode=normalized_mode,
        brain_name=selected_brain,
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
                '- If tool-calls are available, use apply_patch and other tools to make changes.',
                '- If tool-calls are not available, output a single unified diff patch (no commentary).',
                '- After changes, ensure verification commands pass.',
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

    attempts = max_attempts if normalized_mode == 'act' else 1
    for attempt in range(1, attempts + 1):
        attempt_prompt = list(prompt_lines)
        if failure_report:
            attempt_prompt.extend(
                [
                    '',
                    f'Previous attempt failed (attempt={attempt - 1}). Fix the failure and re-run verification.',
                    'Failure summary:',
                    failure_report,
                ]
            )

        agent_output = agent.handle_input('\n'.join(attempt_prompt))
        typer.echo(agent_output)
        if normalized_mode != 'act':
            return

        _maybe_apply_unified_diff(settings.workspace_root.resolve(), agent_output)

        try:
            _audit_task_run_changes(
                settings.workspace_root.resolve(),
                allow_no_changes=allow_no_changes,
                strict=strict_audit,
            )
        except typer.Exit as exc:
            _dump_task_debug(agent)
            failure_report = f'audit_failed={type(exc).__name__}'
            signature = hashlib.sha256(failure_report.encode('utf-8', errors='replace')).hexdigest()
            if stop_on_same_failure and signature == last_signature:
                same_failure_count += 1
            else:
                same_failure_count = 1
                last_signature = signature
            if stop_on_same_failure and same_failure_count >= same_failure_limit:
                raise
            if attempt >= attempts:
                raise
            continue

        ok, failure_report, signature = _run_task_verification_commands(settings.workspace_root.resolve(), pack.verification)
        if ok:
            return

        if stop_on_same_failure and signature == last_signature:
            same_failure_count += 1
        else:
            same_failure_count = 1
            last_signature = signature
        if stop_on_same_failure and same_failure_count >= same_failure_limit:
            typer.echo('task_run_retry_stop_reason=same_failure_repeated')
            raise typer.Exit(1)
        if attempt >= attempts:
            raise typer.Exit(1)


@task_app.command('list', help='List WorkPacks under .godotter/workpacks/.')
def task_list_command(
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
) -> None:
    settings = get_settings()
    root = (workspace or settings.workspace_root).resolve()
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
    settings = get_settings()
    root = (workspace or settings.workspace_root).resolve()
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
    root = (workspace or base_settings.workspace_root).resolve()
    settings = base_settings.model_copy(update={'workspace_root': root})

    configure_logging(settings)
    memory = Memory(settings.resolved_memory_path)
    registry = ToolRegistry(build_default_tools())
    selected_brain = brain or settings.default_brain
    agent = Agent(
        brain=create_brain(settings, selected_brain),
        settings=settings,
        registry=registry,
        memory=memory,
        mode='plan',
        brain_name=selected_brain,
    )
    # PlanPack generation must be a pure text/JSON response. Some providers may
    # otherwise emit tool-calls-only (empty text), which breaks JSON parsing.
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
        # Try to extract the first JSON object from a mixed response.
        start = raw_stripped.find('{')
        end = raw_stripped.rfind('}')
        if start != -1 and end != -1 and end > start:
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
    settings = get_settings()
    root = (workspace or settings.workspace_root).resolve()
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
    settings = get_settings()
    root = (workspace or settings.workspace_root).resolve()
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
    settings = get_settings()
    root = (workspace or settings.workspace_root).resolve()
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
    root = (workspace or base_settings.workspace_root).resolve()
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
        selected_brain = brain or settings.default_brain

        scout_refs: list[WorkPackFileRef] = []
        if not task.scope:
            scoped_scout = scout_workspace(root, task.goal or task.title, max_files=25)
            scout_refs = [
                WorkPackFileRef(path=ref.path, reason=ref.reason or 'scout', priority=15) for ref in scoped_scout.relevant_files[:15]
            ]

        verification_commands = _normalize_verification_commands(task.verification) or [
            'uv run godotter runtime validate-structure',
            'uv run godotter runtime validate-managers',
            'uv run godotter runtime validate-paths',
            'uv run godotter runtime lint --project .',
        ]

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
            state.task_artifacts[task.id] = {'error': f'{type(exc).__name__}: {exc}', 'workpack': wp_path.as_posix()}
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

@project_app.command('new', help='Create a new Godot project with a minimal runnable scaffold.')
def project_new_command(
    name: str = typer.Argument('.', help='Project name or path. Use "." for current directory.'),
    no_git: bool = typer.Option(False, '--no-git', help='Skip git initialization.'),
) -> None:
    try:
        result = scaffold_godot_project(name, no_git=no_git)
    except ValueError as exc:
        typer.echo(f'Error: {exc}')
        raise typer.Exit(1) from exc
    typer.echo(render_project_scaffold_summary(result, no_git=no_git))


@scene_app.command('new', help='Create a scene (.tscn) and paired script (.gd) together.')
def scene_new_command(
    path: str = typer.Argument(..., help='Scene path (res://... or workspace-relative), must end with .tscn.'),
    kind: str = typer.Option('level', '--kind', help='Scene kind: level, ui, prefab.'),
    no_script: bool = typer.Option(False, '--no-script', help='Do not generate a .gd script (scene only).'),
    script_path: str | None = typer.Option(
        None,
        '--script',
        help='Optional script path (res://... or workspace-relative), must end with .gd. Defaults to same stem next to the scene.',
    ),
    layout: str = typer.Option(
        'split',
        '--layout',
        help='Default script layout when --script is not provided: split or colocated.',
    ),
    root_type: str | None = typer.Option(None, '--root-type', help='Override root node type (default depends on --kind).'),
    root_name: str | None = typer.Option(None, '--root-name', help='Override root node name (default inferred from filename).'),
    force: bool = typer.Option(False, '--force', help='Overwrite existing files if they already exist.'),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
) -> None:
    settings = get_settings()
    root = (workspace or settings.workspace_root).resolve()
    try:
        if no_script:
            if script_path:
                raise ValueError('--no-script cannot be used together with --script')
            result_only = scaffold_scene_only(
                workspace_root=root,
                kind=kind,
                scene_path=path,
                root_type=root_type,
                root_name=root_name,
                force=force,
            )
            typer.echo(f'scene={result_only.scene_path.relative_to(root).as_posix()}')
            typer.echo('script=(none)')
            typer.echo(f'uid={result_only.uid}')
            return
        result = scaffold_scene_with_script(
            workspace_root=root,
            kind=kind,
            scene_path=path,
            script_path=script_path,
            root_type=root_type,
            root_name=root_name,
            layout=layout,
            force=force,
        )
    except ValueError as exc:
        typer.echo(f'Error: {exc}')
        raise typer.Exit(1) from exc
    typer.echo(f'scene={result.scene_path.relative_to(root).as_posix()}')
    typer.echo(f'script={result.script_path.relative_to(root).as_posix()}')
    typer.echo(f'uid={result.uid}')


@scene_app.command('create', help='Create a scene (.tscn) only (no script).')
def scene_create_command(
    path: str = typer.Argument(..., help='Scene path (res://... or workspace-relative), must end with .tscn.'),
    kind: str = typer.Option('level', '--kind', help='Scene kind: level, ui, prefab.'),
    root_type: str | None = typer.Option(None, '--root-type', help='Override root node type (default depends on --kind).'),
    root_name: str | None = typer.Option(None, '--root-name', help='Override root node name (default inferred from filename).'),
    force: bool = typer.Option(False, '--force', help='Overwrite existing file if it already exists.'),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
) -> None:
    settings = get_settings()
    root = (workspace or settings.workspace_root).resolve()
    try:
        result = scaffold_scene_only(
            workspace_root=root,
            kind=kind,
            scene_path=path,
            root_type=root_type,
            root_name=root_name,
            force=force,
        )
    except ValueError as exc:
        typer.echo(f'Error: {exc}')
        raise typer.Exit(1) from exc
    typer.echo(f'scene={result.scene_path.relative_to(root).as_posix()}')
    typer.echo('script=(none)')
    typer.echo(f'uid={result.uid}')


@app.command('new', hidden=True)
def new_command(
    name: str = typer.Argument('.', help='Project name or path. Use "." for current directory.'),
    no_git: bool = typer.Option(False, '--no-git', help='Skip git initialization.'),
) -> None:
    typer.echo('Warning: `godotter new` is deprecated. Use `godotter project new` instead.')
    project_new_command(name=name, no_git=no_git)


@app.command('chat', help='Start an AI chat session with the specified message.')
def chat_command(
    message: str = typer.Argument(..., help='The message to send to the AI agent.'),
    mode: str = typer.Option(
        'plan',
        '--mode',
        help='Agent execution mode: plan or act. Deprecated aliases: review->plan, code/debug->act.',
    ),
    brain: str | None = typer.Option(None, '--brain', help='Override the default AI brain/provider for this session.'),
) -> None:
    settings = get_settings()
    normalized_mode, mode_note = _normalize_cli_mode(mode)
    configure_logging(settings)
    memory = Memory(settings.resolved_memory_path)
    registry = ToolRegistry(build_default_tools())
    selected_brain = brain or settings.default_brain
    agent = Agent(
        brain=create_brain(settings, selected_brain),
        settings=settings,
        registry=registry,
        memory=memory,
        mode=normalized_mode,
        brain_name=selected_brain,
    )
    if mode_note:
        typer.echo(mode_note)
    typer.echo(agent.handle_input(message))


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
    touches_levels = any(path.startswith('game/levels/') for path in changed_paths)

    if touches_feature_or_system and not touches_tests:
        if strict:
            typer.echo('task_run_audit_error=missing_tests_for_game_logic_changes')
            raise typer.Exit(1)
        typer.echo('task_run_audit_warn=missing_tests_for_game_logic_changes')

    if touches_feature_or_system and not touches_levels:
        if strict:
            typer.echo('task_run_audit_error=missing_level_updates_for_game_logic_changes')
            raise typer.Exit(1)
        typer.echo('task_run_audit_warn=missing_level_updates_for_game_logic_changes')


def _run_task_verification_commands(workspace_root: Path, commands: list[str]) -> tuple[bool, str | None, str]:
    """
    Returns (ok, failure_report, signature). Prints per-command output.
    signature is a stable hash of the first failing command's results so callers
    can detect repeated failures across retries.
    """
    for command in commands:
        command = _rewrite_verification_command(workspace_root, command)
        typer.echo(f'task_run_verify command={command}')
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


def _rewrite_verification_command(workspace_root: Path, command: str) -> str:
    raw = (command or '').strip()
    if not raw:
        return raw

    # Some models append trailing markers like "passes"/"succeeds" as plain words.
    for suffix in (' passes', ' succeeds', ' success', ' ok'):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)].rstrip()

    if raw == 'uv run godotter runtime lint --project . (project-wide)':
        return 'uv run godotter runtime lint --project .'
    if raw == 'uv run godotter runtime lint --project . all':
        return 'uv run godotter runtime lint --project .'
    if raw == 'uv run godotter runtime lint --project . warnings':
        return 'uv run godotter runtime lint --project .'
    if raw == 'uv run godotter runtime lint --project . clean':
        return 'uv run godotter runtime lint --project .'

    # Fix common case: planner uses bare filename for runtime lint.
    prefix = 'uv run godotter runtime lint --project . '
    if raw.startswith(prefix):
        path = raw[len(prefix) :].strip().strip('"').strip("'")
        if path.startswith('--path '):
            cleaned = path[len('--path ') :].strip().strip('"').strip("'")
            if cleaned:
                return f'{prefix}{cleaned}'
            return 'uv run godotter runtime lint --project .'
        if path and ("/" not in path and "\\" not in path):
            candidate = workspace_root / path
            if not candidate.exists():
                matches = list(workspace_root.rglob(path))
                matches = [m for m in matches if m.is_file()]
                if len(matches) == 1:
                    rel = matches[0].relative_to(workspace_root).as_posix()
                    return f'{prefix}{rel}'
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
            # Accept a few common shapes produced by models/tool logs.
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
                # `runtime lint` only accepts a script path; if a directory is given, lint the whole project.
                if path.endswith('/') or path.endswith('\\'):
                    normalized.append('uv run godotter runtime lint --project .')
                else:
                    normalized.append(f'uv run godotter runtime lint --project . {path}')
            else:
                normalized.append('uv run godotter runtime lint --project .')
            continue

        if 'headless_run' in lower or 'headless run' in lower:
            scene = None
            if 'target=' in raw:
                scene = raw.split('target=', 1)[1].strip().split()[0].strip('"').strip("'")
            else:
                # try to find a res:// token
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

    registry = ToolRegistry(build_default_tools())
    tool = registry.get('apply_patch')
    if tool is None:
        typer.echo('task_run_patch_error=apply_patch_tool_missing')
        raise typer.Exit(1)

    context = ToolContext(
        settings=get_settings(),
        workspace_root=workspace_root.resolve(),
        memory=None,
    )
    try:
        result = tool.execute(context, patch=text)
    except Exception as exc:
        typer.echo(f'task_run_patch_error={type(exc).__name__}: {exc}')
        raise typer.Exit(1) from exc
    typer.echo(f'task_run_patch_applied={result}')


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


@app.command('providers', help='List all configured AI providers and their current models.')
def providers_command() -> None:
    settings = get_settings()
    typer.echo('\n'.join(format_provider_rows(settings)))


@provider_app.command('list', help='List all available AI providers.')
def provider_list_command() -> None:
    settings = get_settings()
    typer.echo('\n'.join(format_provider_rows(settings)))


@provider_app.command('use', help='Set the default AI provider.')
def provider_use_command(
    name: str = typer.Argument(..., help='Provider name (e.g., moonshot, deepseek, siliconflow, alibaba).'),
) -> None:
    try:
        selected = set_default_provider(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f'default provider set to {selected}')


@provider_app.command('check', help='Validate API key and connectivity for a provider.')
def provider_check_command(
    provider: str | None = typer.Option(
        None, '--provider', help='Provider name (defaults to current default provider).'
    ),
    timeout: int = typer.Option(10, '--timeout', help='Timeout in seconds for the check request.'),
) -> None:
    settings = get_settings()
    selected = normalize_provider_name(provider or settings.default_brain)
    typer.echo(check_provider_connectivity(settings, selected, timeout=timeout))


@provider_key_app.command('show', help='Display the API key status for a provider.')
def provider_key_show_command(
    provider: str | None = typer.Option(None, '--provider', help='Provider name (defaults to current default provider).'),
) -> None:
    settings = get_settings()
    selected = normalize_provider_name(provider or settings.default_brain)
    typer.echo(format_provider_key_status(settings, selected))


@provider_key_app.command('set', help='Set or update the API key for a provider.')
def provider_key_set_command(
    value: str = typer.Argument(..., help='The API key value (will be masked in output).'),
    provider: str | None = typer.Option(None, '--provider', help='Provider name (defaults to current default provider).'),
) -> None:
    settings = get_settings()
    try:
        selected, masked = set_provider_key(provider or settings.default_brain, value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f'{selected} key updated: {masked}')


@model_app.command('list', help='List available models for a provider.')
def model_list_command(
    provider: str | None = typer.Option(None, '--provider', help='Provider name (defaults to current default provider).'),
) -> None:
    settings = get_settings()
    try:
        rows = fetch_model_rows(settings, provider or settings.default_brain)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo('\n'.join(rows))


@model_app.command('use', help='Set the default model for a provider.')
def model_use_command(
    name: str = typer.Argument(..., help='Model name (e.g., kimi-k2.5, deepseek-v4-pro).'),
    provider: str | None = typer.Option(None, '--provider', help='Provider name (defaults to current default provider).'),
) -> None:
    settings = get_settings()
    try:
        selected, model = set_model_for_provider(provider or settings.default_brain, name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f'{selected} model set to {model}')


@runtime_app.command('lint', help='Lint Godot scripts or the entire project.')
def runtime_lint_command(
    path: str | None = typer.Argument(None, help='Path to a specific script file (lints entire project if omitted).'),
    timeout: int = typer.Option(60, '--timeout', help='Timeout in seconds for the lint operation.'),
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
    fail_on_stderr: str = typer.Option(
        'SCRIPT ERROR:;Parse Error:;ERROR:',
        '--fail-on-stderr',
        help='Semicolon-separated substrings; if any appears in stderr, mark lint as failed.',
    ),
) -> None:
    settings = get_settings()
    runner = build_runner(settings, project=project)
    if path:
        result = runner.lint_script(path, timeout=timeout)
        target = path
    else:
        result = runner.lint_project(timeout=timeout)
        target = '(project)'
    typer.echo(format_runtime_result('script_lint', target, result))
    stderr_text = result.stderr or ''
    bad_markers = [m for m in (x.strip() for x in fail_on_stderr.split(';')) if m]
    marker_hit = any(m in stderr_text for m in bad_markers)
    if result.exit_code != 0 or result.timed_out or marker_hit:
        raise typer.Exit(1)


@runtime_app.command('run', help='Run the Godot project or a specific scene.')
def runtime_run_command(
    scene: str | None = typer.Option(None, '--scene', help='Scene file path to run (runs main scene if omitted).'),
    timeout: int = typer.Option(60, '--timeout', help='Timeout in seconds for the run operation (0 = no timeout).'),
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
    headless: bool = typer.Option(False, '--headless', help='Run with Godot --headless (recommended for CI/tests).'),
) -> None:
    settings = get_settings()
    runner = build_runner(settings, project=project)
    result = runner.run_project(timeout=timeout, scene=scene, headless=headless)
    typer.echo(format_runtime_result('headless_run', scene or '(project)', result))
    if result.exit_code != 0 or result.timed_out:
        raise typer.Exit(1)


@runtime_app.command('test', help='Run headless test scenes under tests/ (auto-discovers harness scenes).')
def runtime_test_command(
    scene: str | None = typer.Option(
        None,
        '--scene',
        help='Run a single test scene (res://... or workspace-relative path). Overrides --pattern.',
    ),
    pattern: str = typer.Option(
        '*_harness.tscn;*_smoke.tscn',
        '--pattern',
        help='Semicolon-separated glob patterns to match test scenes under tests/.',
    ),
    timeout: int = typer.Option(60, '--timeout', help='Timeout in seconds for each test scene.'),
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
    include_core_harness: bool = typer.Option(
        False,
        '--include-core-harness',
        help='Include tests/core/test_harness.tscn (normally skipped; often not auto-quit).',
    ),
    fail_on_stderr: str = typer.Option(
        'SCRIPT ERROR:;FAIL:',
        '--fail-on-stderr',
        help='Semicolon-separated substrings; if any appears in stderr, mark test as failed.',
    ),
) -> None:
    settings = get_settings()
    runner = build_runner(settings, project=project)
    tests_root = runner.workspace_root / 'tests'
    if not tests_root.exists():
        typer.echo(f'tests_root={tests_root.as_posix()}')
        typer.echo('count=0')
        return

    if scene:
        raw = scene.strip().strip('"').strip("'")
        if raw.startswith('res://'):
            scene_res = raw
        else:
            rel = raw.lstrip('/').lstrip('\\')
            scene_res = f"res://{rel.replace('\\\\', '/')}"
        result = runner.run_project(timeout=timeout, scene=scene_res, headless=True)
        typer.echo(format_runtime_result('headless_run', scene_res, result))
        stderr_text = result.stderr or ''
        bad_markers = [m for m in (x.strip() for x in fail_on_stderr.split(';')) if m]
        marker_hit = any(m in stderr_text for m in bad_markers)
        if result.exit_code != 0 or result.timed_out or marker_hit:
            raise typer.Exit(1)
        return

    patterns = [p.strip().strip('"').strip("'") for p in pattern.split(';') if p.strip()]
    scenes: list[Path] = []
    for pat in patterns:
        scenes.extend(tests_root.rglob(pat))

    # Stable order, unique.
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in sorted(scenes, key=lambda x: x.as_posix()):
        if p in seen or not p.is_file():
            continue
        if not include_core_harness and p.name == 'test_harness.tscn' and 'tests/core/' in p.as_posix().replace('\\', '/'):
            continue
        seen.add(p)
        unique.append(p)

    typer.echo(f'tests_root={tests_root.as_posix()}')
    typer.echo(f'count={len(unique)}')
    if not unique:
        return

    failures: list[str] = []
    bad_markers = [m for m in (x.strip() for x in fail_on_stderr.split(';')) if m]
    for scene_path in unique:
        rel = scene_path.relative_to(runner.workspace_root).as_posix()
        scene_res = f'res://{rel}'
        result = runner.run_project(timeout=timeout, scene=scene_res, headless=True)
        typer.echo(format_runtime_result('headless_run', scene_res, result))
        stderr_text = result.stderr or ''
        marker_hit = any(m in stderr_text for m in bad_markers)
        if result.exit_code != 0 or result.timed_out or marker_hit:
            failures.append(scene_res)

    if failures:
        typer.echo(f'failures={len(failures)}')
        for s in failures[:25]:
            typer.echo(f'failure={s}')
        raise typer.Exit(1)


@runtime_app.command('doctor', help='Diagnose Godot environment and project health.')
def runtime_doctor_command(
    timeout: int = typer.Option(15, '--timeout', help='Timeout in seconds for the diagnosis.'),
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
) -> None:
    settings = get_settings()
    target = resolve_runtime_target(settings, project=project)
    report = run_doctor(target.workspace_root, target.godot_path, timeout=timeout)
    typer.echo(format_doctor_report(report))


@runtime_app.command('uid-fix', help='Fix UID references in Godot project files.')
def runtime_uid_fix_command(
    dry_run: bool = typer.Option(True, '--dry-run/--write', help='Preview changes (default) or actually write fixes.'),
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
) -> None:
    settings = get_settings()
    target = resolve_runtime_target(settings, project=project)
    result = fix_uid_paths(target.workspace_root, dry_run=dry_run)
    typer.echo(format_uid_fix_result(result, dry_run=dry_run, workspace_root=target.workspace_root))


@runtime_app.command('validate-structure', help='Validate project directory structure for the Godotter dev mode.')
def runtime_validate_structure_command(
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
) -> None:
    settings = get_settings()
    root = (workspace or settings.workspace_root).resolve()
    report = validate_structure(root)
    typer.echo(f'workspace_root={root.as_posix()}')
    typer.echo(f'ok={str(report.ok).lower()}')
    for issue in report.issues:
        typer.echo(f'issue code={issue.code} message={issue.message}')
    if not report.ok:
        raise typer.Exit(1)


@runtime_app.command('validate-managers', help='Validate Managers/EventBus conventions for level scenes.')
def runtime_validate_managers_command(
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    levels: Path | None = typer.Option(
        None,
        '--levels',
        help='Levels root directory (defaults to workspace/game/levels).',
    ),
) -> None:
    settings = get_settings()
    root = (workspace or settings.workspace_root).resolve()
    if levels:
        levels_root = levels.resolve() if levels.is_absolute() else (root / levels).resolve()
    else:
        levels_root = None
    report = validate_managers(root, levels_root=levels_root)
    typer.echo(f'workspace_root={root.as_posix()}')
    typer.echo(f'levels_root={(levels_root or (root / "game" / "levels")).as_posix()}')
    typer.echo(f'ok={str(report.ok).lower()}')
    for issue in report.issues:
        typer.echo(f'issue code={issue.code} message={issue.message}')
    if not report.ok:
        raise typer.Exit(1)


@runtime_app.command('validate-nodepaths', help='Validate exported NodePath properties in level scenes.')
def runtime_validate_nodepaths_command(
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    scenes: Path | None = typer.Option(
        None,
        '--scenes',
        help='Scenes root directory (defaults to workspace/game/levels).',
    ),
) -> None:
    settings = get_settings()
    root = (workspace or settings.workspace_root).resolve()
    if scenes:
        scenes_root = scenes.resolve() if scenes.is_absolute() else (root / scenes).resolve()
    else:
        scenes_root = None
    report = validate_nodepaths(root, scenes_root=scenes_root)
    typer.echo(f'workspace_root={root.as_posix()}')
    typer.echo(f'scenes_root={(scenes_root or (root / "game" / "levels")).as_posix()}')
    typer.echo(f'ok={str(report.ok).lower()}')
    for issue in report.issues:
        typer.echo(f'issue code={issue.code} message={issue.message}')
    if not report.ok:
        raise typer.Exit(1)


@runtime_app.command('validate-paths', help='Validate scene NodePaths and res:// resource paths.')
def runtime_validate_paths_command(
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    fix: bool = typer.Option(
        False,
        '--fix',
        help='Rewrite unresolved paths when exactly one safe suggestion is available.',
    ),
) -> None:
    settings = get_settings()
    root = (workspace or settings.workspace_root).resolve()
    report = validate_paths(root, fix=fix)
    typer.echo(f'workspace_root={root.as_posix()}')
    typer.echo(f'fix={str(fix).lower()}')
    typer.echo(f'ok={str(report.ok).lower()}')
    for issue in report.issues:
        typer.echo(f'issue code={issue.code} message={issue.message}')
    if not report.ok:
        raise typer.Exit(1)
