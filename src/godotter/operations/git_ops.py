from __future__ import annotations

from pydantic import BaseModel as SchemaModel
from pydantic import Field

from godotter.operations.specs import OperationSpec, OperationTextResult
from godotter.services.project.git import GitService
from godotter.context import ExecutionContext


class GitStatusInput(SchemaModel):
    pass


class GitDiffInput(SchemaModel):
    cached: bool = Field(default=False, description='Show staged changes if true.')
    path: str | None = Field(default=None, description='Optional path filter relative to the workspace root.')


class GitLogInput(SchemaModel):
    limit: int = Field(default=5, description='Maximum number of commits to show.')


class GitBranchInput(SchemaModel):
    pass


def _git_status(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    GitStatusInput.model_validate(payload)
    return OperationTextResult(text=GitService(context.workspace_root).status())


def _git_diff(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    data = GitDiffInput.model_validate(payload)
    return OperationTextResult(text=GitService(context.workspace_root).diff(cached=data.cached, path=data.path))


def _git_log(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    data = GitLogInput.model_validate(payload)
    return OperationTextResult(text=GitService(context.workspace_root).log(limit=data.limit))


def _git_branch(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    GitBranchInput.model_validate(payload)
    return OperationTextResult(text=GitService(context.workspace_root).branch())


def build_git_operations() -> list[OperationSpec]:
    audience = frozenset({'agent', 'workflow'})
    permissions = frozenset({'read', 'git'})
    return [
        OperationSpec(
            name='git_status',
            summary='Show git status.',
            description='Show a short git status for the current workspace repository.',
            input_model=GitStatusInput,
            output_model=OperationTextResult,
            audience=audience,
            permissions=permissions,
            handler=_git_status,
        ),
        OperationSpec(
            name='git_diff',
            summary='Show git diff.',
            description='Show git diff output for the current workspace repository.',
            input_model=GitDiffInput,
            output_model=OperationTextResult,
            audience=audience,
            permissions=permissions,
            handler=_git_diff,
        ),
        OperationSpec(
            name='git_log',
            summary='Show git log.',
            description='Show recent git commit history for the current workspace repository.',
            input_model=GitLogInput,
            output_model=OperationTextResult,
            audience=audience,
            permissions=permissions,
            handler=_git_log,
        ),
        OperationSpec(
            name='git_branch',
            summary='Show git branches.',
            description='Show local git branches for the current workspace repository.',
            input_model=GitBranchInput,
            output_model=OperationTextResult,
            audience=audience,
            permissions=permissions,
            handler=_git_branch,
        ),
    ]
