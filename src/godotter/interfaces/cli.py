from __future__ import annotations

from datetime import datetime
from pathlib import Path
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
    scaffold_godot_project,
    set_default_provider,
    set_model_for_provider,
    set_provider_key,
)
from godotter.runtime import fix_uid_paths, run_doctor
from godotter.runtime.validators import validate_managers, validate_structure
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

app.add_typer(provider_app, name='provider')
provider_app.add_typer(provider_key_app, name='key')
app.add_typer(model_app, name='model')
app.add_typer(runtime_app, name='runtime')
app.add_typer(project_app, name='project')
app.add_typer(task_app, name='task')
app.add_typer(plan_app, name='plan')


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
        'Run `godotter runtime validate-structure` and `godotter runtime validate-managers` after changes.',
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
            'uv run pytest -q',
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
        'Run `godotter runtime validate-structure` and `godotter runtime validate-managers` after changes.',
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
            'uv run pytest -q',
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

    agent_output = agent.handle_input('\n'.join(prompt_lines))
    typer.echo(agent_output)
    if normalized_mode == 'act':
        _maybe_apply_unified_diff(settings.workspace_root.resolve(), agent_output)
        try:
            _audit_task_run_changes(
                settings.workspace_root.resolve(),
                allow_no_changes=allow_no_changes,
                strict=strict_audit,
            )
        except typer.Exit:
            _dump_task_debug(agent)
            raise
        _run_task_verification_commands(settings.workspace_root.resolve(), pack.verification)


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

    scout = scout_workspace(root, goal, max_files=40)
    constraints = [
        'Split work into small, independently verifiable tasks.',
        'Each task must declare scope and verification commands.',
        'Prefer changing one system/feature per task.',
        'If task changes game/features or game/systems, include tests changes in same task.',
    ]
    prompt = '\n'.join(
        [
            'Create a multi-step implementation plan as JSON.',
            'Output must be a JSON object with keys: tasks (array).',
            'Each task must have: title, goal, scope (array of path prefixes), acceptance (array), verification (array), depends_on (array).',
            'Keep tasks small (5-10 tasks).',
            '',
            f'goal={goal}',
            '',
            'Relevant files (scout):',
            *[f'- {ref.path} {ref.reason}'.rstrip() for ref in scout.relevant_files[:20]],
        ]
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

    task_ids = {t.id for t in tasks}
    title_to_id = {}
    for t in tasks:
        key = t.title.strip().lower()
        if key and key not in title_to_id:
            title_to_id[key] = t.id

    # Normalize depends_on: allow planner to reference either ids or titles.
    for t in tasks:
        normalized: list[str] = []
        for dep in t.depends_on:
            dep_norm = dep.strip()
            if not dep_norm:
                continue
            if dep_norm in task_ids:
                normalized.append(dep_norm)
                continue
            mapped = title_to_id.get(dep_norm.lower())
            if mapped:
                normalized.append(mapped)
                continue
            normalized.append(dep_norm)
        t.depends_on = normalized

    missing_deps = sorted({dep for t in tasks for dep in t.depends_on if dep not in task_ids})
    if missing_deps:
        raise typer.BadParameter(f'Planner returned unknown depends_on ids: {missing_deps}')

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


def _run_task_verification_commands(workspace_root: Path, commands: list[str]) -> None:
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
            raise typer.Exit(1) from exc

        out_b = completed.stdout or b''
        err_b = completed.stderr or b''
        stdout = out_b.decode('utf-8', errors='replace').strip() or '(empty)'
        stderr = err_b.decode('utf-8', errors='replace').strip() or '(empty)'
        typer.echo(f'task_run_verify exit_code={completed.returncode} timed_out=false')
        typer.echo(f'task_run_verify_stdout={stdout}')
        typer.echo(f'task_run_verify_stderr={stderr}')
        if completed.returncode != 0:
            raise typer.Exit(1)


def _rewrite_verification_command(workspace_root: Path, command: str) -> str:
    raw = (command or '').strip()
    if not raw:
        return raw

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
    if result.exit_code != 0:
        raise typer.Exit(1)


@runtime_app.command('run', help='Run the Godot project or a specific scene.')
def runtime_run_command(
    scene: str | None = typer.Option(None, '--scene', help='Scene file path to run (runs main scene if omitted).'),
    timeout: int = typer.Option(60, '--timeout', help='Timeout in seconds for the run operation.'),
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
