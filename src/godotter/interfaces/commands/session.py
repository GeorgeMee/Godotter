from __future__ import annotations

from pathlib import Path

import typer

from godotter.config import Settings, get_settings
from godotter.config.logging import configure_logging
from godotter.services.chat import ChatSessionRepository, SessionService
from godotter.services.godot.cli_helpers import resolve_runtime_target


session_app = typer.Typer(help='Inspect saved chat sessions.', no_args_is_help=True)


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


@session_app.command('list', help='List saved chat sessions in the workspace.')
def list_sessions_command(
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
    status: str = typer.Option(
        'active',
        '--status',
        help='Filter sessions by status: active, archived, or all.',
    ),
) -> None:
    base_settings = get_settings()
    root, settings = _resolve_workspace_root(base_settings, workspace=workspace, project=project)
    configure_logging(settings)
    repository = ChatSessionRepository(root)
    service = SessionService(settings, repository)
    normalized_status = status.strip().lower()
    if normalized_status not in {'active', 'archived', 'all'}:
        raise typer.BadParameter('status must be active, archived, or all')
    sessions = service.list_sessions(project_name=project, status=normalized_status)
    if not sessions:
        typer.echo('(empty)')
        return
    for session in sessions:
        typer.echo(
            f"{session.session_id} | {session.updated_at or session.created_at} | "
            f"project={session.project_name} | title={session.title} | status={session.status} | mode={session.mode} | "
            f"messages={len(session.messages)} | operations={len(session.operation_history)} | "
            f"checkpoint={session.checkpoint_id or '-'}"
        )


@session_app.command('show', help='Show one saved chat session.')
def show_session_command(
    session_id: str = typer.Argument(..., help='Saved session id to show.'),
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
) -> None:
    base_settings = get_settings()
    root, settings = _resolve_workspace_root(base_settings, workspace=workspace, project=project)
    configure_logging(settings)
    repository = ChatSessionRepository(root)
    service = SessionService(settings, repository)
    detail = service.session_detail(session_id)
    session = detail['session']
    messages = detail.get('messages') or []
    operations = detail.get('operations') or []
    typer.echo(f'session_id={session.session_id}')
    typer.echo(f'project={session.project_name}')
    typer.echo(f'title={session.title}')
    typer.echo(f'status={session.status}')
    typer.echo(f'mode={session.mode}')
    typer.echo(f'brain={session.brain_name or "-"}')
    typer.echo(f'checkpoint={session.checkpoint_id or "-"}')
    typer.echo(f'base_commit={session.base_commit or "-"}')
    typer.echo(f'conversation_cursor={session.conversation_cursor}')
    typer.echo(f'operation_cursor={session.operation_cursor}')
    typer.echo(f"messages={len(messages)}")
    typer.echo(f"operations={len(operations)}")
    if messages:
        typer.echo('messages:')
        for message in messages:
            role = str(message.get('role') or '-')
            content = str(message.get('content') or '').strip()
            typer.echo(f'  - {role}: {content}')
    if operations:
        typer.echo('operations:')
        for operation in operations:
            typer.echo(
                f"  - {operation.get('operation_id', '-')}: {operation.get('tool_name', '-')} "
                f"status={operation.get('status', '-')}"
            )


@session_app.command('archive', help='Archive one saved chat session.')
def archive_session_command(
    session_id: str = typer.Argument(..., help='Saved session id to archive.'),
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
) -> None:
    base_settings = get_settings()
    root, settings = _resolve_workspace_root(base_settings, workspace=workspace, project=project)
    configure_logging(settings)
    repository = ChatSessionRepository(root)
    service = SessionService(settings, repository)
    session = service.archive_session(service.load_session(session_id))
    typer.echo(f'session_id={session.session_id} status=archived')
