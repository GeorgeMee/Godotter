from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import Path
from typing import Any

from unidiff import PatchSet

from godotter.tools.base import Tool, ToolContext
from godotter.utils.textio import read_text_utf8, write_text_utf8


@dataclass(slots=True)
class PatchTarget:
    path: Path
    original_lines: list[str]
    is_new: bool = False
    is_delete: bool = False


class GeneratePatch(Tool):
    name = 'generate_patch'
    description = 'Generate a unified diff for a file update without applying it.'
    input_schema = {
        'type': 'object',
        'properties': {
            'path': {'type': 'string', 'description': 'Target file path relative to the workspace root.'},
            'new_content': {'type': 'string', 'description': 'Full replacement content for the target file.'},
            'old_text': {'type': 'string', 'description': 'Optional exact text to replace.'},
            'new_text': {'type': 'string', 'description': 'Replacement text when old_text is provided.'},
        },
        'required': ['path'],
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        path = context.resolve_path(str(kwargs['path']))
        original = read_text_utf8(path) if path.exists() else ''

        if 'new_content' in kwargs and kwargs['new_content'] is not None:
            updated = str(kwargs['new_content'])
        else:
            old_text = str(kwargs.get('old_text', ''))
            new_text = str(kwargs.get('new_text', ''))
            if not old_text:
                return 'Error: Provide new_content or old_text/new_text.'
            if old_text not in original:
                return f'Error: old_text not found in {path.relative_to(context.workspace_root)}'
            updated = original.replace(old_text, new_text, 1)

        relative = path.relative_to(context.workspace_root).as_posix()
        diff = difflib.unified_diff(
            original.splitlines(True),
            updated.splitlines(True),
            fromfile=f'a/{relative}',
            tofile=f'b/{relative}',
        )
        rendered = ''.join(diff)
        return rendered if rendered else 'No changes.'


class ApplyPatch(Tool):
    name = 'apply_patch'
    plan_safe = False
    description = 'Apply a unified diff patch inside the workspace.'
    input_schema = {
        'type': 'object',
        'properties': {
            'patch': {'type': 'string', 'description': 'Unified diff text to apply.'},
            'patch_path': {'type': 'string', 'description': 'Optional path to a patch file relative to the workspace root.'},
        },
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        patch_text = self._load_patch_text(context, kwargs)
        if not patch_text.strip():
            return 'Error: Patch content is empty.'

        patch_set = PatchSet(patch_text.splitlines(True))
        if not patch_set:
            return 'Error: No files found in patch.'

        applied: list[str] = []
        for patched_file in patch_set:
            target = self._load_target(context, patched_file)
            result = self._apply_file_patch(patched_file, target)
            relative = target.path.relative_to(context.workspace_root)
            if target.is_delete:
                if target.path.exists():
                    target.path.unlink()
            else:
                target.path.parent.mkdir(parents=True, exist_ok=True)
                write_text_utf8(target.path, result)
            applied.append(str(relative))
        return 'Applied patch to: ' + ', '.join(applied)

    def _load_patch_text(self, context: ToolContext, kwargs: dict[str, Any]) -> str:
        if kwargs.get('patch'):
            return _normalize_patch_text(str(kwargs['patch']))
        if kwargs.get('patch_path'):
            patch_path = context.resolve_path(str(kwargs['patch_path']))
            return read_text_utf8(patch_path)
        return ''

    def _load_target(self, context: ToolContext, patched_file: Any) -> PatchTarget:
        if patched_file.is_added_file:
            path = context.resolve_path(_normalize_diff_path(patched_file.target_file))
            return PatchTarget(path=path, original_lines=[], is_new=True)
        if patched_file.is_removed_file:
            path = context.resolve_path(_normalize_diff_path(patched_file.source_file))
            original = read_text_utf8(path).splitlines(True)
            return PatchTarget(path=path, original_lines=original, is_delete=True)

        path = context.resolve_path(_normalize_diff_path(patched_file.source_file))
        original = read_text_utf8(path).splitlines(True)
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


def _normalize_diff_path(path: str) -> str:
    if path.startswith('a/') or path.startswith('b/'):
        return path[2:]
    return path


def _normalize_patch_text(patch_text: str) -> str:
    if '\\n' in patch_text and '\n' not in patch_text:
        return patch_text.encode('utf-8').decode('unicode_escape')
    return patch_text

