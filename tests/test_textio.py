from godotter.services.godot.scene_parser import atomic_write
from godotter.utils.textio import write_text_utf8


UTF8_BOM = b'\xef\xbb\xbf'


def test_write_text_utf8_omits_bom(tmp_path):
    path = tmp_path / 'sample.txt'
    write_text_utf8(path, '[gd_scene]\n')
    payload = path.read_bytes()
    assert not payload.startswith(UTF8_BOM)
    assert payload.startswith(b'[gd_scene]')


def test_atomic_write_omits_bom(tmp_path):
    path = tmp_path / 'scene.tscn'
    atomic_write(path, '[gd_scene format=3 uid="uid://abc"]\n')
    payload = path.read_bytes()
    assert not payload.startswith(UTF8_BOM)
    assert payload.startswith(b'[gd_scene')
