"""Runtime adapters for Godot and local process execution."""

from godotter.services.godot.doctor import DoctorReport, run_doctor
from godotter.services.godot.builds import (
    BuildArtifact,
    BuildReport,
    ExportDoctorReport,
    ExportPreset,
    list_build_reports,
    run_export_build,
    run_export_doctor,
)
from godotter.services.godot.runner import GodotRunResult, GodotRunner
from godotter.services.godot.lsp import GodotLspClient, LspDiagnostic, LspStatus, detect_lsp_status
from godotter.services.godot.project_info import ProjectInfo, load_project_info
from godotter.services.godot.scene_parser import (
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
from godotter.services.godot.uid_tools import UidFixChange, UidFixResult, fix_uid_paths, scan_uid_map
from godotter.services.godot.verify import (
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
    'GodotLspClient',
    'LspDiagnostic',
    'LspStatus',
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
    'detect_lsp_status',
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
