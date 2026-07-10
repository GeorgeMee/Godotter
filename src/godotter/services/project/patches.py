from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import Path
from typing import Any

from unidiff import PatchSet

from godotter.utils.textio import read_text_utf8, write_text_utf8


@dataclass(slots=True)
class PatchApplyResult:
    applied_paths: list[str]


@dataclass(slots=True)
class PatchGenerateResult:
    patch: str


@dataclass(slots=True)
class PatchTarget:
    path: Path
    original_lines: list[str]
    is_delete: bool = False


class PatchService:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def generate_file_patch(self, path: str, new_content: str) -> PatchGenerateResult:
        target = self.resolve_path(path)
        original = _normalize_newlines(read_text_utf8(target)) if target.exists() else ''
        patch = self._generate_patch(target, original, new_content)
        self._validate_generated_patch(target, original, new_content, patch)
        return PatchGenerateResult(patch=patch)

    def generate_text_replace_patch(self, path: str, old_text: str, new_text: str) -> PatchGenerateResult:
        target = self.resolve_path(path)
        original = _normalize_newlines(read_text_utf8(target)) if target.exists() else ''
        if not old_text:
            raise ValueError('old_text is required')
        if old_text not in original:
            relative = target.relative_to(self.workspace_root).as_posix()
            raise ValueError(f'old_text not found in {relative}')
        updated = original.replace(old_text, new_text, 1)
        patch = self._generate_patch(target, original, updated)
        self._validate_generated_patch(target, original, updated, patch)
        return PatchGenerateResult(patch=patch)

    def apply_unified_patch(self, patch: str) -> PatchApplyResult:
        patch_text = _normalize_patch_text(patch)
        if not patch_text.strip():
            raise ValueError('Patch content is empty')

        patch_set = PatchSet(patch_text.splitlines(True))
        if not patch_set:
            raise ValueError('No files found in patch')

        applied: list[str] = []
        for patched_file in patch_set:
            target = self._load_target(patched_file)
            result = self._apply_file_patch(patched_file, target)
            relative = target.path.relative_to(self.workspace_root).as_posix()
            if target.is_delete:
                if target.path.exists():
                    target.path.unlink()
            else:
                target.path.parent.mkdir(parents=True, exist_ok=True)
                write_text_utf8(target.path, result)
            applied.append(relative)
        return PatchApplyResult(applied_paths=applied)

    def apply_patch_file(self, path: str) -> PatchApplyResult:
        patch_path = self.resolve_path(path)
        return self.apply_unified_patch(read_text_utf8(patch_path))

    def replace_file(self, path: str, new_content: str) -> PatchApplyResult:
        generated = self.generate_file_patch(path, new_content)
        return self.apply_unified_patch(generated.patch)

    def replace_text(self, path: str, old_text: str, new_text: str) -> PatchApplyResult:
        generated = self.generate_text_replace_patch(path, old_text, new_text)
        return self.apply_unified_patch(generated.patch)

    def resolve_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path)
        resolved = candidate if candidate.is_absolute() else self.workspace_root / candidate
        resolved = resolved.resolve()
        if self.workspace_root not in resolved.parents and resolved != self.workspace_root:
            raise ValueError(f'Path escapes workspace: {raw_path}')
        return resolved

    def _generate_patch(self, target: Path, original: str, updated: str) -> str:
        relative = target.relative_to(self.workspace_root).as_posix()
        diff = difflib.unified_diff(
            _normalize_newlines(original).splitlines(True),
            _normalize_newlines(updated).splitlines(True),
            fromfile=f'a/{relative}',
            tofile=f'b/{relative}',
        )
        rendered = ''.join(diff)
        return rendered if rendered else 'No changes.'

    def _validate_generated_patch(self, target: Path, original: str, updated: str, patch: str) -> None:
        normalized_patch = _normalize_patch_text(patch)
        try:
            patch_set = PatchSet(normalized_patch.splitlines(True))
        except Exception as exc:
            raise ValueError(f'Generated patch is invalid: {exc}') from exc
        if not patch_set:
            raise ValueError('Generated patch is invalid: no files found')
        if len(patch_set) != 1:
            raise ValueError('Generated patch is invalid: expected a single file')
        patched_file = next(iter(patch_set))
        original_lines = _normalize_newlines(original).splitlines(True)
        simulated = self._apply_file_patch(
            patched_file,
            PatchTarget(path=target, original_lines=original_lines),
        )
        if _normalize_newlines(simulated) != _normalize_newlines(updated):
            raise ValueError('Generated patch does not round-trip against source content')

    def _load_target(self, patched_file: Any) -> PatchTarget:
        if patched_file.is_added_file:
            path = self.resolve_path(_normalize_diff_path(patched_file.target_file))
            return PatchTarget(path=path, original_lines=[])
        if patched_file.is_removed_file:
            path = self.resolve_path(_normalize_diff_path(patched_file.source_file))
            original = _normalize_newlines(read_text_utf8(path)).splitlines(True)
            return PatchTarget(path=path, original_lines=original, is_delete=True)
        path = self.resolve_path(_normalize_diff_path(patched_file.source_file))
        original = _normalize_newlines(read_text_utf8(path)).splitlines(True)
        return PatchTarget(path=path, original_lines=original)

    def _apply_file_patch(self, patched_file: Any, target: PatchTarget) -> str:
        original = target.original_lines
        output: list[str] = []
        source_index = 0
        for hunk in patched_file:
            start = max(hunk.source_start - 1, 0)
            output.extend(original[source_index:start])
            source_index = start
            for line in hunk:
                value = line.value
                if line.is_context:
                    self._assert_line_matches(original, source_index, value, target.path)
                    output.append(original[source_index])
                    source_index += 1
                elif line.is_removed:
                    self._assert_line_matches(original, source_index, value, target.path)
                    source_index += 1
                elif line.is_added:
                    output.append(value)
        output.extend(original[source_index:])
        return ''.join(output)

    def _assert_line_matches(self, original: list[str], index: int, expected: str, path: Path) -> None:
        if index >= len(original):
            raise ValueError(f'Patch exceeds file length for {path.name}')
        if original[index] != expected:
            raise ValueError(f'Patch context mismatch for {path.name} at line {index + 1}')


def _normalize_newlines(text: str) -> str:
    return text.replace('\r\n', '\n').replace('\r', '\n')


def _normalize_diff_path(path: str) -> str:
    if path.startswith('a/') or path.startswith('b/'):
        return path[2:]
    return path


def _normalize_patch_text(patch_text: str) -> str:
    if '\\n' in patch_text and '\n' not in patch_text:
        return patch_text.encode('utf-8').decode('unicode_escape')
    return patch_text
