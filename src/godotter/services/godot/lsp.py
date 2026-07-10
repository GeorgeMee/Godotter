from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LspDiagnostic:
    path: str
    line: int
    column: int
    severity: str
    message: str
    source: str = 'godot-lsp'


@dataclass(frozen=True, slots=True)
class LspStatus:
    configured: bool
    available: bool
    enabled: bool
    reason: str
    capabilities: list[str] = field(default_factory=list)


class GodotLspClient:
    """Placeholder boundary for future Godot language-server integration."""

    def __init__(self, workspace_root: Path, godot_path: str | None = None) -> None:
        self.workspace_root = workspace_root.resolve()
        self.godot_path = godot_path

    def status(self) -> LspStatus:
        return detect_lsp_status(self.workspace_root, self.godot_path)


def detect_lsp_status(workspace_root: Path, godot_path: str | None = None) -> LspStatus:
    project_file = workspace_root.resolve() / 'project.godot'
    if not project_file.exists():
        return LspStatus(
            configured=bool(godot_path),
            available=False,
            enabled=False,
            reason='project_godot_not_found',
            capabilities=[],
        )
    if not godot_path:
        return LspStatus(
            configured=False,
            available=False,
            enabled=False,
            reason='godot_path_not_configured',
            capabilities=[],
        )
    return LspStatus(
        configured=True,
        available=False,
        enabled=False,
        reason='godot_lsp_client_not_implemented',
        capabilities=[],
    )

