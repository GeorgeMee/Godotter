from pathlib import Path

from godotter.context.project_summary import ProjectSummary, build_project_summary, render_project_summary


def _make_godot_project(root: Path) -> None:
    (root / 'project.godot').write_text(
        '[application]\nconfig/name="TestGame"\nrun/main_scene="uid://abc123"\n',
        encoding='utf-8',
    )
    game = root / 'game'
    game.mkdir()
    levels = game / 'levels'
    levels.mkdir()
    (levels / 'main.tscn').write_text(
        '[gd_scene load_steps=4 format=3 uid="uid://abc123"]\n\n'
        '[ext_resource type="Script" path="res://game/core/bootstrap/managers.gd" id="1_mgr"]\n'
        '[ext_resource type="Script" path="res://game/core/events/event_bus.gd" id="2_eb"]\n'
        '[ext_resource type="Script" path="res://game/features/player/scripts/player.gd" id="3_player"]\n\n'
        '[node name="Main" type="Node"]\n\n'
        '[node name="Managers" type="Node" parent="."]\n'
        'script = ExtResource("1_mgr")\n\n'
        '[node name="EventBus" type="Node" parent="Managers"]\n'
        'script = ExtResource("2_eb")\n\n'
        '[node name="Player" type="CharacterBody2D" parent="."]\n'
        'script = ExtResource("3_player")\n\n'
        '[connection signal="ready" from="Player" to="Managers" method="_on_player_ready"]\n',
        encoding='utf-8',
    )
    core = game / 'core'
    core_events = core / 'events'
    core_events.mkdir(parents=True)
    (core_events / 'event_bus.gd').write_text('extends Node\n', encoding='utf-8')
    core_bootstrap = core / 'bootstrap'
    core_bootstrap.mkdir(parents=True)
    (core_bootstrap / 'managers.gd').write_text('extends Node\n', encoding='utf-8')
    features = game / 'features' / 'player' / 'scripts'
    features.mkdir(parents=True)
    (features / 'player.gd').write_text('extends CharacterBody2D\n', encoding='utf-8')
    systems = game / 'systems'
    systems.mkdir(parents=True)
    content = game / 'content'
    content.mkdir(parents=True)
    tests = root / 'tests'
    tests.mkdir(parents=True)


def test_build_project_summary_returns_none_without_project_godot(tmp_path):
    assert build_project_summary(tmp_path) is None


def test_build_project_summary_basic_fields(tmp_path):
    _make_godot_project(tmp_path)
    summary = build_project_summary(tmp_path)
    assert summary is not None
    assert summary.project_name == 'TestGame'
    assert summary.main_scene == 'uid://abc123'
    assert len(summary.scripts) >= 3
    assert len(summary.scenes) >= 1
    assert any('main.tscn' in s for s in summary.scenes)


def test_build_project_summary_main_scene_tree(tmp_path):
    _make_godot_project(tmp_path)
    summary = build_project_summary(tmp_path)
    assert summary is not None
    assert len(summary.main_scene_tree) > 0
    tree_text = '\n'.join(summary.main_scene_tree)
    assert 'Main' in tree_text
    assert 'Managers' in tree_text
    assert 'EventBus' in tree_text
    assert 'Player' in tree_text


def test_build_project_summary_main_scene_connections(tmp_path):
    _make_godot_project(tmp_path)
    summary = build_project_summary(tmp_path)
    assert summary is not None
    assert any('ready' in c for c in summary.main_scene_connections)


def test_build_project_summary_constraints(tmp_path):
    _make_godot_project(tmp_path)
    summary = build_project_summary(tmp_path)
    assert summary is not None
    assert len(summary.constraints) >= 4
    assert any('Managers' in c for c in summary.constraints)


def test_render_project_summary(tmp_path):
    _make_godot_project(tmp_path)
    summary = build_project_summary(tmp_path)
    assert summary is not None
    text = render_project_summary(summary)
    assert 'Project: TestGame' in text
    assert 'Workspace:' in text
    assert 'Main scene:' in text
    assert 'Main scene node tree:' in text
    assert 'Project constraints:' in text


def test_render_project_summary_no_main_scene(tmp_path):
    (tmp_path / 'project.godot').write_text(
        '[application]\nconfig/name="NoMain"\n',
        encoding='utf-8',
    )
    summary = build_project_summary(tmp_path)
    assert summary is not None
    text = render_project_summary(summary)
    assert 'Main scene:' not in text
    assert 'Main scene node tree:' not in text


def test_build_project_summary_res_path_main_scene(tmp_path):
    (tmp_path / 'project.godot').write_text(
        '[application]\nconfig/name="ResPath"\nrun/main_scene="res://game/levels/demo.tscn"\n',
        encoding='utf-8',
    )
    levels = tmp_path / 'game' / 'levels'
    levels.mkdir(parents=True)
    (levels / 'demo.tscn').write_text(
        '[gd_scene format=3 uid="uid://demo1"]\n\n'
        '[node name="Demo" type="Node2D"]\n',
        encoding='utf-8',
    )
    summary = build_project_summary(tmp_path)
    assert summary is not None
    assert len(summary.main_scene_tree) > 0
    assert 'Demo' in '\n'.join(summary.main_scene_tree)


def test_build_project_summary_missing_main_scene_file(tmp_path):
    (tmp_path / 'project.godot').write_text(
        '[application]\nconfig/name="Missing"\nrun/main_scene="uid://nonexistent"\n',
        encoding='utf-8',
    )
    summary = build_project_summary(tmp_path)
    assert summary is not None
    assert summary.main_scene_tree == []
    assert summary.main_scene_connections == []
