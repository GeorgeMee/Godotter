from __future__ import annotations

import builtins
from pathlib import Path

import typer

from godotter.services.chat.session_types import ChatSession
from godotter.config import Settings, get_settings
from godotter.config.logging import configure_logging
from godotter.services.chat import ChatSessionRepository, ReplyService, SessionService
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
    save_session: bool = typer.Option(
        False,
        '--save-session',
        help='Persist the chat session to .godotter/sessions.',
    ),
    session_id: str | None = typer.Option(
        None,
        '--session-id',
        help='Resume or create a saved chat session.',
    ),
) -> None:
    base_settings = get_settings()
    normalized_mode, mode_note = _normalize_cli_mode(mode)
    root, settings = _resolve_workspace_root(base_settings, workspace=workspace, project=project)
    configure_logging(settings)
    if mode_note:
        typer.echo(mode_note)
    repository = ChatSessionRepository(root) if (save_session or session_id is not None) else None
    session_service = SessionService(settings, repository)
    reply_service = ReplyService(settings, session_service)
    if message is not None:
        session = _build_session(
            session_service=session_service,
            workspace_root=root,
            project_name=project or 'workspace',
            session_id=session_id,
            title='Chat',
            mode=normalized_mode,
            brain_name=brain or settings.default_brain,
            initial_message=message,
        )
        result = reply_service.generate_reply_for_session(
            session,
            brain_name=brain,
            fallback_brain_name=settings.default_brain,
            no_scout=no_scout,
        )
        typer.echo(result.reply_text)
        return

    _run_chat_interactive(
        session_service=session_service,
        reply_service=reply_service,
        workspace_root=root,
        project=project,
        brain=brain,
        fallback_brain_name=settings.default_brain,
        default_brain=settings.default_brain,
        mode=normalized_mode,
        no_scout=no_scout,
        repository=repository,
        session_id=session_id,
    )


def _run_chat_interactive(
    *,
    session_service: SessionService,
    reply_service: ReplyService,
    workspace_root: Path,
    project: str | None,
    brain: str | None,
    fallback_brain_name: str,
    default_brain: str,
    mode: str,
    no_scout: bool,
    repository: ChatSessionRepository | None,
    session_id: str | None,
) -> None:
    session = _build_session(
        session_service=session_service,
        workspace_root=workspace_root,
        project_name=project or 'workspace',
        session_id=session_id,
        title='Chat',
        mode=mode,
        brain_name=brain or default_brain,
    )
    current_mode = mode
    if repository is not None:
        typer.echo(f'session_id={session.session_id}')
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
                rollback = session_service.rollback_last_operation(session)
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

        session.mode = current_mode
        if repository is not None:
            session_service.append_message(session, role='user', content=user_input)
        else:
            session.messages.append({'role': 'user', 'content': user_input})
        result = reply_service.generate_reply_for_session(
            session,
            brain_name=brain,
            fallback_brain_name=fallback_brain_name,
            no_scout=no_scout,
        )
        typer.echo(result.reply_text)
        session.messages = list(result.conversation)


def _build_session(
    *,
    session_service: SessionService,
    workspace_root: Path,
    project_name: str,
    session_id: str | None,
    title: str,
    mode: str,
    brain_name: str,
    initial_message: str | None = None,
) -> ChatSession:
    session = session_service.load_or_create_session(
        project_name,
        session_id=session_id,
        title=title,
        workspace_root=workspace_root,
        mode=mode,
        brain_name=brain_name,
    )
    session.workspace_root = workspace_root
    session.project_name = project_name
    session.mode = mode
    session.brain_name = brain_name
    if initial_message is not None:
        if session_service.has_repository:
            session_service.append_message(session, role='user', content=initial_message)
        else:
            session.messages.append({'role': 'user', 'content': initial_message})
    return session
