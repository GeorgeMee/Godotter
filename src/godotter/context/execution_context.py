from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from godotter.config import Settings
from godotter.context.memory import Memory


@dataclass(slots=True)
class ExecutionContext:
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
