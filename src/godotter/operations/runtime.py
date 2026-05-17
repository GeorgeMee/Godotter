from __future__ import annotations

import typer

from godotter.project_registry import ProjectRegistryError, resolve_runtime_target as resolve_runtime_target_from_registry
from godotter.runtime import GodotRunner


def resolve_runtime_target(settings, project: str | None = None):
    try:
        return resolve_runtime_target_from_registry(settings, project=project)
    except ProjectRegistryError as exc:
        raise typer.BadParameter(str(exc)) from exc


def build_runner(settings, project: str | None = None) -> GodotRunner:
    target = resolve_runtime_target(settings, project=project)
    if not target.godot_path:
        raise typer.BadParameter('GODOT_PATH is not configured')
    return GodotRunner(target.godot_path, target.workspace_root)


def format_runtime_result(command: str, target: str, result) -> str:
    stdout = result.stdout.strip() or '(empty)'
    stderr = result.stderr.strip() or '(empty)'
    lines = [
        f'command={command}',
        f'target={target}',
        f'exit_code={result.exit_code}',
        f'timed_out={str(result.timed_out).lower()}',
        f'duration_ms={result.duration_ms}',
        f'stdout={stdout}',
        f'stderr={stderr}',
    ]
    return '\n'.join(lines)


def format_doctor_report(report) -> str:
    lines = [
        f'workspace_root={report.workspace_root}',
        f'project_exists={str(report.project_exists).lower()}',
        f'project_name={report.project_name or "(none)"}',
        f'main_scene={report.main_scene or "(none)"}',
        f'script_count={report.script_count}',
        f'scene_count={report.scene_count}',
        f'godot_configured={str(report.godot_configured).lower()}',
        f'godot_runnable={str(report.godot_runnable).lower()}',
        f'godot_version={report.godot_version or "(none)"}',
        f'godot_error={report.godot_error or "(none)"}',
    ]
    return '\n'.join(lines)


def format_uid_fix_result(result, *, dry_run: bool, workspace_root) -> str:
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
