from pathlib import Path
import json

from typer.testing import CliRunner

from godotter.interfaces.cli import app
from godotter.operations.tests import expected_test_dirs_for_paths, infer_test_kinds_for_paths


class FakeRunResult:
    def __init__(self, stdout: str) -> None:
        self.exit_code = 0
        self.stdout = stdout
        self.stderr = ''
        self.timed_out = False
        self.duration_ms = 21


class FakeGodotRunner:
    def __init__(self, godot_path: str, workspace_root) -> None:
        self.godot_path = godot_path
        self.workspace_root = workspace_root

    def lint_script(self, file_path: str, timeout: int = 30):
        return FakeRunResult(f'lint:{file_path}:{timeout}')

    def lint_project(self, timeout: int = 60):
        return FakeRunResult(f'lint-project:{timeout}')

    def run_project(self, timeout: int = 60, scene: str | None = None, *, headless: bool = False):
        return FakeRunResult(f'run:{scene or "(project)"}:{timeout}:headless={str(headless).lower()}')


class FakeDoctorReport:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self.project_exists = True
        self.project_name = 'Demo'
        self.main_scene = 'res://scenes/main.tscn'
        self.script_count = 2
        self.scene_count = 1
        self.godot_configured = True
        self.godot_runnable = True
        self.godot_version = 'Godot Engine v4.4.stable'
        self.godot_error = None


class FakeUidFixChange:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.uid = 'uid://player123'
        self.old_path = 'res://old/player.gd'
        self.new_path = 'res://scripts/player.gd'


class FakeUidFixResult:
    def __init__(self, file_path: Path) -> None:
        self.uid_entries = 1
        self.scanned_files = 2
        self.updated_files = 1
        self.changes = [FakeUidFixChange(file_path)]


class FakeBuildArtifact:
    def __init__(self, path: str = '.godotter/builds/build_demo/game.zip') -> None:
        self.path = path
        self.name = Path(path).name
        self.size_bytes = 123


class FakeBuildReport:
    def __init__(self, status: str = 'passed') -> None:
        self.build_id = 'build_demo'
        self.status = status
        self.preset = 'Web'
        self.output_path = '.godotter/builds/build_demo/index.html'
        self.exit_code = 0 if status == 'passed' else 1
        self.timed_out = False
        self.artifacts = [FakeBuildArtifact()]


class FakeExportPreset:
    def __init__(self) -> None:
        self.index = 0
        self.name = 'Web'
        self.platform = 'Web'


class FakeExportDoctorReport:
    def __init__(self, workspace_root: Path, ok: bool = True) -> None:
        self.workspace_root = workspace_root.as_posix()
        self.project_exists = True
        self.export_presets_exists = True
        self.presets = [FakeExportPreset()]
        self.godot_configured = True
        self.godot_path_exists = True
        self.godot_version = '4.6.1.stable.official'
        self.templates_root = '/tmp/Godot/export_templates/4.6.1.stable'
        self.templates_detected = True
        self.android_sdk_path = None
        self.android_sdk_valid = False
        self.android_build_tools_version = None
        self.android_adb_exists = False
        self.java_home = None
        self.java_valid = False
        self.java_version = None
        self.keystore_path = None
        self.keystore_valid = False
        self.android_template_installed = False
        self.ok = ok
        self.errors = [] if ok else ['export_presets.cfg is missing']
        self.warnings = []


class FakeRuntimeTarget:
    def __init__(self, workspace_root: Path, godot_path: str | None = '/usr/bin/godot') -> None:
        self.project_name = 'demo'
        self.workspace_root = workspace_root
        self.godot_path = godot_path
        self.main_scene = 'res://scenes/main.tscn'


class FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: bytes = b'ok', stderr: bytes = b'') -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


runner = CliRunner()


def test_runtime_lint_command(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'godot_path': '/usr/bin/godot',
        'workspace_root': tmp_path,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.build_runner', lambda settings, project=None: FakeGodotRunner(settings.godot_path, settings.workspace_root))
    result = runner.invoke(app, ['runtime', 'lint', 'scripts/player.gd', '--timeout', '7'])
    assert result.exit_code == 0
    assert 'command=script_lint' in result.stdout
    assert 'stdout=lint:scripts/player.gd:7' in result.stdout


def test_runtime_run_command(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'godot_path': '/usr/bin/godot',
        'workspace_root': tmp_path,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.build_runner', lambda settings, project=None: FakeGodotRunner(settings.godot_path, settings.workspace_root))
    result = runner.invoke(app, ['runtime', 'run', '--scene', 'res://scenes/main.tscn', '--timeout', '11'])
    assert result.exit_code == 0
    assert 'command=headless_run' in result.stdout
    assert 'stdout=run:res://scenes/main.tscn:11:headless=false' in result.stdout


def test_runtime_command_requires_godot_path(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'godot_path': None,
        'workspace_root': tmp_path,
        'resolved_project_registry_path': tmp_path / 'projects.toml',
        'default_project_name': None,
    })())
    result = runner.invoke(app, ['runtime', 'lint'])
    assert result.exit_code != 0
    assert 'GODOT_PATH is not configured' in result.output


def test_runtime_verify_writes_json_report(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'workspace_root': tmp_path,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.resolve_runtime_target', lambda settings, project=None: FakeRuntimeTarget(tmp_path))
    monkeypatch.setattr(
        'godotter.runtime.verify.subprocess.run',
        lambda command, cwd, capture_output, timeout, shell: FakeCompletedProcess(stdout=f'ok:{command}'.encode()),
    )

    result = runner.invoke(app, ['runtime', 'verify', '--json-output', '.godotter/reports/verify/custom.json'])

    assert result.exit_code == 0
    report_path = tmp_path / '.godotter' / 'reports' / 'verify' / 'custom.json'
    latest_path = tmp_path / '.godotter' / 'reports' / 'verify' / 'latest.json'
    assert report_path.exists()
    assert latest_path.exists()
    report = json.loads(report_path.read_text(encoding='utf-8'))
    assert report['result'] == 'pass'
    assert report['summary']['passed'] == 6
    assert 'check name=validate_structure result=pass' in result.stdout


def test_runtime_verify_failed_check_exits_nonzero(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'workspace_root': tmp_path,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.resolve_runtime_target', lambda settings, project=None: FakeRuntimeTarget(tmp_path))

    def _fake_run(command, cwd, capture_output, timeout, shell):
        if 'validate-managers' in command:
            return FakeCompletedProcess(returncode=1, stderr=b'missing Managers')
        return FakeCompletedProcess()

    monkeypatch.setattr('godotter.runtime.verify.subprocess.run', _fake_run)

    result = runner.invoke(app, ['runtime', 'verify', '--json-output', '.godotter/reports/verify/fail.json'])

    assert result.exit_code == 1
    report = json.loads((tmp_path / '.godotter' / 'reports' / 'verify' / 'fail.json').read_text(encoding='utf-8'))
    assert report['result'] == 'fail'
    assert report['failed_check'] == 'validate_managers'
    assert 'check name=validate_managers result=fail exit_code=1' in result.stdout


def test_runtime_doctor_command(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'godot_path': '/usr/bin/godot',
        'workspace_root': tmp_path,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.resolve_runtime_target', lambda settings, project=None: FakeRuntimeTarget(tmp_path))
    monkeypatch.setattr('godotter.interfaces.cli.run_doctor', lambda workspace_root, godot_path, timeout=15: FakeDoctorReport(workspace_root))
    result = runner.invoke(app, ['runtime', 'doctor', '--timeout', '5'])
    assert result.exit_code == 0
    assert 'project_exists=true' in result.stdout
    assert 'godot_version=Godot Engine v4.4.stable' in result.stdout


def test_runtime_uid_fix_command(monkeypatch, tmp_path):
    target = tmp_path / 'scenes' / 'main.tscn'
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'godot_path': '/usr/bin/godot',
        'workspace_root': tmp_path,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.resolve_runtime_target', lambda settings, project=None: FakeRuntimeTarget(tmp_path))
    monkeypatch.setattr('godotter.interfaces.cli.fix_uid_paths', lambda workspace_root, dry_run=True: FakeUidFixResult(target))
    result = runner.invoke(app, ['runtime', 'uid-fix', '--write'])
    assert result.exit_code == 0
    assert 'dry_run=false' in result.stdout
    assert 'updated_files=1' in result.stdout
    assert 'change file=scenes/main.tscn uid=uid://player123 old_path=res://old/player.gd new_path=res://scripts/player.gd' in result.stdout


def test_export_build_command_writes_report_summary(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'godot_path': '/usr/bin/godot',
        'workspace_root': tmp_path,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.resolve_runtime_target', lambda settings, project=None: FakeRuntimeTarget(tmp_path))
    monkeypatch.setattr(
        'godotter.interfaces.cli.run_export_build',
        lambda **kwargs: (FakeBuildReport(), tmp_path / '.godotter' / 'builds' / 'build_demo' / 'build_report.json'),
    )

    result = runner.invoke(app, ['export', 'build', '--preset', 'Web'])

    assert result.exit_code == 0
    assert 'build_id=build_demo' in result.stdout
    assert 'status=passed' in result.stdout
    assert 'artifact path=.godotter/builds/build_demo/game.zip size_bytes=123' in result.stdout


def test_export_list_command_lists_build_reports(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'workspace_root': tmp_path,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.resolve_runtime_target', lambda settings, project=None: FakeRuntimeTarget(tmp_path))
    monkeypatch.setattr(
        'godotter.interfaces.cli.list_build_reports',
        lambda workspace_root: [{'build_id': 'build_demo', 'status': 'passed', 'preset': 'Web', 'artifacts': [{}]}],
    )

    result = runner.invoke(app, ['export', 'list'])

    assert result.exit_code == 0
    assert 'count=1' in result.stdout
    assert 'build id=build_demo status=passed preset=Web artifacts=1' in result.stdout


def test_export_doctor_command_reports_presets_and_templates(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'workspace_root': tmp_path,
        'export_templates_path': None,
        'android_sdk_path': None,
        'java_home': None,
        'android_keystore_path': None,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.resolve_runtime_target', lambda settings, project=None: FakeRuntimeTarget(tmp_path))
    monkeypatch.setattr(
        'godotter.interfaces.cli.run_export_doctor',
        lambda **kwargs: FakeExportDoctorReport(tmp_path),
    )

    result = runner.invoke(app, ['export', 'doctor'])

    assert result.exit_code == 0
    assert 'export_presets=true' in result.stdout
    assert 'preset index=0 name=Web platform=Web' in result.stdout
    assert 'templates_detected=true' in result.stdout
    assert 'ok=true' in result.stdout


def test_export_doctor_command_fails_when_project_not_export_ready(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'workspace_root': tmp_path,
        'export_templates_path': None,
        'android_sdk_path': None,
        'java_home': None,
        'android_keystore_path': None,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.resolve_runtime_target', lambda settings, project=None: FakeRuntimeTarget(tmp_path))
    monkeypatch.setattr(
        'godotter.interfaces.cli.run_export_doctor',
        lambda **kwargs: FakeExportDoctorReport(tmp_path, ok=False),
    )

    result = runner.invoke(app, ['export', 'doctor'])

    assert result.exit_code == 1
    assert 'error=export_presets.cfg is missing' in result.stdout
    assert 'ok=false' in result.stdout


def test_runtime_lint_command_accepts_project_option(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'godot_path': '/usr/bin/godot',
        'workspace_root': tmp_path,
    })())
    captured: dict[str, str | None] = {}

    def fake_build_runner(settings, project=None):
        captured['project'] = project
        return FakeGodotRunner(settings.godot_path, settings.workspace_root)

    monkeypatch.setattr('godotter.interfaces.cli.build_runner', fake_build_runner)
    result = runner.invoke(app, ['runtime', 'lint', '--project', 'demo'])
    assert result.exit_code == 0
    assert captured['project'] == 'demo'


def test_runtime_validate_nodepaths_reports_unresolved_exported_path(monkeypatch, tmp_path):
    levels = tmp_path / 'game' / 'levels'
    levels.mkdir(parents=True)
    (levels / 'main.tscn').write_text(
        '\n'.join(
            [
                '[gd_scene format=3]',
                '',
                '[node name="Main" type="Node"]',
                '',
                '[node name="Managers" type="Node" parent="."]',
                '',
                '[node name="EventBus" type="Node" parent="Managers"]',
                '',
                '[node name="UI" type="Control" parent="."]',
                '',
                '[node name="GameOverScreen" type="Control" parent="UI"]',
                'event_bus_path = NodePath("../../Managers/MissingEventBus")',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'workspace_root': tmp_path,
    })())

    result = runner.invoke(app, ['runtime', 'validate-nodepaths'])

    assert result.exit_code == 1
    assert 'ok=false' in result.stdout
    assert 'unresolved_nodepath' in result.stdout
    assert 'UI/GameOverScreen' in result.stdout
    assert 'suggested=../../Managers/EventBus' in result.stdout


def test_runtime_validate_nodepaths_accepts_resolved_exported_path(monkeypatch, tmp_path):
    levels = tmp_path / 'game' / 'levels'
    levels.mkdir(parents=True)
    (levels / 'main.tscn').write_text(
        '\n'.join(
            [
                '[gd_scene format=3]',
                '',
                '[node name="Main" type="Node"]',
                '',
                '[node name="Managers" type="Node" parent="."]',
                '',
                '[node name="EventBus" type="Node" parent="Managers"]',
                '',
                '[node name="UI" type="Control" parent="."]',
                '',
                '[node name="GameOverScreen" type="Control" parent="UI"]',
                'event_bus_path = NodePath("../../Managers/EventBus")',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'workspace_root': tmp_path,
    })())

    result = runner.invoke(app, ['runtime', 'validate-nodepaths'])

    assert result.exit_code == 0
    assert 'ok=true' in result.stdout


def test_runtime_validate_paths_reports_missing_scene_resource_with_suggestion(monkeypatch, tmp_path):
    levels = tmp_path / 'game' / 'levels'
    scripts = tmp_path / 'game' / 'ui' / 'scripts'
    levels.mkdir(parents=True)
    scripts.mkdir(parents=True)
    (scripts / 'menu.gd').write_text('extends Control\n', encoding='utf-8')
    (levels / 'main.tscn').write_text(
        '\n'.join(
            [
                '[gd_scene load_steps=2 format=3]',
                '',
                '[ext_resource type="Script" path="res://game/ui/scripts/missing_menu.gd" id="1_script"]',
                '',
                '[node name="Main" type="Control"]',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'workspace_root': tmp_path,
    })())

    result = runner.invoke(app, ['runtime', 'validate-paths'])

    assert result.exit_code == 1
    assert 'unresolved_resource_path' in result.stdout
    assert 'res://game/ui/scripts/missing_menu.gd' in result.stdout


def test_runtime_validate_paths_reports_script_res_path_with_unique_suggestion(monkeypatch, tmp_path):
    scripts = tmp_path / 'game' / 'features' / 'demo' / 'scripts'
    views = tmp_path / 'game' / 'ui' / 'views'
    scripts.mkdir(parents=True)
    views.mkdir(parents=True)
    (views / 'game_over.tscn').write_text('[gd_scene format=3]\n\n[node name="GameOver" type="Control"]\n', encoding='utf-8')
    (scripts / 'demo.gd').write_text(
        'extends Node\nconst GameOver = preload("res://game/ui/scenes/game_over.tscn")\n',
        encoding='utf-8',
    )
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'workspace_root': tmp_path,
    })())

    result = runner.invoke(app, ['runtime', 'validate-paths'])

    assert result.exit_code == 1
    assert 'unresolved_resource_path' in result.stdout
    assert 'suggested=res://game/ui/views/game_over.tscn' in result.stdout


def test_runtime_validate_paths_fix_rewrites_unique_nodepath_suggestion(monkeypatch, tmp_path):
    levels = tmp_path / 'game' / 'levels'
    levels.mkdir(parents=True)
    scene_path = levels / 'main.tscn'
    scene_path.write_text(
        '\n'.join(
            [
                '[gd_scene format=3]',
                '',
                '[node name="Main" type="Node"]',
                '',
                '[node name="Managers" type="Node" parent="."]',
                '',
                '[node name="EventBus" type="Node" parent="Managers"]',
                '',
                '[node name="UI" type="Control" parent="."]',
                '',
                '[node name="ScoreLabel" type="Label" parent="UI"]',
                'event_bus_path = NodePath("../../../Managers/EventBus")',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'workspace_root': tmp_path,
    })())

    result = runner.invoke(app, ['runtime', 'validate-paths', '--fix'])

    assert result.exit_code == 0
    assert 'fixed_path' in result.stdout
    assert 'ok=true' in result.stdout
    assert 'event_bus_path = NodePath("../../Managers/EventBus")' in scene_path.read_text(encoding='utf-8')


def test_runtime_validate_paths_fix_rewrites_unique_script_res_path(monkeypatch, tmp_path):
    scripts = tmp_path / 'game' / 'features' / 'demo' / 'scripts'
    levels = tmp_path / 'game' / 'levels'
    views = tmp_path / 'game' / 'ui' / 'views'
    scripts.mkdir(parents=True)
    levels.mkdir(parents=True)
    views.mkdir(parents=True)
    (levels / 'main.tscn').write_text('[gd_scene format=3]\n\n[node name="Main" type="Node"]\n', encoding='utf-8')
    target = views / 'game_over.tscn'
    target.write_text('[gd_scene format=3]\n\n[node name="GameOver" type="Control"]\n', encoding='utf-8')
    script_path = scripts / 'demo.gd'
    script_path.write_text(
        'extends Node\nconst GameOver = preload("res://game/ui/scenes/game_over.tscn")\n',
        encoding='utf-8',
    )
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'workspace_root': tmp_path,
    })())

    result = runner.invoke(app, ['runtime', 'validate-paths', '--fix'])

    assert result.exit_code == 0
    assert 'fixed_path' in result.stdout
    assert 'res://game/ui/views/game_over.tscn' in script_path.read_text(encoding='utf-8')


def test_scaffold_test_command_scaffolds_e2e_harness(monkeypatch, tmp_path):
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'workspace_root': tmp_path,
    })())

    result = runner.invoke(app, ['scaffold', 'test', 'start-game', '--kind', 'e2e'])

    assert result.exit_code == 0
    assert 'kind=e2e' in result.stdout
    assert (tmp_path / 'tests' / 'e2e' / 'start_game' / 'start_game_e2e.tscn').is_file()
    script = tmp_path / 'tests' / 'e2e' / 'start_game' / 'test_start_game_e2e.gd'
    assert script.is_file()
    assert 'InputSim/InputMap/UI signals' in script.read_text(encoding='utf-8')


def test_root_help_exposes_scaffold_not_top_level_test():
    result = runner.invoke(app, ['--help'])

    assert result.exit_code == 0
    assert 'scaffold' in result.stdout
    assert ' test ' not in result.stdout


def test_runtime_test_kind_uses_preset_patterns(monkeypatch, tmp_path):
    tests_root = tmp_path / 'tests'
    (tests_root / 'systems' / 'inventory').mkdir(parents=True)
    (tests_root / 'features' / 'pickup').mkdir(parents=True)
    (tests_root / 'systems' / 'inventory' / 'inventory_harness.tscn').write_text('', encoding='utf-8')
    (tests_root / 'features' / 'pickup' / 'pickup_harness.tscn').write_text('', encoding='utf-8')
    monkeypatch.setattr('godotter.interfaces.cli.get_settings', lambda: type('S', (), {
        'godot_path': '/usr/bin/godot',
        'workspace_root': tmp_path,
    })())
    monkeypatch.setattr('godotter.interfaces.cli.build_runner', lambda settings, project=None: FakeGodotRunner(settings.godot_path, settings.workspace_root))

    result = runner.invoke(app, ['runtime', 'test', '--kind', 'system'])

    assert result.exit_code == 0
    assert 'count=1' in result.stdout
    assert 'target=res://tests/systems/inventory/inventory_harness.tscn' in result.stdout
    assert 'features/pickup' not in result.stdout


def test_infer_test_kinds_and_expected_dirs_from_paths():
    paths = {
        'game/systems/inventory/scripts/inventory_manager.gd',
        'game/features/item_pickup/scripts/item_pickup_feature.gd',
        'ui/views/main_menu.tscn',
    }

    assert infer_test_kinds_for_paths(paths) == ['system', 'feature', 'integration', 'level-smoke', 'e2e']
    assert expected_test_dirs_for_paths(paths) == [
        'tests/systems/inventory/',
        'tests/features/item_pickup/',
        'tests/integration/',
        'tests/levels/',
        'tests/e2e/',
    ]
