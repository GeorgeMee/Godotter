from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from godotter.runtime import load_project_info, parse_scene


_IGNORED_PARTS = {'.git', '.venv', '.godot', '.godotter', '__pycache__'}


@dataclass(slots=True)
class ProjectSummary:
    project_name: str
    workspace_root: str
    main_scene: str | None
    main_scene_resolved: str | None = None
    main_scene_warning: str | None = None
    scripts: list[str] = field(default_factory=list)
    scenes: list[str] = field(default_factory=list)
    main_scene_tree: list[str] = field(default_factory=list)
    main_scene_connections: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)


def build_project_summary(workspace_root: Path) -> ProjectSummary | None:
    root = workspace_root.resolve()
    project_file = root / 'project.godot'
    if not project_file.exists():
        return None

    info = load_project_info(root)

    scripts = _collect_relative(root, root.rglob('*.gd'))
    scenes = _collect_relative(root, root.rglob('*.tscn'))

    main_scene_tree: list[str] = []
    main_scene_connections: list[str] = []
    main_scene_resolved: str | None = None
    main_scene_warning: str | None = None
    if info.main_scene:
        scene_path = _resolve_scene_path(root, info.main_scene)
        if scene_path is not None and scene_path.exists():
            main_scene_resolved = scene_path.relative_to(root).as_posix()
            tree, conns = _parse_main_scene(root, info.main_scene)
            main_scene_tree = tree
            main_scene_connections = conns
            if not main_scene_tree:
                main_scene_warning = (
                    f'Main scene {info.main_scene} resolved to {main_scene_resolved} '
                    f'but has no child nodes; may be an empty placeholder.'
                )
        else:
            main_scene_warning = f'Main scene {info.main_scene} could not be resolved to a file on disk.'

    return ProjectSummary(
        project_name=info.name,
        workspace_root=root.as_posix(),
        main_scene=info.main_scene,
        main_scene_resolved=main_scene_resolved,
        main_scene_warning=main_scene_warning,
        scripts=scripts,
        scenes=scenes,
        main_scene_tree=main_scene_tree,
        main_scene_connections=main_scene_connections,
        constraints=_default_constraints(),
    )


def render_project_summary(summary: ProjectSummary) -> str:
    parts: list[str] = []
    parts.append(f'Project: {summary.project_name}')
    parts.append(f'Workspace: {summary.workspace_root}')
    if summary.main_scene:
        parts.append(f'Main scene: {summary.main_scene}')
    if summary.main_scene_resolved:
        parts.append(f'Main scene resolved: {summary.main_scene_resolved}')
    if summary.main_scene_warning:
        parts.append(f'WARNING: {summary.main_scene_warning}')

    if summary.scenes:
        parts.append(f'Scenes ({len(summary.scenes)}): {", ".join(summary.scenes[:20])}')
    if summary.scripts:
        parts.append(f'Scripts ({len(summary.scripts)}): {", ".join(summary.scripts[:20])}')

    if summary.main_scene_tree:
        parts.append('Main scene node tree:')
        for line in summary.main_scene_tree:
            parts.append(f'  {line}')

    if summary.main_scene_connections:
        parts.append('Main scene connections:')
        for line in summary.main_scene_connections:
            parts.append(f'  {line}')

    if summary.constraints:
        parts.append('Project constraints:')
        for c in summary.constraints:
            parts.append(f'- {c}')

    return '\n'.join(parts)


def _parse_main_scene(workspace_root: Path, main_scene_ref: str) -> tuple[list[str], list[str]]:
    scene_path = _resolve_scene_path(workspace_root, main_scene_ref)
    if scene_path is None or not scene_path.exists():
        return [], []

    try:
        parsed = parse_scene(scene_path)
    except Exception:
        return [], []

    tree_lines = _render_node_tree(parsed.nodes)
    conn_lines = [
        f'{c.from_node} --{c.signal}--> {c.to_node}.{c.method}'
        for c in parsed.connections
    ]
    return tree_lines, conn_lines


def _resolve_scene_path(workspace_root: Path, ref: str) -> Path | None:
    if ref.startswith('uid://'):
        for tscn in workspace_root.rglob('*.tscn'):
            if any(part in _IGNORED_PARTS for part in tscn.parts):
                continue
            try:
                parsed = parse_scene(tscn)
            except Exception:
                continue
            if parsed.header and parsed.header.uid == ref:
                return tscn
        return None
    if ref.startswith('res://'):
        return workspace_root / ref.removeprefix('res://')
    return workspace_root / ref


def _render_node_tree(nodes: list) -> list[str]:
    if not nodes:
        return []

    children_map: dict[str | None, list] = {}
    for node in nodes:
        parent = node.parent if node.parent else None
        children_map.setdefault(parent, []).append(node)

    root_nodes = children_map.get(None, []) + children_map.get('.', [])
    lines: list[str] = []
    _render_subtree(root_nodes, children_map, depth=0, lines=lines)
    return lines


def _render_subtree(
    nodes: list,
    children_map: dict[str | None, list],
    depth: int,
    lines: list[str],
) -> None:
    for node in nodes:
        type_suffix = f' ({node.node_type})' if node.node_type else ''
        instance_suffix = ' [instance]' if node.instance else ''
        prefix = '  ' * depth + ('└─ ' if depth > 0 else '')
        lines.append(f'{prefix}{node.name}{type_suffix}{instance_suffix}')
        child_key = node.name if not node.parent or node.parent == '.' else f'{node.parent}/{node.name}'
        children = children_map.get(child_key, [])
        if children:
            _render_subtree(children, children_map, depth + 1, lines)


def _collect_relative(root: Path, paths) -> list[str]:
    result: list[str] = []
    for p in sorted(paths):
        try:
            rel = p.relative_to(root).as_posix()
        except ValueError:
            continue
        if any(part in _IGNORED_PARTS for part in p.parts):
            continue
        result.append(rel)
    return result


def _default_constraints() -> list[str]:
    return [
        'Levels must have a root Managers node and a Managers/EventBus child.',
        'Prefer structured events via EventBus; avoid implicit get-from-group lookups outside Managers.',
        'Obey Godotter dev-mode directory structure: game/core, game/systems, game/features, game/content, game/levels.',
        'Game code under game/ must never auto-quit, check --scene, or contain test harness logic.',
        'Test harnesses belong under tests/, not game/. They may auto-quit.',
        'Game systems self-initialize tick loops; do not require external callers to start.',
        'Read Docs/Conventions.md in the workspace before creating or modifying files.',
        'Run `godotter runtime verify` after changes; use lower-level runtime validators only for diagnosis.',
    ]
