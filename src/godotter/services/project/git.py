from __future__ import annotations

from pathlib import Path
import subprocess


class GitService:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def status(self) -> str:
        return self._run(['status', '--short'])

    def diff(self, *, cached: bool = False, path: str | None = None) -> str:
        args = ['diff']
        if cached:
            args.append('--cached')
        if path:
            resolved = self.resolve_path(path)
            args.extend(['--', resolved.relative_to(self.workspace_root).as_posix()])
        return self._run(args)

    def log(self, *, limit: int = 5) -> str:
        return self._run(['log', '--oneline', f'-n{max(limit, 1)}'])

    def branch(self) -> str:
        return self._run(['branch', '--list'])

    def resolve_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        resolved = candidate if candidate.is_absolute() else self.workspace_root / candidate
        resolved = resolved.resolve()
        if self.workspace_root not in resolved.parents and resolved != self.workspace_root:
            raise ValueError(f'Path escapes workspace: {raw_path}')
        return resolved

    def _run(self, args: list[str]) -> str:
        if not (self.workspace_root / '.git').exists():
            return 'Error: not a git repository'
        try:
            completed = subprocess.run(
                ['git', *args],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            return 'Error: git executable not found'
        except subprocess.TimeoutExpired:
            return 'Error: git command timed out'
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        if completed.returncode != 0:
            details = stderr or stdout or f'git exited with code {completed.returncode}'
            return f'Error: {details}'
        return stdout or '(empty)'

