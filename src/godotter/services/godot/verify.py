from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import secrets
import shutil
import subprocess
from typing import Any


VERIFY_REPORT_VERSION = 1
VERIFY_REPORT_STDIO_LIMIT = 12000


def new_verify_report_id() -> str:
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    return f'vr_{timestamp}_{secrets.token_hex(3)}'


def default_verify_commands() -> list[str]:
    return [
        'uv run godotter runtime validate-structure --workspace .',
        'uv run godotter runtime validate-managers --workspace .',
        'uv run godotter runtime validate-paths --workspace . --fix',
        'uv run godotter runtime validate-paths --workspace .',
        'uv run godotter runtime lint --project .',
        'uv run godotter runtime test --project . --kind all --timeout 30',
    ]


def default_verify_report_dir(workspace_root: Path) -> Path:
    return workspace_root / '.godotter' / 'reports' / 'verify'


def latest_verify_report_path(workspace_root: Path) -> Path:
    return default_verify_report_dir(workspace_root) / 'latest.json'


def resolve_verify_report_output(workspace_root: Path, output_path: Path | None = None) -> Path:
    if output_path is None:
        return default_verify_report_dir(workspace_root) / f'{new_verify_report_id()}.json'
    if output_path.is_absolute():
        return output_path
    return workspace_root / output_path


def load_latest_verify_report(workspace_root: Path) -> dict[str, Any] | None:
    path = latest_verify_report_path(workspace_root)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def run_verify(
    workspace_root: Path,
    *,
    commands: list[str] | None = None,
    output_path: Path | None = None,
    timeout: int = 300,
    fail_fast: bool = False,
    source: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Path]:
    root = workspace_root.resolve()
    selected_commands = commands or default_verify_commands()
    report_id = new_verify_report_id()
    checks: list[dict[str, Any]] = []
    failed_check: str | None = None

    for command in selected_commands:
        check = _run_check(root, command, timeout=timeout)
        checks.append(check)
        if check['result'] != 'pass' and failed_check is None:
            failed_check = check['name']
            if fail_fast:
                break

    result = 'pass' if all(check['result'] == 'pass' for check in checks) else 'fail'
    report = {
        'schema_version': VERIFY_REPORT_VERSION,
        'report_id': report_id,
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'workspace_root': root.as_posix(),
        'result': result,
        'failed_check': failed_check,
        'source': source or {},
        'commands': selected_commands,
        'checks': checks,
        'summary': {
            'total': len(checks),
            'passed': sum(1 for check in checks if check['result'] == 'pass'),
            'failed': sum(1 for check in checks if check['result'] != 'pass'),
        },
    }
    path = write_verify_report(root, report, output_path=output_path)
    return report, path


def write_verify_report(workspace_root: Path, report: dict[str, Any], *, output_path: Path | None = None) -> Path:
    path = resolve_verify_report_output(workspace_root, output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    report = dict(report)
    report['report_path'] = path.as_posix()
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    latest_path = latest_verify_report_path(workspace_root)
    latest_path.parent.mkdir(parents=True, exist_ok=True)
    if latest_path.resolve() != path.resolve():
        shutil.copyfile(path, latest_path)
    return path


def _run_check(workspace_root: Path, command: str, *, timeout: int) -> dict[str, Any]:
    started_at = datetime.now()
    try:
        completed = subprocess.run(
            command,
            cwd=workspace_root,
            capture_output=True,
            timeout=timeout,
            shell=True,
        )
        stdout = _decode_output(completed.stdout)
        stderr = _decode_output(completed.stderr)
        exit_code = int(completed.returncode)
        timed_out = False
        error = None
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_output(exc.stdout)
        stderr = _decode_output(exc.stderr)
        exit_code = -1
        timed_out = True
        error = f'timeout_after_seconds={timeout}'
    duration_ms = int((datetime.now() - started_at).total_seconds() * 1000)
    result = 'pass' if exit_code == 0 and not timed_out else 'fail'
    return {
        'name': _check_name(command),
        'command': command,
        'result': result,
        'exit_code': exit_code,
        'timed_out': timed_out,
        'duration_ms': duration_ms,
        'stdout': _trim_stdio(stdout),
        'stderr': _trim_stdio(stderr),
        'error': error,
    }


def _decode_output(data: bytes | str | None) -> str:
    if data is None:
        return ''
    if isinstance(data, str):
        return data.strip()
    return data.decode('utf-8', errors='replace').strip()


def _trim_stdio(text: str) -> str:
    if len(text) <= VERIFY_REPORT_STDIO_LIMIT:
        return text
    return text[-VERIFY_REPORT_STDIO_LIMIT:]


def _check_name(command: str) -> str:
    raw = command.strip().lower()
    if 'runtime validate-structure' in raw:
        return 'validate_structure'
    if 'runtime validate-managers' in raw:
        return 'validate_managers'
    if 'runtime validate-paths' in raw:
        return 'validate_paths_fix' if '--fix' in raw else 'validate_paths'
    if 'runtime lint' in raw:
        return 'lint'
    if 'runtime test' in raw:
        return 'test'
    if 'runtime run' in raw:
        return 'run'
    return 'command'
