from __future__ import annotations

import typer

from godotter.agent import Agent
from godotter.config import get_settings
from godotter.context import Memory
from godotter.llm import SUPPORTED_PROVIDERS, create_brain
from godotter.logging import configure_logging, get_logger
from godotter.operations import (
    build_runner,
    fetch_model_rows,
    format_doctor_report,
    format_provider_key_status,
    format_provider_rows,
    format_runtime_result,
    format_uid_fix_result,
    normalize_provider_name,
    resolve_runtime_target,
    set_default_provider,
    set_model_for_provider,
    set_provider_key,
)
from godotter.runtime import fix_uid_paths, run_doctor
from godotter.tools import ToolRegistry, build_default_tools

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
    return None


@app.command('info')
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


@app.command('chat')
def chat_command(
    message: str,
    mode: str = typer.Option('plan', '--mode'),
    brain: str | None = typer.Option(None, '--brain'),
) -> None:
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
    settings = get_settings()
    typer.echo('\n'.join(format_provider_rows(settings)))


@provider_app.command('list')
def provider_list_command() -> None:
    settings = get_settings()
    typer.echo('\n'.join(format_provider_rows(settings)))


@provider_app.command('use')
def provider_use_command(name: str) -> None:
    try:
        selected = set_default_provider(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f'default provider set to {selected}')


@provider_key_app.command('show')
def provider_key_show_command(provider: str | None = typer.Option(None, '--provider')) -> None:
    settings = get_settings()
    selected = normalize_provider_name(provider or settings.default_brain)
    typer.echo(format_provider_key_status(settings, selected))


@provider_key_app.command('set')
def provider_key_set_command(value: str, provider: str | None = typer.Option(None, '--provider')) -> None:
    settings = get_settings()
    try:
        selected, masked = set_provider_key(provider or settings.default_brain, value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f'{selected} key updated: {masked}')


@model_app.command('list')
def model_list_command(provider: str | None = typer.Option(None, '--provider')) -> None:
    settings = get_settings()
    try:
        rows = fetch_model_rows(settings, provider or settings.default_brain)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo('\n'.join(rows))


@model_app.command('use')
def model_use_command(name: str, provider: str | None = typer.Option(None, '--provider')) -> None:
    settings = get_settings()
    try:
        selected, model = set_model_for_provider(provider or settings.default_brain, name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f'{selected} model set to {model}')


@runtime_app.command('lint')
def runtime_lint_command(
    path: str | None = typer.Argument(None),
    timeout: int = typer.Option(60, '--timeout'),
    project: str | None = typer.Option(None, '--project'),
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


@runtime_app.command('run')
def runtime_run_command(
    scene: str | None = typer.Option(None, '--scene'),
    timeout: int = typer.Option(60, '--timeout'),
    project: str | None = typer.Option(None, '--project'),
) -> None:
    settings = get_settings()
    runner = build_runner(settings, project=project)
    result = runner.run_project(timeout=timeout, scene=scene)
    typer.echo(format_runtime_result('headless_run', scene or '(project)', result))


@runtime_app.command('doctor')
def runtime_doctor_command(
    timeout: int = typer.Option(15, '--timeout'),
    project: str | None = typer.Option(None, '--project'),
) -> None:
    settings = get_settings()
    target = resolve_runtime_target(settings, project=project)
    report = run_doctor(target.workspace_root, target.godot_path, timeout=timeout)
    typer.echo(format_doctor_report(report))


@runtime_app.command('uid-fix')
def runtime_uid_fix_command(
    dry_run: bool = typer.Option(True, '--dry-run/--write'),
    project: str | None = typer.Option(None, '--project'),
) -> None:
    settings = get_settings()
    target = resolve_runtime_target(settings, project=project)
    result = fix_uid_paths(target.workspace_root, dry_run=dry_run)
    typer.echo(format_uid_fix_result(result, dry_run=dry_run, workspace_root=target.workspace_root))
