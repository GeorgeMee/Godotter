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

# Main CLI app with rich help
doc = """\b
Godotter - AI-assisted development runtime for Godot projects.

Workflow-first CLI for managing Godot projects with AI assistance.
Supports multiple LLM providers and provides runtime integration.

Examples:
    godotter info                          # Show project info
    godotter providers                     # List available AI providers
    godotter provider use moonshot         # Switch to Moonshot AI
    godotter model list                    # List available models
    godotter chat "Hello"                  # Start AI chat
    godotter runtime doctor                # Check Godot environment
    godotter runtime run --scene main      # Run a scene
"""

app = typer.Typer(
    help='Godotter CLI - AI-assisted Godot development tool.',
    rich_help_panel='Main Commands',
    epilog='Use "godotter COMMAND --help" for more information on a command.',
)

provider_app = typer.Typer(
    help='Manage AI providers (add, list, switch, configure API keys).',
    rich_help_panel='Provider Commands',
)
provider_key_app = typer.Typer(
    help='Manage API keys for AI providers.',
    rich_help_panel='Provider Key Commands',
)
model_app = typer.Typer(
    help='Manage AI models (list available, set default).',
    rich_help_panel='Model Commands',
)
runtime_app = typer.Typer(
    help='Godot runtime operations (run, lint, diagnose, fix UID issues).',
    rich_help_panel='Runtime Commands',
)

app.add_typer(provider_app, name='provider')
provider_app.add_typer(provider_key_app, name='key')
app.add_typer(model_app, name='model')
app.add_typer(runtime_app, name='runtime')


@app.callback()
def main() -> None:
    """Godotter CLI entry point."""
    return None


@app.command(
    'info',
    help='Display project information and configuration.',
)
def info_command() -> None:
    """Show Godotter project info including workspace, environment, and supported providers."""
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


@app.command(
    'chat',
    help='Start an AI chat session with the specified message.',
)
def chat_command(
    message: str = typer.Argument(
        ...,
        help='The message to send to the AI agent.',
    ),
    mode: str = typer.Option(
        'plan',
        '--mode',
        help='Agent mode: plan (default), code, review, or debug.',
    ),
    brain: str | None = typer.Option(
        None,
        '--brain',
        help='Override the default AI brain/provider for this session.',
    ),
) -> None:
    """Send a message to the AI agent and get a response.
    
    Examples:
        godotter chat "How do I create a player controller?"
        godotter chat "Review this script" --mode review
        godotter chat "Debug the error" --brain deepseek
    """
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


@app.command(
    'providers',
    help='List all configured AI providers and their current models.',
)
def providers_command() -> None:
    """Display all available AI providers with their active models.
    
    Shows which provider is currently selected (marked with *).
    """
    settings = get_settings()
    typer.echo('\n'.join(format_provider_rows(settings)))


@provider_app.command(
    'list',
    help='List all available AI providers.',
)
def provider_list_command() -> None:
    """Alias for 'godotter providers'. Shows all configured providers."""
    settings = get_settings()
    typer.echo('\n'.join(format_provider_rows(settings)))


@provider_app.command(
    'use',
    help='Set the default AI provider.',
)
def provider_use_command(
    name: str = typer.Argument(
        ...,
        help='Provider name (e.g., moonshot, deepseek, siliconflow, alibaba).',
    ),
) -> None:
    """Switch the default AI provider.
    
    Examples:
        godotter provider use moonshot
        godotter provider use deepseek
    """
    try:
        selected = set_default_provider(name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f'default provider set to {selected}')


@provider_key_app.command(
    'show',
    help='Display the API key status for a provider.',
)
def provider_key_show_command(
    provider: str | None = typer.Option(
        None,
        '--provider',
        help='Provider name (defaults to current default provider).',
    ),
) -> None:
    """Show whether an API key is configured for the specified provider.
    
    Examples:
        godotter provider key show
        godotter provider key show --provider moonshot
    """
    settings = get_settings()
    selected = normalize_provider_name(provider or settings.default_brain)
    typer.echo(format_provider_key_status(settings, selected))


@provider_key_app.command(
    'set',
    help='Set or update the API key for a provider.',
)
def provider_key_set_command(
    value: str = typer.Argument(
        ...,
        help='The API key value (will be masked in output).',
    ),
    provider: str | None = typer.Option(
        None,
        '--provider',
        help='Provider name (defaults to current default provider).',
    ),
) -> None:
    """Configure the API key for an AI provider.
    
    Examples:
        godotter provider key set "your-api-key-here"
        godotter provider key set "sk-xxx" --provider moonshot
    """
    settings = get_settings()
    try:
        selected, masked = set_provider_key(provider or settings.default_brain, value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f'{selected} key updated: {masked}')


@model_app.command(
    'list',
    help='List available models for a provider.',
)
def model_list_command(
    provider: str | None = typer.Option(
        None,
        '--provider',
        help='Provider name (defaults to current default provider).',
    ),
) -> None:
    """Show all available AI models for the specified provider.
    
    Examples:
        godotter model list
        godotter model list --provider moonshot
    """
    settings = get_settings()
    try:
        rows = fetch_model_rows(settings, provider or settings.default_brain)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo('\n'.join(rows))


@model_app.command(
    'use',
    help='Set the default model for a provider.',
)
def model_use_command(
    name: str = typer.Argument(
        ...,
        help='Model name (e.g., kimi-k2.5, deepseek-v4-pro).',
    ),
    provider: str | None = typer.Option(
        None,
        '--provider',
        help='Provider name (defaults to current default provider).',
    ),
) -> None:
    """Set the default model for an AI provider.
    
    Examples:
        godotter model use kimi-k2.5
        godotter model use qwen-plus --provider alibaba
    """
    settings = get_settings()
    try:
        selected, model = set_model_for_provider(provider or settings.default_brain, name)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f'{selected} model set to {model}')


@runtime_app.command(
    'lint',
    help='Lint Godot scripts or the entire project.',
)
def runtime_lint_command(
    path: str | None = typer.Argument(
        None,
        help='Path to a specific script file (lints entire project if omitted).',
    ),
    timeout: int = typer.Option(
        60,
        '--timeout',
        help='Timeout in seconds for the lint operation.',
    ),
    project: str | None = typer.Option(
        None,
        '--project',
        help='Project name or path (uses default project if omitted).',
    ),
) -> None:
    """Run static analysis on Godot scripts.
    
    Checks for syntax errors and common issues in GDScript files.
    
    Examples:
        godotter runtime lint                      # Lint entire project
        godotter runtime lint scripts/player.gd    # Lint specific file
        godotter runtime lint --timeout 120        # With custom timeout
    """
    settings = get_settings()
    runner = build_runner(settings, project=project)
    if path:
        result = runner.lint_script(path, timeout=timeout)
        target = path
    else:
        result = runner.lint_project(timeout=timeout)
        target = '(project)'
    typer.echo(format_runtime_result('script_lint', target, result))


@runtime_app.command(
    'run',
    help='Run the Godot project or a specific scene.',
)
def runtime_run_command(
    scene: str | None = typer.Option(
        None,
        '--scene',
        help='Scene file path to run (runs main scene if omitted).',
    ),
    timeout: int = typer.Option(
        60,
        '--timeout',
        help='Timeout in seconds for the run operation.',
    ),
    project: str | None = typer.Option(
        None,
        '--project',
        help='Project name or path (uses default project if omitted).',
    ),
) -> None:
    """Execute the Godot project in headless mode.
    
    Runs the project or a specific scene for testing.
    
    Examples:
        godotter runtime run                       # Run main scene
        godotter runtime run --scene main.tscn     # Run specific scene
        godotter runtime run --timeout 300         # With 5 minute timeout
    """
    settings = get_settings()
    runner = build_runner(settings, project=project)
    result = runner.run_project(timeout=timeout, scene=scene)
    typer.echo(format_runtime_result('headless_run', scene or '(project)', result))


@runtime_app.command(
    'doctor',
    help='Diagnose Godot environment and project health.',
)
def runtime_doctor_command(
    timeout: int = typer.Option(
        15,
        '--timeout',
        help='Timeout in seconds for the diagnosis.',
    ),
    project: str | None = typer.Option(
        None,
        '--project',
        help='Project name or path (uses default project if omitted).',
    ),
) -> None:
    """Check Godot installation and project configuration.
    
    Verifies:
    - Godot binary is available and runnable
    - Project files exist and are valid
    - Main scene is configured
    - Script and scene counts
    
    Examples:
        godotter runtime doctor
        godotter runtime doctor --project mygame
    """
    settings = get_settings()
    target = resolve_runtime_target(settings, project=project)
    report = run_doctor(target.workspace_root, target.godot_path, timeout=timeout)
    typer.echo(format_doctor_report(report))


@runtime_app.command(
    'uid-fix',
    help='Fix UID references in Godot project files.',
)
def runtime_uid_fix_command(
    dry_run: bool = typer.Option(
        True,
        '--dry-run/--write',
        help='Preview changes (default) or actually write fixes.',
    ),
    project: str | None = typer.Option(
        None,
        '--project',
        help='Project name or path (uses default project if omitted).',
    ),
) -> None:
    """Repair broken UID references in .tscn and .tres files.
    
    Godot uses UIDs to track resources. This command fixes mismatches
    between UIDs and file paths.
    
    Examples:
        godotter runtime uid-fix              # Preview changes (safe)
        godotter runtime uid-fix --write      # Apply fixes
        godotter runtime uid-fix --dry-run    # Same as default
    """
    settings = get_settings()
    target = resolve_runtime_target(settings, project=project)
    result = fix_uid_paths(target.workspace_root, dry_run=dry_run)
    typer.echo(format_uid_fix_result(result, dry_run=dry_run, workspace_root=target.workspace_root))
