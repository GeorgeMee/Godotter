from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from godotter.utils.textio import read_text_utf8


IGNORED_PARTS = {'.git', '.venv', '__pycache__', 'References', '.godotter'}


@dataclass(slots=True)
class ReadFileResult:
    path: str
    content: str
    line_count: int


@dataclass(slots=True)
class FileEntry:
    path: str
    kind: str


@dataclass(slots=True)
class FileSearchMatch:
    path: str
    line: int
    text: str


class FileService:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def read_file(self, path: str) -> ReadFileResult:
        target = self.resolve_path(path)
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(path)
        content = _normalize_newlines(read_text_utf8(target))
        return ReadFileResult(
            path=target.relative_to(self.workspace_root).as_posix(),
            content=content,
            line_count=len(content.splitlines()),
        )

    def list_files(self, path: str = '.') -> list[FileEntry]:
        root = self.resolve_path(path)
        if not root.exists():
            raise FileNotFoundError(path)
        entries: list[FileEntry] = []
        for item in sorted(root.rglob('*')):
            relative = item.relative_to(self.workspace_root)
            if any(part in IGNORED_PARTS for part in relative.parts):
                continue
            entries.append(
                FileEntry(
                    path=relative.as_posix() + ('/' if item.is_dir() else ''),
                    kind='directory' if item.is_dir() else 'file',
                )
            )
        return entries

    def search_code(self, query: str, path: str = '.') -> list[FileSearchMatch]:
        lowered = query.lower()
        root = self.resolve_path(path)
        matches: list[FileSearchMatch] = []
        for item in sorted(root.rglob('*')):
            if not item.is_file():
                continue
            relative = item.relative_to(self.workspace_root)
            if any(part in IGNORED_PARTS for part in relative.parts):
                continue
            try:
                lines = _normalize_newlines(read_text_utf8(item)).splitlines()
            except OSError:
                continue
            for index, line in enumerate(lines, start=1):
                if lowered in line.lower():
                    matches.append(
                        FileSearchMatch(
                            path=relative.as_posix(),
                            line=index,
                            text=line.strip(),
                        )
                    )
        return matches

    def resolve_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        resolved = candidate if candidate.is_absolute() else self.workspace_root / candidate
        resolved = resolved.resolve()
        if self.workspace_root not in resolved.parents and resolved != self.workspace_root:
            raise ValueError(f'Path escapes workspace: {raw_path}')
        return resolved


def _normalize_newlines(text: str) -> str:
    return text.replace('\r\n', '\n').replace('\r', '\n')

