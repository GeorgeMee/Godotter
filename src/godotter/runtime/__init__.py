"""Runtime adapters for Godot and local process execution."""

from godotter.runtime.doctor import DoctorReport, run_doctor
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

__all__ = [
    'DoctorReport',
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
    'generate_minimal_scene',
    'generate_uid',
    'load_project_info',
    'parse_scene',
    'parse_scene_header',
    'parse_scene_text',
    'run_doctor',
    'scan_uid_map',
]
