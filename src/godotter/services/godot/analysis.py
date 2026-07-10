from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from godotter.services.godot import GodotRunResult, ParsedScene, parse_scene
from godotter.services.godot.runner import GodotRunner
from godotter.services.godot.lsp import LspStatus, detect_lsp_status


@dataclass(frozen=True, slots=True)
class AnalysisStatus:
    lsp: LspStatus
    fallbacks: list[str]

    def to_dict(self) -> dict[str, object]:
        return {
            'lsp': asdict(self.lsp),
            'fallbacks': list(self.fallbacks),
        }


class AnalysisService:
    def __init__(self, workspace_root: Path, godot_path: str | None = None) -> None:
        self.workspace_root = workspace_root.resolve()
        self.godot_path = godot_path

    def status(self) -> AnalysisStatus:
        return AnalysisStatus(
            lsp=detect_lsp_status(self.workspace_root, self.godot_path),
            fallbacks=[
                'project_info',
                'scene_parser',
                'runtime_validators',
                'godot_headless_lint',
            ],
        )

    def inspect_scene_text(self, path: str) -> str:
        return _render_scene(parse_scene(self._resolve_path(path)))

    def validate_scene_text(self, path: str) -> str:
        parsed = parse_scene(self._resolve_path(path))
        issues: list[str] = []
        for resource in parsed.ext_resources:
            if resource.path.startswith('res://'):
                target = self.workspace_root / resource.path.removeprefix('res://')
                if not target.exists():
                    issues.append(f'error missing_resource id={resource.id} path={resource.path}')
        for node in parsed.nodes:
            if not node.node_type and node.parent is not None and node.instance is None:
                issues.append(f'warning missing_type node={node.name}')
        return '\n'.join(issues) if issues else 'OK no issues'

    def lint_script_text(self, path: str | None = None, *, timeout: int = 60) -> str:
        if not self.godot_path:
            raise ValueError('GODOT_PATH is not configured')
        runner = GodotRunner(self.godot_path, self.workspace_root)
        if path:
            resolved = self._resolve_path(path)
            target = resolved.relative_to(self.workspace_root).as_posix()
            result = runner.lint_script(target, timeout=timeout)
        else:
            target = '(project)'
            result = runner.lint_project(timeout=timeout)
        return _render_run_result('script_lint', result, target=target)

    def _resolve_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        resolved = candidate if candidate.is_absolute() else self.workspace_root / candidate
        resolved = resolved.resolve()
        if self.workspace_root not in resolved.parents and resolved != self.workspace_root:
            raise ValueError(f'Path escapes workspace: {raw_path}')
        return resolved


def _render_scene(parsed: ParsedScene) -> str:
    lines: list[str] = []
    if parsed.header:
        lines.append(f'uid={parsed.header.uid or "(none)"}')
        lines.append(f'format={parsed.header.format}')
        lines.append(f'load_steps={parsed.header.load_steps if parsed.header.load_steps is not None else "(none)"}')
    else:
        lines.append('uid=(none)')
    lines.append(f'ext_resources={len(parsed.ext_resources)}')
    for resource in parsed.ext_resources:
        lines.append(f'ext_resource id={resource.id} type={resource.resource_type} path={resource.path}')
    lines.append(f'nodes={len(parsed.nodes)}')
    for node in parsed.nodes:
        lines.append(f'node name={node.name} type={node.node_type or "(none)"} parent={node.parent or "."}')
        for prop in node.properties:
            lines.append(f'property node={node.name} {prop.key}={prop.value}')
    lines.append(f'connections={len(parsed.connections)}')
    for connection in parsed.connections:
        lines.append(
            f'connection from={connection.from_node} signal={connection.signal} to={connection.to_node} method={connection.method}'
        )
    return '\n'.join(lines)


def _render_run_result(command: str, result: GodotRunResult, *, target: str) -> str:
    lines = [
        f'command={command}',
        f'target={target}',
        f'exit_code={result.exit_code}',
        f'timed_out={str(result.timed_out).lower()}',
        f'duration_ms={result.duration_ms}',
    ]
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    lines.append(f'stdout={stdout or "(empty)"}')
    lines.append(f'stderr={stderr or "(empty)"}')
    return '\n'.join(lines)
