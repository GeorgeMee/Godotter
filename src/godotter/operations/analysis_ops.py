from __future__ import annotations

from pydantic import BaseModel as SchemaModel
from pydantic import Field

from godotter.operations.specs import OperationSpec
from godotter.services.godot.analysis import AnalysisService
from godotter.context import ExecutionContext


class AnalysisStatusInput(SchemaModel):
    pass


class ScenePathInput(SchemaModel):
    path: str = Field(description='Scene path relative to the workspace root.')


class ScriptLintInput(SchemaModel):
    path: str | None = Field(
        default=None,
        description='Optional GDScript path relative to the workspace root. If omitted, lint the whole project.',
    )
    timeout: int = Field(default=60, description='Timeout in seconds.')


class LspStatusOutput(SchemaModel):
    configured: bool
    available: bool
    enabled: bool
    reason: str
    capabilities: list[str] = Field(default_factory=list)


class AnalysisStatusOutput(SchemaModel):
    lsp: LspStatusOutput
    fallbacks: list[str]


class AnalysisTextOutput(SchemaModel):
    text: str


def _analysis_status(context: ExecutionContext, payload: SchemaModel) -> AnalysisStatusOutput:
    AnalysisStatusInput.model_validate(payload)
    status = AnalysisService(
        context.workspace_root,
        godot_path=context.settings.godot_path,
    ).status()
    return AnalysisStatusOutput.model_validate(status.to_dict())


def _scene_inspect(context: ExecutionContext, payload: SchemaModel) -> AnalysisTextOutput:
    data = ScenePathInput.model_validate(payload)
    return AnalysisTextOutput(
        text=AnalysisService(
            context.workspace_root,
            godot_path=context.settings.godot_path,
        ).inspect_scene_text(data.path)
    )


def _scene_validate(context: ExecutionContext, payload: SchemaModel) -> AnalysisTextOutput:
    data = ScenePathInput.model_validate(payload)
    return AnalysisTextOutput(
        text=AnalysisService(
            context.workspace_root,
            godot_path=context.settings.godot_path,
        ).validate_scene_text(data.path)
    )


def _script_lint(context: ExecutionContext, payload: SchemaModel) -> AnalysisTextOutput:
    data = ScriptLintInput.model_validate(payload)
    return AnalysisTextOutput(
        text=AnalysisService(
            context.workspace_root,
            godot_path=context.settings.godot_path,
        ).lint_script_text(data.path, timeout=data.timeout)
    )


def build_analysis_operations() -> list[OperationSpec]:
    return [
        OperationSpec(
            name='analysis_status',
            summary='Show analysis and LSP capability status.',
            description='Report Godot LSP availability and fallback analysis capabilities.',
            input_model=AnalysisStatusInput,
            output_model=AnalysisStatusOutput,
            audience=frozenset({'agent', 'workflow'}),
            permissions=frozenset({'read'}),
            handler=_analysis_status,
        ),
        OperationSpec(
            name='scene_inspect',
            summary='Inspect a Godot scene.',
            description='Inspect a Godot scene and report its header, ext_resources, nodes, and connections.',
            input_model=ScenePathInput,
            output_model=AnalysisTextOutput,
            audience=frozenset({'agent', 'workflow'}),
            permissions=frozenset({'read'}),
            handler=_scene_inspect,
        ),
        OperationSpec(
            name='scene_validate',
            summary='Validate a Godot scene.',
            description='Validate a Godot scene for missing external resources and malformed nodes.',
            input_model=ScenePathInput,
            output_model=AnalysisTextOutput,
            audience=frozenset({'agent', 'workflow'}),
            permissions=frozenset({'read'}),
            handler=_scene_validate,
        ),
        OperationSpec(
            name='script_lint',
            summary='Run Godot script linting.',
            description='Run Godot headless linting for a single GDScript file or the whole project.',
            input_model=ScriptLintInput,
            output_model=AnalysisTextOutput,
            audience=frozenset({'agent', 'workflow'}),
            permissions=frozenset({'read'}),
            handler=_script_lint,
        ),
    ]
