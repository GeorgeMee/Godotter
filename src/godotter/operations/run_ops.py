from __future__ import annotations

from pydantic import BaseModel as SchemaModel
from pydantic import Field

from godotter.operations.specs import OperationSpec
from godotter.services.godot.run import RunService
from godotter.context import ExecutionContext


class HeadlessRunInput(SchemaModel):
    scene: str | None = Field(default=None, description='Optional res:// scene path to run.')
    timeout: int = Field(default=60, description='Timeout in seconds.')


class RunTextOutput(SchemaModel):
    text: str


def _headless_run(context: ExecutionContext, payload: SchemaModel) -> RunTextOutput:
    data = HeadlessRunInput.model_validate(payload)
    return RunTextOutput(
        text=RunService(
            context.workspace_root,
            godot_path=context.settings.godot_path,
        ).headless_run_text(data.scene, timeout=data.timeout)
    )


def build_run_operations() -> list[OperationSpec]:
    return [
        OperationSpec(
            name='headless_run',
            summary='Run the Godot project in headless mode.',
            description='Run the Godot project in headless mode, optionally with a specific scene.',
            input_model=HeadlessRunInput,
            output_model=RunTextOutput,
            audience=frozenset({'agent', 'workflow'}),
            permissions=frozenset({'read', 'execute'}),
            handler=_headless_run,
        ),
    ]
