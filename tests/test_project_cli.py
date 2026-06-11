from typer.testing import CliRunner

from godotter.interfaces.cli import app


runner = CliRunner()


def test_project_new_command_creates_scaffold(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ['project', 'new', 'demo', '--no-git'])
    assert result.exit_code == 0

    project_root = tmp_path / 'demo'
    assert (project_root / 'project.godot').exists()
    assert (project_root / '.gitignore').exists()
    assert (project_root / 'icon.svg').exists()
    assert (project_root / 'game' / 'levels' / 'main_level.tscn').exists()
    assert (project_root / 'game' / 'levels' / 'game_level.tscn').exists()
    assert (project_root / 'game' / 'core' / 'events').is_dir()
    assert (project_root / 'game' / 'systems').is_dir()
    assert (project_root / 'game' / 'features').is_dir()
    assert (project_root / 'game' / 'content' / 'prefabs').is_dir()
    assert (project_root / 'ui').is_dir()
    assert (project_root / 'ui' / 'views').is_dir()
    assert not (project_root / '.godotter').exists()
    assert '.godotter/' in (project_root / '.gitignore').read_text(encoding='utf-8')

    project_text = (project_root / 'project.godot').read_text(encoding='utf-8')
    assert 'run/main_scene="res://game/levels/main_level.tscn"' in project_text
    assert 'config/icon="res://icon.svg"' in project_text

    scene_text = (project_root / 'game' / 'levels' / 'main_level.tscn').read_text(encoding='utf-8')
    assert '[gd_scene' in scene_text
    assert 'uid="uid://' in scene_text
    assert '[node name="MainLevel" type="Control"]' in scene_text
    assert '[node name="Managers" type="Node" parent="."]' in scene_text


def test_project_new_command_writes_no_bom(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ['project', 'new', 'demo', '--no-git'])
    assert result.exit_code == 0

    for relative in ['project.godot', '.gitignore', 'icon.svg', 'game/levels/main_level.tscn']:
        payload = (tmp_path / 'demo' / relative).read_bytes()
        assert not payload.startswith(b'\xef\xbb\xbf')


def test_top_level_new_alias_warns_and_delegates(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ['new', 'demo', '--no-git'])
    assert result.exit_code == 0
    assert 'deprecated' in result.stdout.lower()
    assert (tmp_path / 'demo' / 'project.godot').exists()
