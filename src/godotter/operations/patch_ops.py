from __future__ import annotations

from pydantic import BaseModel as SchemaModel
from pydantic import Field

from godotter.operations.specs import OperationSpec, OperationTextResult
from godotter.services.project.patches import PatchService
from godotter.context import ExecutionContext


class GenerateFilePatchInput(SchemaModel):
    path: str = Field(description='Target file path relative to the workspace root.')
    new_content: str = Field(description='Full replacement content for the target file.')


class GenerateTextReplacePatchInput(SchemaModel):
    path: str = Field(description='Target file path relative to the workspace root.')
    old_text: str = Field(description='Exact text to replace.')
    new_text: str = Field(description='Replacement text.')


class ApplyUnifiedPatchInput(SchemaModel):
    patch: str = Field(description='Unified diff text to apply.')


class ApplyPatchFileInput(SchemaModel):
    path: str = Field(description='Patch file path relative to the workspace root.')


class ReplaceFileInput(SchemaModel):
    path: str = Field(description='Target file path relative to the workspace root.')
    new_content: str = Field(description='Full replacement content for the target file.')


class ReplaceTextInput(SchemaModel):
    path: str = Field(description='Target file path relative to the workspace root.')
    old_text: str = Field(description='Exact text to replace.')
    new_text: str = Field(description='Replacement text.')


class LegacyGeneratePatchInput(SchemaModel):
    path: str = Field(description='Target file path relative to the workspace root.')
    new_content: str | None = Field(default=None, description='Full replacement content for the target file.')
    old_text: str | None = Field(default=None, description='Exact text to replace.')
    new_text: str | None = Field(default=None, description='Replacement text when old_text is provided.')


class LegacyApplyPatchInput(SchemaModel):
    patch: str | None = Field(default=None, description='Unified diff text to apply.')
    patch_path: str | None = Field(default=None, description='Patch file path relative to the workspace root.')
    path: str | None = Field(default=None, description='Target file path relative to the workspace root.')
    new_content: str | None = Field(default=None, description='Full replacement content for the target file.')
    old_text: str | None = Field(default=None, description='Exact text to replace.')
    new_text: str | None = Field(default=None, description='Replacement text when old_text is provided.')


def _generate_file_patch(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    data = GenerateFilePatchInput.model_validate(payload)
    return OperationTextResult(text=PatchService(context.workspace_root).generate_file_patch(data.path, data.new_content).patch)


def _generate_text_replace_patch(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    data = GenerateTextReplacePatchInput.model_validate(payload)
    result = PatchService(context.workspace_root).generate_text_replace_patch(data.path, data.old_text, data.new_text)
    return OperationTextResult(text=result.patch)


def _apply_unified_patch(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    data = ApplyUnifiedPatchInput.model_validate(payload)
    result = PatchService(context.workspace_root).apply_unified_patch(data.patch)
    return OperationTextResult(text='Applied patch to: ' + ', '.join(result.applied_paths))


def _apply_patch_file(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    data = ApplyPatchFileInput.model_validate(payload)
    result = PatchService(context.workspace_root).apply_patch_file(data.path)
    return OperationTextResult(text='Applied patch to: ' + ', '.join(result.applied_paths))


def _replace_file(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    data = ReplaceFileInput.model_validate(payload)
    result = PatchService(context.workspace_root).replace_file(data.path, data.new_content)
    return OperationTextResult(text='Applied patch to: ' + ', '.join(result.applied_paths))


def _replace_text(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    data = ReplaceTextInput.model_validate(payload)
    result = PatchService(context.workspace_root).replace_text(data.path, data.old_text, data.new_text)
    return OperationTextResult(text='Applied patch to: ' + ', '.join(result.applied_paths))


def _legacy_generate_patch(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    data = LegacyGeneratePatchInput.model_validate(payload)
    service = PatchService(context.workspace_root)
    if data.new_content is not None:
        return OperationTextResult(text=service.generate_file_patch(data.path, data.new_content).patch)
    if data.old_text:
        return OperationTextResult(text=service.generate_text_replace_patch(data.path, data.old_text, data.new_text or '').patch)
    raise ValueError('Provide new_content or old_text/new_text.')


def _legacy_apply_patch(context: ExecutionContext, payload: SchemaModel) -> OperationTextResult:
    data = LegacyApplyPatchInput.model_validate(payload)
    service = PatchService(context.workspace_root)
    if data.path:
        if data.new_content is not None:
            result = service.replace_file(data.path, data.new_content)
        elif data.old_text:
            result = service.replace_text(data.path, data.old_text, data.new_text or '')
        else:
            raise ValueError('Provide new_content or old_text/new_text when path is provided.')
    elif data.patch:
        result = service.apply_unified_patch(data.patch)
    elif data.patch_path:
        result = service.apply_patch_file(data.patch_path)
    else:
        raise ValueError('Provide patch, patch_path, or path edit arguments.')
    return OperationTextResult(text='Applied patch to: ' + ', '.join(result.applied_paths))


def build_patch_operations() -> list[OperationSpec]:
    agent_workflow = frozenset({'agent', 'workflow'})
    compatibility = frozenset({'workflow'})
    return [
        OperationSpec(
            name='generate_file_patch',
            summary='Generate a full-file replacement patch.',
            description='Generate a unified diff that replaces one file with new full content without applying it.',
            input_model=GenerateFilePatchInput,
            output_model=OperationTextResult,
            audience=agent_workflow,
            permissions=frozenset({'read'}),
            handler=_generate_file_patch,
        ),
        OperationSpec(
            name='generate_text_replace_patch',
            summary='Generate an exact text replacement patch.',
            description='Generate a unified diff that replaces exact text in one file without applying it.',
            input_model=GenerateTextReplacePatchInput,
            output_model=OperationTextResult,
            audience=agent_workflow,
            permissions=frozenset({'read'}),
            handler=_generate_text_replace_patch,
        ),
        OperationSpec(
            name='apply_unified_patch',
            summary='Apply a unified diff patch.',
            description='Apply a unified diff patch inside the workspace.',
            input_model=ApplyUnifiedPatchInput,
            output_model=OperationTextResult,
            audience=agent_workflow,
            permissions=frozenset({'write'}),
            handler=_apply_unified_patch,
        ),
        OperationSpec(
            name='apply_patch_file',
            summary='Apply a patch file.',
            description='Apply a unified diff patch loaded from a workspace patch file.',
            input_model=ApplyPatchFileInput,
            output_model=OperationTextResult,
            audience=agent_workflow,
            permissions=frozenset({'read', 'write'}),
            handler=_apply_patch_file,
        ),
        OperationSpec(
            name='replace_file',
            summary='Replace one file.',
            description='Replace one file with new full content.',
            input_model=ReplaceFileInput,
            output_model=OperationTextResult,
            audience=agent_workflow,
            permissions=frozenset({'read', 'write'}),
            handler=_replace_file,
        ),
        OperationSpec(
            name='replace_text',
            summary='Replace exact text in one file.',
            description='Replace exact text in one file.',
            input_model=ReplaceTextInput,
            output_model=OperationTextResult,
            audience=agent_workflow,
            permissions=frozenset({'read', 'write'}),
            handler=_replace_text,
        ),
        OperationSpec(
            name='generate_patch',
            summary='Deprecated patch generator compatibility alias.',
            description='Deprecated compatibility alias. Prefer generate_file_patch or generate_text_replace_patch.',
            input_model=LegacyGeneratePatchInput,
            output_model=OperationTextResult,
            audience=compatibility,
            permissions=frozenset({'read'}),
            handler=_legacy_generate_patch,
        ),
        OperationSpec(
            name='apply_patch',
            summary='Deprecated patch apply compatibility alias.',
            description='Deprecated compatibility alias. Prefer apply_unified_patch, apply_patch_file, replace_file, or replace_text.',
            input_model=LegacyApplyPatchInput,
            output_model=OperationTextResult,
            audience=compatibility,
            permissions=frozenset({'write'}),
            handler=_legacy_apply_patch,
        ),
    ]
