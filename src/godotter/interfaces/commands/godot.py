from __future__ import annotations

from pathlib import Path

import typer

from godotter.config import Settings, get_settings
from godotter.services.godot.cli_helpers import (
    build_runner,
    format_doctor_report,
    format_runtime_result,
    format_uid_fix_result,
    resolve_runtime_target,
)
from godotter.services.project.scaffolding import (
    render_project_scaffold_summary,
    scaffold_godot_project,
    scaffold_scene_only,
    scaffold_scene_with_script,
    scaffold_test,
    test_kind_pattern,
)
from godotter.services.godot import (
    fix_uid_paths,
    list_build_reports,
    run_doctor,
    run_export_build,
    run_export_doctor,
    run_verify,
)
from godotter.services.godot.builds import find_export_templates_root, _detect_godot_version
from godotter.services.godot.validators import validate_managers, validate_nodepaths, validate_paths, validate_structure
from godotter.utils.envfile import EnvFile


app = typer.Typer(
    help='Godotter machine interface for workflow automation and Agent tools.',
    no_args_is_help=True,
)
runtime_app = typer.Typer(help='Godot runtime operations (run, lint, diagnose, fix UID issues).')
project_app = typer.Typer(help='Manage Godot projects and scaffolding.')
scene_app = typer.Typer(help='Create scenes and paired scripts (tscn + gd).')
test_app = typer.Typer(help='Create and manage Godotter test harnesses.')
scaffold_app = typer.Typer(help='Generate convention-compliant project files.')
export_app = typer.Typer(help='Build and list Godot export packages.')
template_app = typer.Typer(help='Configure export template paths.')
export_app.add_typer(template_app, name='template')

app.add_typer(project_app, name='project')
app.add_typer(scene_app, name='scene')
app.add_typer(scaffold_app, name='scaffold')
app.add_typer(runtime_app, name='runtime')
app.add_typer(export_app, name='export')


@app.command('new', hidden=True)
def new_command(
    name: str = typer.Argument('.', help='Project name or path. Use "." for current directory.'),
    no_git: bool = typer.Option(False, '--no-git', help='Skip git initialization.'),
) -> None:
    typer.echo('Warning: `godotter new` is deprecated. Use `godotter project create` instead.')
    project_new_command(name=name, no_git=no_git)


def _copy_settings(base: Settings, **overrides) -> Settings:
    try:
        return base.model_copy(update=overrides)
    except AttributeError:
        return base


def _resolve_workspace_root(
    base_settings: Settings,
    workspace: Path | None = None,
    project: str | None = None,
) -> tuple[Path, Settings]:
    if workspace:
        effective = workspace.resolve()
        return effective, _copy_settings(base_settings, workspace_root=effective)
    try:
        target = resolve_runtime_target(base_settings, project=project)
        effective = target.workspace_root
        return effective, _copy_settings(base_settings, workspace_root=effective)
    except Exception:
        root = base_settings.workspace_root.resolve()
        return root, _copy_settings(base_settings, workspace_root=root)


@project_app.command('new', help='Create a new Godot project with a minimal runnable scaffold.')
def project_new_command(
    name: str = typer.Argument('.', help='Project name or path. Use "." for current directory.'),
    no_git: bool = typer.Option(False, '--no-git', help='Skip git initialization.'),
) -> None:
    try:
        result = scaffold_godot_project(name, no_git=no_git)
    except ValueError as exc:
        typer.echo(f'Error: {exc}')
        raise typer.Exit(1) from exc
    typer.echo(render_project_scaffold_summary(result, no_git=no_git))


@project_app.command('root-show', help='Show the default parent directory for new projects.')
def project_root_show_command() -> None:
    settings = get_settings()
    root = Path(settings.projects_root)
    if not root.is_absolute():
        root = (Path.cwd() / root).resolve()
    root = root.resolve()
    typer.echo(f'projects_root={root.as_posix()}')
    typer.echo(f'exists={str(root.exists()).lower()}')


@project_app.command('root-set', help='Set the default parent directory for new projects.')
def project_root_set_command(
    path: str = typer.Argument(..., help='Directory path where new projects will be created.'),
) -> None:
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise typer.BadParameter(f'Path does not exist: {resolved}')
    EnvFile(Path('.env')).set('GODOTTER_PROJECTS_ROOT', resolved.as_posix())
    get_settings.cache_clear()
    typer.echo(f'projects_root={resolved.as_posix()}')


@scene_app.command('new', help='Create a scene (.tscn) and paired script (.gd) together.')
def scene_new_command(
    path: str = typer.Argument(..., help='Scene path (res://... or workspace-relative), must end with .tscn.'),
    kind: str = typer.Option('level', '--kind', help='Scene kind: level, ui, prefab.'),
    no_script: bool = typer.Option(False, '--no-script', help='Do not generate a .gd script (scene only).'),
    script_path: str | None = typer.Option(
        None,
        '--script',
        help='Optional script path (res://... or workspace-relative), must end with .gd. Defaults to same stem next to the scene.',
    ),
    layout: str = typer.Option(
        'split',
        '--layout',
        help='Default script layout when --script is not provided: split or colocated.',
    ),
    root_type: str | None = typer.Option(None, '--root-type', help='Override root node type (default depends on --kind).'),
    root_name: str | None = typer.Option(None, '--root-name', help='Override root node name (default inferred from filename).'),
    force: bool = typer.Option(False, '--force', help='Overwrite existing files if they already exist.'),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
) -> None:
    base_settings = get_settings()
    root, _ = _resolve_workspace_root(base_settings, workspace=workspace)
    try:
        if no_script:
            if script_path:
                raise ValueError('--no-script cannot be used together with --script')
            result_only = scaffold_scene_only(
                workspace_root=root,
                kind=kind,
                scene_path=path,
                root_type=root_type,
                root_name=root_name,
                force=force,
            )
            typer.echo(f'scene={result_only.scene_path.relative_to(root).as_posix()}')
            typer.echo('script=(none)')
            typer.echo(f'uid={result_only.uid}')
            return
        result = scaffold_scene_with_script(
            workspace_root=root,
            kind=kind,
            scene_path=path,
            script_path=script_path,
            root_type=root_type,
            root_name=root_name,
            layout=layout,
            force=force,
        )
    except ValueError as exc:
        typer.echo(f'Error: {exc}')
        raise typer.Exit(1) from exc
    typer.echo(f'scene={result.scene_path.relative_to(root).as_posix()}')
    typer.echo(f'script={result.script_path.relative_to(root).as_posix()}')
    typer.echo(f'uid={result.uid}')


@scene_app.command('create', help='Create a scene (.tscn) only (no script).')
def scene_create_command(
    path: str = typer.Argument(..., help='Scene path (res://... or workspace-relative), must end with .tscn.'),
    kind: str = typer.Option('level', '--kind', help='Scene kind: level, ui, prefab.'),
    root_type: str | None = typer.Option(None, '--root-type', help='Override root node type (default depends on --kind).'),
    root_name: str | None = typer.Option(None, '--root-name', help='Override root node name (default inferred from filename).'),
    force: bool = typer.Option(False, '--force', help='Overwrite existing file if it already exists.'),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
) -> None:
    base_settings = get_settings()
    root, _ = _resolve_workspace_root(base_settings, workspace=workspace)
    try:
        result = scaffold_scene_only(
            workspace_root=root,
            kind=kind,
            scene_path=path,
            root_type=root_type,
            root_name=root_name,
            force=force,
        )
    except ValueError as exc:
        typer.echo(f'Error: {exc}')
        raise typer.Exit(1) from exc
    typer.echo(f'scene={result.scene_path.relative_to(root).as_posix()}')
    typer.echo('script=(none)')
    typer.echo(f'uid={result.uid}')


@test_app.command('create', help='Create a test harness scene and paired test script.')
def test_create_command(
    name: str = typer.Argument(..., help='Test name, e.g. inventory, pickup_flow, main_menu_flow.'),
    kind: str = typer.Option(
        'feature',
        '--kind',
        help='Test kind: system, feature, integration, level-smoke, e2e.',
    ),
    force: bool = typer.Option(False, '--force', help='Overwrite existing files if they already exist.'),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
) -> None:
    _scaffold_test_command(name=name, kind=kind, force=force, workspace=workspace)


@scaffold_app.command('test', help='Create a test harness scene and paired test script.')
def scaffold_test_command(
    name: str = typer.Argument(..., help='Test name, e.g. inventory, pickup_flow, main_menu_flow.'),
    kind: str = typer.Option(
        'feature',
        '--kind',
        help='Test kind: system, feature, integration, level-smoke, e2e.',
    ),
    force: bool = typer.Option(False, '--force', help='Overwrite existing files if they already exist.'),
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
) -> None:
    _scaffold_test_command(name=name, kind=kind, force=force, workspace=workspace)


def _scaffold_test_command(
    *,
    name: str,
    kind: str,
    force: bool,
    workspace: Path | None,
) -> None:
    base_settings = get_settings()
    root, _ = _resolve_workspace_root(base_settings, workspace=workspace)
    try:
        result = scaffold_test(workspace_root=root, name=name, kind=kind, force=force)
    except ValueError as exc:
        typer.echo(f'Error: {exc}')
        raise typer.Exit(1) from exc
    typer.echo(f'kind={result.kind}')
    typer.echo(f'scene={result.scene_path.relative_to(root).as_posix()}')
    typer.echo(f'script={result.script_path.relative_to(root).as_posix()}')
    typer.echo(f'uid={result.uid}')


@export_app.command('build', help='Export a Godot project package using an export preset.')
def export_build_command(
    preset: str = typer.Option(..., '--preset', help='Godot export preset name, e.g. Web, Android, Windows Desktop.'),
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
    output: Path | None = typer.Option(
        None,
        '--output',
        help='Optional output file path. Relative paths are resolved under the project root.',
    ),
    debug: bool = typer.Option(False, '--debug', help='Use --export-debug instead of --export-release.'),
    timeout: int = typer.Option(1800, '--timeout', min=0, help='Timeout in seconds (0 = no timeout).'),
) -> None:
    settings = get_settings()
    target = resolve_runtime_target(settings, project=project)
    if not target.godot_path:
        raise typer.BadParameter('GODOT_PATH is not configured')
    report, path = run_export_build(
        godot_path=target.godot_path,
        workspace_root=target.workspace_root,
        preset=preset,
        output=output,
        release=not debug,
        timeout=timeout,
    )
    typer.echo(f'build_report={path.as_posix()}')
    typer.echo(f'build_id={report.build_id}')
    typer.echo(f'status={report.status}')
    typer.echo(f'preset={report.preset}')
    typer.echo(f'output={report.output_path}')
    typer.echo(f'exit_code={report.exit_code}')
    typer.echo(f'timed_out={str(report.timed_out).lower()}')
    for artifact in report.artifacts:
        typer.echo(f'artifact path={artifact.path} size_bytes={artifact.size_bytes}')
    if report.status != 'passed':
        raise typer.Exit(1)


@export_app.command('list', help='List package builds under .godotter/builds/.')
def export_list_command(
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
) -> None:
    settings = get_settings()
    target = resolve_runtime_target(settings, project=project)
    reports = list_build_reports(target.workspace_root)
    typer.echo(f'workspace_root={target.workspace_root.as_posix()}')
    typer.echo(f'count={len(reports)}')
    for report in reports[:50]:
        typer.echo(
            'build '
            f'id={report.get("build_id")} '
            f'status={report.get("status")} '
            f'preset={report.get("preset")} '
            f'artifacts={len(report.get("artifacts", []))}'
        )


@export_app.command('doctor', help='Check export presets and Godot export template availability.')
def export_doctor_command(
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
    timeout: int = typer.Option(15, '--timeout', min=1, help='Timeout in seconds for Godot version detection.'),
) -> None:
    settings = get_settings()
    target = resolve_runtime_target(settings, project=project)
    report = run_export_doctor(
        workspace_root=target.workspace_root,
        godot_path=target.godot_path,
        timeout=timeout,
        templates_path=settings.export_templates_path,
        android_sdk_path=settings.android_sdk_path,
        java_home=settings.java_home,
        keystore_path=settings.android_keystore_path,
    )
    typer.echo(f'workspace_root={report.workspace_root}')
    typer.echo(f'project_godot={str(report.project_exists).lower()}')
    typer.echo(f'export_presets={str(report.export_presets_exists).lower()}')
    typer.echo(f'presets={len(report.presets)}')
    for preset in report.presets:
        typer.echo(f'preset index={preset.index} name={preset.name} platform={preset.platform}')
    typer.echo(f'godot_configured={str(report.godot_configured).lower()}')
    typer.echo(f'godot_path_exists={str(report.godot_path_exists).lower()}')
    typer.echo(f'godot_version={report.godot_version or ""}')
    typer.echo(f'templates_root={report.templates_root or ""}')
    typer.echo(f'templates_detected={str(report.templates_detected).lower()}')

    typer.echo(f'android_sdk_path={report.android_sdk_path or "(not set)"}')
    typer.echo(f'android_sdk_valid={str(report.android_sdk_valid).lower()}')
    if report.android_build_tools_version:
        typer.echo(f'android_build_tools={report.android_build_tools_version}')
    typer.echo(f'android_adb={str(report.android_adb_exists).lower()}')

    typer.echo(f'java_home={report.java_home or "(not set)"}')
    typer.echo(f'java_valid={str(report.java_valid).lower()}')
    if report.java_version:
        typer.echo(f'java_version={report.java_version}')

    typer.echo(f'keystore_path={report.keystore_path or "(not set)"}')
    typer.echo(f'keystore_valid={str(report.keystore_valid).lower()}')
    typer.echo(f'android_template_installed={str(report.android_template_installed).lower()}')

    for warning in report.warnings:
        typer.echo(f'warning={warning}')
    for error in report.errors:
        typer.echo(f'error={error}')
    typer.echo(f'ok={str(report.ok).lower()}')
    if not report.ok:
        raise typer.Exit(1)


@template_app.command('set', help='Set the path to Godot export templates.')
def export_template_set_command(
    path: str = typer.Argument(..., help='Path to the export templates directory.'),
) -> None:
    resolved = Path(path).resolve()
    if not resolved.exists():
        raise typer.BadParameter(f'Path does not exist: {resolved}')
    EnvFile(Path('.env')).set('GODOTTER_EXPORT_TEMPLATES_PATH', resolved.as_posix())
    typer.echo(f'export-templates-path={resolved.as_posix()}')


@template_app.command('show', help='Show the current export templates path configuration.')
def export_template_show_command() -> None:
    settings = get_settings()
    configured = settings.export_templates_path
    if configured:
        exists = ' (exists)' if Path(configured).exists() else ' (NOT FOUND)'
        typer.echo(f'configured: {configured}{exists}')
    else:
        typer.echo('configured: (not set)')
    godot_path = settings.godot_path
    if godot_path and Path(godot_path).exists():
        version = _detect_godot_version(godot_path, timeout=15)
        if version:
            auto = find_export_templates_root(version)
            if auto:
                typer.echo(f'auto-detected: {auto}')
            else:
                typer.echo('auto-detected: (not found)')


@runtime_app.command('lint', help='Lint Godot scripts or the entire project.')
def runtime_lint_command(
    path: str | None = typer.Argument(None, help='Path to a specific script file (lints entire project if omitted).'),
    timeout: int = typer.Option(60, '--timeout', help='Timeout in seconds for the lint operation.'),
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
    headless: bool = typer.Option(True, '--headless/--no-headless', help='Run Godot editor in headless mode.'),
    fail_on_stderr: str = typer.Option(
        'SCRIPT ERROR:;Parse Error:;ERROR:',
        '--fail-on-stderr',
        help='Semicolon-separated substrings; if any appears in stderr, mark lint as failed.',
    ),
) -> None:
    settings = get_settings()
    runner = build_runner(settings, project=project)
    if path:
        result = runner.lint_script(path, timeout=timeout)
        target = path
    else:
        result = runner.lint_project(timeout=timeout)
        target = '(project)'
    typer.echo(format_runtime_result('script_lint', target, result))
    stderr_text = result.stderr or ''
    bad_markers = [m for m in (x.strip() for x in fail_on_stderr.split(';')) if m]
    marker_hit = any(m in stderr_text for m in bad_markers)
    if result.exit_code != 0 or result.timed_out or marker_hit:
        raise typer.Exit(1)


@runtime_app.command('run', help='Run the Godot project or a specific scene.')
def runtime_run_command(
    scene: str | None = typer.Option(None, '--scene', help='Scene file path to run (runs main scene if omitted).'),
    timeout: int = typer.Option(60, '--timeout', help='Timeout in seconds for the run operation (0 = no timeout).'),
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
    headless: bool = typer.Option(False, '--headless', help='Run with Godot --headless (recommended for CI/tests).'),
) -> None:
    settings = get_settings()
    runner = build_runner(settings, project=project)
    result = runner.run_project(timeout=timeout, scene=scene, headless=headless)
    typer.echo(format_runtime_result('headless_run', scene or '(project)', result))
    if result.exit_code != 0 or result.timed_out:
        raise typer.Exit(1)


@runtime_app.command('test', help='Run headless test scenes under tests/ (auto-discovers harness scenes).')
def runtime_test_command(
    scene: str | None = typer.Option(
        None,
        '--scene',
        help='Run a single test scene (res://... or workspace-relative path). Overrides --pattern.',
    ),
    pattern: str = typer.Option(
        '*_harness.tscn;*_smoke.tscn',
        '--pattern',
        help='Semicolon-separated glob patterns to match test scenes under tests/.',
    ),
    kind: str | None = typer.Option(
        None,
        '--kind',
        help='Test kind preset: unit, system, feature, integration, level-smoke, e2e, all. Overrides --pattern.',
    ),
    timeout: int = typer.Option(60, '--timeout', help='Timeout in seconds for each test scene.'),
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
    include_core_harness: bool = typer.Option(
        False,
        '--include-core-harness',
        help='Include tests/core/test_harness.tscn (normally skipped; often not auto-quit).',
    ),
    fail_on_stderr: str = typer.Option(
        'SCRIPT ERROR:;FAIL:',
        '--fail-on-stderr',
        help='Semicolon-separated substrings; if any appears in stderr, mark test as failed.',
    ),
    diagnose: bool = typer.Option(
        False,
        '--diagnose',
        help='On failure, parse test output and emit a structured failure summary for agent consumption.',
    ),
) -> None:
    settings = get_settings()
    runner = build_runner(settings, project=project)
    tests_root = runner.workspace_root / 'tests'
    if not tests_root.exists():
        typer.echo(f'tests_root={tests_root.as_posix()}')
        typer.echo('count=0')
        return

    if scene:
        raw = scene.strip().strip('"').strip("'")
        if raw.startswith('res://'):
            scene_res = raw
        else:
            rel = raw.lstrip('/').lstrip('\\')
            scene_res = f"res://{rel.replace('\\\\', '/')}"
        result = runner.run_project(timeout=timeout, scene=scene_res, headless=True)
        typer.echo(format_runtime_result('headless_run', scene_res, result))
        stderr_text = result.stderr or ''
        bad_markers = [m for m in (x.strip() for x in fail_on_stderr.split(';')) if m]
        marker_hit = any(m in stderr_text for m in bad_markers)
        if result.exit_code != 0 or result.timed_out or marker_hit:
            raise typer.Exit(1)
        return

    if kind:
        try:
            pattern = test_kind_pattern(kind)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

    patterns = [p.strip().strip('"').strip("'") for p in pattern.split(';') if p.strip()]
    scenes: list[Path] = []
    for pat in patterns:
        scenes.extend(tests_root.rglob(pat))

    # Stable order, unique.
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in sorted(scenes, key=lambda x: x.as_posix()):
        if p in seen or not p.is_file():
            continue
        if not include_core_harness and p.name == 'test_harness.tscn' and 'tests/core/' in p.as_posix().replace('\\', '/'):
            continue
        seen.add(p)
        unique.append(p)

    typer.echo(f'tests_root={tests_root.as_posix()}')
    typer.echo(f'count={len(unique)}')
    if not unique:
        return

    failures: list[str] = []
    bad_markers = [m for m in (x.strip() for x in fail_on_stderr.split(';')) if m]
    for scene_path in unique:
        rel = scene_path.relative_to(runner.workspace_root).as_posix()
        scene_res = f'res://{rel}'
        result = runner.run_project(timeout=timeout, scene=scene_res, headless=True)
        typer.echo(format_runtime_result('headless_run', scene_res, result))
        stderr_text = result.stderr or ''
        marker_hit = any(m in stderr_text for m in bad_markers)
        if result.exit_code != 0 or result.timed_out or marker_hit:
            failures.append(scene_res)

    if failures:
        typer.echo(f'failures={len(failures)}')
        for s in failures[:25]:
            typer.echo(f'failure={s}')
        if diagnose:
            _emit_test_diagnose(failures, unique, runner, timeout, bad_markers)
        raise typer.Exit(1)


def _emit_test_diagnose(
    failures: list[str],
    scenes: list[Path],
    runner,
    timeout: int,
    bad_markers: list[str],
) -> None:
    """Re-run each failing scene once and parse stderr for structured failure analysis."""
    typer.echo('')
    typer.echo('diagnose')
    for scene_res in failures:
        result = runner.run_project(timeout=timeout, scene=scene_res, headless=True)
        stderr = result.stderr or ''
        stdout = result.stdout or ''

        fail_lines = [line.strip() for line in stderr.splitlines() if 'FAIL:' in line]
        pass_lines = [line.strip() for line in stdout.splitlines() if 'PASS:' in line]
        assert_count = len(pass_lines) + len(fail_lines)

        typer.echo(f'  scene: {scene_res}')
        typer.echo(f'  exit_code: {result.exit_code}')
        typer.echo(f'  assertions_total: {assert_count}')
        typer.echo(f'  assertions_passed: {len(pass_lines)}')
        typer.echo(f'  assertions_failed: {len(fail_lines)}')

        for fl in fail_lines:
            typer.echo(f'  failed: {fl.removeprefix("FAIL: ").strip()}')

        # Extract last meaningful lines from stdout (skip verbose engine output)
        relevant_lines = [
            line for line in stdout.splitlines()
            if line.strip() and not any(
                prefix in line for prefix in ('Godot Engine', 'Vulkan', 'OpenGL', 'ERROR: Condition', 'ERROR:', 'WARNING:', '   at:')
            )
        ]
        last = relevant_lines[-15:]
        if last:
            typer.echo('  last_stdout:')
            for line in last:
                typer.echo(f'    {line.strip()[:200]}')

        # Simple heuristic suggestions
        suggestions = []
        stderr_lower = stderr.lower()
        if 'expected ' in stderr_lower and "got ''" in stderr_lower:
            suggestions.append('variable_set_in_callback_but_read_as_empty — check signal timing, try await get_tree().process_frame before assertion')
        if 'not in bounds' in stderr_lower or 'wall collision' in stderr_lower:
            suggestions.append('unexpected_wall_collision — check grid_size or position initialization')
        if 'unexpectedly' in stderr_lower or 'died unexpectedly' in stderr_lower:
            suggestions.append('unexpected_death — check collision detection or initial body placement')
        if 'body should' in stderr_lower or 'size is' in stderr_lower:
            suggestions.append('body_size_mismatch — check grow_pending logic or tail removal')
        if suggestions:
            typer.echo('  suggest:')
            for s in suggestions:
                typer.echo(f'    - {s}')
        typer.echo('')


@runtime_app.command('verify', help='Run standard validation/lint/tests and write a VerifyReport JSON.')
def runtime_verify_command(
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
    json_output: Path | None = typer.Option(
        None,
        '--json-output',
        help='Write VerifyReport JSON to this path (relative paths are resolved under the project root).',
    ),
    timeout: int = typer.Option(300, '--timeout', min=1, help='Timeout in seconds for each verification command.'),
    fail_fast: bool = typer.Option(
        False,
        '--fail-fast/--no-fail-fast',
        help='Stop after the first failed check instead of collecting all check results.',
    ),
) -> None:
    settings = get_settings()
    target = resolve_runtime_target(settings, project=project)
    report, path = run_verify(
        target.workspace_root,
        output_path=json_output,
        timeout=timeout,
        fail_fast=fail_fast,
        source={'command': 'runtime verify', 'project': project},
    )
    typer.echo(f'report={path.as_posix()}')
    typer.echo(f'result={report["result"]}')
    for check in report['checks']:
        typer.echo(
            'check '
            f'name={check["name"]} '
            f'result={check["result"]} '
            f'exit_code={check["exit_code"]} '
            f'timed_out={str(check["timed_out"]).lower()}'
        )
    if report['result'] != 'pass':
        raise typer.Exit(1)


@runtime_app.command('doctor', help='Diagnose Godot environment and project health.')
def runtime_doctor_command(
    timeout: int = typer.Option(15, '--timeout', help='Timeout in seconds for the diagnosis.'),
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
) -> None:
    settings = get_settings()
    target = resolve_runtime_target(settings, project=project)
    report = run_doctor(target.workspace_root, target.godot_path, timeout=timeout)
    typer.echo(format_doctor_report(report))


@runtime_app.command('uid-fix', help='Fix UID references in Godot project files.')
def runtime_uid_fix_command(
    dry_run: bool = typer.Option(True, '--dry-run/--write', help='Preview changes (default) or actually write fixes.'),
    project: str | None = typer.Option(None, '--project', help='Project name or path (uses default project if omitted).'),
) -> None:
    settings = get_settings()
    target = resolve_runtime_target(settings, project=project)
    result = fix_uid_paths(target.workspace_root, dry_run=dry_run)
    typer.echo(format_uid_fix_result(result, dry_run=dry_run, workspace_root=target.workspace_root))


@runtime_app.command('validate-structure', help='Validate project directory structure for the Godotter dev mode.')
def runtime_validate_structure_command(
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
) -> None:
    base_settings = get_settings()
    root, _ = _resolve_workspace_root(base_settings, workspace=workspace)
    report = validate_structure(root)
    typer.echo(f'workspace_root={root.as_posix()}')
    typer.echo(f'ok={str(report.ok).lower()}')
    for issue in report.issues:
        typer.echo(f'issue code={issue.code} message={issue.message}')
    if not report.ok:
        raise typer.Exit(1)


@runtime_app.command('validate-managers', help='Validate Managers/EventBus conventions for level scenes.')
def runtime_validate_managers_command(
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    levels: Path | None = typer.Option(
        None,
        '--levels',
        help='Levels root directory (defaults to workspace/game/levels).',
    ),
) -> None:
    base_settings = get_settings()
    root, _ = _resolve_workspace_root(base_settings, workspace=workspace)
    if levels:
        levels_root = levels.resolve() if levels.is_absolute() else (root / levels).resolve()
    else:
        levels_root = None
    report = validate_managers(root, levels_root=levels_root)
    typer.echo(f'workspace_root={root.as_posix()}')
    typer.echo(f'levels_root={(levels_root or (root / "game" / "levels")).as_posix()}')
    typer.echo(f'ok={str(report.ok).lower()}')
    for issue in report.issues:
        typer.echo(f'issue code={issue.code} message={issue.message}')
    if not report.ok:
        raise typer.Exit(1)


@runtime_app.command('validate-nodepaths', help='Validate exported NodePath properties in level scenes.')
def runtime_validate_nodepaths_command(
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    scenes: Path | None = typer.Option(
        None,
        '--scenes',
        help='Scenes root directory (defaults to workspace/game/levels).',
    ),
) -> None:
    base_settings = get_settings()
    root, _ = _resolve_workspace_root(base_settings, workspace=workspace)
    if scenes:
        scenes_root = scenes.resolve() if scenes.is_absolute() else (root / scenes).resolve()
    else:
        scenes_root = None
    report = validate_nodepaths(root, scenes_root=scenes_root)
    typer.echo(f'workspace_root={root.as_posix()}')
    typer.echo(f'scenes_root={(scenes_root or (root / "game" / "levels")).as_posix()}')
    typer.echo(f'ok={str(report.ok).lower()}')
    for issue in report.issues:
        typer.echo(f'issue code={issue.code} message={issue.message}')
    if not report.ok:
        raise typer.Exit(1)


@runtime_app.command('validate-paths', help='Validate scene NodePaths and res:// resource paths.')
def runtime_validate_paths_command(
    workspace: Path | None = typer.Option(
        None,
        '--workspace',
        help='Workspace root path (defaults to current directory / GODOTTER_WORKSPACE_ROOT).',
    ),
    fix: bool = typer.Option(
        False,
        '--fix',
        help='Rewrite unresolved paths when exactly one safe suggestion is available.',
    ),
) -> None:
    base_settings = get_settings()
    root, _ = _resolve_workspace_root(base_settings, workspace=workspace)
    report = validate_paths(root, fix=fix)
    typer.echo(f'workspace_root={root.as_posix()}')
    typer.echo(f'fix={str(fix).lower()}')
    typer.echo(f'ok={str(report.ok).lower()}')
    for issue in report.issues:
        typer.echo(f'issue code={issue.code} message={issue.message}')
    if not report.ok:
        raise typer.Exit(1)

