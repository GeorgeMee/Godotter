from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import time


@dataclass(slots=True)
class GodotRunResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int


class GodotRunner:
    def __init__(self, godot_path: str, workspace_root: Path) -> None:
        self.godot_path = godot_path
        self.workspace_root = workspace_root

    def lint_script(self, file_path: str, timeout: int = 30) -> GodotRunResult:
        return self._run(['--headless', '-s', file_path, '--check-only'], timeout=timeout)

    def lint_project(self, timeout: int = 60) -> GodotRunResult:
        return self._run(['--quit'], timeout=timeout)

    def run_project(self, timeout: int = 60, scene: str | None = None) -> GodotRunResult:
        args: list[str] = []
        if scene:
            args.extend(['--scene', scene])
        return self._run(args, timeout=timeout)

    def _run(self, args: list[str], timeout: int) -> GodotRunResult:
        start = time.perf_counter()
        try:
            completed = subprocess.run(
                [self.godot_path, *args],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            duration_ms = int((time.perf_counter() - start) * 1000)
            return GodotRunResult(
                exit_code=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                timed_out=False,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            return GodotRunResult(
                exit_code=-1,
                stdout=exc.stdout or '',
                stderr=exc.stderr or '',
                timed_out=True,
                duration_ms=duration_ms,
            )