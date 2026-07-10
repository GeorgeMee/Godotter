from __future__ import annotations

import typer

from godotter.config import get_settings
from godotter.llm.catalog import list_models
from godotter.services.llm.providers import (
    check_provider_connectivity,
    fetch_model_rows,
    format_provider_key_status,
    format_provider_rows,
    normalize_provider_name,
    set_default_provider,
    set_model_for_provider,
    set_provider_key,
)


provider_app = typer.Typer(help='Configure LLM providers and API keys.')
provider_key_app = typer.Typer(help='API key actions for providers.')
model_app = typer.Typer(help='Inspect and set provider models.')
provider_app.add_typer(provider_key_app, name='key')


@provider_app.command('list', help='Show configured providers and their current models.')
def provider_list_command() -> None:
    settings = get_settings()
    typer.echo('\n'.join(format_provider_rows(settings)))


@provider_app.command('use', help='Set the active provider, optionally scoped to chat/plan/act.')
def provider_use_command(
    name: str = typer.Argument(..., help='Provider name (e.g., moonshot, deepseek, siliconflow, alibaba).'),
    task: str | None = typer.Option(
        None,
        '--task',
        help='Scope to chat, plan, or act. Omitting sets the global default.',
    ),
) -> None:
    try:
        selected = set_default_provider(name, task=task)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    label = f'{task} ' if task else ''
    typer.echo(f'default {label}provider set to {selected}')


@provider_app.command('check', help='Check connectivity and API key status for a provider.')
def provider_check_command(
    provider: str | None = typer.Option(
        None, '--provider', help='Provider name (defaults to current default provider).'
    ),
    task: str | None = typer.Option(
        None,
        '--task',
        help='Check the provider configured for chat, plan, or act.',
    ),
    timeout: int = typer.Option(10, '--timeout', help='Timeout in seconds.'),
) -> None:
    settings = get_settings()
    if task:
        selected = {'chat': settings.resolved_chat_brain, 'plan': settings.resolved_plan_brain, 'act': settings.resolved_act_brain}.get(
            task
        )
        if selected is None:
            raise typer.BadParameter(f'Invalid task: {task}. Use chat, plan, or act.')
    else:
        selected = normalize_provider_name(provider or settings.default_brain)
    typer.echo(check_provider_connectivity(settings, selected, timeout=timeout))


@provider_key_app.command('show', help='Show the API key status for a provider.')
def provider_key_show_command(
    provider: str | None = typer.Option(None, '--provider', help='Provider to inspect. Defaults to the active provider.'),
) -> None:
    settings = get_settings()
    selected = normalize_provider_name(provider or settings.default_brain)
    typer.echo(format_provider_key_status(settings, selected))


@provider_key_app.command('set', help='Set or update a provider API key.')
def provider_key_set_command(
    value: str = typer.Argument(..., help='API key value.'),
    provider: str | None = typer.Option(None, '--provider', help='Provider to update. Defaults to the active provider.'),
) -> None:
    settings = get_settings()
    try:
        selected, masked = set_provider_key(provider or settings.default_brain, value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f'{selected} key updated: {masked}')


@model_app.command('list', help='List available models for the selected provider.')
def model_list_command(
    provider: str | None = typer.Option(None, '--provider', help='Provider to inspect. Defaults to the active provider.'),
) -> None:
    settings = get_settings()
    try:
        rows = fetch_model_rows(settings, provider or settings.default_brain)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo('\n'.join(rows))


@model_app.command('use', help='Set the active model after validating it exists.')
def model_use_command(
    name: str = typer.Argument(..., help='Model name.'),
    provider: str | None = typer.Option(None, '--provider', help='Provider to update. Defaults to the active provider.'),
) -> None:
    settings = get_settings()
    selected_provider = normalize_provider_name(provider or settings.default_brain)
    available_models = list_models(settings, selected_provider)
    if name not in available_models:
        raise typer.BadParameter(
            f'Model {name!r} is not available for provider {selected_provider}. '
            f'Use `gdt model list --provider {selected_provider}` to inspect available models.'
        )
    try:
        selected, model = set_model_for_provider(selected_provider, name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f'{selected} model set to {model}')
