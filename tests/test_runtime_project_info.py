from pathlib import Path

from godotter.runtime import load_project_info


def test_load_project_info_reads_main_scene_and_counts(tmp_path: Path):
    (tmp_path / 'project.godot').write_text(
        '[application]\nconfig/name="Demo"\nrun/main_scene="res://scenes/main.tscn"\nautoload/Test="*res://test.gd"\n',
        encoding='utf-8',
    )
    (tmp_path / 'scenes').mkdir()
    (tmp_path / 'scenes' / 'main.tscn').write_text('', encoding='utf-8')
    (tmp_path / 'test.gd').write_text('extends Node\n', encoding='utf-8')

    info = load_project_info(tmp_path)
    assert info.name == 'Demo'
    assert info.main_scene == 'res://scenes/main.tscn'
    assert info.autoloads == ['Test']
    assert info.script_count == 1
    assert info.scene_count == 1