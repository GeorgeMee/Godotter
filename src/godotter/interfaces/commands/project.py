from __future__ import annotations

from pathlib import Path

import typer

from godotter.config import get_settings
from godotter.utils.envfile import EnvFile


human_project_app = typer.Typer(help='Manage human project defaults.')


@human_project_app.command('root-show', help='Show the default parent directory for new projects.')
def project_root_show_command() -> None:
    settings = get_settings()
    root = Path(settings.projects_root)
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    root = root.resolve()
    typer.echo(f'projects_root={root.as_posix()}')
    typer.echo(f'exists={str(root.exists()).lower()}')


@human_project_app.command('root-set', help='Set the default parent directory for new projects.')
def project_root_set_command(
    path: str = typer.Argument(..., help='Directory path where new projects will be created.'),
) -> None:
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise typer.BadParameter(f'Path does not exist: {resolved}')
    EnvFile(Path('.env')).set('GODOTTER_PROJECTS_ROOT', resolved.as_posix())
    get_settings.cache_clear()
    typer.echo(f'projects_root={resolved.as_posix()}')
