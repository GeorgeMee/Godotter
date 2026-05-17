from __future__ import annotations

from typing import Any

from godotter.runtime import (
    DoctorReport,
    GodotRunResult,
    GodotRunner,
    ParsedScene,
    UidFixResult,
    atomic_write,
    filename_to_node_name,
    fix_uid_paths,
    generate_minimal_scene,
    generate_uid,
    load_project_info,
    parse_scene,
    run_doctor,
)
from godotter.tools.base import Tool, ToolContext


class ProjectInfoTool(Tool):
    name = 'project_info'
    description = 'Read project metadata from project.godot and count scripts and scenes.'
    input_schema = {
        'type': 'object',
        'properties': {},
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        info = load_project_info(context.workspace_root)
        autoloads = ', '.join(info.autoloads) if info.autoloads else '(none)'
        lines = [
            f'name={info.name}',
            f'main_scene={info.main_scene or "(none)"}',
            f'autoloads={autoloads}',
            f'script_count={info.script_count}',
            f'scene_count={info.scene_count}',
        ]
        return '\n'.join(lines)


class SceneCreateTool(Tool):
    name = 'scene_create'
    plan_safe = False
    description = 'Create a minimal Godot .tscn scene with a generated scene UID.'
    input_schema = {
        'type': 'object',
        'properties': {
            'path': {'type': 'string', 'description': 'Scene path relative to the workspace root.'},
            'root_type': {'type': 'string', 'description': 'Root node type.', 'default': 'Node2D'},
            'root_name': {'type': 'string', 'description': 'Optional root node name.'},
            'script_path': {'type': 'string', 'description': 'Optional res:// script path to attach.'},
            'force': {'type': 'boolean', 'description': 'Overwrite existing file if true.', 'default': False},
        },
        'required': ['path'],
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        path = context.resolve_path(str(kwargs['path']))
        root_type = str(kwargs.get('root_type', 'Node2D'))
        root_name = str(kwargs.get('root_name') or filename_to_node_name(path.name))
        script_path = kwargs.get('script_path')
        force = _to_bool(kwargs.get('force', False))

        if path.exists() and not force:
            return f'Error: File already exists: {path.relative_to(context.workspace_root)}'

        uid = generate_uid()
        content = generate_minimal_scene(root_type, root_name, uid, str(script_path) if script_path else None)
        atomic_write(path, content)
        return '\n'.join([
            f'path={path.relative_to(context.workspace_root).as_posix()}',
            f'root_type={root_type}',
            f'root_name={root_name}',
            f'uid={uid}',
        ])


class SceneInspectTool(Tool):
    name = 'scene_inspect'
    description = 'Inspect a Godot scene and report its header, ext_resources, nodes, and connections.'
    input_schema = {
        'type': 'object',
        'properties': {
            'path': {'type': 'string', 'description': 'Scene path relative to the workspace root.'},
        },
        'required': ['path'],
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        path = context.resolve_path(str(kwargs['path']))
        parsed = parse_scene(path)
        return _render_scene(parsed)


class SceneValidateTool(Tool):
    name = 'scene_validate'
    description = 'Validate a Godot scene for missing external resources and malformed nodes.'
    input_schema = {
        'type': 'object',
        'properties': {
            'path': {'type': 'string', 'description': 'Scene path relative to the workspace root.'},
        },
        'required': ['path'],
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        path = context.resolve_path(str(kwargs['path']))
        parsed = parse_scene(path)
        issues: list[str] = []

        for resource in parsed.ext_resources:
            if resource.path.startswith('res://'):
                target = context.workspace_root / resource.path.removeprefix('res://')
                if not target.exists():
                    issues.append(f'error missing_resource id={resource.id} path={resource.path}')

        for node in parsed.nodes:
            if not node.node_type and node.parent is not None and node.instance is None:
                issues.append(f'warning missing_type node={node.name}')

        if not issues:
            return 'OK no issues'
        return '\n'.join(issues)


class ScriptLintTool(Tool):
    name = 'script_lint'
    description = 'Run Godot headless linting for a single GDScript file or the whole project.'
    input_schema = {
        'type': 'object',
        'properties': {
            'path': {
                'type': 'string',
                'description': 'Optional GDScript path relative to the workspace root. If omitted, lint the whole project.',
            },
            'timeout': {
                'type': 'integer',
                'description': 'Timeout in seconds.',
                'default': 60,
            },
        },
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        runner = _build_godot_runner(context)
        timeout = _to_int(kwargs.get('timeout'), default=60)
        raw_path = kwargs.get('path')
        if raw_path:
            path = context.resolve_path(str(raw_path))
            relative = path.relative_to(context.workspace_root).as_posix()
            result = runner.lint_script(relative, timeout=timeout)
            target = relative
        else:
            result = runner.lint_project(timeout=timeout)
            target = '(project)'
        return _render_run_result('script_lint', result, target=target)


class HeadlessRunTool(Tool):
    name = 'headless_run'
    plan_safe = False
    description = 'Run the Godot project in headless mode, optionally with a specific scene.'
    input_schema = {
        'type': 'object',
        'properties': {
            'scene': {
                'type': 'string',
                'description': 'Optional res:// scene path to run.',
            },
            'timeout': {
                'type': 'integer',
                'description': 'Timeout in seconds.',
                'default': 60,
            },
        },
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        runner = _build_godot_runner(context)
        timeout = _to_int(kwargs.get('timeout'), default=60)
        scene = kwargs.get('scene')
        result = runner.run_project(timeout=timeout, scene=str(scene) if scene else None)
        return _render_run_result('headless_run', result, target=str(scene or '(project)'))


class RuntimeDoctorTool(Tool):
    name = 'runtime_doctor'
    description = 'Check Godot binary configuration and basic project metadata for headless execution.'
    input_schema = {
        'type': 'object',
        'properties': {
            'timeout': {
                'type': 'integer',
                'description': 'Timeout in seconds for the Godot version probe.',
                'default': 15,
            },
        },
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        timeout = _to_int(kwargs.get('timeout'), default=15)
        report = run_doctor(context.workspace_root, context.settings.godot_path, timeout=timeout)
        return _render_doctor_report(report)


class UidFixTool(Tool):
    name = 'uid_fix'
    plan_safe = False
    description = 'Fix stale ext_resource paths in scenes and resources by scanning .uid files.'
    input_schema = {
        'type': 'object',
        'properties': {
            'dry_run': {
                'type': 'boolean',
                'description': 'When true, only report changes without writing files.',
                'default': True,
            },
        },
    }

    def execute(self, context: ToolContext, **kwargs: Any) -> str:
        dry_run = _to_bool(kwargs.get('dry_run', True))
        result = fix_uid_paths(context.workspace_root, dry_run=dry_run)
        return _render_uid_fix_result(result, dry_run=dry_run, workspace_root=context.workspace_root)


def _render_scene(parsed: ParsedScene) -> str:
    lines: list[str] = []
    if parsed.header:
        lines.append(f'uid={parsed.header.uid or "(none)"}')
        lines.append(f'format={parsed.header.format}')
        lines.append(f'load_steps={parsed.header.load_steps if parsed.header.load_steps is not None else "(none)"}')
    else:
        lines.append('uid=(none)')
    lines.append(f'ext_resources={len(parsed.ext_resources)}')
    for resource in parsed.ext_resources:
        lines.append(f'ext_resource id={resource.id} type={resource.resource_type} path={resource.path}')
    lines.append(f'nodes={len(parsed.nodes)}')
    for node in parsed.nodes:
        lines.append(f'node name={node.name} type={node.node_type or "(none)"} parent={node.parent or "."}')
        for prop in node.properties:
            lines.append(f'property node={node.name} {prop.key}={prop.value}')
    lines.append(f'connections={len(parsed.connections)}')
    for connection in parsed.connections:
        lines.append(
            f'connection from={connection.from_node} signal={connection.signal} to={connection.to_node} method={connection.method}'
        )
    return '\n'.join(lines)


def _render_doctor_report(report: DoctorReport) -> str:
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


def _render_uid_fix_result(result: UidFixResult, *, dry_run: bool, workspace_root) -> str:
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


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def _to_int(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return default
        return int(stripped)
    return int(value)


def _build_godot_runner(context: ToolContext) -> GodotRunner:
    if not context.settings.godot_path:
        raise ValueError('GODOT_PATH is not configured')
    return GodotRunner(context.settings.godot_path, context.workspace_root)


def _render_run_result(command: str, result: GodotRunResult, *, target: str) -> str:
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
