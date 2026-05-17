from dataclasses import dataclass
from pathlib import Path

from godotter.agent import Agent
from godotter.config import Settings
from godotter.context import Memory
from godotter.llm import StubBrain
from godotter.tools import ToolRegistry, build_default_tools


@dataclass
class FakeRunResult:
    exit_code: int = 0
    stdout: str = 'ok'
    stderr: str = ''
    timed_out: bool = False
    duration_ms: int = 12


class FakeGodotRunner:
    def __init__(self, godot_path: str, workspace_root) -> None:
        self.godot_path = godot_path
        self.workspace_root = workspace_root

    def lint_script(self, file_path: str, timeout: int = 30):
        return FakeRunResult(stdout=f'lint:{file_path}:{timeout}')

    def lint_project(self, timeout: int = 60):
        return FakeRunResult(stdout=f'lint-project:{timeout}')

    def run_project(self, timeout: int = 60, scene: str | None = None):
        return FakeRunResult(stdout=f'run:{scene or "(project)"}:{timeout}')


@dataclass
class FakeDoctorReport:
    workspace_root: Path
    project_exists: bool = True
    project_name: str | None = 'Demo'
    main_scene: str | None = 'res://scenes/main.tscn'
    script_count: int = 2
    scene_count: int = 1
    godot_configured: bool = True
    godot_runnable: bool = True
    godot_version: str | None = 'Godot Engine v4.4.stable'
    godot_error: str | None = None


def build_agent(tmp_path, mode: str = 'plan', godot_path: str | None = None) -> Agent:
    values = {
        'GODOTTER_WORKSPACE_ROOT': str(tmp_path),
        'GODOTTER_MEMORY_PATH': '.godotter/memory.md',
    }
    if godot_path is not None:
        values['GODOT_PATH'] = godot_path
    settings = Settings(**values)
    memory = Memory(settings.resolved_memory_path)
    registry = ToolRegistry(build_default_tools())
    return Agent(StubBrain(), settings=settings, registry=registry, memory=memory, mode=mode)


def test_plain_chat_echoes(tmp_path):
    agent = build_agent(tmp_path)
    assert '[stub:' in agent.handle_input('hello')


def test_read_file_tool_flow_json(tmp_path):
    sample = tmp_path / 'sample.txt'
    sample.write_text('alpha\nbeta\n', encoding='utf-8')
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool read_file {"path": "sample.txt"}')
    assert '1 | alpha' in result
    assert '2 | beta' in result


def test_read_file_tool_flow_key_value(tmp_path):
    sample = tmp_path / 'sample.txt'
    sample.write_text('alpha\nbeta\n', encoding='utf-8')
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool read_file path=sample.txt')
    assert '1 | alpha' in result
    assert '2 | beta' in result


def test_memory_tool_flow(tmp_path):
    agent = build_agent(tmp_path)
    result = agent.handle_input('remember preferred mode is command-first')
    assert 'Memory updated.' in result
    assert 'preferred mode is command-first' in agent.memory.content


def test_generate_patch_tool_flow(tmp_path):
    sample = tmp_path / 'sample.txt'
    sample.write_text('alpha\nbeta\n', encoding='utf-8')
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool generate_patch path=sample.txt old_text=beta new_text=gamma')
    assert '--- a/sample.txt' in result
    assert '+++ b/sample.txt' in result
    assert '+gamma' in result


def test_apply_patch_tool_flow_requires_act_mode(tmp_path):
    sample = tmp_path / 'sample.txt'
    sample.write_text('alpha\nbeta\n', encoding='utf-8')
    agent = build_agent(tmp_path)
    patch = '--- a/sample.txt\n+++ b/sample.txt\n@@ -1,2 +1,2 @@\n alpha\n-beta\n+gamma\n'
    result = agent.handle_input(f'tool apply_patch patch={patch!r}')
    assert 'not available in plan mode' in result


def test_apply_patch_tool_flow_act_mode(tmp_path):
    sample = tmp_path / 'sample.txt'
    sample.write_text('alpha\nbeta\n', encoding='utf-8')
    agent = build_agent(tmp_path, mode='act')
    patch = '--- a/sample.txt\n+++ b/sample.txt\n@@ -1,2 +1,2 @@\n alpha\n-beta\n+gamma\n'
    result = agent.handle_input(f'tool apply_patch patch={patch!r}')
    assert 'Applied patch to: sample.txt' in result
    assert sample.read_text(encoding='utf-8') == 'alpha\ngamma\n'


def test_validate_project_reports_scaffold(tmp_path):
    (tmp_path / 'pyproject.toml').write_text('[project]\nname="demo"\n', encoding='utf-8')
    (tmp_path / 'README.md').write_text('# Demo\n', encoding='utf-8')
    (tmp_path / 'src' / 'godotter').mkdir(parents=True)
    (tmp_path / 'tests').mkdir()
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool validate_project {}')
    assert 'OK   pyproject.toml present' in result
    assert 'OK   src/godotter present' in result


def test_project_info_tool_flow(tmp_path):
    (tmp_path / 'project.godot').write_text(
        '[application]\nconfig/name="Demo"\nrun/main_scene="res://scenes/main.tscn"\nautoload/Test="*res://test.gd"\n',
        encoding='utf-8',
    )
    (tmp_path / 'scenes').mkdir()
    (tmp_path / 'scenes' / 'main.tscn').write_text('', encoding='utf-8')
    (tmp_path / 'test.gd').write_text('extends Node\n', encoding='utf-8')
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool project_info {}')
    assert 'name=Demo' in result
    assert 'main_scene=res://scenes/main.tscn' in result
    assert 'script_count=1' in result


def test_scene_create_requires_act_mode(tmp_path):
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool scene_create path=scenes/main.tscn root_type=Node2D')
    assert 'not available in plan mode' in result


def test_scene_create_tool_flow_act_mode(tmp_path):
    agent = build_agent(tmp_path, mode='act')
    result = agent.handle_input('tool scene_create path=scenes/main_menu.tscn root_type=Node2D root_name=MainMenu')
    scene_path = tmp_path / 'scenes' / 'main_menu.tscn'
    assert 'path=scenes/main_menu.tscn' in result
    assert 'root_name=MainMenu' in result
    content = scene_path.read_text(encoding='utf-8')
    assert '[gd_scene format=3 uid="uid://' in content
    assert '[node name="MainMenu" type="Node2D"]' in content


def test_scene_inspect_tool_flow(tmp_path):
    scene_path = tmp_path / 'scenes' / 'main_menu.tscn'
    scene_path.parent.mkdir()
    scene_path.write_text(
        '[gd_scene load_steps=2 format=3 uid="uid://abc"]\n\n'
        '[ext_resource type="Script" path="res://main.gd" id="1_script"]\n\n'
        '[node name="MainMenu" type="Node2D"]\n'
        'script = ExtResource("1_script")\n',
        encoding='utf-8',
    )
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool scene_inspect path=scenes/main_menu.tscn')
    assert 'uid=uid://abc' in result
    assert 'ext_resource id=1_script type=Script path=res://main.gd' in result
    assert 'node name=MainMenu type=Node2D parent=.' in result


def test_scene_validate_reports_missing_resource(tmp_path):
    scene_path = tmp_path / 'scenes' / 'main_menu.tscn'
    scene_path.parent.mkdir()
    scene_path.write_text(
        '[gd_scene load_steps=2 format=3 uid="uid://abc"]\n\n'
        '[ext_resource type="Script" path="res://missing.gd" id="1_script"]\n\n'
        '[node name="MainMenu" type="Node2D"]\n'
        'script = ExtResource("1_script")\n',
        encoding='utf-8',
    )
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool scene_validate path=scenes/main_menu.tscn')
    assert 'error missing_resource id=1_script path=res://missing.gd' in result


def test_script_lint_requires_godot_path(monkeypatch, tmp_path):
    monkeypatch.delenv('GODOT_PATH', raising=False)
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool script_lint {}')
    assert 'GODOT_PATH is not configured' in result


def test_script_lint_tool_flow(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.tools.runtime.GodotRunner', FakeGodotRunner)
    script_path = tmp_path / 'scripts' / 'player.gd'
    script_path.parent.mkdir()
    script_path.write_text('extends Node\n', encoding='utf-8')
    agent = build_agent(tmp_path, godot_path='/usr/bin/godot')
    result = agent.handle_input('tool script_lint path=scripts/player.gd timeout=9')
    assert 'command=script_lint' in result
    assert 'target=scripts/player.gd' in result
    assert 'stdout=lint:scripts/player.gd:9' in result


def test_headless_run_requires_act_mode(tmp_path):
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool headless_run scene=res://scenes/main.tscn')
    assert 'not available in plan mode' in result


def test_headless_run_tool_flow_act_mode(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.tools.runtime.GodotRunner', FakeGodotRunner)
    agent = build_agent(tmp_path, mode='act', godot_path='/usr/bin/godot')
    result = agent.handle_input('tool headless_run scene=res://scenes/main.tscn timeout=15')
    assert 'command=headless_run' in result
    assert 'target=res://scenes/main.tscn' in result
    assert 'stdout=run:res://scenes/main.tscn:15' in result


def test_runtime_doctor_tool_flow(monkeypatch, tmp_path):
    monkeypatch.setattr(
        'godotter.tools.runtime.run_doctor',
        lambda workspace_root, godot_path, timeout=15: FakeDoctorReport(workspace_root=workspace_root),
    )
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool runtime_doctor timeout=5')
    assert 'project_exists=true' in result
    assert 'godot_runnable=true' in result
    assert 'godot_version=Godot Engine v4.4.stable' in result


def test_uid_fix_requires_act_mode(tmp_path):
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool uid_fix dry_run=false')
    assert 'not available in plan mode' in result


def test_uid_fix_tool_flow_act_mode(tmp_path):
    script_path = tmp_path / 'scripts' / 'player.gd'
    script_path.parent.mkdir(parents=True)
    script_path.write_text('extends Node\n', encoding='utf-8')
    (tmp_path / 'scripts' / 'player.gd.uid').write_text('uid://player123\n', encoding='utf-8')
    scene_path = tmp_path / 'scenes' / 'main.tscn'
    scene_path.parent.mkdir(parents=True)
    scene_path.write_text(
        '[gd_scene load_steps=2 format=3 uid="uid://scene1"]\n\n'
        '[ext_resource type="Script" uid="uid://player123" path="res://old/player.gd" id="1_script"]\n\n'
        '[node name="Main" type="Node2D"]\n'
        'script = ExtResource("1_script")\n',
        encoding='utf-8',
    )
    agent = build_agent(tmp_path, mode='act')
    result = agent.handle_input('tool uid_fix dry_run=false')
    assert 'updated_files=1' in result
    assert 'change file=scenes/main.tscn uid=uid://player123 old_path=res://old/player.gd new_path=res://scripts/player.gd' in result
    updated = scene_path.read_text(encoding='utf-8')
    assert 'path="res://scripts/player.gd"' in updated
