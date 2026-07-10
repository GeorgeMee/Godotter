from __future__ import annotations

import typer

from godotter.interfaces.commands.chat import chat_command
from godotter.interfaces.commands.info import info_command
from godotter.interfaces.commands.llm import model_app, provider_app
from godotter.interfaces.commands.project import human_project_app


app = typer.Typer(
    help='Human-facing configuration and chat console.',
    epilog='Use "gdt COMMAND --help" for more information on a command.',
    no_args_is_help=True,
)

app.command('info', help='Show project information and configuration.')(info_command)
app.command('chat', help='Start an interactive AI chat session or send one message.')(chat_command)
app.add_typer(provider_app, name='provider')
app.add_typer(model_app, name='model')
app.add_typer(human_project_app, name='project')

__all__ = ['app']


if __name__ == '__main__':
    app()
