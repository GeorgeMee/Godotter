from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from godotter.services.godot import GodotRunResult, GodotRunner


@dataclass(frozen=True, slots=True)
class RunTextResult:
    command: str
    target: str
    exit_code: int
    timed_out: bool
    duration_ms: int
    stdout: str
    stderr: str


class RunService:
    def __init__(self, workspace_root: Path, godot_path: str | None = None) -> None:
        self.workspace_root = workspace_root.resolve()
        self.godot_path = godot_path

    def headless_run_text(self, scene: str | None = None, *, timeout: int = 60) -> str:
        if not self.godot_path:
            raise ValueError('GODOT_PATH is not configured')
        runner = GodotRunner(self.godot_path, self.workspace_root)
        result = runner.run_project(timeout=timeout, scene=scene)
        target = str(scene or '(project)')
        return render_run_result('headless_run', target, result)


def render_run_result(command: str, target: str, result: GodotRunResult) -> str:
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

