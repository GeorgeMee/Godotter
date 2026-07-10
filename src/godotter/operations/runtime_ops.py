from __future__ import annotations

from pydantic import BaseModel as SchemaModel

from godotter.operations.specs import OperationSpec, OperationTextResult
from godotter.services.godot.runtime_info import RuntimeInfoService
from godotter.context import ExecutionContext


class ProjectInfoInput(SchemaModel):
    pass


def _project_info(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    ProjectInfoInput.model_validate(payload)
    return OperationTextResult(text=RuntimeInfoService(context.workspace_root).project_info_text())


def build_runtime_operations() -> list[OperationSpec]:
    return [
        OperationSpec(
            name='project_info',
            summary='Read Godot project metadata.',
            description='Read project metadata from project.godot and count scripts and scenes.',
            input_model=ProjectInfoInput,
            output_model=OperationTextResult,
            audience=frozenset({'agent', 'workflow'}),
            permissions=frozenset({'read'}),
            handler=_project_info,
        ),
    ]
