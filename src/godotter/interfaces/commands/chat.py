from __future__ import annotations

import builtins
from pathlib import Path

import typer

from godotter.services.chat.session_types import ChatSession
from godotter.config import Settings, get_settings
from godotter.config.logging import configure_logging
from godotter.services.chat import ChatSessionService
from godotter.services.godot.cli_helpers import resolve_runtime_target


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


def chat_command(
    message: str | None = typer.Option(
        None,
        '--message',
        '-m',
        help='Send one message and exit. Omit to start an interactive chat session.',
    ),
    mode: str = typer.Option(
        'plan',
        '--mode',
        help='Agent execution mode: plan or act. Deprecated aliases: review->plan, code/debug->act.',
    ),
    brain: str | None = typer.Option(None, '--brain', help='Override the default AI brain/provider for this session.'),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to GODOTTER_WORKSPACE_ROOT or the default project).',
    ),
    project: str | None = typer.Option(
        None,
        '--project',
        help='Project name from config/projects.toml (uses default project if omitted).',
    ),
    no_scout: bool = typer.Option(
        False,
        '--no-scout',
        help='Skip automatic workspace scouting (sends raw message only).',
    ),
) -> None:
    base_settings = get_settings()
    normalized_mode, mode_note = _normalize_cli_mode(mode)
    root, settings = _resolve_workspace_root(base_settings, workspace=workspace, project=project)
    configure_logging(settings)
    if mode_note:
        typer.echo(mode_note)
    service = ChatSessionService(settings)
    if message is not None:
        session = ChatSession(
            session_id='tmp',
            workspace_root=root,
            project_name=project or 'workspace',
            title='Chat',
            created_at='',
            updated_at='',
            messages=[{'role': 'user', 'content': message}],
            mode=normalized_mode,
            brain_name=brain or settings.default_brain,
        )
        result = service.generate_reply_for_session(
            session,
            brain_name=brain,
            fallback_brain_name=settings.default_brain,
            no_scout=no_scout,
        )
        typer.echo(result.reply_text)
        return

    _run_chat_interactive(
        service=service,
        workspace_root=root,
        project=project,
        brain=brain,
        fallback_brain_name=settings.default_brain,
        default_brain=settings.default_brain,
        mode=normalized_mode,
        no_scout=no_scout,
    )


def _run_chat_interactive(
    *,
    service: ChatSessionService,
    workspace_root: Path,
    project: str | None,
    brain: str | None,
    fallback_brain_name: str,
    default_brain: str,
    mode: str,
    no_scout: bool,
) -> None:
    session = ChatSession(
        session_id='tmp',
        workspace_root=workspace_root,
        project_name=project or 'workspace',
        title='Chat',
        created_at='',
        updated_at='',
        messages=[],
        mode=mode,
        brain_name=brain or default_brain,
    )
    current_mode = mode
    typer.echo('Interactive chat. Use /mode plan|act to switch mode, /q to quit.')
    while True:
        try:
            user_input = builtins.input(f'{current_mode}> ').strip()
        except (EOFError, KeyboardInterrupt):
            typer.echo()
            break
        if not user_input:
            continue
        if user_input in {'/q', '/quit', 'quit', 'exit'}:
            break
        if user_input in {'/rollback', '/undo'}:
            try:
                rollback = service.rollback_last_operation(session)
                typer.echo(
                    f"rollback={rollback['tool_name']} target={rollback['args'].get('target_tool_name', '')} "
                    f"paths={', '.join(rollback.get('affected_paths') or []) or '(none)'}"
                )
            except Exception as exc:
                typer.echo(f'rollback_error={exc}')
            continue
        if user_input.startswith('/mode '):
            current_mode = _normalize_cli_mode(user_input.split(maxsplit=1)[1])[0]
            typer.echo(f'mode={current_mode}')
            continue

        session.messages.append({'role': 'user', 'content': user_input})
        session.mode = current_mode
        result = service.generate_reply_for_session(
            session,
            brain_name=brain,
            fallback_brain_name=fallback_brain_name,
            no_scout=no_scout,
        )
        typer.echo(result.reply_text)
        session.messages = list(result.conversation)
