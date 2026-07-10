from __future__ import annotations

from pydantic import BaseModel as SchemaModel

from godotter.operations.specs import OperationSpec
from godotter.services.godot.uid import UidService
from godotter.context import ExecutionContext


class UidScanInput(SchemaModel):
    pass


class UidFixApplyInput(SchemaModel):
    pass


class UidTextOutput(SchemaModel):
    text: str


def _uid_scan(context: ExecutionContext, payload: SchemaModel) -> UidTextOutput:
    UidScanInput.model_validate(payload)
    return UidTextOutput(text=UidService(context.workspace_root).scan_text())


def _uid_fix_apply(context: ExecutionContext, payload: SchemaModel) -> UidTextOutput:
    UidFixApplyInput.model_validate(payload)
    return UidTextOutput(text=UidService(context.workspace_root).fix_apply_text())


def build_uid_operations() -> list[OperationSpec]:
    return [
        OperationSpec(
            name='uid_scan',
            summary='Scan stale Godot UID resource paths.',
            description='Scan scenes/resources for stale ext_resource paths using Godot .uid files without writing changes.',
            input_model=UidScanInput,
            output_model=UidTextOutput,
            audience=frozenset({'agent', 'workflow'}),
            permissions=frozenset({'read'}),
            handler=_uid_scan,
        ),
        OperationSpec(
            name='uid_fix_apply',
            summary='Apply Godot UID resource path fixes.',
            description='Rewrite stale ext_resource paths using Godot .uid files.',
            input_model=UidFixApplyInput,
            output_model=UidTextOutput,
            audience=frozenset({'agent', 'workflow'}),
            permissions=frozenset({'write'}),
            handler=_uid_fix_apply,
        ),
    ]
