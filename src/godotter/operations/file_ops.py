from __future__ import annotations

from pydantic import BaseModel as SchemaModel
from pydantic import Field

from godotter.operations.specs import OperationSpec, OperationTextResult
from godotter.services.project.files import FileService
from godotter.context import ExecutionContext


class ReadFileInput(SchemaModel):
    path: str = Field(description='File path relative to the workspace root.')


class ListFilesInput(SchemaModel):
    path: str = Field(default='.', description='Directory path relative to the workspace root.')


class SearchCodeInput(SchemaModel):
    query: str = Field(description='Text to search for.')
    path: str = Field(default='.', description='Directory path relative to the workspace root.')


def _read_file(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    data = ReadFileInput.model_validate(payload)
    result = FileService(context.workspace_root).read_file(data.path)
    text = ''.join(
        f'{index} | {line}\n'
        for index, line in enumerate(result.content.splitlines(), start=1)
    )
    return OperationTextResult(text=text)


def _list_files(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    data = ListFilesInput.model_validate(payload)
    entries = FileService(context.workspace_root).list_files(data.path)
    return OperationTextResult(text='\n'.join(entry.path for entry in entries) if entries else '(empty)')


def _search_code(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    data = SearchCodeInput.model_validate(payload)
    matches = FileService(context.workspace_root).search_code(data.query, data.path)
    if not matches:
        return OperationTextResult(text='No matches found.')
    return OperationTextResult(
        text='\n'.join(f'{match.path}:{match.line}: {match.text}' for match in matches)
    )


def build_file_operations() -> list[OperationSpec]:
    audience = frozenset({'agent', 'workflow'})
    permissions = frozenset({'read'})
    return [
        OperationSpec(
            name='read_file',
            summary='Read a text file.',
            description='Read a UTF-8 text file from the workspace with line numbers.',
            input_model=ReadFileInput,
            output_model=OperationTextResult,
            audience=audience,
            permissions=permissions,
            handler=_read_file,
        ),
        OperationSpec(
            name='list_files',
            summary='List workspace files.',
            description='List files and directories under a workspace path.',
            input_model=ListFilesInput,
            output_model=OperationTextResult,
            audience=audience,
            permissions=permissions,
            handler=_list_files,
        ),
        OperationSpec(
            name='search_code',
            summary='Search workspace text.',
            description='Search for a text string across workspace files.',
            input_model=SearchCodeInput,
            output_model=OperationTextResult,
            audience=audience,
            permissions=permissions,
            handler=_search_code,
        ),
    ]
