from __future__ import annotations

from collections.abc import Iterable

from godotter.context import ExecutionContext
from godotter.operations.results import OperationEnvelope, error_envelope, success_envelope
from godotter.operations.specs import Audience, OperationSpec


class OperationRegistry:
    def __init__(self, operations: Iterable[OperationSpec] = ()) -> None:
        self._operations = {operation.name: operation for operation in operations}

    def register(self, operation: OperationSpec) -> None:
        if operation.name in self._operations:
            raise ValueError(f'Duplicate operation: {operation.name}')
        self._operations[operation.name] = operation

    def get(self, name: str) -> OperationSpec | None:
        return self._operations.get(name)

    def list(self, *, audience: Audience | None = None) -> list[OperationSpec]:
        values = list(self._operations.values())
        if audience is None:
            return values
        return [operation for operation in values if audience in operation.audience]

    def tool_definitions(self, *, audience: Audience = 'agent') -> list[dict[str, object]]:
        return [operation.tool_definition() for operation in self.list(audience=audience)]

    def execute(self, name: str, context: ExecutionContext, args: dict[str, object]):
        operation = self.get(name)
        if operation is None:
            raise KeyError(name)
        return operation.execute(context, args)

    def execute_envelope(self, name: str, context: ExecutionContext, args: dict[str, object]) -> OperationEnvelope:
        operation = self.get(name)
        if operation is None:
            return error_envelope(name, KeyError(name))
        try:
            return success_envelope(name, operation.execute(context, args))
        except Exception as exc:
            return error_envelope(name, exc)
