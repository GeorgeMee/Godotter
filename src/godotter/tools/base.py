from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from godotter.config import Settings
from godotter.context import Memory


@dataclass(slots=True)
class ToolContext:
    settings: Settings
    workspace_root: Path
    memory: Memory | None = None

    def resolve_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        resolved = candidate if candidate.is_absolute() else self.workspace_root / candidate
        resolved = resolved.resolve()
        if self.workspace_root not in resolved.parents and resolved != self.workspace_root:
            raise ValueError(f'Path escapes workspace: {raw_path}')
        return resolved


class Tool(ABC):
    name: str
    description: str
    input_schema: dict[str, Any]
    plan_safe: bool = True

    def definition(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'description': self.description,
            'input_schema': self.input_schema,
        }

    @abstractmethod
    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        raise NotImplementedError