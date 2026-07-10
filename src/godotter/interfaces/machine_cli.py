from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from godotter.config import get_settings
from godotter.context import ExecutionContext, Memory
from godotter.interfaces.commands.godot import export_app, project_new_command, runtime_app, scaffold_app, scene_app
from godotter.operations import build_default_operations
from godotter.operations.specs import operation_result_text


app = typer.Typer(
    help='Godotter machine interface for workflow automation and Agent tools.',
    no_args_is_help=True,
)
tool_app = typer.Typer(help='Run or inspect Agent-safe atomic operations.', no_args_is_help=True)
workflow_app = typer.Typer(help='Workflow operations. This namespace is reserved for the service migration.', no_args_is_help=True)
state_app = typer.Typer(help='State inspection operations. This namespace is reserved for the service migration.', no_args_is_help=True)
project_app = typer.Typer(help='Create and inspect Godot projects.', no_args_is_help=True)

app.add_typer(tool_app, name='tool')
app.add_typer(workflow_app, name='workflow')
app.add_typer(state_app, name='state')
app.add_typer(project_app, name='project')
app.add_typer(scene_app, name='scene')
app.add_typer(scaffold_app, name='scaffold')
app.add_typer(runtime_app, name='runtime')
app.add_typer(export_app, name='export')

project_app.command('create', help='Create a new Godot project with a minimal runnable scaffold.')(project_new_command)


@app.callback()
def main() -> None:
    return None


@tool_app.command('list', help='List Agent-facing tool operations as JSON.')
def tool_list() -> None:
    registry = build_default_operations()
    payload = [
        {
            'name': operation.name,
            'summary': operation.summary,
            'audience': sorted(operation.audience),
            'permissions': sorted(operation.permissions),
        }
        for operation in registry.list(audience='agent')
    ]
    typer.echo(json.dumps({'tools': payload}, ensure_ascii=False, indent=2))


@tool_app.command('schema', help='Print one tool schema, or all Agent tool schemas, as JSON.')
def tool_schema(
    name: Annotated[str | None, typer.Argument(help='Optional tool operation name.')] = None,
) -> None:
    registry = build_default_operations()
    if name:
        operation = registry.get(name)
        if operation is None or 'agent' not in operation.audience:
            raise typer.BadParameter(f'Unknown Agent tool operation: {name}')
        payload: object = operation.tool_definition()
    else:
        payload = registry.tool_definitions(audience='agent')
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@tool_app.command('run', help='Run one Agent tool operation with JSON args.')
def tool_run(
    name: Annotated[str, typer.Argument(help='Tool operation name.')],
    args: Annotated[str, typer.Option('--args', help='JSON object passed to the operation.')] = '{}',
    args_file: Annotated[
        Path | None,
        typer.Option('--args-file', help='Path to a JSON object file passed to the operation.'),
    ] = None,
    workspace: Annotated[
        Path | None,
        typer.Option('--workspace', help='Workspace root. Defaults to configured workspace root.'),
    ] = None,
    text: Annotated[
        bool,
        typer.Option('--text/--json', help='Print legacy text output instead of structured JSON.'),
    ] = False,
) -> None:
    registry = build_default_operations()
    operation = registry.get(name)
    if operation is None or 'agent' not in operation.audience:
        raise typer.BadParameter(f'Unknown Agent tool operation: {name}')
    raw_args = args
    if args_file is not None:
        raw_args = args_file.read_text(encoding='utf-8-sig')
    try:
        parsed_args = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        source = '--args-file' if args_file is not None else '--args'
        raise typer.BadParameter(f'{source} must contain a JSON object: {exc}') from exc
    if not isinstance(parsed_args, dict):
        raise typer.BadParameter('--args must be a JSON object')

    settings = get_settings()
    workspace_root = (workspace or settings.workspace_root).resolve()
    context = ExecutionContext(
        settings=settings.model_copy(update={'workspace_root': workspace_root}),
        workspace_root=workspace_root,
        memory=Memory(settings.resolved_memory_path),
    )
    envelope = registry.execute_envelope(name, context, parsed_args)
    if text:
        if envelope.ok and envelope.data is not None:
            result = operation.output_model.model_validate(envelope.data)
            typer.echo(operation_result_text(result))
            return
        typer.echo(envelope.error.message if envelope.error else 'operation failed')
        raise typer.Exit(1)
    else:
        typer.echo(envelope.model_dump_json(indent=2))
        if not envelope.ok:
            raise typer.Exit(1)


@workflow_app.command('list', help='List workflow operations as JSON.')
def workflow_list() -> None:
    typer.echo(json.dumps({'workflows': []}, ensure_ascii=False, indent=2))


@state_app.command('list', help='List state operations as JSON.')
def state_list() -> None:
    typer.echo(json.dumps({'state_operations': []}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    app()
