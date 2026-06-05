"""Runtime adapters for Godot and local process execution."""

from godotter.runtime.doctor import DoctorReport, run_doctor
from godotter.runtime.builds import (
    BuildArtifact,
    BuildReport,
    ExportDoctorReport,
    ExportPreset,
    list_build_reports,
    run_export_build,
    run_export_doctor,
)
from godotter.runtime.godot_runner import GodotRunResult, GodotRunner
from godotter.runtime.project_info import ProjectInfo, load_project_info
from godotter.runtime.scene_parser import (
    EXT_RESOURCE_RE,
    ExtResource,
    ParsedScene,
    SceneConnection,
    SceneHeader,
    SceneNode,
    SceneProperty,
    atomic_write,
    filename_to_node_name,
    generate_minimal_scene,
    generate_uid,
    parse_scene,
    parse_scene_header,
    parse_scene_text,
)
from godotter.runtime.uid_tools import UidFixChange, UidFixResult, fix_uid_paths, scan_uid_map
from godotter.runtime.verify import (
    default_verify_commands,
    latest_verify_report_path,
    load_latest_verify_report,
    run_verify,
)

__all__ = [
    'DoctorReport',
    'BuildArtifact',
    'BuildReport',
    'ExportDoctorReport',
    'ExportPreset',
    'EXT_RESOURCE_RE',
    'ExtResource',
    'GodotRunResult',
    'GodotRunner',
    'ParsedScene',
    'ProjectInfo',
    'SceneConnection',
    'SceneHeader',
    'SceneNode',
    'SceneProperty',
    'UidFixChange',
    'UidFixResult',
    'atomic_write',
    'filename_to_node_name',
    'fix_uid_paths',
    'default_verify_commands',
    'generate_minimal_scene',
    'generate_uid',
    'latest_verify_report_path',
    'load_project_info',
    'list_build_reports',
    'load_latest_verify_report',
    'parse_scene',
    'parse_scene_header',
    'parse_scene_text',
    'run_doctor',
    'run_export_build',
    'run_export_doctor',
    'run_verify',
    'scan_uid_map',
]
