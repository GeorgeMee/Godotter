from __future__ import annotations

from pathlib import Path

import typer

from godotter.config import Settings, get_settings
from godotter.config.logging import configure_logging
from godotter.services.chat import ChatSessionRepository, SessionService
from godotter.services.chat.session_types import ChatSession
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


def rollback_command(
    session_id: str | None = typer.Option(
        None,
        '--session-id',
        help='Rollback a specific saved chat session.',
    ),
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
    session = _load_session(service, session_id=session_id, project_name=project)
    rollback = service.rollback_last_operation(session)
    typer.echo(
        f"session_id={session.session_id} rollback={rollback['tool_name']} "
        f"target={rollback['args'].get('target_tool_name', '')} "
        f"paths={', '.join(rollback.get('affected_paths') or []) or '(none)'}"
    )


def _load_session(
    service: SessionService,
    *,
    session_id: str | None,
    project_name: str | None,
) -> ChatSession:
    if session_id:
        return service.load_session(session_id)
    sessions = service.list_sessions()
    if project_name:
        for session in sessions:
            if session.project_name == project_name:
                return session
    if sessions:
        return sessions[0]
    raise typer.BadParameter('No saved chat sessions found.')
