from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from godotter.services.godot.scene_parser import EXT_RESOURCE_RE, atomic_write


UID_VALUE_RE = re.compile(r'uid://[A-Za-z0-9_\-]+')
PATH_ATTR_RE = re.compile(r'path="([^"]*)"')
UID_ATTR_RE = re.compile(r'uid="([^"]*)"')
EXCLUDED_DIRS = {'.git', '.venv', '__pycache__', 'References', '.godotter'}


@dataclass(slots=True)
class UidFixChange:
    file_path: Path
    uid: str
    old_path: str
    new_path: str


@dataclass(slots=True)
class UidFixResult:
    uid_entries: int
    scanned_files: int
    updated_files: int
    changes: list[UidFixChange]


def scan_uid_map(workspace_root: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for uid_file in _iter_files(workspace_root, '*.uid'):
        uid_value = _read_uid_value(uid_file)
        if not uid_value:
            continue
        resource_path = uid_file.with_suffix('')
        if not resource_path.exists():
            continue
        relative = resource_path.relative_to(workspace_root).as_posix()
        mapping[uid_value] = f'res://{relative}'
    return mapping


def fix_uid_paths(workspace_root: Path, dry_run: bool = True) -> UidFixResult:
    uid_map = scan_uid_map(workspace_root)
    changes: list[UidFixChange] = []
    updated_files = 0
    scanned_files = 0

    for pattern in ('*.tscn', '*.tres'):
        for target in _iter_files(workspace_root, pattern):
            scanned_files += 1
            original = target.read_text(encoding='utf-8')
            rewritten, file_changes = _rewrite_uid_paths(original, target, uid_map)
            if not file_changes:
                continue
            changes.extend(file_changes)
            updated_files += 1
            if not dry_run:
                atomic_write(target, rewritten)

    return UidFixResult(
        uid_entries=len(uid_map),
        scanned_files=scanned_files,
        updated_files=updated_files,
        changes=changes,
    )


def _rewrite_uid_paths(content: str, file_path: Path, uid_map: dict[str, str]) -> tuple[str, list[UidFixChange]]:
    lines = content.splitlines(keepends=True)
    rewritten_lines: list[str] = []
    changes: list[UidFixChange] = []

    for line in lines:
        stripped = line.strip()
        if not EXT_RESOURCE_RE.match(stripped):
            rewritten_lines.append(line)
            continue
        uid_match = UID_ATTR_RE.search(stripped)
        path_match = PATH_ATTR_RE.search(stripped)
        if not uid_match or not path_match:
            rewritten_lines.append(line)
            continue
        uid_value = uid_match.group(1)
        current_path = path_match.group(1)
        expected_path = uid_map.get(uid_value)
        if not expected_path or expected_path == current_path:
            rewritten_lines.append(line)
            continue
        updated_line = line.replace(f'path="{current_path}"', f'path="{expected_path}"', 1)
        rewritten_lines.append(updated_line)
        changes.append(
            UidFixChange(
                file_path=file_path,
                uid=uid_value,
                old_path=current_path,
                new_path=expected_path,
            )
        )

    return ''.join(rewritten_lines), changes


def _read_uid_value(path: Path) -> str | None:
    content = path.read_text(encoding='utf-8').strip()
    match = UID_VALUE_RE.search(content)
    if match:
        return match.group(0)
    return None


def _iter_files(workspace_root: Path, pattern: str):
    for path in workspace_root.rglob(pattern):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.is_file():
            yield path
