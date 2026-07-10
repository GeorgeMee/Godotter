from __future__ import annotations

from pydantic import BaseModel as SchemaModel
from pydantic import Field

from godotter.operations.specs import OperationSpec
from godotter.services.godot.diagnostics import DiagnosticsService
from godotter.context import ExecutionContext


class ValidateProjectInput(SchemaModel):
    pass


class RuntimeDoctorInput(SchemaModel):
    timeout: int = Field(default=15, description='Timeout in seconds for the Godot version probe.')


class DiagnosticTextOutput(SchemaModel):
    text: str


def _validate_project(context: ExecutionContext, payload: SchemaModel) -> DiagnosticTextOutput:
    ValidateProjectInput.model_validate(payload)
    return DiagnosticTextOutput(
        text=DiagnosticsService(
            context.workspace_root,
            godot_path=context.settings.godot_path,
        ).validate_project_text()
    )


def _runtime_doctor(context: ExecutionContext, payload: SchemaModel) -> DiagnosticTextOutput:
    data = RuntimeDoctorInput.model_validate(payload)
    return DiagnosticTextOutput(
        text=DiagnosticsService(
            context.workspace_root,
            godot_path=context.settings.godot_path,
        ).runtime_doctor_text(timeout=data.timeout)
    )


def build_diagnostic_operations() -> list[OperationSpec]:
    return [
        OperationSpec(
            name='validate_project',
            summary='Validate workspace structure.',
            description='Run lightweight workspace validation for the current project structure.',
            input_model=ValidateProjectInput,
            output_model=DiagnosticTextOutput,
            audience=frozenset({'agent', 'workflow'}),
            permissions=frozenset({'read'}),
            handler=_validate_project,
        ),
        OperationSpec(
            name='runtime_doctor',
            summary='Check Godot runtime configuration.',
            description='Check Godot binary configuration and basic project metadata for headless execution.',
            input_model=RuntimeDoctorInput,
            output_model=DiagnosticTextOutput,
            audience=frozenset({'agent', 'workflow'}),
            permissions=frozenset({'read'}),
            handler=_runtime_doctor,
        ),
    ]
