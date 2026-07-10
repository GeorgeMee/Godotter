from dataclasses import dataclass
from pathlib import Path
import subprocess

from godotter.agent import Agent
from godotter.config import Settings
from godotter.context import Memory
from godotter.llm import StubBrain, Thought, ToolCall
from godotter.operations import OperationRegistry, build_default_operations


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
    registry = build_default_operations()
    return Agent(StubBrain(), settings=settings, registry=registry, memory=memory, mode=mode)


def _init_git_repo(path: Path) -> None:
    subprocess.run(['git', 'init'], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.name', 'Godotter Test'], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'config', 'user.email', 'godotter@example.com'], cwd=path, check=True, capture_output=True, text=True)


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


def test_memory_is_context_not_tool(tmp_path):
    agent = build_agent(tmp_path)
    result = agent.handle_input('remember preferred mode is command-first')
    assert 'save_memory' not in result
    assert agent.memory is not None
    assert 'preferred mode is command-first' not in agent.memory.content


def test_generate_patch_tool_flow(tmp_path):
    sample = tmp_path / 'sample.txt'
    sample.write_text('alpha\nbeta\n', encoding='utf-8')
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool generate_text_replace_patch path=sample.txt old_text=beta new_text=gamma')
    assert '--- a/sample.txt' in result
    assert '+++ b/sample.txt' in result
    assert '+gamma' in result


def test_apply_patch_tool_flow_requires_act_mode(tmp_path):
    sample = tmp_path / 'sample.txt'
    sample.write_text('alpha\nbeta\n', encoding='utf-8')
    agent = build_agent(tmp_path)
    patch = '--- a/sample.txt\n+++ b/sample.txt\n@@ -1,2 +1,2 @@\n alpha\n-beta\n+gamma\n'
    result = agent.handle_input(f'tool apply_unified_patch patch={patch!r}')
    assert 'not available in plan mode' in result


def test_apply_patch_tool_flow_act_mode(tmp_path):
    sample = tmp_path / 'sample.txt'
    sample.write_text('alpha\nbeta\n', encoding='utf-8')
    agent = build_agent(tmp_path, mode='act')
    patch = '--- a/sample.txt\n+++ b/sample.txt\n@@ -1,2 +1,2 @@\n alpha\n-beta\n+gamma\n'
    result = agent.handle_input(f'tool apply_unified_patch patch={patch!r}')
    assert 'Applied patch to: sample.txt' in result
    assert sample.read_text(encoding='utf-8') == 'alpha\ngamma\n'


def test_replace_text_tool_flow_act_mode(tmp_path):
    sample = tmp_path / 'sample.txt'
    sample.write_text('alpha\nbeta\n', encoding='utf-8')
    agent = build_agent(tmp_path, mode='act')
    result = agent.handle_input('tool replace_text path=sample.txt old_text=beta new_text=gamma')
    assert 'Applied patch to: sample.txt' in result
    assert sample.read_text(encoding='utf-8') == 'alpha\ngamma\n'


def test_write_tool_records_operation(tmp_path):
    sample = tmp_path / 'sample.txt'
    sample.write_text('alpha\nbeta\n', encoding='utf-8')
    recorded = []

    agent = build_agent(tmp_path, mode='act')
    agent.operation_recorder = lambda record: recorded.append(record)

    result = agent._execute_tool('replace_text', {'path': 'sample.txt', 'old_text': 'beta', 'new_text': 'gamma'})

    assert 'Applied patch to: sample.txt' in result
    assert recorded
    assert recorded[0]['tool_name'] == 'replace_text'
    assert recorded[0]['args']['path'] == 'sample.txt'
    assert 'Applied patch to: sample.txt' in recorded[0]['result_text']
    assert recorded[0]['affected_paths'] == ['sample.txt']
    assert recorded[0]['before_hash']['sample.txt'] is not None
    assert recorded[0]['after_hash']['sample.txt'] is not None
    assert '--- a/sample.txt' in recorded[0]['forward_patch']
    assert '+++ b/sample.txt' in recorded[0]['forward_patch']
    assert '--- a/sample.txt' in recorded[0]['inverse_patch']
    assert '+beta' in recorded[0]['inverse_patch']


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


def test_scene_create_is_not_an_agent_tool(tmp_path):
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool scene_create path=scenes/main.tscn root_type=Node2D')
    assert "Error: Tool 'scene_create' not found" in result


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
    monkeypatch.setattr('godotter.services.godot.analysis.GodotRunner', FakeGodotRunner)
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
    monkeypatch.setattr('godotter.services.godot.run.GodotRunner', FakeGodotRunner)
    agent = build_agent(tmp_path, mode='act', godot_path='/usr/bin/godot')
    result = agent.handle_input('tool headless_run scene=res://scenes/main.tscn timeout=15')
    assert 'command=headless_run' in result
    assert 'target=res://scenes/main.tscn' in result
    assert 'stdout=run:res://scenes/main.tscn:15' in result


def test_runtime_doctor_tool_flow(monkeypatch, tmp_path):
    monkeypatch.setattr(
        'godotter.services.godot.diagnostics.run_doctor',
        lambda workspace_root, godot_path, timeout=15: FakeDoctorReport(workspace_root=workspace_root),
    )
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool runtime_doctor timeout=5')
    assert 'project_exists=true' in result
    assert 'godot_runnable=true' in result
    assert 'godot_version=Godot Engine v4.4.stable' in result


def test_uid_fix_apply_requires_act_mode(tmp_path):
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool uid_fix_apply {}')
    assert 'not available in plan mode' in result


def test_uid_scan_reports_without_writing(tmp_path):
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
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool uid_scan {}')
    assert 'dry_run=true' in result
    assert 'updated_files=1' in result
    assert 'change file=scenes/main.tscn uid=uid://player123 old_path=res://old/player.gd new_path=res://scripts/player.gd' in result
    assert 'path="res://old/player.gd"' in scene_path.read_text(encoding='utf-8')


def test_uid_fix_apply_tool_flow_act_mode(tmp_path):
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
    result = agent.handle_input('tool uid_fix_apply {}')
    assert 'dry_run=false' in result
    assert 'updated_files=1' in result
    assert 'change file=scenes/main.tscn uid=uid://player123 old_path=res://old/player.gd new_path=res://scripts/player.gd' in result
    updated = scene_path.read_text(encoding='utf-8')
    assert 'path="res://scripts/player.gd"' in updated


def test_git_tools_report_missing_repo(tmp_path):
    agent = build_agent(tmp_path)
    result = agent.handle_input('tool git_status {}')
    assert 'not a git repository' in result.lower()


def test_git_tools_report_repo_state(tmp_path):
    _init_git_repo(tmp_path)
    tracked = tmp_path / 'tracked.txt'
    tracked.write_text('alpha\n', encoding='utf-8')
    subprocess.run(['git', 'add', 'tracked.txt'], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(['git', 'commit', '-m', 'initial commit'], cwd=tmp_path, check=True, capture_output=True, text=True)
    tracked.write_text('beta\n', encoding='utf-8')
    untracked = tmp_path / 'notes.txt'
    untracked.write_text('todo\n', encoding='utf-8')

    agent = build_agent(tmp_path)

    status_result = agent.handle_input('tool git_status {}')
    assert 'M tracked.txt' in status_result or 'M  tracked.txt' in status_result
    assert '?? notes.txt' in status_result

    diff_result = agent.handle_input('tool git_diff path=tracked.txt')
    assert '--- a/tracked.txt' in diff_result
    assert '+++ b/tracked.txt' in diff_result

    log_result = agent.handle_input('tool git_log limit=1')
    assert 'initial commit' in log_result

    branch_result = agent.handle_input('tool git_branch {}')
    assert '*' in branch_result


def test_agent_preserves_reasoning_content_on_assistant_messages(tmp_path):
    class ReasoningBrain(StubBrain):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def think(self, conversation):
            self.calls += 1
            if self.calls == 1:
                return Thought(
                    text='planning',
                    tool_calls=[ToolCall(id='tool-1', name='git_status', args={})],
                    raw_content={'reasoning_content': 'trace-1'},
                )
            return Thought(text='done', raw_content={'type': 'text'})

    values = {
        'GODOTTER_WORKSPACE_ROOT': str(tmp_path),
        'GODOTTER_MEMORY_PATH': '.godotter/memory.md',
    }
    settings = Settings(**values)
    memory = Memory(settings.resolved_memory_path)
    registry = build_default_operations()
    agent = Agent(ReasoningBrain(), settings=settings, registry=registry, memory=memory, mode='plan')

    agent.handle_input('inspect repo')

    assistant_messages = [item for item in agent.conversation if item.get('role') == 'assistant']
    assert assistant_messages
    assert assistant_messages[0]['reasoning_content'] == 'trace-1'


def test_agent_project_summary_in_system_prompt(tmp_path):
    values = {
        'GODOTTER_WORKSPACE_ROOT': str(tmp_path),
        'GODOTTER_MEMORY_PATH': '.godotter/memory.md',
    }
    settings = Settings(**values)
    memory = Memory(settings.resolved_memory_path)
    registry = build_default_operations()
    summary_text = 'Project: TestGame\nWorkspace: /tmp/test\nMain scene: res://main.tscn'
    agent = Agent(
        StubBrain(),
        settings=settings,
        registry=registry,
        memory=memory,
        mode='plan',
        project_summary=summary_text,
    )
    assert agent.project_summary == summary_text
    assert 'TestGame' in agent.brain.system_prompt
    assert 'Workspace: /tmp/test' in agent.brain.system_prompt


def test_agent_plan_mode_prompt_encourages_tool_use(tmp_path):
    values = {
        'GODOTTER_WORKSPACE_ROOT': str(tmp_path),
        'GODOTTER_MEMORY_PATH': '.godotter/memory.md',
    }
    settings = Settings(**values)
    memory = Memory(settings.resolved_memory_path)
    registry = build_default_operations()
    agent = Agent(StubBrain(), settings=settings, registry=registry, memory=memory, mode='plan')
    assert 'ALWAYS use tools first' in agent.brain.system_prompt
    assert 'inspect the actual code' in agent.brain.system_prompt


def test_agent_without_project_summary(tmp_path):
    values = {
        'GODOTTER_WORKSPACE_ROOT': str(tmp_path),
        'GODOTTER_MEMORY_PATH': '.godotter/memory.md',
    }
    settings = Settings(**values)
    memory = Memory(settings.resolved_memory_path)
    registry = build_default_operations()
    agent = Agent(StubBrain(), settings=settings, registry=registry, memory=memory, mode='plan')
    assert agent.project_summary is None
    assert 'Current mode: plan.' in agent.brain.system_prompt
