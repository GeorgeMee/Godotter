from godotter.runtime.uid_tools import fix_uid_paths, scan_uid_map


def test_scan_uid_map_reads_uid_files(tmp_path):
    script_path = tmp_path / 'scripts' / 'player.gd'
    script_path.parent.mkdir(parents=True)
    script_path.write_text('extends Node\n', encoding='utf-8')
    (tmp_path / 'scripts' / 'player.gd.uid').write_text('uid://player123\n', encoding='utf-8')
    mapping = scan_uid_map(tmp_path)
    assert mapping == {'uid://player123': 'res://scripts/player.gd'}


def test_fix_uid_paths_dry_run_does_not_write(tmp_path):
    script_path = tmp_path / 'scripts' / 'player.gd'
    script_path.parent.mkdir(parents=True)
    script_path.write_text('extends Node\n', encoding='utf-8')
    (tmp_path / 'scripts' / 'player.gd.uid').write_text('uid://player123\n', encoding='utf-8')
    scene_path = tmp_path / 'scenes' / 'main.tscn'
    scene_path.parent.mkdir(parents=True)
    scene_path.write_text(
        '[gd_scene format=3 uid="uid://scene1"]\n\n'
        '[ext_resource type="Script" uid="uid://player123" path="res://old/player.gd" id="1_script"]\n\n'
        '[node name="Main" type="Node2D"]\n',
        encoding='utf-8',
    )
    result = fix_uid_paths(tmp_path, dry_run=True)
    assert result.uid_entries == 1
    assert result.updated_files == 1
    assert len(result.changes) == 1
    assert scene_path.read_text(encoding='utf-8').count('res://old/player.gd') == 1


def test_fix_uid_paths_write_mode_updates_scene(tmp_path):
    script_path = tmp_path / 'scripts' / 'player.gd'
    script_path.parent.mkdir(parents=True)
    script_path.write_text('extends Node\n', encoding='utf-8')
    (tmp_path / 'scripts' / 'player.gd.uid').write_text('uid://player123\n', encoding='utf-8')
    scene_path = tmp_path / 'scenes' / 'main.tscn'
    scene_path.parent.mkdir(parents=True)
    scene_path.write_text(
        '[gd_scene format=3 uid="uid://scene1"]\n\n'
        '[ext_resource type="Script" uid="uid://player123" path="res://old/player.gd" id="1_script"]\n\n'
        '[node name="Main" type="Node2D"]\n',
        encoding='utf-8',
    )
    result = fix_uid_paths(tmp_path, dry_run=False)
    assert result.updated_files == 1
    updated = scene_path.read_text(encoding='utf-8')
    assert 'path="res://scripts/player.gd"' in updated
