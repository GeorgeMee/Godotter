from __future__ import annotations

import typer

from godotter.config import get_settings
from godotter.config.logging import configure_logging, get_logger
from godotter.llm import SUPPORTED_PROVIDERS


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
