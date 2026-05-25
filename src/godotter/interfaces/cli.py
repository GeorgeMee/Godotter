from __future__ import annotations

from datetime import datetime
from pathlib import Path
import secrets
import subprocess

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
from godotter.tasks.scout import collect_changed_files, scout_workspace
from godotter.tasks.workpack import WorkPack, WorkPackFileRef, load_workpack, write_workpack
from godotter.tools import ToolRegistry, build_default_tools

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

app.add_typer(provider_app, name='provider')
provider_app.add_typer(provider_key_app, name='key')
app.add_typer(model_app, name='model')
app.add_typer(runtime_app, name='runtime')
app.add_typer(project_app, name='project')
app.add_typer(task_app, name='task')


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
    agent_output = agent.handle_input('\n'.join(prompt_lines))
    typer.echo(agent_output)
    if normalized_mode == 'act':
        _maybe_apply_unified_diff(settings.workspace_root.resolve(), agent_output)
        _audit_task_run_changes(settings.workspace_root.resolve())
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


def _audit_task_run_changes(workspace_root: Path) -> None:
    changed = [ref for ref in collect_changed_files(workspace_root) if not ref.path.startswith('.godotter/')]
    typer.echo(f'task_run_audit changed_files={len(changed)}')
    for ref in changed[:10]:
        typer.echo(f'task_run_change path={ref.path} reason={ref.reason}')

    if not changed:
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
        typer.echo('task_run_audit_error=missing_tests_for_game_logic_changes')
        raise typer.Exit(1)

    if touches_feature_or_system and not touches_levels:
        typer.echo('task_run_audit_error=missing_level_updates_for_game_logic_changes')
        raise typer.Exit(1)


def _run_task_verification_commands(workspace_root: Path, commands: list[str]) -> None:
    for command in commands:
        typer.echo(f'task_run_verify command={command}')
        try:
            completed = subprocess.run(
                command,
                cwd=workspace_root,
                capture_output=True,
                text=True,
                timeout=300,
                shell=True,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or '').strip() or '(empty)'
            stderr = (exc.stderr or '').strip() or '(empty)'
            typer.echo('task_run_verify exit_code=-1 timed_out=true')
            typer.echo(f'task_run_verify_stdout={stdout}')
            typer.echo(f'task_run_verify_stderr={stderr}')
            raise typer.Exit(1) from exc

        stdout = completed.stdout.strip() or '(empty)'
        stderr = completed.stderr.strip() or '(empty)'
        typer.echo(f'task_run_verify exit_code={completed.returncode} timed_out=false')
        typer.echo(f'task_run_verify_stdout={stdout}')
        typer.echo(f'task_run_verify_stderr={stderr}')
        if completed.returncode != 0:
            raise typer.Exit(1)


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


@runtime_app.command('run', help='Run the Godot project or a specific scene.')
def runtime_run_command(
    scene: str | None = typer.Option(None, '--scene', help='Scene file path to run (runs main scene if omitted).'),
    timeout: int = typer.Option(60, '--timeout', help='Timeout in seconds for the run operation.'),
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
) -> None:
    settings = get_settings()
    runner = build_runner(settings, project=project)
    result = runner.run_project(timeout=timeout, scene=scene)
    typer.echo(format_runtime_result('headless_run', scene or '(project)', result))


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
