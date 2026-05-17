from pathlib import Path

from godotter.runtime import filename_to_node_name, generate_minimal_scene, generate_uid, parse_scene_header, parse_scene_text


def test_generate_uid_has_expected_prefix():
    uid = generate_uid()
    assert uid.startswith('uid://')
    assert len(uid) > 10


def test_filename_to_node_name_uses_pascal_case():
    assert filename_to_node_name('scenes/player_main.tscn') == 'PlayerMain'


def test_generate_minimal_scene_includes_uid_and_root():
    scene = generate_minimal_scene('Node2D', 'Main', 'uid://abc123')
    assert '[gd_scene format=3 uid="uid://abc123"]' in scene
    assert '[node name="Main" type="Node2D"]' in scene


def test_parse_scene_header_reads_uid_and_format():
    content = '[gd_scene load_steps=2 format=3 uid="uid://abc"]\n\n[node name="Main" type="Node2D"]\n'
    header = parse_scene_header(content)
    assert header is not None
    assert header.uid == 'uid://abc'
    assert header.format == 3
    assert header.load_steps == 2


def test_parse_scene_text_reads_resources_nodes_and_properties():
    content = (
        '[gd_scene load_steps=2 format=3 uid="uid://abc"]\n\n'
        '[ext_resource type="Script" path="res://main.gd" id="1_script"]\n\n'
        '[node name="Main" type="Node2D"]\n'
        'script = ExtResource("1_script")\n'
        '\n'
        '[connection signal="pressed" from="Button" to="." method="_on_button_pressed"]\n'
    )
    parsed = parse_scene_text(content)
    assert parsed.header is not None
    assert len(parsed.ext_resources) == 1
    assert parsed.ext_resources[0].path == 'res://main.gd'
    assert len(parsed.nodes) == 1
    assert parsed.nodes[0].name == 'Main'
    assert parsed.nodes[0].properties[0].key == 'script'
    assert len(parsed.connections) == 1
    assert parsed.connections[0].signal == 'pressed'