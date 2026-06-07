from __future__ import annotations

import html
import json
import os
import sys
import secrets
import subprocess
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from godotter.agent import Agent
from godotter.config import get_settings
from godotter.context import Memory
from godotter.llm import create_brain
from godotter.operations.projects import scaffold_godot_project
from godotter.project_registry import load_project_registry
from godotter.runtime.builds import list_build_reports, run_export_build, run_export_doctor
from godotter.tasks.planpack import (
    PlanPack,
    PlanState,
    PlanTask,
    new_plan_id,
    plan_state_path,
    write_planpack,
    write_planstate,
)
from godotter.tasks.planning import (
    ScoutPromptRef,
    build_plan_prompt,
    normalize_plan_dependencies,
    validate_plan_tasks,
)
from godotter.tasks.scout import scout_workspace
from godotter.tools import ToolRegistry, build_default_tools


app = FastAPI(title='Godotter Web Console', version='0.0.1')

ENV_FILENAME = '.env'
PROJECTS_CONFIG = Path('config') / 'projects.toml'
STATIC_DIR = Path(__file__).parent / 'static'
ACTIVE_RUN_STATUSES = {'queued', 'running'}
RUN_PROCESSES: dict[str, subprocess.Popen[str]] = {}
RUN_PROCESS_LOCK = threading.Lock()

if STATIC_DIR.exists():
    app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


def _repo_root() -> Path:
    # bin/run_web.* sets CWD to repo root; this is a fallback.
    cwd = Path.cwd()
    if (cwd / 'pyproject.toml').exists():
        return cwd
    for parent in [cwd, *cwd.parents]:
        if (parent / 'pyproject.toml').exists():
            return parent
    return cwd


def _env_path() -> Path:
    return _repo_root() / ENV_FILENAME


def _projects_path() -> Path:
    return _repo_root() / PROJECTS_CONFIG


def _default_new_project_parent() -> Path:
    return _repo_root() / 'tmp'


def _validate_project_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise HTTPException(status_code=400, detail='project_name_required')
    allowed = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-')
    if any(ch not in allowed for ch in normalized):
        raise HTTPException(status_code=400, detail='project_name_must_use_letters_numbers_dash_underscore')
    return normalized


def _write_projects_toml(default_project: str | None, projects: dict[str, dict[str, object]]) -> None:
    lines: list[str] = []
    if default_project:
        lines.append(f'default_project = "{default_project}"')
        lines.append('')
    for name in sorted(projects):
        item = projects[name]
        lines.append(f'[projects.{name}]')
        workspace_text = str(item['workspace_root']).replace('\\', '/')
        lines.append(f'workspace_root = "{workspace_text}"')
        for optional_key in ('godot_path', 'main_scene', 'platform'):
            value = item.get(optional_key)
            if value:
                escaped = str(value).replace('\\', '/')
                lines.append(f'{optional_key} = "{escaped}"')
        lines.append('')
    _projects_path().parent.mkdir(parents=True, exist_ok=True)
    _projects_path().write_text('\n'.join(lines).rstrip() + '\n', encoding='utf-8', newline='\n')


def _register_project(name: str, workspace_root: Path, *, set_default: bool = True) -> None:
    registry = load_project_registry(_projects_path())
    if name in registry.projects:
        raise HTTPException(status_code=409, detail='project_already_registered')
    projects: dict[str, dict[str, object]] = {}
    for existing_name, entry in registry.projects.items():
        projects[existing_name] = {
            'workspace_root': entry.workspace_root.as_posix(),
            'godot_path': entry.godot_path,
            'main_scene': entry.main_scene,
            'platform': entry.platform,
        }
    projects[name] = {'workspace_root': workspace_root.resolve().as_posix()}
    _write_projects_toml(name if set_default else registry.default_project, projects)


def _registered_projects() -> dict[str, dict[str, object]]:
    registry = load_project_registry(_projects_path())
    projects: dict[str, dict[str, object]] = {}
    for name, entry in registry.projects.items():
        root = entry.workspace_root.resolve()
        projects[name] = {
            'name': name,
            'workspace_root': root.as_posix(),
            'exists': root.exists(),
            'is_default': name == registry.default_project,
            'godot_path': entry.godot_path,
            'main_scene': entry.main_scene,
            'platform': entry.platform,
        }
    return projects


def _project_root_or_404(name: str) -> Path:
    registry = load_project_registry(_projects_path())
    entry = registry.projects.get(name)
    if entry is None:
        raise HTTPException(status_code=404, detail='unknown_project')
    return entry.workspace_root.resolve()


def _run_git(workspace_root: Path, args: list[str], *, timeout: int = 30, require_repo: bool = True) -> dict[str, object]:
    if require_repo and not (workspace_root / '.git').exists():
        raise HTTPException(status_code=400, detail='workspace_is_not_a_git_repository')
    completed = subprocess.run(
        ['git', *args],
        cwd=workspace_root,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
    )
    return {
        'args': ['git', *args],
        'exit_code': completed.returncode,
        'stdout': completed.stdout,
        'stderr': completed.stderr,
        'ok': completed.returncode == 0,
    }


def _safe_git_relpath(value: str) -> str:
    text = value.strip().replace('\\', '/')
    if not text or text.startswith('/') or '..' in Path(text).parts:
        raise HTTPException(status_code=400, detail='invalid_git_path')
    return text


def _safe_git_branch(value: str) -> str:
    text = value.strip()
    if not text or text.startswith('-') or any(ch.isspace() for ch in text):
        raise HTTPException(status_code=400, detail='invalid_git_branch')
    if '..' in text or text.endswith('/') or text.endswith('.lock') or '@{' in text:
        raise HTTPException(status_code=400, detail='invalid_git_branch')
    return text


def _safe_project_relpath(value: str) -> str:
    text = value.strip().replace('\\', '/')
    if not text:
        return ''
    if text.startswith('/') or '..' in Path(text).parts:
        raise HTTPException(status_code=400, detail='invalid_project_path')
    return text


def _project_tree(
    workspace_root: Path,
    rel_path: str = '',
    *,
    max_depth: int = 3,
    max_entries: int = 300,
) -> dict[str, object]:
    safe_rel = _safe_project_relpath(rel_path)
    root = workspace_root.resolve()
    target = (root / safe_rel).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail='path_outside_project')
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail='directory_not_found')
    counter = {'count': 0, 'truncated': False}

    def walk(path: Path, depth: int) -> dict[str, object]:
        counter['count'] += 1
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = ''
        node: dict[str, object] = {
            'name': path.name or root.name,
            'path': relative,
            'kind': 'directory' if path.is_dir() else 'file',
        }
        if path.is_file():
            node['size'] = path.stat().st_size
            return node
        if depth <= 0 or counter['count'] >= max_entries:
            node['children'] = []
            node['truncated'] = True
            counter['truncated'] = True
            return node
        children: list[dict[str, object]] = []
        for child in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
            if child.name in {'.git', '.godotter', '.import', '.venv', '__pycache__'}:
                continue
            if child.name == '.gitkeep':
                continue
            if child.name.endswith('.uid'):
                continue
            if counter['count'] >= max_entries:
                counter['truncated'] = True
                break
            children.append(walk(child, depth - 1))
        node['children'] = children
        return node

    return {
        'root': root.as_posix(),
        'path': safe_rel,
        'max_depth': max_depth,
        'max_entries': max_entries,
        'truncated': counter['truncated'],
        'tree': walk(target, max(0, max_depth)),
    }


def _git_status_summary(workspace_root: Path) -> dict[str, object]:
    if not (workspace_root / '.git').exists():
        return {
            'is_repo': False,
            'branch': '',
            'upstream': '',
            'branch_line': '',
            'dirty': False,
            'files': [],
            'branches': [],
            'commits': [],
            'recent_commits': [],
        }
    status = _run_git(workspace_root, ['status', '--porcelain=v1', '-b'])
    branches_result = _run_git(
        workspace_root,
        ['branch', '--all', '--format=%(HEAD)%09%(refname:short)%09%(upstream:short)%09%(objectname:short)%09%(subject)'],
    )
    log = _run_git(workspace_root, ['log', '--date=relative', '--format=%h%x1f%H%x1f%an%x1f%ar%x1f%s%x1e', '-n', '30'])
    lines = str(status['stdout']).splitlines()
    branch_line = lines[0] if lines and lines[0].startswith('## ') else ''
    files = []
    for line in lines[1:]:
        if not line:
            continue
        files.append(
            {
                'code': line[:2],
                'path': line[3:],
            }
        )
    branch = branch_line[3:].split('...', 1)[0].split(' ', 1)[0] if branch_line else ''
    upstream = ''
    if '...' in branch_line:
        upstream = branch_line.split('...', 1)[1].split(' ', 1)[0]
    branches = _parse_git_branches(str(branches_result['stdout']) if branches_result['ok'] else '')
    commits = _parse_git_commits(str(log['stdout']) if log['ok'] else '')
    return {
        'is_repo': True,
        'branch': branch,
        'upstream': upstream,
        'branch_line': branch_line[3:] if branch_line else '',
        'dirty': bool(files),
        'files': files,
        'branches': branches,
        'commits': commits,
        'recent_commits': [commit['line'] for commit in commits],
    }


def _parse_git_branches(output: str) -> list[dict[str, object]]:
    branches: list[dict[str, object]] = []
    seen: set[str] = set()
    for line in output.splitlines():
        parts = line.split('\t')
        if len(parts) < 5:
            continue
        marker, name, upstream, commit, subject = parts[:5]
        if not name or name.startswith('origin/HEAD') or name in seen:
            continue
        seen.add(name)
        branches.append(
            {
                'name': name,
                'current': marker.strip() == '*',
                'upstream': upstream,
                'commit': commit,
                'subject': subject,
                'remote': name.startswith('remotes/') or name.startswith('origin/'),
            }
        )
    return branches


def _parse_git_commits(output: str) -> list[dict[str, object]]:
    commits: list[dict[str, object]] = []
    for raw in output.split('\x1e'):
        line = raw.strip()
        if not line:
            continue
        parts = line.split('\x1f')
        if len(parts) < 5:
            continue
        short, full, author, relative_date, subject = parts[:5]
        commits.append(
            {
                'short': short,
                'hash': full,
                'author': author,
                'relative_date': relative_date,
                'subject': subject,
                'line': f'{short} {subject}',
            }
        )
    return commits


def _json_file_summary(path: Path) -> dict[str, object]:
    summary: dict[str, object] = {
        'name': path.name,
        'path': path.as_posix(),
        'size': path.stat().st_size,
        'modified_at': path.stat().st_mtime,
    }
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        summary['error'] = f'{type(exc).__name__}: {exc}'
        return summary
    if isinstance(data, dict):
        for key in ('plan_id', 'task_id', 'created_at', 'goal', 'workspace_root'):
            if key in data:
                summary[key] = data[key]
        if isinstance(data.get('tasks'), list):
            summary['tasks'] = len(data['tasks'])
        if isinstance(data.get('relevant_files'), list):
            summary['relevant_files'] = len(data['relevant_files'])
    return summary


def _list_json_files(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    files = [
        file
        for file in path.glob('*.json')
        if file.is_file() and file.name != 'latest.json' and not file.name.endswith('.state.json')
    ]
    return [_json_file_summary(file) for file in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)]


def _latest_json(path: Path) -> dict[str, object] | None:
    latest = path / 'latest.json'
    if not latest.exists():
        return None
    return _json_file_summary(latest)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec='seconds')


def _new_id(prefix: str) -> str:
    return f'{prefix}_{secrets.token_hex(6)}'


def _validate_id(value: str, *, prefix: str) -> str:
    text = value.strip()
    if not text.startswith(f'{prefix}_'):
        raise HTTPException(status_code=400, detail=f'invalid_{prefix}_id')
    allowed = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_')
    if any(ch not in allowed for ch in text):
        raise HTTPException(status_code=400, detail=f'invalid_{prefix}_id')
    return text


def _sessions_dir(workspace_root: Path) -> Path:
    return workspace_root / '.godotter' / 'sessions'


def _session_meta_path(workspace_root: Path, session_id: str) -> Path:
    return _sessions_dir(workspace_root) / f'{session_id}.json'


def _session_data_dir(workspace_root: Path, session_id: str) -> Path:
    return _sessions_dir(workspace_root) / session_id


def _session_messages_path(workspace_root: Path, session_id: str) -> Path:
    return _session_data_dir(workspace_root, session_id) / 'messages.jsonl'


def _session_plan_errors_path(workspace_root: Path, session_id: str) -> Path:
    return _session_data_dir(workspace_root, session_id) / 'plan_errors.jsonl'


def _reviews_dir(workspace_root: Path, session_id: str) -> Path:
    return _session_data_dir(workspace_root, session_id) / 'reviews'


def _runs_dir(workspace_root: Path, session_id: str) -> Path:
    return _session_data_dir(workspace_root, session_id) / 'runs'


def _review_path(workspace_root: Path, session_id: str, review_id: str) -> Path:
    return _reviews_dir(workspace_root, session_id) / f'{review_id}.json'


def _run_path(workspace_root: Path, session_id: str, run_id: str) -> Path:
    return _runs_dir(workspace_root, session_id) / f'{run_id}.json'


def _run_events_path(workspace_root: Path, session_id: str, run_id: str) -> Path:
    return _runs_dir(workspace_root, session_id) / f'{run_id}.events.jsonl'


def _load_review(workspace_root: Path, session_id: str, review_id: str) -> dict[str, object]:
    review_id = _validate_id(review_id, prefix='pr')
    path = _review_path(workspace_root, session_id, review_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail='review_not_found')
    return _read_json(path)


def _save_review(workspace_root: Path, session_id: str, review: dict[str, object]) -> None:
    review_id = str(review.get('review_id', ''))
    _write_json(_review_path(workspace_root, session_id, review_id), review)


def _load_run(workspace_root: Path, session_id: str, run_id: str) -> dict[str, object]:
    run_id = _validate_id(run_id, prefix='rj')
    path = _run_path(workspace_root, session_id, run_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail='run_not_found')
    return _read_json(path)


def _save_run(workspace_root: Path, session_id: str, run: dict[str, object]) -> None:
    _write_json(_run_path(workspace_root, session_id, str(run['run_id'])), run)


def _append_run_event(workspace_root: Path, session_id: str, run_id: str, event: dict[str, object]) -> None:
    event_payload = {'created_at': _now_iso(), **event}
    path = _run_events_path(workspace_root, session_id, run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(event_payload, ensure_ascii=False) + '\n')


def _append_plan_error(
    workspace_root: Path,
    project_name: str,
    session_id: str,
    *,
    goal: str,
    brain: str | None,
    error: Exception,
) -> dict[str, object]:
    if isinstance(error, HTTPException):
        detail = str(error.detail)
        status_code = error.status_code
    else:
        detail = str(error)
        status_code = 500
    payload = {
        'created_at': _now_iso(),
        'project_name': project_name,
        'session_id': session_id,
        'goal_preview': goal[:500],
        'brain': brain or None,
        'error_type': type(error).__name__,
        'status_code': status_code,
        'detail': detail,
    }
    path = _session_plan_errors_path(workspace_root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + '\n')
    return payload


def _read_run_events(workspace_root: Path, session_id: str, run_id: str, *, after: int = 0) -> list[dict[str, object]]:
    path = _run_events_path(workspace_root, session_id, run_id)
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


def _run_process_key(session_id: str, run_id: str) -> str:
    return f'{session_id}:{run_id}'


def _register_run_process(session_id: str, run_id: str, process: subprocess.Popen[str]) -> None:
    with RUN_PROCESS_LOCK:
        RUN_PROCESSES[_run_process_key(session_id, run_id)] = process


def _unregister_run_process(session_id: str, run_id: str, process: subprocess.Popen[str]) -> None:
    with RUN_PROCESS_LOCK:
        key = _run_process_key(session_id, run_id)
        if RUN_PROCESSES.get(key) is process:
            RUN_PROCESSES.pop(key, None)


def _terminate_process_tree(pid: int) -> None:
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


def _terminate_run_process(session_id: str, run_id: str) -> bool:
    with RUN_PROCESS_LOCK:
        process = RUN_PROCESSES.get(_run_process_key(session_id, run_id))
    if process is None or process.poll() is not None:
        return False
    _terminate_process_tree(process.pid)
    return True


def _command_timeout_seconds() -> int:
    raw = os.getenv('GODOTTER_WEB_RUN_COMMAND_TIMEOUT_SECONDS', '900').strip()
    try:
        value = int(raw)
    except ValueError:
        return 900
    return max(value, 0)


def _list_runs(workspace_root: Path, session_id: str) -> list[dict[str, object]]:
    runs_root = _runs_dir(workspace_root, session_id)
    if not runs_root.exists():
        return []
    runs: list[dict[str, object]] = []
    for path in sorted(runs_root.glob('rj_*.json'), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            runs.append(_enrich_run_artifacts(workspace_root, _read_json(path)))
        except Exception:
            continue
    return runs


def _enrich_run_artifacts(workspace_root: Path, run: dict[str, object]) -> dict[str, object]:
    enriched = dict(run)
    commands = []
    for command in enriched.get('commands', []) or []:
        if isinstance(command, dict):
            commands.append(_enrich_command_artifacts(workspace_root, command))
    enriched['commands'] = commands
    return enriched


def _enrich_command_artifacts(workspace_root: Path, command: dict[str, object]) -> dict[str, object]:
    enriched = dict(command)
    runstate_path = str(enriched.get('runstate_path') or '').strip()
    verify_report_path = str(enriched.get('verify_report_path') or '').strip()
    if runstate_path and 'runstate' not in enriched:
        runstate = _read_artifact_json(workspace_root, runstate_path)
        if runstate is not None:
            enriched['runstate'] = runstate
    if verify_report_path and 'verify_report' not in enriched:
        verify_report = _read_artifact_json(workspace_root, verify_report_path)
        if verify_report is not None:
            enriched['verify_report'] = verify_report
    return enriched


def _read_artifact_json(workspace_root: Path, value: str) -> dict[str, object] | None:
    path = Path(value)
    if not path.is_absolute():
        path = workspace_root / path
    try:
        if not path.exists() or not path.is_file():
            return None
        return _read_json(path)
    except Exception:
        return None


def _safe_project_file(workspace_root: Path, rel_path: str) -> Path:
    normalized = rel_path.strip().replace('\\', '/')
    if not normalized or normalized.startswith('/') or '..' in normalized.split('/'):
        raise HTTPException(status_code=400, detail='invalid_artifact_path')
    path = (workspace_root / normalized).resolve()
    root = workspace_root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail='artifact_path_outside_project') from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail='artifact_not_found')
    return path


def _extract_prefixed_path(text: str, prefix: str) -> str | None:
    for line in reversed((text or '').splitlines()):
        raw = line.strip()
        if raw.startswith(prefix):
            value = raw[len(prefix) :].strip()
            return value or None
    return None


def _find_active_run_for_review(
    workspace_root: Path,
    session_id: str,
    review_id: str,
    *,
    item_ids: list[str] | None = None,
) -> dict[str, object] | None:
    requested = set(item_ids or [])
    for run in _list_runs(workspace_root, session_id):
        if run.get('review_id') != review_id:
            continue
        if str(run.get('status', '')) not in ACTIVE_RUN_STATUSES:
            continue
        if requested and set(str(item_id) for item_id in run.get('task_ids', [])) != requested:
            continue
        return run
    return None


def _update_review_status(review: dict[str, object]) -> None:
    items = review.get('items', [])
    if not isinstance(items, list) or not items:
        review['status'] = 'draft'
        return
    statuses = [str(item.get('status', '')) for item in items if isinstance(item, dict)]
    if all(status == 'approved' for status in statuses):
        review['status'] = 'approved'
    elif any(status == 'approved' for status in statuses):
        review['status'] = 'partially_approved'
    elif any(status == 'needs_revision' for status in statuses):
        review['status'] = 'needs_revision'
    elif all(status == 'rejected' for status in statuses):
        review['status'] = 'rejected'
    else:
        review['status'] = 'in_review'


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding='utf-8'))


def _create_session(workspace_root: Path, project_name: str, *, title: str = '') -> dict[str, object]:
    session_id = _new_id('cs')
    now = _now_iso()
    session = {
        'session_id': session_id,
        'created_at': now,
        'updated_at': now,
        'title': title.strip() or 'New chat',
        'project_name': project_name,
        'workspace_root': workspace_root.as_posix(),
        'status': 'drafting',
        'latest_review_id': None,
        'latest_run_id': None,
    }
    _write_json(_session_meta_path(workspace_root, session_id), session)
    _session_data_dir(workspace_root, session_id).mkdir(parents=True, exist_ok=True)
    _session_messages_path(workspace_root, session_id).write_text('', encoding='utf-8', newline='\n')
    return session


def _list_sessions(workspace_root: Path) -> list[dict[str, object]]:
    sessions_root = _sessions_dir(workspace_root)
    if not sessions_root.exists():
        return []
    paths = [path for path in sessions_root.glob('cs_*.json') if path.is_file()]
    sessions = []
    for path in sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            sessions.append(_read_json(path))
        except Exception:
            continue
    return sessions


def _load_session(workspace_root: Path, session_id: str) -> dict[str, object]:
    session_id = _validate_id(session_id, prefix='cs')
    path = _session_meta_path(workspace_root, session_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail='session_not_found')
    return _read_json(path)


def _read_messages(workspace_root: Path, session_id: str) -> list[dict[str, object]]:
    path = _session_messages_path(workspace_root, session_id)
    if not path.exists():
        return []
    messages = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        try:
            messages.append(json.loads(line))
        except Exception:
            continue
    return messages


def _append_message(
    workspace_root: Path,
    project_name: str,
    session_id: str,
    *,
    role: str,
    content: str,
    kind: str = 'text',
    refs: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    session = _load_session(workspace_root, session_id)
    content = content.strip()
    if not content:
        raise HTTPException(status_code=400, detail='message_content_required')
    if role not in {'user', 'assistant', 'system', 'tool'}:
        raise HTTPException(status_code=400, detail='invalid_message_role')

    message = {
        'message_id': _new_id('msg'),
        'created_at': _now_iso(),
        'role': role,
        'kind': kind,
        'content': content,
        'refs': refs or [],
    }
    path = _session_messages_path(workspace_root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(message, ensure_ascii=False) + '\n')

    session['updated_at'] = message['created_at']
    if session.get('title') == 'New chat' and role == 'user':
        session['title'] = content[:48]
    session['project_name'] = project_name
    session['workspace_root'] = workspace_root.as_posix()
    _write_json(_session_meta_path(workspace_root, session_id), session)
    return message


def _session_detail(workspace_root: Path, session_id: str) -> dict[str, object]:
    session = _load_session(workspace_root, session_id)
    latest_review = None
    latest_review_id = session.get('latest_review_id')
    if isinstance(latest_review_id, str) and latest_review_id:
        try:
            latest_review = _load_review(workspace_root, session_id, latest_review_id)
        except HTTPException:
            latest_review = None
    return {
        'ok': True,
        'session': session,
        'messages': _read_messages(workspace_root, session_id),
        'latest_review': latest_review,
    }


def _approved_review_item_ids(review: dict[str, object]) -> list[str]:
    items = review.get('items', [])
    if not isinstance(items, list):
        return []
    return [
        str(item['item_id'])
        for item in items
        if isinstance(item, dict) and item.get('status') == 'approved' and item.get('item_id')
    ]


def _create_run_job(
    workspace_root: Path,
    project_name: str,
    session_id: str,
    review: dict[str, object],
    *,
    item_ids: list[str] | None = None,
) -> dict[str, object]:
    approved_ids = _approved_review_item_ids(review)
    requested_ids = item_ids or approved_ids
    task_ids = [item_id for item_id in requested_ids if item_id in approved_ids]
    if not task_ids:
        raise HTTPException(status_code=400, detail='no_approved_items_to_run')
    planpack_path = str(review.get('planpack_path', '')).strip()
    if not planpack_path:
        raise HTTPException(status_code=400, detail='review_missing_planpack_path')

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
    _save_run(workspace_root, session_id, run)
    session = _load_session(workspace_root, session_id)
    session['latest_run_id'] = run_id
    session['status'] = 'running'
    session['updated_at'] = run['created_at']
    _write_json(_session_meta_path(workspace_root, session_id), session)
    _append_run_event(workspace_root, session_id, run_id, {'type': 'status', 'message': 'run_queued'})
    return run


def _start_run_job_background(workspace_root: Path, session_id: str, run_id: str) -> None:
    worker = threading.Thread(
        target=_run_job_worker,
        args=(workspace_root, session_id, run_id),
        name=f'godotter-web-run-{run_id}',
        daemon=True,
    )
    worker.start()


def _run_job_worker(workspace_root: Path, session_id: str, run_id: str) -> None:
    try:
        _execute_run_job_sync(workspace_root, session_id, run_id)
    except Exception as exc:
        try:
            run = _load_run(workspace_root, session_id, run_id)
            run['status'] = 'failed'
            run['finished_at'] = _now_iso()
            run.setdefault('commands', [])
            run['error'] = str(exc)
            _save_run(workspace_root, session_id, run)
            _append_run_event(
                workspace_root,
                session_id,
                run_id,
                {'type': 'error', 'message': f'runner_exception: {exc}'},
            )
            session = _load_session(workspace_root, session_id)
            session['status'] = 'blocked'
            session['updated_at'] = str(run['finished_at'])
            _write_json(_session_meta_path(workspace_root, session_id), session)
        except Exception:
            pass


def _execute_run_job_sync(workspace_root: Path, session_id: str, run_id: str) -> dict[str, object]:
    run = _load_run(workspace_root, session_id, run_id)
    run['status'] = 'running'
    run['started_at'] = _now_iso()
    _save_run(workspace_root, session_id, run)
    _append_run_event(workspace_root, session_id, run_id, {'type': 'status', 'message': 'run_started'})

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
        _append_run_event(workspace_root, session_id, run_id, {'type': 'command', 'task_id': task_id, 'message': ' '.join(command)})
        process = subprocess.Popen(
            command,
            cwd=_repo_root(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == 'win32' else 0,
            start_new_session=sys.platform != 'win32',
        )
        _register_run_process(session_id, run_id, process)
        timed_out = {'value': False}
        timeout_seconds = _command_timeout_seconds()

        def kill_on_timeout() -> None:
            if process.poll() is None:
                timed_out['value'] = True
                _append_run_event(
                    workspace_root,
                    session_id,
                    run_id,
                    {
                        'type': 'timeout',
                        'task_id': task_id,
                        'message': f'command_timeout_seconds={timeout_seconds}',
                    },
                )
                _terminate_process_tree(process.pid)

        timer = threading.Timer(timeout_seconds, kill_on_timeout) if timeout_seconds > 0 else None
        if timer is not None:
            timer.daemon = True
            timer.start()
        stdout_lines: list[str] = []
        try:
            if process.stdout is not None:
                for line in process.stdout:
                    stdout_lines.append(line)
                    _append_run_event(
                        workspace_root,
                        session_id,
                        run_id,
                        {'type': 'stdout', 'task_id': task_id, 'message': line.rstrip()},
                    )
            return_code = process.wait()
        finally:
            if timer is not None:
                timer.cancel()
            _unregister_run_process(session_id, run_id, process)
        command_result = {
            'task_id': task_id,
            'command': command,
            'exit_code': return_code,
            'stdout': ''.join(stdout_lines),
            'stderr': '',
            'timed_out': timed_out['value'],
        }
        stdout_text = str(command_result['stdout'])
        runstate_path = _extract_prefixed_path(stdout_text, 'runstate=')
        verify_report_path = _extract_prefixed_path(stdout_text, 'task_run_verify_report=') or _extract_prefixed_path(stdout_text, 'report=')
        if runstate_path:
            command_result['runstate_path'] = runstate_path
            runstate = _read_artifact_json(workspace_root, runstate_path)
            if runstate is not None:
                command_result['runstate'] = runstate
        if verify_report_path:
            command_result['verify_report_path'] = verify_report_path
            verify_report = _read_artifact_json(workspace_root, verify_report_path)
            if verify_report is not None:
                command_result['verify_report'] = verify_report
        commands.append(command_result)
        exit_codes.append(return_code)
        _append_run_event(
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
        stored_run = _load_run(workspace_root, session_id, run_id)
        if stored_run.get('status') == 'interrupted':
            stored_run['commands'] = commands
            stored_run['finished_at'] = stored_run.get('finished_at') or _now_iso()
            _save_run(workspace_root, session_id, stored_run)
            return stored_run
        stored_run['commands'] = commands
        _save_run(workspace_root, session_id, stored_run)
        if timed_out['value']:
            break
        if return_code != 0:
            break

    run['commands'] = commands
    run['finished_at'] = _now_iso()
    run['status'] = 'passed' if exit_codes and all(code == 0 for code in exit_codes) else 'failed'
    _save_run(workspace_root, session_id, run)
    _append_run_event(workspace_root, session_id, run_id, {'type': 'status', 'message': str(run['status'])})

    session = _load_session(workspace_root, session_id)
    session['status'] = 'completed' if run['status'] == 'passed' else 'blocked'
    session['updated_at'] = str(run['finished_at'])
    _write_json(_session_meta_path(workspace_root, session_id), session)
    return run


def _parse_planner_json(raw: str, workspace_root: Path) -> dict[str, object]:
    raw_stripped = raw.strip()
    try:
        parsed = json.loads(raw_stripped)
    except Exception:
        start = raw_stripped.find('{')
        end = raw_stripped.rfind('}')
        if start == -1 or end == -1 or end <= start:
            debug_path = workspace_root / '.godotter' / 'plans' / 'last_planner_output.txt'
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(raw_stripped, encoding='utf-8', newline='\n')
            raise HTTPException(
                status_code=502,
                detail=f'planner_did_not_return_json saved={debug_path.as_posix()}',
            )
        try:
            parsed = json.loads(raw_stripped[start : end + 1])
        except Exception as exc:
            debug_path = workspace_root / '.godotter' / 'plans' / 'last_planner_output.txt'
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(raw_stripped, encoding='utf-8', newline='\n')
            raise HTTPException(
                status_code=502,
                detail=f'planner_json_parse_failed: {exc} saved={debug_path.as_posix()}',
            ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=502, detail='planner_json_root_must_be_object')
    return parsed


def _plan_tasks_from_json(parsed: dict[str, object]) -> list[PlanTask]:
    raw_tasks = parsed.get('tasks', [])
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise HTTPException(status_code=502, detail='planner_json_missing_tasks')

    tasks: list[PlanTask] = []
    used_ids: set[str] = set()
    for index, item in enumerate(raw_tasks, start=1):
        if not isinstance(item, dict):
            continue
        raw_id = str(item.get('id', '')).strip()
        task_id = raw_id or f't{index}'
        if task_id in used_ids:
            task_id = f'{task_id}_{index}'
        used_ids.add(task_id)
        tasks.append(
            PlanTask(
                id=task_id,
                title=str(item.get('title', '')).strip() or 'task',
                goal=str(item.get('goal', '')).strip() or '',
                depends_on=[str(x) for x in item.get('depends_on', []) if x],
                scope=[str(x) for x in item.get('scope', []) if x],
                acceptance=[str(x) for x in item.get('acceptance', []) if x],
                verification=[str(x) for x in item.get('verification', []) if x],
            )
        )

    try:
        normalize_plan_dependencies(tasks)
        validate_plan_tasks(tasks)
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return tasks


def _generate_planpack(workspace_root: Path, goal: str, *, brain_name: str | None = None) -> tuple[PlanPack, Path]:
    base_settings = get_settings()
    settings = base_settings.model_copy(update={'workspace_root': workspace_root})
    memory = Memory(settings.resolved_memory_path)
    registry = ToolRegistry(build_default_tools())
    selected_brain = brain_name or settings.default_brain
    agent = Agent(
        brain=create_brain(settings, selected_brain),
        settings=settings,
        registry=registry,
        memory=memory,
        mode='plan',
        brain_name=selected_brain,
    )
    agent.brain.tools = []
    if hasattr(agent.brain, 'tool_choice'):
        setattr(agent.brain, 'tool_choice', 'none')

    scout = scout_workspace(workspace_root, goal, max_files=40)
    prompt, constraints = build_plan_prompt(
        goal,
        [ScoutPromptRef(path=ref.path, reason=ref.reason) for ref in scout.relevant_files],
    )
    raw = agent.handle_input(prompt)
    parsed = _parse_planner_json(raw, workspace_root)
    tasks = _plan_tasks_from_json(parsed)
    pack = PlanPack(
        plan_id=new_plan_id(),
        created_at=_now_iso(),
        workspace_root=workspace_root.as_posix(),
        goal=goal,
        global_constraints=constraints,
        tasks=tasks,
    )
    out_path = write_planpack(workspace_root, pack)
    state = PlanState(
        plan_id=pack.plan_id,
        updated_at=_now_iso(),
        task_status={task.id: 'pending' for task in tasks},
    )
    write_planstate(plan_state_path(out_path), state)
    latest_path = workspace_root / '.godotter' / 'plans' / 'latest.json'
    if latest_path.exists():
        write_planstate(plan_state_path(latest_path), state)
    return pack, out_path


def _generate_chat_reply(workspace_root: Path, messages: list[dict[str, object]], *, brain_name: str | None = None) -> str:
    base_settings = get_settings()
    settings = base_settings.model_copy(update={'workspace_root': workspace_root})
    selected_brain = brain_name or settings.default_brain
    brain = create_brain(settings, selected_brain)
    brain.tools = []
    if hasattr(brain, 'tool_choice'):
        setattr(brain, 'tool_choice', 'none')

    conversation: list[dict[str, object]] = [
        {
            'role': 'system',
            'content': (
                'You are Godotter Web Console assistant. Reply conversationally and concisely in Chinese by default. '
                'Do not execute tasks and do not create a PlanPack in chat replies. '
                'If the user wants implementation planning, tell them to use the Generate Plan button.'
            ),
        }
    ]
    for message in messages[-20:]:
        role = str(message.get('role', ''))
        if role not in {'user', 'assistant'}:
            continue
        content = str(message.get('content', '')).strip()
        if content:
            conversation.append({'role': role, 'content': content})

    thought = brain.think(conversation)
    return (thought.text or '').strip() or '我收到消息了。'


def _create_plan_review(
    workspace_root: Path,
    session_id: str,
    planpack: PlanPack,
    planpack_path: Path,
) -> dict[str, object]:
    review_id = _new_id('pr')
    review = {
        'review_id': review_id,
        'session_id': session_id,
        'created_at': _now_iso(),
        'status': 'in_review',
        'planpack_path': planpack_path.as_posix(),
        'plan_id': planpack.plan_id,
        'goal': planpack.goal,
        'items': [
            {
                'item_id': task.id,
                'title': task.title,
                'goal': task.goal,
                'status': 'needs_review',
                'comment': '',
                'approved_at': None,
                'run_job_id': None,
            }
            for task in planpack.tasks
        ],
    }
    _write_json(_review_path(workspace_root, session_id, review_id), review)
    session = _load_session(workspace_root, session_id)
    session['latest_review_id'] = review_id
    session['status'] = 'reviewing'
    session['updated_at'] = review['created_at']
    _write_json(_session_meta_path(workspace_root, session_id), session)
    return review


def _require_token_if_configured(request: Request) -> None:
    token = (os.getenv('GODOTTER_WEB_TOKEN') or '').strip()
    if not token:
        return
    header = (request.headers.get('x-godotter-token') or '').strip()
    if header == token:
        return
    raise HTTPException(status_code=401, detail='missing_or_invalid_token')


def _write_env_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    path.write_bytes(normalized.encode('utf-8'))


@app.get('/health')
def health() -> dict[str, object]:
    return {'ok': True}


@app.get('/', response_class=HTMLResponse)
def index():
    index_path = STATIC_DIR / 'index.html'
    if index_path.exists():
        return FileResponse(index_path)
    return """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Godotter Web Console</title>
  </head>
  <body>
    <h1>Godotter Web Console</h1>
    <p>Backend is running.</p>
  </body>
</html>
"""


@app.get('/api/chat/state')
def chat_state() -> dict[str, object]:
    root = _repo_root()
    return {
        'ok': True,
        'workspace_root': root.as_posix(),
        'workflow': {
            'default_mode': 'plan-first',
            'stages': ['需求对话', '生成计划', '逐项审批', '执行任务', '验证修复', '结果通知'],
            'execution_gate': '必须显式批准后才能执行',
        },
        'conversation_status': {
            'implemented': False,
            'current_storage': 'Agent.conversation is in-memory only; PlanPack/WorkPack are persisted separately.',
            'recommended_next': 'Add persisted ChatSession + messages + approval comments before wiring real chat actions.',
        },
    }


@app.get('/api/projects')
def projects_list(request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    registry = load_project_registry(_projects_path())
    return {
        'ok': True,
        'default_project': registry.default_project,
        'projects': list(_registered_projects().values()),
    }


@app.post('/api/projects')
async def project_create(request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    name = _validate_project_name(str(payload.get('name', '')))
    parent = _default_new_project_parent()
    target = parent / name
    try:
        result = scaffold_godot_project(str(target), no_git=bool(payload.get('no_git', False)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _register_project(name, result.project_path, set_default=bool(payload.get('set_default', True)))
    return {
        'ok': True,
        'name': name,
        'workspace_root': result.project_path.as_posix(),
        'registered': True,
        'is_default': bool(payload.get('set_default', True)),
        'git_initialized': result.git_initialized,
    }


@app.get('/api/projects/{name}/summary')
def project_summary(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    godotter_dir = root / '.godotter'
    plans_dir = godotter_dir / 'plans'
    workpacks_dir = godotter_dir / 'workpacks'
    plans = _list_json_files(plans_dir)
    workpacks = _list_json_files(workpacks_dir)
    return {
        'ok': True,
        'name': name,
        'workspace_root': root.as_posix(),
        'exists': root.exists(),
        'godotter_dir': godotter_dir.as_posix(),
        'plans_count': len(plans),
        'workpacks_count': len(workpacks),
        'latest_plan': _latest_json(plans_dir),
        'latest_workpack': _latest_json(workpacks_dir),
    }


@app.get('/api/projects/{name}/tree')
def project_tree(name: str, request: Request, path: str = '', max_depth: int = 3) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    depth = max(0, min(max_depth, 8))
    return {'ok': True, 'name': name, **_project_tree(root, path, max_depth=depth)}


@app.get('/api/projects/{name}/plans')
def project_plans(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    return {
        'ok': True,
        'name': name,
        'plans': _list_json_files(root / '.godotter' / 'plans'),
    }


@app.get('/api/projects/{name}/workpacks')
def project_workpacks(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    return {
        'ok': True,
        'name': name,
        'workpacks': _list_json_files(root / '.godotter' / 'workpacks'),
    }


@app.get('/api/projects/{name}/builds')
def project_builds(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    return {
        'ok': True,
        'name': name,
        'builds': list_build_reports(root),
    }


@app.get('/api/projects/{name}/builds/doctor')
def project_build_doctor(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    settings = get_settings()
    report = run_export_doctor(workspace_root=root, godot_path=settings.godot_path)
    return {'ok': report.ok, 'doctor': asdict(report)}


@app.post('/api/projects/{name}/builds')
async def project_build_create(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    preset = str(payload.get('preset', '')).strip()
    if not preset:
        raise HTTPException(status_code=400, detail='preset_required')
    settings = get_settings()
    if not settings.godot_path:
        raise HTTPException(status_code=400, detail='GODOT_PATH is not configured')
    output_value = str(payload.get('output', '')).strip()
    output = Path(output_value) if output_value else None
    debug = bool(payload.get('debug', False))
    timeout = int(payload.get('timeout', 1800) or 1800)
    report, report_path = run_export_build(
        godot_path=settings.godot_path,
        workspace_root=root,
        preset=preset,
        output=output,
        release=not debug,
        timeout=timeout,
    )
    return {
        'ok': report.status == 'passed',
        'build': asdict(report),
        'build_report': report_path.as_posix(),
    }


@app.get('/api/projects/{name}/builds/{build_id}/download/{artifact_path:path}')
def project_build_download(name: str, build_id: str, artifact_path: str, request: Request) -> FileResponse:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    build_id = _validate_id(build_id, prefix='build')
    rel_path = f'.godotter/builds/{build_id}/{artifact_path}'
    path = _safe_project_file(root, rel_path)
    return FileResponse(path, filename=path.name)


@app.get('/api/projects/{name}/git/status')
def project_git_status(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    return {'ok': True, 'git': _git_status_summary(root)}


@app.post('/api/projects/{name}/git/init')
def project_git_init(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    if (root / '.git').exists():
        return {'ok': True, 'already_exists': True, 'git': _git_status_summary(root), 'results': []}
    results = [
        _run_git(root, ['init'], require_repo=False),
        _run_git(root, ['add', '.']),
        _run_git(root, ['commit', '-m', 'Initial commit: Godot project setup'], timeout=120),
    ]
    return {
        'ok': all(result['ok'] for result in results),
        'already_exists': False,
        'results': results,
        'git': _git_status_summary(root),
    }


@app.get('/api/projects/{name}/git/diff')
def project_git_diff(name: str, request: Request, path: str = '') -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    args = ['diff', '--']
    if path.strip():
        args.append(_safe_git_relpath(path))
    result = _run_git(root, args)
    return {'ok': result['ok'], 'diff': result}


@app.post('/api/projects/{name}/git/fetch')
def project_git_fetch(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    result = _run_git(root, ['fetch', '--prune'], timeout=120)
    return {'ok': result['ok'], 'result': result, 'git': _git_status_summary(root)}


@app.post('/api/projects/{name}/git/pull')
def project_git_pull(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    summary = _git_status_summary(root)
    if summary['dirty']:
        raise HTTPException(status_code=409, detail='working_tree_must_be_clean_before_pull')
    result = _run_git(root, ['pull', '--ff-only'], timeout=120)
    return {'ok': result['ok'], 'result': result, 'git': _git_status_summary(root)}


@app.post('/api/projects/{name}/git/push')
def project_git_push(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    result = _run_git(root, ['push'], timeout=120)
    return {'ok': result['ok'], 'result': result, 'git': _git_status_summary(root)}


@app.post('/api/projects/{name}/git/checkout')
async def project_git_checkout(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    branch = _safe_git_branch(str(payload.get('branch', '')))
    summary = _git_status_summary(root)
    if summary['dirty']:
        raise HTTPException(status_code=409, detail='working_tree_must_be_clean_before_checkout')
    result = _run_git(root, ['switch', branch], timeout=120)
    if not result['ok'] and branch.startswith('origin/'):
        local_name = _safe_git_branch(branch.split('/', 1)[1])
        result = _run_git(root, ['switch', '--track', '-c', local_name, branch], timeout=120)
    return {'ok': result['ok'], 'result': result, 'git': _git_status_summary(root)}


@app.post('/api/projects/{name}/git/commit')
async def project_git_commit(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    message = str(payload.get('message', '')).strip()
    if not message:
        raise HTTPException(status_code=400, detail='commit_message_required')
    files = [_safe_git_relpath(str(item)) for item in payload.get('files', []) if str(item).strip()]
    if not files:
        raise HTTPException(status_code=400, detail='commit_files_required')
    add_result = _run_git(root, ['add', '--', *files])
    if not add_result['ok']:
        return {'ok': False, 'result': add_result, 'git': _git_status_summary(root)}
    commit_result = _run_git(root, ['commit', '-m', message], timeout=120)
    return {'ok': commit_result['ok'], 'result': commit_result, 'git': _git_status_summary(root)}


@app.get('/api/projects/{name}/sessions')
def project_sessions(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    return {
        'ok': True,
        'name': name,
        'sessions': _list_sessions(root),
    }


@app.post('/api/projects/{name}/sessions')
async def project_session_create(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    title = str(payload.get('title', '')).strip()
    session = _create_session(root, name, title=title)
    return {
        'ok': True,
        'session': session,
        'messages': [],
    }


@app.get('/api/projects/{name}/sessions/{session_id}')
def project_session_get(name: str, session_id: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    return _session_detail(root, session_id)


@app.post('/api/projects/{name}/sessions/{session_id}/messages')
async def project_session_message_create(name: str, session_id: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    message = _append_message(
        root,
        name,
        session_id,
        role='user',
        content=str(payload.get('content', '')),
        kind='text',
    )
    return {
        'ok': True,
        'message': message,
        'session': _load_session(root, session_id),
    }


@app.post('/api/projects/{name}/sessions/{session_id}/reply')
async def project_session_reply_create(name: str, session_id: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    _load_session(root, session_id)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    brain_name = str(payload.get('brain') or '').strip() or None
    try:
        reply_text = _generate_chat_reply(root, _read_messages(root, session_id), brain_name=brain_name)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'chat_reply_failed: {type(exc).__name__}: {exc}') from exc
    message = _append_message(
        root,
        name,
        session_id,
        role='assistant',
        content=reply_text,
        kind='chat_reply',
    )
    return {
        'ok': True,
        'message': message,
        'session': _load_session(root, session_id),
    }


@app.post('/api/projects/{name}/sessions/{session_id}/plan')
async def project_session_plan_create(name: str, session_id: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    _load_session(root, session_id)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    goal = str(payload.get('goal', '')).strip()
    if not goal:
        messages = _read_messages(root, session_id)
        for message in reversed(messages):
            if message.get('role') == 'user' and str(message.get('content', '')).strip():
                goal = str(message['content']).strip()
                break
    if not goal:
        raise HTTPException(status_code=400, detail='plan_goal_required')

    brain_name = str(payload.get('brain') or '').strip() or None
    try:
        planpack, planpack_path = _generate_planpack(root, goal, brain_name=brain_name)
    except Exception as exc:
        error_payload = _append_plan_error(
            root,
            name,
            session_id,
            goal=goal,
            brain=brain_name,
            error=exc,
        )
        try:
            _append_message(
                root,
                name,
                session_id,
                role='assistant',
                content=f"计划生成失败：{error_payload['detail']}",
                kind='plan_error',
                refs=[{'type': 'plan_error', 'created_at': str(error_payload['created_at'])}],
            )
        except Exception:
            pass
        if isinstance(exc, HTTPException):
            raise exc
        raise HTTPException(status_code=502, detail=f"plan_generation_failed: {error_payload['detail']}") from exc
    review = _create_plan_review(root, session_id, planpack, planpack_path)
    summary = f'已生成计划草案：{len(planpack.tasks)} 个任务。请逐项审阅后再执行。'
    assistant_message = _append_message(
        root,
        name,
        session_id,
        role='assistant',
        content=summary,
        kind='plan_summary',
        refs=[{'type': 'plan_review', 'id': review['review_id']}],
    )
    return {
        'ok': True,
        'planpack': {
            'plan_id': planpack.plan_id,
            'path': planpack_path.as_posix(),
            'tasks': [
                {
                    'id': task.id,
                    'title': task.title,
                    'goal': task.goal,
                    'depends_on': task.depends_on,
                    'scope': task.scope,
                    'acceptance': task.acceptance,
                    'verification': task.verification,
                }
                for task in planpack.tasks
            ],
        },
        'review': review,
        'message': assistant_message,
        'session': _load_session(root, session_id),
    }


@app.get('/api/projects/{name}/sessions/{session_id}/reviews/{review_id}')
def project_session_review_get(name: str, session_id: str, review_id: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    _load_session(root, session_id)
    return {
        'ok': True,
        'review': _load_review(root, session_id, review_id),
    }


@app.post('/api/projects/{name}/sessions/{session_id}/reviews/{review_id}/items/{item_id}/approval')
async def project_session_review_item_approval(
    name: str,
    session_id: str,
    review_id: str,
    item_id: str,
    request: Request,
) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    _load_session(root, session_id)
    review = _load_review(root, session_id, review_id)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    status = str(payload.get('status', '')).strip()
    comment = str(payload.get('comment', '')).strip()
    if status not in {'approved', 'rejected', 'needs_revision', 'needs_review'}:
        raise HTTPException(status_code=400, detail='invalid_approval_status')
    if status == 'needs_revision' and not comment:
        raise HTTPException(status_code=400, detail='revision_comment_required')

    items = review.get('items', [])
    if not isinstance(items, list):
        raise HTTPException(status_code=500, detail='review_items_invalid')
    target = None
    for item in items:
        if isinstance(item, dict) and item.get('item_id') == item_id:
            target = item
            break
    if target is None:
        raise HTTPException(status_code=404, detail='review_item_not_found')

    target['status'] = status
    target['comment'] = comment
    target['approved_at'] = _now_iso() if status == 'approved' else None
    _update_review_status(review)
    review['updated_at'] = _now_iso()
    _save_review(root, session_id, review)
    return {
        'ok': True,
        'review': review,
    }


@app.post('/api/projects/{name}/sessions/{session_id}/reviews/{review_id}/run')
async def project_session_review_run(name: str, session_id: str, review_id: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    _load_session(root, session_id)
    review = _load_review(root, session_id, review_id)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    raw_item_ids = payload.get('item_ids')
    item_ids = [str(item_id) for item_id in raw_item_ids] if isinstance(raw_item_ids, list) else None
    active_run = _find_active_run_for_review(root, session_id, review_id, item_ids=item_ids)
    if active_run is not None:
        return {
            'ok': True,
            'reused': True,
            'run': active_run,
            'session': _load_session(root, session_id),
        }
    run = _create_run_job(root, name, session_id, review, item_ids=item_ids)
    _start_run_job_background(root, session_id, str(run['run_id']))
    return {
        'ok': True,
        'reused': False,
        'run': run,
        'session': _load_session(root, session_id),
    }


@app.get('/api/projects/{name}/sessions/{session_id}/runs/{run_id}')
def project_session_run_get(name: str, session_id: str, run_id: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    _load_session(root, session_id)
    return {
        'ok': True,
        'run': _enrich_run_artifacts(root, _load_run(root, session_id, run_id)),
    }


@app.post('/api/projects/{name}/sessions/{session_id}/runs/{run_id}/cancel')
def project_session_run_cancel(name: str, session_id: str, run_id: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    _load_session(root, session_id)
    run = _load_run(root, session_id, run_id)
    if str(run.get('status', '')) not in ACTIVE_RUN_STATUSES:
        return {
            'ok': True,
            'cancelled': False,
            'run': run,
        }

    terminated = _terminate_run_process(session_id, run_id)
    run['status'] = 'interrupted'
    run['finished_at'] = _now_iso()
    run['error'] = 'cancelled_by_user'
    _save_run(root, session_id, run)
    _append_run_event(
        root,
        session_id,
        run_id,
        {
            'type': 'status',
            'message': 'interrupted',
            'payload': {'terminated_process': terminated},
        },
    )
    session = _load_session(root, session_id)
    session['status'] = 'blocked'
    session['updated_at'] = str(run['finished_at'])
    _write_json(_session_meta_path(root, session_id), session)
    return {
        'ok': True,
        'cancelled': True,
        'run': run,
    }


@app.get('/api/projects/{name}/sessions/{session_id}/runs')
def project_session_runs_list(name: str, session_id: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    _load_session(root, session_id)
    return {
        'ok': True,
        'runs': _list_runs(root, session_id),
    }


@app.get('/api/projects/{name}/sessions/{session_id}/runs/{run_id}/events')
def project_session_run_events(
    name: str,
    session_id: str,
    run_id: str,
    request: Request,
    after: int = 0,
) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    _load_session(root, session_id)
    return {
        'ok': True,
        'run': _enrich_run_artifacts(root, _load_run(root, session_id, run_id)),
        'events': _read_run_events(root, session_id, run_id, after=after),
    }


@app.get('/projects', response_class=HTMLResponse)
def projects_page(request: Request) -> str:
    _require_token_if_configured(request)
    return """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Godotter Projects</title>
    <style>
      :root { color-scheme: dark; }
      * { box-sizing: border-box; }
      body { margin: 0; color: #f1f5f9; background: #111318; font-family: system-ui, -apple-system, Segoe UI, sans-serif; }
      main { width: min(1080px, 100%); margin: 0 auto; padding: 16px; }
      h1 { margin: 10px 0 4px; font-size: 28px; letter-spacing: -0.02em; }
      h2 { margin: 0 0 8px; font-size: 18px; }
      a, button, input { color: inherit; font: inherit; }
      button, .button { display: inline-flex; min-height: 40px; align-items: center; justify-content: center; border: 0; border-radius: 10px; padding: 0 14px; background: #2563eb; color: #fff; text-decoration: none; font-weight: 650; }
      button.secondary, .button.secondary { border: 1px solid #303640; background: transparent; }
      input { width: 100%; min-height: 40px; border: 1px solid #303640; border-radius: 10px; padding: 0 12px; background: #0b0d11; }
      .nav { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
      .muted { color: #9aa3af; }
      .grid { display: grid; gap: 12px; }
      .form { display: grid; gap: 8px; margin-bottom: 14px; }
      .card { border: 1px solid #303640; border-radius: 16px; padding: 14px; background: #181b22; }
      .project-list { display: grid; gap: 8px; }
      .project-button { width: 100%; justify-content: space-between; background: #181b22; border: 1px solid #303640; text-align: left; }
      .project-button.active { border-color: #60a5fa; background: #1d3558; }
      code, pre { background: #0b0d11; border: 1px solid #303640; border-radius: 12px; }
      code { padding: 2px 6px; }
      pre { overflow: auto; padding: 12px; white-space: pre-wrap; }
      .items { display: grid; gap: 8px; margin-top: 10px; }
      .item { border: 1px solid #303640; border-radius: 12px; padding: 10px; background: #14171d; }
      .item strong { display: block; margin-bottom: 4px; }
      @media (min-width: 860px) { .grid { grid-template-columns: 320px 1fr; align-items: start; } }
      @media (max-width: 520px) { main { padding: 12px; } button, .button { width: 100%; } }
    </style>
  </head>
  <body>
    <main>
      <nav class="nav">
        <a class="button secondary" href="/">首页</a>
        <a class="button secondary" href="/config">配置</a>
        <a class="button secondary" href="/env">.env</a>
        <a class="button secondary" href="/health">health</a>
      </nav>
      <h1>工作区</h1>
      <p class="muted">选择一个注册项目作为当前聊天工作区。只读取 <code>config/projects.toml</code> 里注册过的项目。</p>
      <section class="grid">
        <aside class="card">
          <h2>可用工作区</h2>
          <form id="create-project" class="form">
            <input id="new-project-name" autocomplete="off" placeholder="新项目名，例如 tetris4" />
            <button type="submit">新建项目并设为工作区</button>
            <p id="create-status" class="muted"></p>
          </form>
          <div id="project-list" class="project-list"></div>
        </aside>
        <section class="card">
          <h2 id="project-title">选择一个项目</h2>
          <p id="project-path" class="muted"></p>
          <p><button id="use-workspace" disabled>设为聊天工作区</button></p>
          <div id="summary"></div>
          <h2>最近 Plans</h2>
          <div id="plans" class="items"></div>
          <h2>最近 WorkPacks</h2>
          <div id="workpacks" class="items"></div>
        </section>
      </section>
    </main>
    <script>
      async function api(path) {
        const response = await fetch(path);
        if (!response.ok) throw new Error(await response.text());
        return response.json();
      }

      function itemHtml(item) {
        const title = item.goal || item.name;
        const meta = [
          item.created_at ? `created=${item.created_at}` : '',
          item.tasks !== undefined ? `tasks=${item.tasks}` : '',
          item.relevant_files !== undefined ? `files=${item.relevant_files}` : '',
        ].filter(Boolean).join(' · ');
        return `<div class="item"><strong>${escapeHtml(title)}</strong><div class="muted">${escapeHtml(meta || item.name)}</div></div>`;
      }

      function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
      }

      let selectedProject = null;

      async function selectProject(name, button) {
        selectedProject = name;
        for (const node of document.querySelectorAll('.project-button')) node.classList.remove('active');
        button?.classList.add('active');
        const summary = await api(`/api/projects/${encodeURIComponent(name)}/summary`);
        const plans = await api(`/api/projects/${encodeURIComponent(name)}/plans`);
        const workpacks = await api(`/api/projects/${encodeURIComponent(name)}/workpacks`);
        document.getElementById('project-title').textContent = name;
        document.getElementById('project-path').textContent = summary.workspace_root;
        document.getElementById('summary').innerHTML = `<pre>${escapeHtml(JSON.stringify({
          exists: summary.exists,
          plans_count: summary.plans_count,
          workpacks_count: summary.workpacks_count,
          latest_plan: summary.latest_plan?.name || null,
          latest_workpack: summary.latest_workpack?.name || null,
        }, null, 2))}</pre>`;
        document.getElementById('plans').innerHTML = plans.plans.length ? plans.plans.slice(0, 10).map(itemHtml).join('') : '<p class="muted">暂无 PlanPack</p>';
        document.getElementById('workpacks').innerHTML = workpacks.workpacks.length ? workpacks.workpacks.slice(0, 10).map(itemHtml).join('') : '<p class="muted">暂无 WorkPack</p>';
        document.getElementById('use-workspace').disabled = false;
      }

      document.getElementById('use-workspace').addEventListener('click', () => {
        if (!selectedProject) return;
        localStorage.setItem('godotter:selectedProject', selectedProject);
        window.location.href = '/';
      });

      document.getElementById('create-project').addEventListener('submit', async (event) => {
        event.preventDefault();
        const nameInput = document.getElementById('new-project-name');
        const status = document.getElementById('create-status');
        const name = nameInput.value.trim();
        if (!name) {
          status.textContent = '请输入项目名。';
          return;
        }
        status.textContent = '创建中...';
        try {
          const created = await fetch('/api/projects', {
            method: 'POST',
            headers: {'content-type': 'application/json'},
            body: JSON.stringify({name, no_git: true, set_default: true}),
          });
          if (!created.ok) throw new Error(await created.text());
          localStorage.setItem('godotter:selectedProject', name);
          status.textContent = '已创建。';
          nameInput.value = '';
          await init();
        } catch (error) {
          status.textContent = `创建失败：${error.message}`;
        }
      });

      async function init() {
        const data = await api('/api/projects');
        const list = document.getElementById('project-list');
        list.innerHTML = '';
        for (const project of data.projects) {
          const button = document.createElement('button');
          button.className = 'project-button';
          button.innerHTML = `<span>${escapeHtml(project.name)}${project.is_default ? ' · 默认' : ''}</span><span>${project.exists ? '存在' : '缺失'}</span>`;
          button.addEventListener('click', () => selectProject(project.name, button));
          list.appendChild(button);
          const saved = localStorage.getItem('godotter:selectedProject');
          if (project.name === saved || (!saved && project.is_default) || data.projects.length === 1) {
            selectProject(project.name, button);
          }
        }
        if (!data.projects.length) {
          list.innerHTML = '<p class="muted">config/projects.toml 没有注册项目。</p>';
        }
      }

      init().catch((error) => {
        document.getElementById('summary').innerHTML = `<pre>${escapeHtml(error.message)}</pre>`;
      });
    </script>
  </body>
</html>
"""


@app.get('/config', response_class=HTMLResponse)
def config_page(request: Request) -> str:
    _require_token_if_configured(request)
    env_path = _env_path()
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Godotter Config</title>
    <style>
      body {{ margin: 0; color: #f1f5f9; background: #111318; font-family: system-ui, -apple-system, Segoe UI, sans-serif; }}
      main {{ width: min(920px, 100%); margin: 0 auto; padding: 16px; }}
      a, button {{ color: #bfdbfe; }}
      .card {{ border: 1px solid #303640; border-radius: 16px; padding: 14px; margin: 12px 0; background: #181b22; }}
      .row {{ display: flex; gap: 10px; align-items: center; justify-content: space-between; flex-wrap: wrap; }}
      .nav {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 0 0 16px; }}
      .muted {{ color: #9aa3af; }}
      code, pre {{ background: #0b0d11; border: 1px solid #303640; border-radius: 12px; }}
      code {{ padding: 2px 6px; }}
      pre {{ overflow: auto; padding: 14px; white-space: pre-wrap; }}
      .button {{ display: inline-block; min-height: 40px; line-height: 40px; padding: 0 14px; border-radius: 10px; background: #2563eb; color: white; text-decoration: none; font-weight: 650; }}
      .button.secondary {{ border: 1px solid #303640; background: transparent; }}
    </style>
  </head>
  <body>
    <main>
      <nav class="nav">
        <a class="button secondary" href="/">首页</a>
        <a class="button secondary" href="/projects">项目</a>
        <a class="button" href="/env">编辑 .env</a>
        <a class="button secondary" href="/api/projects.toml">查看 projects.toml</a>
        <a class="button secondary" href="/health">health</a>
      </nav>
      <h1>配置</h1>
      <section class="card">
        <div class="row">
          <div>
            <h2>.env</h2>
            <p class="muted"><code>{html.escape(env_path.as_posix())}</code></p>
          </div>
          <a class="button" href="/env">编辑 .env</a>
        </div>
      </section>
      <section class="card">
        <div class="row">
          <div>
            <h2>工作区</h2>
            <p class="muted">项目选择属于聊天工作区，不在系统配置页里直接编辑。</p>
          </div>
          <a class="button secondary" href="/projects">选择工作区</a>
        </div>
      </section>
    </main>
  </body>
</html>
"""


@app.get('/env', response_class=HTMLResponse)
def env_editor(request: Request) -> str:
    _require_token_if_configured(request)
    path = _env_path()
    content = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    token_enabled = bool((os.getenv('GODOTTER_WEB_TOKEN') or '').strip())
    hint = 'Token required (x-godotter-token).' if token_enabled else 'No token configured.'
    escaped_path = html.escape(path.as_posix())
    escaped_hint = html.escape(hint)
    escaped_content = html.escape(content)
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Godotter .env Editor</title>
    <style>
      :root {{ color-scheme: dark; }}
      * {{ box-sizing: border-box; }}
      body {{ margin: 0; color: #f1f5f9; background: #111318; font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif; }}
      main {{ width: min(980px, 100%); margin: 0 auto; padding: 16px; }}
      h1 {{ margin: 10px 0 4px; font-size: 28px; letter-spacing: -0.02em; }}
      h2 {{ margin: 0; font-size: 18px; }}
      label {{ display: block; color: #cbd5e1; font-weight: 700; }}
      input, textarea {{ width: 100%; border: 1px solid #303640; border-radius: 12px; padding: 12px; color: #f1f5f9; background: #0b0d11; font: inherit; }}
      textarea {{ min-height: 44vh; font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; resize: vertical; }}
      button, .button {{ display:inline-flex; min-height: 40px; align-items:center; justify-content:center; padding: 0 14px; border: 0; border-radius: 10px; background: #2563eb; color: white; text-decoration:none; font-weight: 650; }}
      button.secondary, .button.secondary {{ border: 1px solid #303640; background: transparent; }}
      code {{ background: #0b0d11; border: 1px solid #303640; padding: 2px 6px; border-radius: 6px; }}
      .nav {{ display:flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }}
      .muted {{ color: #9aa3af; }}
      .hero {{ margin-bottom: 14px; }}
      .card {{ border: 1px solid #303640; border-radius: 16px; padding: 14px; margin: 12px 0; background: #181b22; }}
      .qa {{ display: grid; gap: 12px; }}
      .qa-item {{ border: 1px solid #303640; border-radius: 14px; padding: 12px; background: #14171d; }}
      .qa-item p {{ margin: 4px 0 10px; }}
      .grid {{ display: grid; gap: 10px; }}
      .actions {{ position: sticky; bottom: 0; display:flex; gap: 10px; align-items:center; flex-wrap: wrap; margin: 14px -16px -16px; padding: 12px 16px; border-top: 1px solid #303640; background: rgba(17, 19, 24, 0.96); }}
      .status {{ color: #9aa3af; }}
      @media (min-width: 760px) {{
        .grid.two {{ grid-template-columns: 1fr 1fr; }}
      }}
      @media (max-width: 520px) {{
        main {{ padding: 12px; }}
        button, .button {{ width: 100%; }}
        .actions {{ margin-left: -12px; margin-right: -12px; margin-bottom: -12px; }}
      }}
    </style>
  </head>
  <body>
    <main>
      <nav class="nav">
        <a class="button secondary" href="/">首页</a>
        <a class="button secondary" href="/config">配置</a>
        <a class="button secondary" href="/projects">项目</a>
        <a class="button secondary" href="/api/projects.toml">projects.toml</a>
        <a class="button secondary" href="/health">health</a>
      </nav>
      <section class="hero">
        <h1>.env 配置</h1>
        <p class="muted">路径：<code>{escaped_path}</code> · {escaped_hint}</p>
      </section>

      <section class="card">
        <h2>常用问题</h2>
        <p class="muted">这里是问答式编辑。保存时会同步到底部原始文本；未知字段会保留在原始文本里。</p>
        <div class="qa" id="qa"></div>
      </section>

      <section class="card">
        <h2>原始 .env 文本</h2>
        <p class="muted">需要高级配置时直接改这里。保存前会和上面的问答字段合并。</p>
        <textarea id="text" spellcheck="false">{escaped_content}</textarea>
      </section>

      <div class="actions">
        <button id="save">保存配置</button>
        <button id="sync" class="secondary">从原始文本重新读取</button>
        <span id="status" class="status"></span>
      </div>
    </main>
    <script>
      const knownFields = [
        {{ key: 'GODOTTER_DEFAULT_BRAIN', q: '默认使用哪个提供商？', hint: '例如 deepseek / moonshot / alibaba / siliconflow。' }},
        {{ key: 'DEEPSEEK_API_KEY', q: 'DeepSeek API Key 是什么？', hint: '留空表示不配置 DeepSeek。', secret: true }},
        {{ key: 'MOONSHOT_API_KEY', q: 'Moonshot API Key 是什么？', hint: '留空表示不配置 Moonshot。', secret: true }},
        {{ key: 'ALIBABA_API_KEY', q: '阿里云百炼 API Key 是什么？', hint: '留空表示不配置 Alibaba。', secret: true }},
        {{ key: 'SILICONFLOW_API_KEY', q: 'SiliconFlow API Key 是什么？', hint: '留空表示不配置 SiliconFlow。', secret: true }},
        {{ key: 'GODOTTER_WEB_TOKEN', q: '网页访问 Token 是什么？', hint: '配置后接口需要 x-godotter-token；本机测试可留空。', secret: true }},
        {{ key: 'GODOT_PATH', q: 'Godot 可执行文件路径是什么？', hint: '例如 D:/Godots/Engines/Godot_v4.6.1-stable_win64/...console.exe。' }},
      ];

      const btn = document.getElementById('save');
      const status = document.getElementById('status');
      const text = document.getElementById('text');
      const qa = document.getElementById('qa');
      const sync = document.getElementById('sync');

      function parseEnv(raw) {{
        const result = {{}};
        for (const line of raw.split(/\\r?\\n/)) {{
          const trimmed = line.trim();
          if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) continue;
          const idx = trimmed.indexOf('=');
          result[trimmed.slice(0, idx)] = trimmed.slice(idx + 1);
        }}
        return result;
      }}

      function mergeEnv(raw, values) {{
        const seen = new Set();
        const lines = raw.split(/\\r?\\n/).map((line) => {{
          const trimmed = line.trim();
          if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) return line;
          const idx = trimmed.indexOf('=');
          const key = trimmed.slice(0, idx);
          if (!(key in values)) return line;
          seen.add(key);
          return `${{key}}=${{values[key]}}`;
        }});
        for (const field of knownFields) {{
          if (!seen.has(field.key) && values[field.key]) {{
            lines.push(`${{field.key}}=${{values[field.key]}}`);
          }}
        }}
        return lines.join('\\n').replace(/\\n*$/, '\\n');
      }}

      function renderQA() {{
        qa.innerHTML = '';
        const values = parseEnv(text.value);
        for (const field of knownFields) {{
          const item = document.createElement('div');
          item.className = 'qa-item';
          const label = document.createElement('label');
          label.setAttribute('for', `env-${{field.key}}`);
          label.textContent = field.q;
          const hint = document.createElement('p');
          hint.className = 'muted';
          hint.textContent = `${{field.key}} · ${{field.hint}}`;
          const input = document.createElement('input');
          input.id = `env-${{field.key}}`;
          input.dataset.key = field.key;
          input.type = field.secret ? 'password' : 'text';
          input.autocomplete = 'off';
          input.value = values[field.key] || '';
          item.append(label, hint, input);
          qa.appendChild(item);
        }}
      }}

      function syncToText() {{
        const values = {{}};
        for (const input of qa.querySelectorAll('input[data-key]')) {{
          values[input.dataset.key] = input.value.trim();
        }}
        text.value = mergeEnv(text.value, values);
      }}

      btn.addEventListener('click', async () => {{
        syncToText();
        status.textContent = 'Saving...';
        const resp = await fetch('/api/env', {{
          method: 'PUT',
          headers: {{ 'content-type': 'text/plain' }},
          body: text.value
        }});
        if (resp.ok) {{
          status.textContent = '已保存。';
        }} else {{
          const t = await resp.text();
          status.textContent = 'Error: ' + t;
        }}
      }});
      sync.addEventListener('click', renderQA);
      renderQA();
    </script>
  </body>
</html>
"""


@app.get('/api/env', response_class=PlainTextResponse)
def env_get(request: Request) -> str:
    _require_token_if_configured(request)
    path = _env_path()
    return path.read_text(encoding='utf-8-sig') if path.exists() else ''


@app.get('/api/projects.toml', response_class=PlainTextResponse)
def projects_get(request: Request) -> str:
    _require_token_if_configured(request)
    path = _projects_path()
    return path.read_text(encoding='utf-8-sig') if path.exists() else ''


@app.put('/api/env', response_class=PlainTextResponse)
async def env_put(request: Request) -> str:
    _require_token_if_configured(request)
    payload = (await request.body()).decode('utf-8', errors='replace')
    _write_env_text(_env_path(), payload)
    return 'ok'
