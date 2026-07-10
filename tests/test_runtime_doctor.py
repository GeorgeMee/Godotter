from dataclasses import dataclass
from pathlib import Path
import subprocess

from godotter.services.godot.doctor import run_doctor


@dataclass
class Completed:
    returncode: int = 0
    stdout: str = 'Godot Engine v4.4.stable\n'
    stderr: str = ''


def test_run_doctor_reports_project_and_godot(monkeypatch, tmp_path):
    (tmp_path / 'project.godot').write_text(
        '[application]\nconfig/name="Demo"\nrun/main_scene="res://scenes/main.tscn"\n',
        encoding='utf-8',
    )
    (tmp_path / 'scenes').mkdir()
    (tmp_path / 'scenes' / 'main.tscn').write_text('', encoding='utf-8')
    (tmp_path / 'player.gd').write_text('extends Node\n', encoding='utf-8')
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: Completed())
    report = run_doctor(tmp_path, '/usr/bin/godot', timeout=8)
    assert report.project_exists is True
    assert report.project_name == 'Demo'
    assert report.godot_configured is True
    assert report.godot_runnable is True
    assert report.godot_version == 'Godot Engine v4.4.stable'
    assert report.script_count == 1
    assert report.scene_count == 1


def test_run_doctor_handles_missing_godot_path(tmp_path):
    report = run_doctor(tmp_path, None)
    assert report.project_exists is False
    assert report.godot_configured is False
    assert report.godot_runnable is False
    assert report.godot_version is None
    assert report.godot_error is None
