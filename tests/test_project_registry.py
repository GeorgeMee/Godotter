from godotter.config import Settings
from godotter.project_registry import ProjectRegistryError, load_project_registry, resolve_runtime_target


def test_load_project_registry_reads_projects(tmp_path):
    registry_path = tmp_path / 'projects.toml'
    registry_path.write_text(
        'default_project = "demo"\n\n'
        '[projects.demo]\n'
        'workspace_root = "/srv/demo"\n'
        'godot_path = "/usr/local/bin/godot"\n'
        'main_scene = "res://scenes/main.tscn"\n',
        encoding='utf-8',
    )
    registry = load_project_registry(registry_path)
    assert registry.default_project == 'demo'
    assert registry.projects['demo'].godot_path == '/usr/local/bin/godot'
    assert registry.projects['demo'].main_scene == 'res://scenes/main.tscn'


def test_resolve_runtime_target_uses_default_project(tmp_path):
    registry_path = tmp_path / 'projects.toml'
    project_root = tmp_path / 'demo'
    registry_path.write_text(
        'default_project = "demo"\n\n'
        '[projects.demo]\n'
        f'workspace_root = "{project_root.as_posix()}"\n'
        'godot_path = "/usr/local/bin/godot"\n',
        encoding='utf-8',
    )
    settings = Settings(
        GODOTTER_WORKSPACE_ROOT=str(tmp_path),
        GODOTTER_PROJECT_REGISTRY_PATH=str(registry_path),
    )
    target = resolve_runtime_target(settings)
    assert target.project_name == 'demo'
    assert target.workspace_root == project_root.resolve()
    assert target.godot_path == '/usr/local/bin/godot'


def test_resolve_runtime_target_falls_back_to_settings(tmp_path):
    settings = Settings(
        GODOTTER_WORKSPACE_ROOT=str(tmp_path),
        GODOT_PATH='/usr/bin/godot',
        GODOTTER_PROJECT_REGISTRY_PATH=str(tmp_path / 'missing.toml'),
    )
    target = resolve_runtime_target(settings)
    assert target.project_name is None
    assert target.workspace_root == tmp_path.resolve()
    assert target.godot_path == '/usr/bin/godot'


def test_resolve_runtime_target_raises_on_unknown_project(tmp_path):
    registry_path = tmp_path / 'projects.toml'
    registry_path.write_text('[projects.demo]\nworkspace_root = "/srv/demo"\n', encoding='utf-8')
    settings = Settings(
        GODOTTER_WORKSPACE_ROOT=str(tmp_path),
        GODOTTER_PROJECT_REGISTRY_PATH=str(registry_path),
    )
    try:
        resolve_runtime_target(settings, project='missing')
    except ProjectRegistryError as exc:
        assert 'Unknown project: missing' in str(exc)
    else:
        raise AssertionError('Expected ProjectRegistryError')
