from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from godotter.runtime.scene_parser import filename_to_node_name, generate_uid
from godotter.utils.textio import write_text_utf8


@dataclass(slots=True)
class SceneScaffoldResult:
    scene_path: Path
    script_path: Path
    uid: str


@dataclass(slots=True)
class SceneOnlyScaffoldResult:
    scene_path: Path
    uid: str


def _resolve_script_path_from_scene(
    workspace_root: Path,
    resolved_scene: Path,
    *,
    layout: str,
) -> Path:
    """
    Map a scene path to its default script path.

    layout:
      - colocated: <dir>/<name>.tscn -> <dir>/<name>.gd
      - split: follow Godotter conventions:
          ui/views/<x>.tscn            -> ui/scripts/<x>.gd
          game/levels/<x>.tscn         -> game/scripts/<x>.gd
          game/content/prefabs/<x>.tscn -> game/content/scripts/<x>.gd
        Otherwise falls back to colocated.
    """
    layout_norm = (layout or '').strip().lower()
    if layout_norm == 'colocated':
        return resolved_scene.with_suffix('.gd')
    if layout_norm != 'split':
        raise ValueError(f'Unsupported layout: {layout}')

    rel = resolved_scene.relative_to(workspace_root)
    parts = list(rel.parts)
    if len(parts) < 2:
        return resolved_scene.with_suffix('.gd')

    # ui/views -> ui/scripts
    if parts[0] == 'ui' and len(parts) >= 3 and parts[1] == 'views':
        target_parts = ['ui', 'scripts', *parts[2:]]
        return (workspace_root / Path(*target_parts)).with_suffix('.gd').resolve()

    # game/levels -> game/scripts
    if parts[0] == 'game' and len(parts) >= 3 and parts[1] == 'levels':
        target_parts = ['game', 'scripts', *parts[2:]]
        return (workspace_root / Path(*target_parts)).with_suffix('.gd').resolve()

    # game/content/prefabs -> game/content/scripts
    if parts[0] == 'game' and len(parts) >= 4 and parts[1] == 'content' and parts[2] == 'prefabs':
        target_parts = ['game', 'content', 'scripts', *parts[3:]]
        return (workspace_root / Path(*target_parts)).with_suffix('.gd').resolve()

    return resolved_scene.with_suffix('.gd')


def _resolve_scene_path(workspace_root: Path, raw: str) -> Path:
    text = (raw or '').strip().strip('"').strip("'")
    if text.startswith('res://'):
        text = text[len('res://') :]
    if not text:
        raise ValueError('scene path is empty')
    if not text.lower().endswith('.tscn'):
        raise ValueError('scene path must end with .tscn')
    return (workspace_root / text).resolve()


def _generate_script_template(scene_stem: str, *, extends_type: str) -> str:
    class_name = filename_to_node_name(scene_stem)
    return '\n'.join(
        [
            f'extends {extends_type}',
            '',
            f'class_name {class_name}',
            '',
            'func _ready() -> void:',
            '\treturn',
            '',
        ]
    )


def _generate_level_scene(
    *,
    uid: str,
    root_name: str,
    root_type: str,
    script_res_path: str,
    managers_res_path: str = 'res://game/core/bootstrap/managers.gd',
    event_bus_res_path: str = 'res://game/core/events/event_bus.gd',
) -> str:
    # Keep it minimal but convention-compliant: LevelRoot + Managers/EventBus.
    return '\n'.join(
        [
            f'[gd_scene load_steps=4 format=3 uid="{uid}"]',
            '',
            f'[ext_resource type="Script" path="{script_res_path}" id="1_script"]',
            f'[ext_resource type="Script" path="{managers_res_path}" id="2_managers"]',
            f'[ext_resource type="Script" path="{event_bus_res_path}" id="3_event_bus"]',
            '',
            f'[node name="{root_name}" type="{root_type}"]',
            'script = ExtResource("1_script")',
            '',
            '[node name="Managers" type="Node" parent="."]',
            'script = ExtResource("2_managers")',
            '',
            '[node name="EventBus" type="Node" parent="Managers"]',
            'script = ExtResource("3_event_bus")',
            '',
        ]
    )


def _generate_minimal_scene_no_script(*, uid: str, root_name: str, root_type: str) -> str:
    return '\n'.join(
        [
            f'[gd_scene format=3 uid="{uid}"]',
            '',
            f'[node name="{root_name}" type="{root_type}"]',
            '',
        ]
    )


def _generate_level_scene_no_script(
    *,
    uid: str,
    root_name: str,
    root_type: str,
    managers_res_path: str = 'res://game/core/bootstrap/managers.gd',
    event_bus_res_path: str = 'res://game/core/events/event_bus.gd',
) -> str:
    return '\n'.join(
        [
            f'[gd_scene load_steps=2 format=3 uid="{uid}"]',
            '',
            f'[ext_resource type="Script" path="{managers_res_path}" id="1_managers"]',
            f'[ext_resource type="Script" path="{event_bus_res_path}" id="2_event_bus"]',
            '',
            f'[node name="{root_name}" type="{root_type}"]',
            '',
            '[node name="Managers" type="Node" parent="."]',
            'script = ExtResource("1_managers")',
            '',
            '[node name="EventBus" type="Node" parent="Managers"]',
            'script = ExtResource("2_event_bus")',
            '',
        ]
    )


def scaffold_scene_with_script(
    *,
    workspace_root: Path,
    kind: str,
    scene_path: str,
    script_path: str | None = None,
    root_type: str | None = None,
    root_name: str | None = None,
    layout: str = 'split',
    force: bool = False,
) -> SceneScaffoldResult:
    resolved_scene = _resolve_scene_path(workspace_root, scene_path)
    if resolved_scene.exists() and not force:
        raise ValueError(f'File already exists: {resolved_scene.relative_to(workspace_root).as_posix()}')

    resolved_script: Path
    if script_path:
        raw = script_path.strip().strip('"').strip("'")
        if raw.startswith('res://'):
            raw = raw[len('res://') :]
        if not raw.lower().endswith('.gd'):
            raise ValueError('script path must end with .gd')
        resolved_script = (workspace_root / raw).resolve()
    else:
        resolved_script = _resolve_script_path_from_scene(workspace_root, resolved_scene, layout=layout)

    if resolved_script.exists() and not force:
        raise ValueError(f'File already exists: {resolved_script.relative_to(workspace_root).as_posix()}')

    kind_norm = (kind or '').strip().lower()
    if kind_norm not in {'level', 'ui', 'prefab'}:
        raise ValueError(f'Unsupported kind: {kind}')

    if kind_norm == 'level':
        default_root_type = 'Node'
        script_extends = 'Node'
    elif kind_norm == 'ui':
        default_root_type = 'Control'
        script_extends = 'Control'
    else:
        default_root_type = 'Node2D'
        script_extends = 'Node2D'

    chosen_root_type = root_type or default_root_type
    chosen_root_name = root_name or filename_to_node_name(resolved_scene.name)

    uid = generate_uid()

    script_text = _generate_script_template(resolved_scene.stem, extends_type=script_extends)
    write_text_utf8(resolved_script, script_text)

    script_res = f'res://{resolved_script.relative_to(workspace_root).as_posix()}'
    if kind_norm == 'level':
        scene_text = _generate_level_scene(
            uid=uid,
            root_name=chosen_root_name,
            root_type=chosen_root_type,
            script_res_path=script_res,
        )
    else:
        scene_text = '\n'.join(
            [
                f'[gd_scene load_steps=2 format=3 uid="{uid}"]',
                '',
                f'[ext_resource type="Script" path="{script_res}" id="1_script"]',
                '',
                f'[node name="{chosen_root_name}" type="{chosen_root_type}"]',
                'script = ExtResource("1_script")',
                '',
            ]
        )
    write_text_utf8(resolved_scene, scene_text)

    return SceneScaffoldResult(scene_path=resolved_scene, script_path=resolved_script, uid=uid)


def scaffold_scene_only(
    *,
    workspace_root: Path,
    kind: str,
    scene_path: str,
    root_type: str | None = None,
    root_name: str | None = None,
    force: bool = False,
) -> SceneOnlyScaffoldResult:
    resolved_scene = _resolve_scene_path(workspace_root, scene_path)
    if resolved_scene.exists() and not force:
        raise ValueError(f'File already exists: {resolved_scene.relative_to(workspace_root).as_posix()}')

    kind_norm = (kind or '').strip().lower()
    if kind_norm not in {'level', 'ui', 'prefab'}:
        raise ValueError(f'Unsupported kind: {kind}')

    if kind_norm == 'level':
        default_root_type = 'Node'
    elif kind_norm == 'ui':
        default_root_type = 'Control'
    else:
        default_root_type = 'Node2D'

    chosen_root_type = root_type or default_root_type
    chosen_root_name = root_name or filename_to_node_name(resolved_scene.name)
    uid = generate_uid()

    if kind_norm == 'level':
        scene_text = _generate_level_scene_no_script(uid=uid, root_name=chosen_root_name, root_type=chosen_root_type)
    else:
        scene_text = _generate_minimal_scene_no_script(uid=uid, root_name=chosen_root_name, root_type=chosen_root_type)
    write_text_utf8(resolved_scene, scene_text)
    return SceneOnlyScaffoldResult(scene_path=resolved_scene, uid=uid)
