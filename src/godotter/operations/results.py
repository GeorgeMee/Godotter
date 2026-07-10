from __future__ import annotations

from typing import Any

from pydantic import BaseModel as SchemaModel
from pydantic import Field
from pydantic import ValidationError


class OperationError(SchemaModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class OperationEnvelope(SchemaModel):
    ok: bool
    operation: str
    data: dict[str, Any] | None = None
    message: str = ''
    error: OperationError | None = None


def success_envelope(operation: str, data: SchemaModel) -> OperationEnvelope:
    payload = data.model_dump()
    message = str(payload.get('text') or '')
    return OperationEnvelope(
        ok=True,
        operation=operation,
        data=payload,
        message=message,
        error=None,
    )


def error_envelope(operation: str, exc: Exception) -> OperationEnvelope:
    code = _error_code(exc)
    details: dict[str, Any] = {}
    if isinstance(exc, ValidationError):
        details['errors'] = exc.errors()
    return OperationEnvelope(
        ok=False,
        operation=operation,
        data=None,
        message='',
        error=OperationError(code=code, message=str(exc), details=details),
    )


def _error_code(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        return 'validation_error'
    if isinstance(exc, FileNotFoundError):
        return 'file_not_found'
    if isinstance(exc, KeyError):
        return 'operation_not_found'
    if isinstance(exc, ValueError) and 'escapes workspace' in str(exc):
        return 'path_escapes_workspace'
    return 'execution_error'

