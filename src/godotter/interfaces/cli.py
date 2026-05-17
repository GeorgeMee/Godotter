from __future__ import annotations

from pathlib import Path

import typer

from godotter.agent import Agent
from godotter.config import get_settings
from godotter.context import Memory
from godotter.llm import SUPPORTED_PROVIDERS, create_brain
from godotter.llm.catalog import (
    current_key_for_provider,
    current_model_for_provider,
    key_env_name,
    list_models,
    model_env_name,
)
from godotter.logging import configure_logging, get_logger
from godotter.runtime import GodotRunner, fix_uid_paths, run_doctor
from godotter.tools import ToolRegistry, build_default_tools
from godotter.utils.envfile import EnvFile

app = typer.Typer(help='Godotter CLI.')
provider_app = typer.Typer(help='Provider management commands.')
provider_key_app = typer.Typer(help='Provider API key commands.')
model_app = typer.Typer(help='Model management commands.')
runtime_app = typer.Typer(help='Godot runtime commands.')
app.add_typer(provider_app, name='provider')
provider_app.add_typer(provider_key_app, name='key')
app.add_typer(model_app, name='model')
app.add_typer(runtime_app, name='runtime')


@app.callback()
def main() -> None:
    """Root command group for Godotter."""
    return None


@app.command('info')
def info_command() -> None:
    """Show the current scaffold status."""
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


@app.command('chat')
def chat_command(
    message: str,
    mode: str = typer.Option('plan', '--mode'),
    brain: str | None = typer.Option(None, '--brain'),
) -> None:
    """Run the agent loop for local testing."""
    settings = get_settings()
    configure_logging(settings)
    memory = Memory(settings.resolved_memory_path)
    registry = ToolRegistry(build_default_tools())
    selected_brain = brain or settings.default_brain
    agent = Agent(
        brain=create_brain(settings, selected_brain),
        settings=settings,
        registry=registry,
        memory=memory,
        mode=mode,
        brain_name=selected_brain,
    )
    typer.echo(agent.handle_input(message))


@app.command('providers')
def providers_command() -> None:
    """Show configured provider names for the China-region setup."""
    settings = get_settings()
    rows = []
    for provider in SUPPORTED_PROVIDERS:
        marker = '*' if provider == settings.default_brain else ' '
        model = current_model_for_provider(settings, provider)
        rows.append(f'{marker} {provider}: model={model}')
    typer.echo('\n'.join(rows))


@provider_app.command('list')
def provider_list_command() -> None:
    settings = get_settings()
    rows = []
    for provider in SUPPORTED_PROVIDERS:
        marker = '*' if provider == settings.default_brain else ' '
        model = current_model_for_provider(settings, provider)
        rows.append(f'{marker} {provider}: model={model}')
    typer.echo('\n'.join(rows))


@provider_app.command('use')
def provider_use_command(name: str) -> None:
    normalized = name.strip().lower()
    if normalized not in SUPPORTED_PROVIDERS:
        raise typer.BadParameter(f'Unsupported provider: {name}')
    envfile = EnvFile(Path('.env'))
    envfile.set('GODOTTER_DEFAULT_BRAIN', normalized)
    typer.echo(f'default provider set to {normalized}')


@provider_key_app.command('show')
def provider_key_show_command(provider: str | None = typer.Option(None, '--provider')) -> None:
    settings = get_settings()
    selected = (provider or settings.default_brain).strip().lower()
    if selected == 'stub':
        typer.echo('provider=stub\nkey=not-required')
        return
    key = current_key_for_provider(settings, selected)
    masked = _mask_secret(key)
    status = 'configured' if key else 'missing'
    typer.echo(f'provider={selected}\nstatus={status}\nkey={masked}')


@provider_key_app.command('set')
def provider_key_set_command(value: str, provider: str | None = typer.Option(None, '--provider')) -> None:
    settings = get_settings()
    selected = (provider or settings.default_brain).strip().lower()
    env_name = key_env_name(selected)
    envfile = EnvFile(Path('.env'))
    envfile.set(env_name, value)
    typer.echo(f'{selected} key updated: {_mask_secret(value)}')


@model_app.command('list')
def model_list_command(provider: str | None = typer.Option(None, '--provider')) -> None:
    settings = get_settings()
    selected = (provider or settings.default_brain).strip().lower()
    models = list_models(settings, selected)
    current = current_model_for_provider(settings, selected)
    rows = [f'provider={selected}']
    for model in models:
        marker = '*' if model == current else ' '
        rows.append(f'{marker} {model}')
    typer.echo('\n'.join(rows))


@model_app.command('use')
def model_use_command(name: str, provider: str | None = typer.Option(None, '--provider')) -> None:
    settings = get_settings()
    selected = (provider or settings.default_brain).strip().lower()
    env_name = model_env_name(selected)
    envfile = EnvFile(Path('.env'))
    envfile.set(env_name, name)
    typer.echo(f'{selected} model set to {name}')


@runtime_app.command('lint')
def runtime_lint_command(
    path: str | None = typer.Argument(None),
    timeout: int = typer.Option(60, '--timeout'),
) -> None:
    settings = get_settings()
    runner = _build_runner(settings)
    if path:
        result = runner.lint_script(path, timeout=timeout)
        target = path
    else:
        result = runner.lint_project(timeout=timeout)
        target = '(project)'
    typer.echo(_format_runtime_result('script_lint', target, result))


@runtime_app.command('run')
def runtime_run_command(
    scene: str | None = typer.Option(None, '--scene'),
    timeout: int = typer.Option(60, '--timeout'),
) -> None:
    settings = get_settings()
    runner = _build_runner(settings)
    result = runner.run_project(timeout=timeout, scene=scene)
    typer.echo(_format_runtime_result('headless_run', scene or '(project)', result))


@runtime_app.command('doctor')
def runtime_doctor_command(
    timeout: int = typer.Option(15, '--timeout'),
) -> None:
    settings = get_settings()
    report = run_doctor(settings.workspace_root, settings.godot_path, timeout=timeout)
    typer.echo(_format_doctor_report(report))


@runtime_app.command('uid-fix')
def runtime_uid_fix_command(
    dry_run: bool = typer.Option(True, '--dry-run/--write'),
) -> None:
    settings = get_settings()
    result = fix_uid_paths(settings.workspace_root, dry_run=dry_run)
    typer.echo(_format_uid_fix_result(result, dry_run=dry_run, workspace_root=settings.workspace_root))


def _build_runner(settings):
    if not settings.godot_path:
        raise typer.BadParameter('GODOT_PATH is not configured')
    return GodotRunner(settings.godot_path, settings.workspace_root)


def _format_runtime_result(command: str, target: str, result) -> str:
    stdout = result.stdout.strip() or '(empty)'
    stderr = result.stderr.strip() or '(empty)'
    lines = [
        f'command={command}',
        f'target={target}',
        f'exit_code={result.exit_code}',
        f'timed_out={str(result.timed_out).lower()}',
        f'duration_ms={result.duration_ms}',
        f'stdout={stdout}',
        f'stderr={stderr}',
    ]
    return '\n'.join(lines)


def _format_doctor_report(report) -> str:
    lines = [
        f'workspace_root={report.workspace_root}',
        f'project_exists={str(report.project_exists).lower()}',
        f'project_name={report.project_name or "(none)"}',
        f'main_scene={report.main_scene or "(none)"}',
        f'script_count={report.script_count}',
        f'scene_count={report.scene_count}',
        f'godot_configured={str(report.godot_configured).lower()}',
        f'godot_runnable={str(report.godot_runnable).lower()}',
        f'godot_version={report.godot_version or "(none)"}',
        f'godot_error={report.godot_error or "(none)"}',
    ]
    return '\n'.join(lines)


def _format_uid_fix_result(result, *, dry_run: bool, workspace_root) -> str:
    lines = [
        f'dry_run={str(dry_run).lower()}',
        f'uid_entries={result.uid_entries}',
        f'scanned_files={result.scanned_files}',
        f'updated_files={result.updated_files}',
        f'changes={len(result.changes)}',
    ]
    for change in result.changes:
        relative = change.file_path.relative_to(workspace_root).as_posix()
        lines.append(
            f'change file={relative} uid={change.uid} old_path={change.old_path} new_path={change.new_path}'
        )
    return '\n'.join(lines)


def _mask_secret(value: str | None) -> str:
    if not value:
        return '(not set)'
    if len(value) <= 8:
        return '*' * len(value)
    return f'{value[:4]}...{value[-4:]}'
