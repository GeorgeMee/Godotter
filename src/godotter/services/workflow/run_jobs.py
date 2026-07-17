from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
from pathlib import Path

from godotter.config import get_settings
from godotter.services.chat import ChatSessionRepository, SessionService


ACTIVE_RUN_STATUSES = {'queued', 'running'}
RUN_PROCESSES: dict[str, subprocess.Popen[str]] = {}
RUN_PROCESS_LOCK = threading.Lock()


class RunJobError(ValueError):
    pass


def approved_review_item_ids(review: dict[str, object]) -> list[str]:
    items = review.get('items', [])
    if not isinstance(items, list):
        return []
    return [
        str(item['item_id'])
        for item in items
        if isinstance(item, dict) and item.get('status') == 'approved' and item.get('item_id')
    ]


def create_run_job(
    workspace_root: Path,
    project_name: str,
    session_id: str,
    review: dict[str, object],
    *,
    item_ids: list[str] | None = None,
) -> dict[str, object]:
    approved_ids = approved_review_item_ids(review)
    requested_ids = item_ids or approved_ids
    task_ids = [item_id for item_id in requested_ids if item_id in approved_ids]
    if not task_ids:
        raise RunJobError('no_approved_items_to_run')
    planpack_path = str(review.get('planpack_path', '')).strip()
    if not planpack_path:
        raise RunJobError('review_missing_planpack_path')

    run_id = _new_id('rj')
    run = {
        'run_id': run_id,
        'session_id': session_id,
        'review_id': review['review_id'],
        'project_name': project_name,
        'workspace_root': workspace_root.as_posix(),
        'status': 'queued',
        'task_ids': task_ids,
        'created_at': _now_iso(),
        'started_at': None,
        'finished_at': None,
        'commands': [],
        'artifacts': {'planpack_path': planpack_path},
    }
    save_run(workspace_root, session_id, run)
    repository = ChatSessionRepository(workspace_root)
    service = SessionService(get_settings(), repository)
    service.set_latest_run(repository.load_session(session_id), run_id, status='running')
    append_run_event(workspace_root, session_id, run_id, {'type': 'status', 'message': 'run_queued'})
    return run


def start_run_job_background(workspace_root: Path, session_id: str, run_id: str, *, repo_root: Path) -> None:
    worker = threading.Thread(
        target=run_job_worker,
        args=(workspace_root, session_id, run_id, repo_root),
        name=f'godotter-web-run-{run_id}',
        daemon=True,
    )
    worker.start()


def run_job_worker(workspace_root: Path, session_id: str, run_id: str, repo_root: Path) -> None:
    try:
        execute_run_job_sync(workspace_root, session_id, run_id, repo_root=repo_root)
    except Exception as exc:
        try:
            run = load_run(workspace_root, session_id, run_id)
            run['status'] = 'failed'
            run['finished_at'] = _now_iso()
            run.setdefault('commands', [])
            run['error'] = str(exc)
            save_run(workspace_root, session_id, run)
            append_run_event(
                workspace_root,
                session_id,
                run_id,
                {'type': 'error', 'message': f'runner_exception: {exc}'},
            )
            repository = ChatSessionRepository(workspace_root)
            service = SessionService(get_settings(), repository)
            service.set_status(repository.load_session(session_id), 'blocked')
        except Exception:
            pass


def execute_run_job_sync(
    workspace_root: Path,
    session_id: str,
    run_id: str,
    *,
    repo_root: Path,
) -> dict[str, object]:
    run = load_run(workspace_root, session_id, run_id)
    run['status'] = 'running'
    run['started_at'] = _now_iso()
    save_run(workspace_root, session_id, run)
    append_run_event(workspace_root, session_id, run_id, {'type': 'status', 'message': 'run_started'})

    planpack_path = str(run.get('artifacts', {}).get('planpack_path', ''))
    commands: list[dict[str, object]] = []
    exit_codes: list[int] = []
    for task_id in run.get('task_ids', []):
        command = [
            'uv',
            'run',
            'godotter',
            'plan',
            'run',
            '--plan',
            planpack_path,
            '--workspace',
            workspace_root.as_posix(),
            '--only',
            str(task_id),
        ]
        append_run_event(workspace_root, session_id, run_id, {'type': 'command', 'task_id': task_id, 'message': ' '.join(command)})
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
            start_new_session=sys.platform != 'win32',
        )
        register_run_process(session_id, run_id, process)
        timed_out = {'value': False}
        timeout_seconds = command_timeout_seconds()

        def kill_on_timeout() -> None:
            if process.poll() is None:
                timed_out['value'] = True
                append_run_event(
                    workspace_root,
                    session_id,
                    run_id,
                    {
                        'type': 'timeout',
                        'task_id': task_id,
                        'message': f'command_timeout_seconds={timeout_seconds}',
                    },
                )
                terminate_process_tree(process.pid)

        timer = threading.Timer(timeout_seconds, kill_on_timeout) if timeout_seconds > 0 else None
        if timer is not None:
            timer.daemon = True
            timer.start()
        stdout_lines: list[str] = []
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    stdout_lines.append(line)
                    append_run_event(
                        workspace_root,
                        session_id,
                        run_id,
                        {'type': 'stdout', 'task_id': task_id, 'message': line.rstrip()},
                    )
            return_code = process.wait()
        finally:
            if timer is not None:
                timer.cancel()
            unregister_run_process(session_id, run_id, process)
        command_result = {
            'task_id': task_id,
            'command': command,
            'exit_code': return_code,
            'stdout': ''.join(stdout_lines),
            'stderr': '',
            'timed_out': timed_out['value'],
        }
        stdout_text = str(command_result['stdout'])
        runstate_path = extract_prefixed_path(stdout_text, 'runstate=')
        verify_report_path = extract_prefixed_path(stdout_text, 'task_run_verify_report=') or extract_prefixed_path(stdout_text, 'report=')
        if runstate_path:
            command_result['runstate_path'] = runstate_path
            runstate = read_artifact_json(workspace_root, runstate_path)
            if runstate is not None:
                command_result['runstate'] = runstate
        if verify_report_path:
            command_result['verify_report_path'] = verify_report_path
            verify_report = read_artifact_json(workspace_root, verify_report_path)
            if verify_report is not None:
                command_result['verify_report'] = verify_report
        commands.append(command_result)
        exit_codes.append(return_code)
        append_run_event(
            workspace_root,
            session_id,
            run_id,
            {
                'type': 'command_result',
                'task_id': task_id,
                'message': f'exit_code={return_code}',
                'payload': command_result,
            },
        )
        stored_run = load_run(workspace_root, session_id, run_id)
        if stored_run.get('status') == 'interrupted':
            stored_run['commands'] = commands
            stored_run['finished_at'] = stored_run.get('finished_at') or _now_iso()
            save_run(workspace_root, session_id, stored_run)
            return stored_run
        stored_run['commands'] = commands
        save_run(workspace_root, session_id, stored_run)
        if timed_out['value']:
            break
        if return_code != 0:
            break

    run['commands'] = commands
    run['finished_at'] = _now_iso()
    run['status'] = 'passed' if exit_codes and all(code == 0 for code in exit_codes) else 'failed'
    save_run(workspace_root, session_id, run)
    append_run_event(workspace_root, session_id, run_id, {'type': 'status', 'message': str(run['status'])})

    repository = ChatSessionRepository(workspace_root)
    service = SessionService(get_settings(), repository)
    service.set_status(repository.load_session(session_id), 'completed' if run['status'] == 'passed' else 'blocked')
    return run


def load_run(workspace_root: Path, session_id: str, run_id: str) -> dict[str, object]:
    path = run_path(workspace_root, session_id, run_id)
    if not path.exists():
        raise FileNotFoundError(run_id)
    return read_json(path)


def save_run(workspace_root: Path, session_id: str, run: dict[str, object]) -> None:
    write_json(run_path(workspace_root, session_id, str(run['run_id'])), run)


def append_run_event(workspace_root: Path, session_id: str, run_id: str, event: dict[str, object]) -> None:
    event_payload = {'created_at': _now_iso(), **event}
    path = run_events_path(workspace_root, session_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(event_payload, ensure_ascii=False) + '\n')


def read_run_events(workspace_root: Path, session_id: str, run_id: str, *, after: int = 0) -> list[dict[str, object]]:
    path = run_events_path(workspace_root, session_id, run_id)
    if not path.exists():
        return []
    events: list[dict[str, object]] = []
    for index, line in enumerate(path.read_text(encoding='utf-8').splitlines()):
        if index < after or not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        event['index'] = index
        events.append(event)
    return events


def list_runs(workspace_root: Path, session_id: str) -> list[dict[str, object]]:
    runs_root = runs_dir(workspace_root, session_id)
    if not runs_root.exists():
        return []
    runs: list[dict[str, object]] = []
    for path in sorted(runs_root.glob('rj_*.json'), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            runs.append(enrich_run_artifacts(workspace_root, read_json(path)))
        except Exception:
            continue
    return runs


def enrich_run_artifacts(workspace_root: Path, run: dict[str, object]) -> dict[str, object]:
    enriched = dict(run)
    commands = []
    for command in enriched.get('commands', []) or []:
        if isinstance(command, dict):
            commands.append(enrich_command_artifacts(workspace_root, command))
    enriched['commands'] = commands
    return enriched


def enrich_command_artifacts(workspace_root: Path, command: dict[str, object]) -> dict[str, object]:
    enriched = dict(command)
    runstate_path = str(enriched.get('runstate_path') or '').strip()
    verify_report_path = str(enriched.get('verify_report_path') or '').strip()
    if runstate_path and 'runstate' not in enriched:
        runstate = read_artifact_json(workspace_root, runstate_path)
        if runstate is not None:
            enriched['runstate'] = runstate
    if verify_report_path and 'verify_report' not in enriched:
        verify_report = read_artifact_json(workspace_root, verify_report_path)
        if verify_report is not None:
            enriched['verify_report'] = verify_report
    return enriched


def read_artifact_json(workspace_root: Path, value: str) -> dict[str, object] | None:
    path = Path(value)
    if not path.is_absolute():
        path = workspace_root / path
    try:
        if not path.exists() or not path.is_file():
            return None
        return read_json(path)
    except Exception:
        return None


def find_active_run_for_review(
    workspace_root: Path,
    session_id: str,
    review_id: str,
    *,
    item_ids: list[str] | None = None,
) -> dict[str, object] | None:
    requested = set(item_ids or [])
    for run in list_runs(workspace_root, session_id):
        if run.get('review_id') != review_id:
            continue
        if str(run.get('status', '')) not in ACTIVE_RUN_STATUSES:
            continue
        if requested and set(str(item_id) for item_id in run.get('task_ids', [])) != requested:
            continue
        return run
    return None


def run_process_key(session_id: str, run_id: str) -> str:
    return f'{session_id}:{run_id}'


def register_run_process(session_id: str, run_id: str, process: subprocess.Popen[str]) -> None:
    with RUN_PROCESS_LOCK:
        RUN_PROCESSES[run_process_key(session_id, run_id)] = process


def unregister_run_process(session_id: str, run_id: str, process: subprocess.Popen[str]) -> None:
    with RUN_PROCESS_LOCK:
        key = run_process_key(session_id, run_id)
        if RUN_PROCESSES.get(key) is process:
            RUN_PROCESSES.pop(key, None)


def terminate_run_process(session_id: str, run_id: str) -> bool:
    with RUN_PROCESS_LOCK:
        process = RUN_PROCESSES.get(run_process_key(session_id, run_id))
    if process is None or process.poll() is not None:
        return False
    terminate_process_tree(process.pid)
    return True


def terminate_process_tree(pid: int) -> None:
    if pid <= 0:
        return
    if sys.platform == 'win32':
        subprocess.run(
            ['taskkill', '/PID', str(pid), '/T', '/F'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(pid, 15)
        except Exception:
            try:
                os.kill(pid, 15)
            except Exception:
                pass


def command_timeout_seconds() -> int:
    raw = os.getenv('GODOTTER_WEB_RUN_COMMAND_TIMEOUT_SECONDS', '900').strip()
    try:
        value = int(raw)
    except ValueError:
        return 900
    return max(value, 0)


def extract_prefixed_path(text: str, prefix: str) -> str | None:
    for line in reversed((text or '').splitlines()):
        raw = line.strip()
        if raw.startswith(prefix):
            value = raw[len(prefix) :].strip()
            return value or None
    return None


def runs_dir(workspace_root: Path, session_id: str) -> Path:
    return workspace_root / '.godotter' / 'sessions' / session_id / 'runs'


def run_path(workspace_root: Path, session_id: str, run_id: str) -> Path:
    return runs_dir(workspace_root, session_id) / f'{run_id}.json'


def run_events_path(workspace_root: Path, session_id: str, run_id: str) -> Path:
    return runs_dir(workspace_root, session_id) / f'{run_id}.events.jsonl'


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')


def read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding='utf-8'))


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec='seconds')


def _new_id(prefix: str) -> str:
    return f'{prefix}_{secrets.token_hex(8)}'

