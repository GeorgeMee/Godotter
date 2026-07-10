from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib


class ProjectRegistryError(ValueError):
    pass


@dataclass(slots=True)
class ProjectEntry:
    name: str
    workspace_root: Path
    godot_path: str | None = None
    main_scene: str | None = None
    platform: str | None = None


@dataclass(slots=True)
class ProjectRegistry:
    default_project: str | None
    projects: dict[str, ProjectEntry]


@dataclass(slots=True)
class RuntimeTarget:
    project_name: str | None
    workspace_root: Path
    godot_path: str | None
    main_scene: str | None = None


def load_project_registry(path: Path) -> ProjectRegistry:
    if not path.exists():
        return ProjectRegistry(default_project=None, projects={})
    data = tomllib.loads(path.read_text(encoding='utf-8-sig'))
    projects_data = data.get('projects', {})
    projects: dict[str, ProjectEntry] = {}
    for name, raw in projects_data.items():
        if 'workspace_root' not in raw:
            raise ProjectRegistryError(f'Project {name!r} is missing workspace_root')
        projects[name] = ProjectEntry(
            name=name,
            workspace_root=Path(str(raw['workspace_root'])),
            godot_path=_optional_string(raw.get('godot_path')),
            main_scene=_optional_string(raw.get('main_scene')),
            platform=_optional_string(raw.get('platform')),
        )
    default_project = _optional_string(data.get('default_project'))
    return ProjectRegistry(default_project=default_project, projects=projects)


def resolve_runtime_target(settings, project: str | None = None) -> RuntimeTarget:
    registry = load_project_registry(settings.resolved_project_registry_path)
    # If `--project` looks like a path and exists, treat it as a direct workspace root.
    direct = _optional_string(project)
    if direct:
        try:
            direct_path = Path(direct)
            if direct_path.exists():
                return RuntimeTarget(
                    project_name=None,
                    workspace_root=direct_path.resolve(),
                    godot_path=settings.godot_path,
                    main_scene=None,
                )
        except Exception:
            pass

    selected = direct or _optional_string(settings.default_project_name) or registry.default_project
    if selected:
        entry = registry.projects.get(selected)
        if entry is None:
            raise ProjectRegistryError(f'Unknown project: {selected}')
        workspace_root = entry.workspace_root.resolve()
        godot_path = entry.godot_path or settings.godot_path
        return RuntimeTarget(
            project_name=entry.name,
            workspace_root=workspace_root,
            godot_path=godot_path,
            main_scene=entry.main_scene,
        )
    return RuntimeTarget(
        project_name=None,
        workspace_root=settings.workspace_root.resolve(),
        godot_path=settings.godot_path,
        main_scene=None,
    )


def _optional_string(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
