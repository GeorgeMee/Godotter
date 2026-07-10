from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel as SchemaModel

from godotter.context import ExecutionContext


Audience = Literal['human', 'workflow', 'agent', 'web']
Permission = Literal['read', 'write', 'execute', 'network', 'git']


class OperationTextResult(SchemaModel):
    text: str


OperationHandler = Callable[[ExecutionContext, SchemaModel], SchemaModel]


@dataclass(frozen=True, slots=True)
class OperationSpec:
    name: str
    summary: str
    description: str
    input_model: type[SchemaModel]
    output_model: type[SchemaModel]
    audience: frozenset[Audience]
    permissions: frozenset[Permission]
    handler: OperationHandler

    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    def tool_definition(self) -> dict[str, object]:
        return {
            'name': self.name,
            'description': self.description,
            'input_schema': self.input_schema(),
        }

    def execute(self, context: ExecutionContext, args: dict[str, Any]) -> SchemaModel:
        payload = self.input_model.model_validate(args)
        return self.handler(context, payload)


def operation_result_text(result: SchemaModel) -> str:
    if isinstance(result, OperationTextResult):
        return result.text
    if hasattr(result, 'model_dump_json'):
        return result.model_dump_json(indent=2)
    return str(result)
