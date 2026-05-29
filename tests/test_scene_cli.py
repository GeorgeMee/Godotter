from typer.testing import CliRunner

from godotter.interfaces.cli import app


runner = CliRunner()


def test_scene_new_creates_level_scene_and_script(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    # Create minimal project structure expected by level scenes.
    (tmp_path / 'game' / 'core' / 'bootstrap').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'game' / 'core' / 'events').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'game' / 'core' / 'bootstrap' / 'managers.gd').write_text('extends Node\n', encoding='utf-8')
    (tmp_path / 'game' / 'core' / 'events' / 'event_bus.gd').write_text('extends Node\n', encoding='utf-8')

    result = runner.invoke(app, ['scene', 'new', 'game/levels/menu.tscn', '--kind', 'level', '--workspace', '.'])
    assert result.exit_code == 0
    assert (tmp_path / 'game' / 'levels' / 'menu.tscn').exists()
    assert (tmp_path / 'game' / 'levels' / 'menu.gd').exists()

    scene_text = (tmp_path / 'game' / 'levels' / 'menu.tscn').read_text(encoding='utf-8')
    assert 'uid="uid://' in scene_text
    assert '[node name="Managers" type="Node" parent="."]' in scene_text
    assert '[node name="EventBus" type="Node" parent="Managers"]' in scene_text
    assert 'script = ExtResource("1_script")' in scene_text

    script_text = (tmp_path / 'game' / 'levels' / 'menu.gd').read_text(encoding='utf-8')
    assert script_text.startswith('extends Node\n')


def test_scene_new_creates_ui_scene_and_script(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ['scene', 'new', 'game/ui/scenes/main_menu.tscn', '--kind', 'ui', '--workspace', '.'])
    assert result.exit_code == 0
    assert (tmp_path / 'game' / 'ui' / 'scenes' / 'main_menu.tscn').exists()
    assert (tmp_path / 'game' / 'ui' / 'scenes' / 'main_menu.gd').exists()

    script_text = (tmp_path / 'game' / 'ui' / 'scenes' / 'main_menu.gd').read_text(encoding='utf-8')
    assert script_text.startswith('extends Control\n')
