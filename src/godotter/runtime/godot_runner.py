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
        return self._run(['--headless', '--quit'], timeout=timeout)

    def run_project(
        self,
        timeout: int = 60,
        scene: str | None = None,
        *,
        headless: bool = False,
    ) -> GodotRunResult:
        args: list[str] = []
        if headless:
            args.append('--headless')
        if scene:
            args.extend(['--scene', scene])
        return self._run(args, timeout=timeout)

    def _run(self, args: list[str], timeout: int) -> GodotRunResult:
        start = time.perf_counter()
        def _decode(value) -> str:
            if value is None:
                return ''
            if isinstance(value, bytes):
                return value.decode('utf-8', errors='replace')
            return str(value)
        try:
            timeout_arg = None if timeout <= 0 else timeout
            completed = subprocess.run(
                [self.godot_path, *args],
                cwd=self.workspace_root,
                capture_output=True,
                timeout=timeout_arg,
            )
            duration_ms = int((time.perf_counter() - start) * 1000)
            return GodotRunResult(
                exit_code=completed.returncode,
                stdout=_decode(completed.stdout),
                stderr=_decode(completed.stderr),
                timed_out=False,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = int((time.perf_counter() - start) * 1000)
            return GodotRunResult(
                exit_code=-1,
                stdout=_decode(exc.stdout),
                stderr=_decode(exc.stderr),
                timed_out=True,
                duration_ms=duration_ms,
            )
