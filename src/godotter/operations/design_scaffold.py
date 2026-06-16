from __future__ import annotations

from pathlib import Path
from dataclasses import dataclass, field

from godotter.utils.textio import write_text_utf8


@dataclass(slots=True)
class ScaffoldResult:
    created_files: list[str] = field(default_factory=list)


SYSTEM_GD_TEMPLATE = '''extends Node

var event_bus = null

func configure(bus) -> void:
    event_bus = bus
{subscribe_calls}

{event_handlers}

'''

FEATURE_GD_TEMPLATE = '''extends Node

var event_bus = null

func configure(bus) -> void:
    event_bus = bus
{subscribe_calls}

{event_handlers}

'''

GAMEMODE_GD_TEMPLATE = '''extends Node

@export var level_data_path: String = ""

func _ready() -> void:
    var bus = EventBus.new()
    bus.name = &"EventBus"
    add_child(bus)
{create_systems}
{create_features}
'''


def _subscribe_line(event: str) -> str:
    return f'    event_bus.subscribe(EventTypes.{event}, _on_{event.lower()})'


def _handler_stub(event: str) -> str:
    camel = _event_to_method(event)
    return f'''
func {camel}(event) -> void:
    # TODO: handle {event}
    pass'''


def _event_to_method(event: str) -> str:
    parts = event.lower().split('_')
    return '_on_' + '_'.join(parts)


def _preload_line(dir_name: str, name: str, suffix: str, directory: str | None = None) -> str:
    if directory:
        # directory ends with /, strip it for path building
        d = directory.rstrip('/')
        return f'var {name} = preload("res://{d}/{name}_{suffix}.gd").new()'
    return f'var {name} = preload("res://game/{dir_name}/{name}/{name}_{suffix}.gd").new()'


def _add_child_line(name: str, group: str | None = None) -> str:
    if group:
        return f'    {name}.add_to_group("mgr:{group}")\n    add_child({name})'
    return f'    add_child({name})'


def scaffold_from_design(json_data: dict, project_root: Path) -> ScaffoldResult:
    result = ScaffoldResult()

    # Create event_types.gd additions
    event_types = json_data.get("event_types", [])
    if event_types:
        events_dir = project_root / 'game' / 'core' / 'events'
        events_dir.mkdir(parents=True, exist_ok=True)
        events_path = events_dir / 'event_types.gd'
        if events_path.exists():
            existing = events_path.read_text(encoding='utf-8')
        else:
            existing = 'extends Node\n\n## Event type constants\n\n'

        # Find the last const line and append after it
        new_consts = []
        existing_events = set()
        for line in existing.splitlines():
            stripped = line.strip()
            if stripped.startswith('const ') and ':' in stripped:
                ev_name = stripped.split()[1].strip(':')
                existing_events.add(ev_name)

        for ev in event_types:
            name = ev.get('name', '')
            desc = ev.get('description', '')
            if name and name not in existing_events:
                new_consts.append(f'const {name}: StringName = &"{name.lower()}"  # {desc}')
                existing_events.add(name)

        if new_consts:
            # Insert before the last closing, or append
            write_text = existing.rstrip() + '\n' + '\n'.join(new_consts) + '\n'
            events_path.write_text(write_text, encoding='utf-8')
            result.created_files.append(events_path.relative_to(project_root).as_posix())

    # Create systems
    for sys in json_data.get("systems", []):
        name = sys.get("name", "")
        directory = sys.get("directory", f"game/systems/{name}/")
        dir_path = project_root / directory
        dir_path.mkdir(parents=True, exist_ok=True)

        subscribes = sys.get("subscribes", [])
        subscribe_calls = '\n'.join(_subscribe_line(ev) for ev in subscribes)
        event_handlers = '\n'.join(_handler_stub(ev) for ev in subscribes)

        gd_content = SYSTEM_GD_TEMPLATE.format(
            subscribe_calls=subscribe_calls,
            event_handlers=event_handlers,
        )

        main_file = f'{name}_system.gd'
        gd_path = dir_path / main_file
        if not gd_path.exists():
            gd_path.write_text(gd_content, encoding='utf-8')
            result.created_files.append(gd_path.relative_to(project_root).as_posix())

    # Create features
    for feat in json_data.get("features", []):
        name = feat.get("name", "")
        directory = feat.get("directory", f"game/features/{name}/")
        dir_path = project_root / directory
        dir_path.mkdir(parents=True, exist_ok=True)

        subscribes = feat.get("subscribes", [])
        subscribe_calls = '\n'.join(_subscribe_line(ev) for ev in subscribes)
        event_handlers = '\n'.join(_handler_stub(ev) for ev in subscribes)

        gd_content = FEATURE_GD_TEMPLATE.format(
            subscribe_calls=subscribe_calls,
            event_handlers=event_handlers,
        )

        main_file = f'{name}_feature.gd'
        gd_path = dir_path / main_file
        if not gd_path.exists():
            gd_path.write_text(gd_content, encoding='utf-8')
            result.created_files.append(gd_path.relative_to(project_root).as_posix())

    # Create gamemodes
    for gm in json_data.get("gamemodes", []):
        name = gm.get("name", "")
        directory = gm.get("directory", f"game/gamemodes/{name}/")
        dir_path = project_root / directory
        dir_path.mkdir(parents=True, exist_ok=True)

        system_names = gm.get("systems", [])
        feature_names = gm.get("features", [])

        create_systems = []
        for sys_name in system_names:
            sys_dir = json_data.get("systems", [])
            # Find the directory for this system
            dir_found = f"game/systems/{sys_name}/"
            for s in sys_dir:
                if s.get("name") == sys_name and s.get("directory"):
                    dir_found = s["directory"]
                    break
            create_systems.append(f'    {_preload_line("systems", sys_name, "system", dir_found)}')
            create_systems.append(f'    {sys_name}.name = &"{sys_name.title().replace("_", "")}System"')
            create_systems.append(f'    {sys_name}.add_to_group("mgr:{sys_name}")')
            create_systems.append(f'    add_child({sys_name})')
            create_systems.append(f'    {sys_name}.configure(bus)')
            create_systems.append('')

        create_features = []
        for feat_name in feature_names:
            feat_dir = json_data.get("features", [])
            dir_found = f"game/features/{feat_name}/"
            for f in feat_dir:
                if f.get("name") == feat_name and f.get("directory"):
                    dir_found = f["directory"]
                    break
            create_features.append(f'    {_preload_line("features", feat_name, "feature", dir_found)}')
            create_features.append(f'    {feat_name}.name = &"{feat_name.title().replace("_", "")}Feature"')
            create_features.append(f'    add_child({feat_name})')
            create_features.append(f'    {feat_name}.configure(bus)')
            create_features.append('')

        gd_content = GAMEMODE_GD_TEMPLATE.format(
            create_systems='\n'.join(create_systems),
            create_features='\n'.join(create_features),
        )

        main_file = f'{name}_mode.gd'
        gd_path = dir_path / main_file
        if not gd_path.exists():
            gd_path.write_text(gd_content, encoding='utf-8')
            result.created_files.append(gd_path.relative_to(project_root).as_posix())

    return result
