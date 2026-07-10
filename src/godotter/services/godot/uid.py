from __future__ import annotations

from pathlib import Path

from godotter.services.godot import UidFixResult, fix_uid_paths


class UidService:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def scan_text(self) -> str:
        result = fix_uid_paths(self.workspace_root, dry_run=True)
        return render_uid_result(result, dry_run=True, workspace_root=self.workspace_root)

    def fix_apply_text(self) -> str:
        result = fix_uid_paths(self.workspace_root, dry_run=False)
        return render_uid_result(result, dry_run=False, workspace_root=self.workspace_root)


def render_uid_result(result: UidFixResult, *, dry_run: bool, workspace_root: Path) -> str:
    lines = [
        f'dry_run={str(dry_run).lower()}',
        f'uid_entries={result.uid_entries}',
        f'scanned_files={result.scanned_files}',
        f'updated_files={result.updated_files}',
        f'changes={len(result.changes)}',
    ]
    for change in result.changes:
        relative = change.file_path.relative_to(workspace_root).as_posix()
        lines.append(
            f'change file={relative} uid={change.uid} old_path={change.old_path} new_path={change.new_path}'
        )
    return '\n'.join(lines)

