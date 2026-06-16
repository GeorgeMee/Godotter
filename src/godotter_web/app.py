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
from godotter.context import Memory, build_project_summary, render_project_summary, build_chat_scout_context
from godotter.llm import create_brain
from godotter.operations.projects import scaffold_godot_project
from godotter.project_registry import load_project_registry
from godotter.runtime.builds import list_build_reports, run_export_build, run_export_doctor
from godotter.utils.envfile import EnvFile
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
    settings = get_settings()
    root = Path(settings.projects_root)
    return root.resolve() if root.is_absolute() else (_repo_root() / root).resolve()


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


def _registered_project_entries() -> dict[str, dict[str, object]]:
    registry = load_project_registry(_projects_path())
    projects: dict[str, dict[str, object]] = {}
    for existing_name, entry in registry.projects.items():
        projects[existing_name] = {
            'workspace_root': entry.workspace_root.as_posix(),
            'godot_path': entry.godot_path,
            'main_scene': entry.main_scene,
            'platform': entry.platform,
        }
    return projects


def _set_env_key(key: str, value: str) -> None:
    path = _env_path()
    text = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    output: list[str] = []
    replaced = False
    prefix = f'{key}='
    for line in lines:
        if line.startswith(prefix):
            output.append(f'{key}={value}')
            replaced = True
        else:
            output.append(line)
    while output and output[-1] == '':
        output.pop()
    if not replaced:
        output.append(f'{key}={value}')
    _write_env_text(path, '\n'.join(output) + '\n')


def _set_default_project(name: str) -> dict[str, object]:
    normalized = _validate_project_name(name)
    registry = load_project_registry(_projects_path())
    if normalized not in registry.projects:
        raise HTTPException(status_code=404, detail='unknown_project')
    _write_projects_toml(normalized, _registered_project_entries())
    _set_env_key('GODOTTER_DEFAULT_PROJECT', normalized)
    return {
        'ok': True,
        'default_project': normalized,
        'projects': list(_registered_projects().values()),
    }


def _register_project(name: str, workspace_root: Path, *, set_default: bool = True) -> None:
    registry = load_project_registry(_projects_path())
    if name in registry.projects:
        raise HTTPException(status_code=409, detail='project_already_registered')
    projects = _registered_project_entries()
    projects[name] = {'workspace_root': workspace_root.resolve().as_posix()}
    default_project = name if set_default else registry.default_project
    _write_projects_toml(default_project, projects)
    if set_default:
        _set_env_key('GODOTTER_DEFAULT_PROJECT', name)


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
    max_entries: int = 800,
) -> dict[str, object]:
    safe_rel = _safe_project_relpath(rel_path)
    root = workspace_root.resolve()
    target = (root / safe_rel).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail='path_outside_project')
    if not target.exists() or not target.is_dir():
        raise HTTPException(status_code=404, detail='directory_not_found')
    counter = {'count': 0, 'truncated': False}

    git_statuses: dict[str, str] = {}
    if (root / '.git').exists():
        try:
            import subprocess
            completed = subprocess.run(
                ['git', 'status', '--porcelain', '-u'],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in completed.stdout.splitlines():
                stripped = line.strip()
                if len(stripped) >= 2:
                    code = stripped[:2].strip()
                    fpath = stripped[3:].strip()
                    if '->' in fpath:
                        fpath = fpath.split('->')[-1].strip()
                    fpath = fpath.replace('\\', '/')
                    if code == '??':
                        git_statuses[fpath] = 'untracked'
                    elif code == 'M ' or code == 'AM':
                        git_statuses[fpath] = 'staged'
                    elif code in (' M', 'MM', 'MD'):
                        git_statuses[fpath] = 'modified'
        except Exception:
            pass

    def walk(path: Path, depth: int) -> dict[str, object]:
        total_size = 0
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
            if relative in git_statuses:
                node['git_status'] = git_statuses[relative]
            return node
        if depth <= 0 or counter['count'] >= max_entries:
            node['children'] = []
            node['truncated'] = True
            counter['truncated'] = True
            return node
        children: list[dict[str, object]] = []
        for child in sorted(path.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
            if child.name in {'.git', '.godot', '.godotter', '.import', '.venv', '__pycache__', 'android'}:
                continue
            if child.name == '.gitkeep':
                continue
            if child.name.endswith('.uid'):
                continue
            if counter['count'] >= max_entries:
                counter['truncated'] = True
                break
            child_node = walk(child, depth - 1)
            children.append(child_node)
            if isinstance(child_node.get('size'), int):
                total_size += child_node['size']
        node['children'] = children
        node['size'] = total_size
        child_statuses = {c.get('git_status', '') for c in children if c.get('git_status')}
        if child_statuses:
            if 'modified' in child_statuses:
                node['git_status'] = 'modified'
            elif 'staged' in child_statuses:
                node['git_status'] = 'staged'
            elif 'untracked' in child_statuses:
                node['git_status'] = 'untracked'
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


def _ensure_gitignore_excludes_godotter(workspace_root: Path) -> None:
    path = workspace_root / '.gitignore'
    existing = path.read_text(encoding='utf-8') if path.exists() else ''
    lines = [line.strip() for line in existing.splitlines()]
    if '.godotter/' in lines or '.godotter' in lines:
        return
    suffix = '\n' if existing and not existing.endswith('\n') else ''
    path.write_text(existing + f'{suffix}\n# Godotter local state\n.godotter/\n', encoding='utf-8', newline='\n')


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
        for key in ('plan_id', 'task_id', 'created_at', 'goal', 'name', 'workspace_root'):
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
    review = _read_json(path)
    planpack_path = str(review.get('planpack_path') or '').strip()
    if planpack_path:
        state_path = plan_state_path(Path(planpack_path))
        if not state_path.is_absolute():
            state_path = workspace_root / state_path
        try:
            if state_path.exists():
                review['plan_state'] = _read_json(state_path)
        except Exception:
            pass
    return review


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
        end = raw_stripped.rfind('}')
        if end == -1:
            debug_path = workspace_root / '.godotter' / 'plans' / 'last_planner_output.txt'
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            debug_path.write_text(raw_stripped, encoding='utf-8', newline='\n')
            raise HTTPException(
                status_code=502,
                detail=f'planner_did_not_return_json saved={debug_path.as_posix()}',
            )
        # Find "tasks" keyword first, then locate the root { before it.
        tasks_pos = raw_stripped.rfind('"tasks"', 0, end)
        if tasks_pos != -1:
            start = raw_stripped.rfind('{', 0, tasks_pos)
        else:
            start = raw_stripped.rfind('{', 0, end)
        if start == -1 or end <= start:
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


def _generate_planpack(
    workspace_root: Path,
    goal: str,
    *,
    brain_name: str | None = None,
) -> tuple[PlanPack, Path]:
    base_settings = get_settings()
    settings = base_settings.model_copy(update={'workspace_root': workspace_root})
    memory = Memory(settings.resolved_memory_path)
    registry = ToolRegistry(build_default_tools())
    selected_brain = brain_name or settings.resolved_plan_brain
    summary = build_project_summary(workspace_root)
    summary_text = render_project_summary(summary) if summary else None
    agent = Agent(
        brain=create_brain(settings, selected_brain, model_override=getattr(settings, 'plan_model', None)),
        settings=settings,
        registry=registry,
        memory=memory,
        mode='plan',
        brain_name=selected_brain,
        project_summary=summary_text,
    )
    # Plan generation is a single-shot JSON prompt with no chat history.
    # Tools are disabled to force pure JSON output.
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
        name=str(parsed.get('name', '')).strip() or goal[:80],
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
    selected_brain = brain_name or settings.resolved_chat_brain
    memory = Memory(settings.resolved_memory_path)
    registry = ToolRegistry(build_default_tools())

    summary = build_project_summary(workspace_root)
    summary_text = render_project_summary(summary) if summary else None

    agent = Agent(
        brain=create_brain(settings, selected_brain, model_override=getattr(settings, 'chat_model', None)),
        settings=settings,
        registry=registry,
        memory=memory,
        mode='plan',
        brain_name=selected_brain,
        project_summary=summary_text,
    )
    agent.expose_tool_output = False
    if hasattr(agent.brain, 'tool_choice'):
        setattr(agent.brain, 'tool_choice', 'auto')

    for msg in messages[-20:]:
        role = str(msg.get('role', ''))
        if role not in {'user', 'assistant'}:
            continue
        content = str(msg.get('content', '')).strip()
        if content:
            agent.conversation.append({'role': role, 'content': content})

    last_user_msg = ''
    for m in reversed(messages):
        if str(m.get('role', '')) == 'user':
            last_user_msg = str(m.get('content', '')).strip()
            break

    enriched_message = last_user_msg
    scout_context = build_chat_scout_context(workspace_root, last_user_msg) if last_user_msg else None
    if scout_context:
        enriched_message = f'{last_user_msg}\n\n--- Relevant project context (auto-scanned) ---\n{scout_context}'

    agent.conversation.append({'role': 'user', 'content': enriched_message})
    return agent._agentic_loop()


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
                'scope': task.scope,
                'acceptance': task.acceptance,
                'verification': task.verification,
                'depends_on': task.depends_on,
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


def _secondary_page(
    *,
    title: str,
    eyebrow: str,
    summary: str,
    current: str,
    body_html: str,
    script_html: str = '',
) -> str:
    title_text = html.escape(title)
    return f"""<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
    <title>{title_text}</title>
    <style>
      :root {{
        color-scheme: dark;
        --bg: #0f141c;
        --bg-soft: #151b24;
        --panel: rgba(22, 28, 38, 0.9);
        --panel-2: rgba(18, 23, 32, 0.92);
        --line: rgba(148, 163, 184, 0.18);
        --line-strong: rgba(96, 165, 250, 0.35);
        --text: #edf2f7;
        --muted: #97a6ba;
        --accent: #4f8cff;
        --accent-2: #78a9ff;
        --accent-soft: rgba(79, 140, 255, 0.16);
        font-family: "Segoe UI", "PingFang SC", "Noto Sans SC", sans-serif;
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        color: var(--text);
        background:
          radial-gradient(circle at top left, rgba(79, 140, 255, 0.18), transparent 28rem),
          linear-gradient(180deg, #0b1016 0%, var(--bg) 100%);
      }}
      a, button, input, textarea {{ font: inherit; color: inherit; }}
      .page {{
        width: min(1380px, 100%);
        margin: 0 auto;
        padding: 60px 16px 16px;
      }}
       .hero, .panel {{
         border: 1px solid var(--line);
         border-radius: 22px;
         background: var(--panel);
         box-shadow: 0 18px 42px rgba(0, 0, 0, 0.16);
       }}
       .hero {{
         padding: 16px;
       }}
      .topbar {{
        position: fixed;
        top: 0;
        left: 0;
        right: 0;
        z-index: 100;
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        padding: 8px 16px;
        background: rgba(17, 19, 24, 0.95);
        backdrop-filter: blur(10px);
        border-bottom: 1px solid var(--line);
      }}
       .topbar a {{
         text-decoration: none;
       }}
       .topbar .brand {{
         font-weight: 800;
         font-size: 15px;
         color: var(--text);
         margin-right: 12px;
       }}
       .topbar .tab {{
         display: inline-flex;
         min-height: 36px;
         align-items: center;
         padding: 0 14px;
         border: 1px solid var(--line);
         border-radius: 10px;
         font-size: 13px;
         font-weight: 700;
         color: var(--muted);
         background: transparent;
         transition: border-color 0.15s, background 0.15s, color 0.15s;
       }}
       .topbar .tab:hover {{
         border-color: var(--line-strong);
         color: var(--text);
       }}
       .topbar .tab.active {{
         border-color: var(--line-strong);
         color: #d8e6ff;
         background: var(--accent-soft);
       }}
      .eyebrow {{
        margin: 0 0 8px;
        color: var(--muted);
        font-size: 11px;
        font-weight: 800;
        letter-spacing: 0.14em;
        text-transform: uppercase;
      }}
      h1, h2, h3, p {{ margin: 0; }}
      h1 {{
        font-size: clamp(24px, 4vw, 34px);
        line-height: 1.12;
      }}
      h2 {{
        font-size: 18px;
        line-height: 1.25;
      }}
      h3 {{
        font-size: 14px;
        line-height: 1.3;
      }}
      .subtle, .muted, small {{
        color: var(--muted);
      }}
      .nav-link, .nav-button, .button, button {{
        display: inline-flex;
        min-height: 44px;
        align-items: center;
        justify-content: center;
        gap: 8px;
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 0 14px;
        text-decoration: none;
        background: rgba(255, 255, 255, 0.02);
        transition: border-color 0.15s ease, background 0.15s ease;
      }}
      .nav-link:hover, .nav-button:hover, .button:hover, button:hover {{
        border-color: var(--line-strong);
        background: rgba(255, 255, 255, 0.05);
      }}
      .nav-link.active {{
        border-color: var(--line-strong);
        color: #d8e6ff;
        background: var(--accent-soft);
      }}
      .panel {{
        padding: 18px;
      }}
      .panel-stack {{
        display: grid;
        gap: 14px;
        min-width: 0;
      }}
      .panel-head {{
        display: grid;
        gap: 6px;
        margin-bottom: 14px;
      }}
      .card-grid {{
        display: grid;
        grid-template-columns: 1fr;
        gap: 14px;
      }}
      .card {{
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 14px;
        background: var(--panel-2);
      }}
      .row {{
        display: flex;
        gap: 12px;
        align-items: center;
        justify-content: space-between;
        flex-wrap: wrap;
      }}
      .form {{
        display: grid;
        gap: 10px;
      }}
      input, textarea, select {{
        width: 100%;
        border: 1px solid var(--line);
        border-radius: 12px;
        padding: 12px 14px;
        color: var(--text);
        background: rgba(10, 14, 20, 0.88);
      }}
      textarea {{
        min-height: 260px;
        resize: vertical;
        font-family: Consolas, "SFMono-Regular", monospace;
      }}
      code, pre {{
        border: 1px solid var(--line);
        border-radius: 12px;
        background: rgba(9, 12, 17, 0.92);
      }}
      code {{
        padding: 2px 6px;
      }}
      pre {{
        overflow: auto;
        margin: 0;
        padding: 12px;
        white-space: pre-wrap;
        min-width: 0;
      }}
      .project-list, .items, .qa {{
        display: grid;
        gap: 10px;
      }}
      .items .card {{
        min-width: 0;
        overflow: hidden;
      }}
      .items .card strong {{
        display: block;
        overflow-wrap: break-word;
        word-break: break-word;
      }}
      .project-button {{
        width: 100%;
        justify-content: space-between;
        text-align: left;
      }}
      .project-button.active {{
        border-color: var(--line-strong);
        background: var(--accent-soft);
      }}
      .project-row {{
        display: flex;
        gap: 0;
        align-items: stretch;
      }}
      .project-row .project-button {{
        border-top-right-radius: 0;
        border-bottom-right-radius: 0;
      }}
      .project-delete-btn {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        min-width: 44px;
        min-height: 44px;
        border: 1px solid var(--line);
        border-left: none;
        border-radius: 0 12px 12px 0;
        background: transparent;
        color: var(--muted);
        font-size: 14px;
        cursor: pointer;
        transition: border-color 0.15s, color 0.15s, background 0.15s;
        padding: 0;
      }}
      .project-delete-btn:hover {{
        border-color: #ef4444;
        color: #ef4444;
        background: rgba(239, 68, 68, 0.08);
      }}
      .modal-overlay {{
        position: fixed;
        inset: 0;
        z-index: 300;
        background: rgba(0, 0, 0, 0.6);
        display: flex;
        align-items: center;
        justify-content: center;
      }}
      .modal-box {{
        background: var(--panel);
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 20px;
        min-width: min(360px, 90vw);
        max-width: 90vw;
      }}
      .qa-item {{
        border: 1px solid var(--line);
        border-radius: 14px;
        padding: 12px;
        background: rgba(12, 16, 23, 0.76);
      }}
      .qa-item p {{
        margin: 6px 0 10px;
      }}
      .actions {{
        display: flex;
        gap: 10px;
        align-items: center;
        flex-wrap: wrap;
      }}
      .mono {{
        font-family: Consolas, "SFMono-Regular", monospace;
      }}
      @media (max-width: 640px) {{
        .page {{
          padding: 60px 10px 10px;
        }}
        .hero, .panel {{
          border-radius: 18px;
        }}
        .hero {{
          padding: 14px;
        }}
        .panel {{
          padding: 14px;
        }}
        .actions {{
          display: grid;
        }}
      }}

      .settings-row {{
        display: flex;
        align-items: center;
        gap: 10px;
        min-height: 36px;
        padding: 4px 0;
        min-width: 0;
        flex-wrap: wrap;
      }}
      .settings-row .settings-label {{
        width: 90px;
        flex-shrink: 0;
        font-size: 0.85rem;
        color: var(--text);
      }}
      .settings-row code {{
        flex: 1;
        min-width: 0;
        font-size: 0.8rem;
        color: var(--muted);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }}
      .settings-row .env-set-btn {{
        font-size: 0.72rem;
        padding: 4px 10px;
        border: 1px solid var(--line);
        border-radius: 6px;
        background: transparent;
        color: var(--muted);
        cursor: pointer;
        white-space: nowrap;
      }}
      .settings-row .env-set-btn:hover {{
        color: var(--text);
        border-color: var(--line-strong);
      }}
      .settings-inline {{
        display: flex;
        align-items: center;
        gap: 10px;
        min-height: 36px;
        min-width: 0;
        flex-wrap: wrap;
      }}
      .settings-inline select {{
        flex: 1;
      }}
      .settings-inline code {{
        flex: 1;
        font-size: 0.8rem;
        color: var(--muted);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
      }}
      .settings-inline button {{
        white-space: nowrap;
      }}
      .settings-inline .env-set-btn {{
        font-size: 0.72rem;
        padding: 4px 10px;
        border: 1px solid var(--line);
        border-radius: 6px;
        background: transparent;
        color: var(--muted);
        cursor: pointer;
        white-space: nowrap;
      }}
      .settings-inline .env-set-btn:hover {{
        color: var(--text);
        border-color: var(--line-strong);
      }}

      .agent-row {{
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        align-items: center;
        padding: 8px 0;
        min-width: 0;
      }}
      .agent-task-label {{
        width: 42px;
        flex-shrink: 0;
        font-size: 0.85rem;
        font-weight: 700;
        color: var(--text);
      }}
      .agent-selects {{
        display: flex;
        flex-direction: column;
        gap: 4px;
        flex: 1;
        min-width: 0;
      }}
      .agent-inline-label {{
        font-size: 0.72rem;
        color: var(--muted);
        white-space: nowrap;
      }}
      .agent-selects select {{
        min-width: 0;
      }}
      .agent-table {{
        min-width: 0;
      }}

      .burger {{
        display: none;
        min-height: 32px;
        min-width: 32px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: transparent;
        color: var(--muted);
        font-size: 16px;
        cursor: pointer;
      }}
      .burger:hover {{
        border-color: var(--line-strong);
        color: var(--text);
      }}

      .gtabs-desktop {{
        display: flex;
        align-items: center;
        gap: 6px;
        flex-wrap: wrap;
      }}

      .nav-overlay {{
        display: none;
        position: fixed;
        inset: 0;
        z-index: 200;
        background: rgba(0,0,0,0.5);
      }}
      .nav-overlay:not([hidden]) {{
        display: flex;
      }}
      .nav-sidebar {{
        width: min(80vw, 280px);
        height: 100%;
        background: rgba(15,20,28,0.98);
        backdrop-filter: blur(10px);
        border-right: 1px solid var(--line);
        padding: 16px;
        overflow-y: auto;
      }}
      .nav-sidebar-head {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 16px;
        font-weight: 700;
        font-size: 15px;
      }}
      .nav-sidebar-close {{
        background: transparent;
        border: none;
        color: var(--muted);
        font-size: 18px;
        cursor: pointer;
        padding: 4px;
      }}
      .nav-sidebar-links {{
        display: flex;
        flex-direction: column;
        gap: 4px;
      }}
      .nav-sidebar-links a,
      .nav-sidebar-links button {{
        display: block;
        padding: 10px 12px;
        border-radius: 8px;
        color: var(--muted);
        text-decoration: none;
        font-size: 14px;
        font-weight: 600;
        background: transparent;
        border: none;
        cursor: pointer;
        text-align: left;
        width: 100%;
      }}
      .nav-sidebar-links a:hover,
      .nav-sidebar-links button:hover {{
        background: rgba(255,255,255,0.05);
        color: var(--text);
      }}
      .nav-sidebar-links a.active {{
        color: #93c5fd;
        background: rgba(59,130,246,0.12);
      }}
      .nav-sidebar-links hr {{
        border: none;
        border-top: 1px solid var(--line);
        margin: 8px 0;
      }}

      @media (max-width: 768px) {{
        .topbar .brand {{
          margin-right: auto;
        }}
        .burger {{
          display: inline-flex;
        }}
        .gtabs-desktop {{
          display: none;
        }}
      }}
      @media (min-width: 769px) {{
        .nav-overlay {{
          display: none !important;
        }}
      }}

      .design-tabs {{
        display: flex;
        gap: 6px;
        flex-wrap: wrap;
      }}

      button.primary {{
        background: var(--accent);
        border-color: var(--accent);
        color: #fff;
        font-weight: 700;
      }}
      button.primary:hover {{
        background: var(--accent-2);
        border-color: var(--accent-2);
      }}
      button.primary:disabled {{
        opacity: 0.4;
        cursor: not-allowed;
      }}
      .design-tab {{
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 6px 14px;
        border: 1px solid var(--line);
        border-radius: 8px;
        background: transparent;
        color: var(--muted);
        font-size: 13px;
        font-weight: 700;
        cursor: pointer;
        transition: border-color 0.15s, background 0.15s, color 0.15s;
      }}
      .design-tab:hover {{
        border-color: var(--line-strong);
        color: var(--text);
      }}
      .design-tab.active {{
        border-color: var(--line-strong);
        color: #d8e6ff;
        background: var(--accent-soft);
      }}
      .design-card {{
        border: 1px solid var(--line);
        border-radius: 10px;
        padding: 12px;
        margin-bottom: 8px;
        background: rgba(12, 16, 23, 0.76);
      }}
      .design-card-head {{
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
        margin-bottom: 6px;
      }}
      .design-card-head code {{
        font-size: 0.78em;
      }}
      .design-card-desc {{
        margin: 6px 0;
        font-size: 0.88em;
        color: var(--text);
      }}
      .design-card-meta {{
        margin-top: 6px;
        font-size: 0.8em;
        color: var(--muted);
      }}
      .design-tags {{
        display: flex;
        gap: 4px;
        flex-wrap: wrap;
      }}
      .tag {{
        display: inline-block;
        padding: 2px 8px;
        border-radius: 6px;
        font-size: 0.72em;
        font-family: Consolas, monospace;
      }}
      .tag-pub {{
        background: var(--accent-soft);
        color: var(--accent);
      }}
      .tag-sub {{
        background: rgba(255, 255, 255, 0.06);
        color: var(--muted);
      }}
      .design-event-table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 0.9em;
      }}
      .design-event-table th,
      .design-event-table td {{
        border: 1px solid var(--line);
        padding: 8px 10px;
        text-align: left;
      }}
      .design-event-table th {{
        background: var(--panel-strong);
        font-weight: 700;
      }}
      .design-section {{
        display: grid;
        gap: 0;
      }}
    </style>
  </head>
  <body>
    <main class="page">
      <nav class="topbar">
        <button class="burger" id="burger-btn" aria-label="菜单">☰</button>
        <span class="brand">Godotter</span>
        <div class="gtabs-desktop">
          <a class="tab{' active' if current == 'Projects' else ''}" href="/projects">Projects</a>
          <a class="tab{' active' if current == 'Design' else ''}" href="/design">Design</a>
          <a class="tab{' active' if current == 'Workspace' else ''}" href="/">Workspace</a>
          <a class="tab{' active' if current == 'Settings' else ''}" href="/settings">Settings</a>
        </div>
      </nav>
      <div class="nav-overlay" id="nav-overlay" hidden>
        <div class="nav-sidebar">
          <div class="nav-sidebar-head">
            <span>Godotter</span>
            <button class="nav-sidebar-close" id="nav-close">✕</button>
          </div>
          <nav class="nav-sidebar-links">
            <a href="/"{' class="active"' if current == 'Workspace' else ''}>Workspace</a>
            <a href="/projects"{' class="active"' if current == 'Projects' else ''}>Projects</a>
            <a href="/design"{' class="active"' if current == 'Design' else ''}>Design</a>
            <a href="/settings"{' class="active"' if current == 'Settings' else ''}>Settings</a>
          </nav>
        </div>
      </div>
      {body_html}
    </main>
    <script>
      document.getElementById("burger-btn").addEventListener("click", () => {{
        document.getElementById("nav-overlay").hidden = false;
      }});
      document.getElementById("nav-close").addEventListener("click", () => {{
        document.getElementById("nav-overlay").hidden = true;
      }});
      document.getElementById("nav-overlay").addEventListener("click", (e) => {{
        if (e.target === document.getElementById("nav-overlay")) {{
          document.getElementById("nav-overlay").hidden = true;
        }}
      }});
      for (const link of document.querySelectorAll(".nav-sidebar-links a")) {{
        link.addEventListener("click", (e) => {{
          document.getElementById("nav-overlay").hidden = true;
          if (link.getAttribute("href") === "/") {{
            e.preventDefault();
            window.location.href = "/";
          }}
        }});
      }}
    </script>
    {script_html}
  </body>
</html>
"""


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


@app.get('/api/projects-root')
def projects_root_get() -> dict[str, object]:
    path = _default_new_project_parent()
    return {'ok': True, 'path': path.as_posix(), 'exists': path.exists()}


@app.post('/api/projects-root')
async def projects_root_set(request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    path = str(payload.get('path', '')).strip()
    if not path:
        raise HTTPException(status_code=400, detail='path_required')
    EnvFile(Path('.env')).set('GODOTTER_PROJECTS_ROOT', path)
    get_settings.cache_clear()
    return {'ok': True, 'path': path}


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


@app.post('/api/projects/{name}/default')
def project_set_default(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    return _set_default_project(name)


@app.delete('/api/projects/{name}')
async def project_delete(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    normalized = _validate_project_name(name)
    registry = load_project_registry(_projects_path())
    if normalized not in registry.projects:
        raise HTTPException(status_code=404, detail='unknown_project')

    entry = registry.projects[normalized]

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    delete_files = bool(payload.get('delete_files', False))

    removed_root = None
    if delete_files:
        removed_root = entry.workspace_root.as_posix()
        import shutil
        shutil.rmtree(entry.workspace_root, ignore_errors=True)

    projects = _registered_project_entries()
    del projects[normalized]

    default_project = registry.default_project
    if default_project == normalized:
        default_project = next(iter(projects)) if projects else None

    _write_projects_toml(default_project, projects)
    if default_project != normalized:
        _set_env_key('GODOTTER_DEFAULT_PROJECT', default_project or '')

    return {
        'ok': True,
        'removed': normalized,
        'deleted_files': delete_files,
        'removed_root': removed_root,
        'projects': list(_registered_projects().values()),
    }


@app.post('/api/projects/{name}/scaffold')
async def project_scaffold(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='invalid_json')

    from godotter.tasks.planning import validate_design_json
    from godotter.operations.design_scaffold import scaffold_from_design

    errors = validate_design_json(payload)
    if errors:
        raise HTTPException(status_code=400, detail='; '.join(errors))

    result = scaffold_from_design(payload, root)
    return {
        'ok': True,
        'created_files': result.created_files,
        'count': len(result.created_files),
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
    return {'ok': True, 'name': name, **_project_tree(root, path, max_depth=depth, max_entries=800)}


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


@app.post('/api/config')
async def config_set(request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    key = str(payload.get('key', '')).strip()
    value = str(payload.get('value', '')).strip()
    if not key:
        raise HTTPException(status_code=400, detail='config_key_required')
    EnvFile(Path('.env')).set(key, value)
    get_settings.cache_clear()
    return {'ok': True, 'key': key, 'value': value}


@app.get('/api/models')
def models_list(provider: str = '') -> dict[str, object]:
    from godotter.llm.catalog import list_models
    settings = get_settings()
    selected = (provider or settings.default_brain).strip().lower()
    if selected == 'stub':
        return {'ok': True, 'provider': 'stub', 'models': ['stub']}
    try:
        models = list_models(settings, selected)
        return {'ok': True, 'provider': selected, 'models': models}
    except Exception as exc:
        return {'ok': False, 'error': str(exc)}


@app.get('/api/keys')
def api_keys_list() -> dict[str, object]:
    settings = get_settings()
    return {
        'ok': True,
        'keys': {
            'deepseek': settings.deepseek_api_key,
            'siliconflow': settings.siliconflow_api_key,
            'alibaba': settings.alibaba_api_key,
            'moonshot': settings.moonshot_api_key,
        },
    }


@app.get('/api/config-state')
def config_state() -> dict[str, object]:
    settings = get_settings()
    providers = ['deepseek', 'siliconflow', 'alibaba', 'moonshot']
    provider_models = {}
    for p in providers:
        model = getattr(settings, f'{p}_model', '') or ''
        provider_models[p] = model
    return {
        'ok': True,
        'config': {
            'default_brain': settings.default_brain,
            'chat_brain': settings.resolved_chat_brain,
            'plan_brain': settings.resolved_plan_brain,
            'act_brain': settings.resolved_act_brain,
            'design_brain': settings.resolved_design_brain,
            'chat_model': settings.chat_model or '',
            'plan_model': settings.plan_model or '',
            'act_model': settings.act_model or '',
            'design_model': settings.design_model or '',
            'provider_models': provider_models,
        },
    }


@app.post('/api/keys')
async def api_keys_set(request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    provider = str(payload.get('provider', '')).strip().lower()
    key = str(payload.get('key', '')).strip()
    if provider not in ('deepseek', 'siliconflow', 'alibaba', 'moonshot'):
        raise HTTPException(status_code=400, detail='invalid_provider')
    env_name = {'deepseek': 'DEEPSEEK_API_KEY', 'siliconflow': 'SILICONFLOW_API_KEY', 'alibaba': 'ALIBABA_API_KEY', 'moonshot': 'MOONSHOT_API_KEY'}[provider]
    EnvFile(Path('.env')).set(env_name, key)
    get_settings.cache_clear()
    return {'ok': True, 'provider': provider}


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
    report = run_export_doctor(
        workspace_root=root,
        godot_path=settings.godot_path,
        templates_path=settings.export_templates_path,
        android_sdk_path=settings.android_sdk_path,
        java_home=settings.java_home,
        keystore_path=settings.android_keystore_path,
    )
    # Auto-detect suggestions for missing configs
    suggestions: dict[str, object] = {}
    if not report.android_sdk_path or not report.android_sdk_valid:
        detected = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
        if detected and Path(detected).exists():
            suggestions['android_sdk_path'] = detected
    if not report.java_home or not report.java_valid:
        detected = os.environ.get("JAVA_HOME")
        if not detected:
            import shutil
            detected = shutil.which("java")
            if detected:
                detected = str(Path(detected).resolve().parent.parent)
        if detected and Path(detected).exists():
            suggestions['java_home'] = detected
    if not report.keystore_valid:
        for candidate in [
            Path(os.environ.get("APPDATA", "")) / "Godot" / "keystores" / "debug.keystore",
            Path.home() / ".android" / "debug.keystore",
        ]:
            if candidate.exists():
                suggestions['android_keystore_path'] = candidate.as_posix()
                break
    return {'ok': report.ok, 'doctor': asdict(report), 'suggestions': suggestions}


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


@app.get('/api/projects/{name}/builds/{build_id}/preview/{file_path:path}')
def project_build_preview(name: str, build_id: str, file_path: str, request: Request) -> FileResponse:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    build_id = _validate_id(build_id, prefix='build')
    rel_path = f'.godotter/builds/{build_id}/{file_path}'
    path = _safe_project_file(root, rel_path)
    return FileResponse(path)


@app.post('/api/projects/{name}/builds/install-template')
async def project_builds_install_template(name: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    settings = get_settings()
    if not settings.godot_path:
        raise HTTPException(status_code=400, detail='GODOT_PATH is not configured')
    try:
        completed = subprocess.run(
            [settings.godot_path, "--headless", "--path", root.as_posix(), "--install-android-build-template"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        ok = completed.returncode == 0
        return {
            'ok': ok,
            'exit_code': completed.returncode,
            'stdout': completed.stdout[-2000:] if completed.stdout else '',
            'stderr': completed.stderr[-500:] if completed.stderr else '',
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail='template_install_timeout')


@app.delete('/api/projects/{name}/builds/{build_id}')
def project_build_delete(name: str, build_id: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    build_id = _validate_id(build_id, prefix='build')
    build_dir = root / '.godotter' / 'builds' / build_id
    import shutil
    if build_dir.exists():
        shutil.rmtree(build_dir)
    return {'ok': True, 'deleted': build_id}


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
    _ensure_gitignore_excludes_godotter(root)
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


@app.delete('/api/projects/{name}/sessions/{session_id}')
def project_session_delete(name: str, session_id: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    root = _project_root_or_404(name)
    sid = _validate_id(session_id, prefix='cs')
    meta = _session_meta_path(root, sid)
    data = _session_data_dir(root, sid)
    removed = []
    if meta.exists():
        meta.unlink()
        removed.append(meta.name)
    if data.exists():
        import shutil
        shutil.rmtree(data)
        removed.append(data.name)
    return {'ok': True, 'removed': removed, 'session_id': sid}


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
    messages = _read_messages(root, session_id)
    goal = str(payload.get('goal', '')).strip()
    if not goal:
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
    body_html = """
      <section class="panel">
        <div class="panel-head">
          <p class="eyebrow">Registry</p>
          <h2>Projects</h2>
        </div>
        <div class="card-grid">
          <section class="card">
            <div class="panel-stack">
              <h3>Registered</h3>
              <p class="muted">Sourced from <code>config/projects.toml</code>.</p>
              <form id="create-project" class="form">
                <input id="new-project-name" autocomplete="off" placeholder="New project name" />
                <button type="submit">Create</button>
                <p id="create-status" class="muted"></p>
              </form>
              <div id="project-list" class="project-list"></div>
            </div>
          </section>
          <section class="card">
            <div class="panel-stack">
              <div>
                <h3 id="project-title">Choose a project</h3>
                <p id="project-path" class="muted"></p>
              </div>
              <div class="actions">
                <button id="use-workspace" disabled>Use as active workspace</button>
              </div>
              <div id="summary"></div>
              <div>
                <h3>Recent plans</h3>
                <div id="plans" class="items"></div>
              </div>
              <div>
                <h3>Recent workpacks</h3>
                <div id="workpacks" class="items"></div>
              </div>
            </div>
          </section>
        </div>
      </section>
    """
    script_html = """<script>
      async function api(path, options = {}) {
        const response = await fetch(path, options);
        if (!response.ok) throw new Error(await response.text());
        return response.json();
      }

      function itemHtml(item) {
        const title = item.name || item.goal;
        const meta = [
          item.created_at ? `created=${item.created_at}` : '',
          item.tasks !== undefined ? `tasks=${item.tasks}` : '',
          item.relevant_files !== undefined ? `files=${item.relevant_files}` : '',
        ].filter(Boolean).join(' - ');
        return `<div class="card"><strong>${escapeHtml(title)}</strong><div class="muted">${escapeHtml(meta || item.name)}</div></div>`;
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
        document.getElementById('plans').innerHTML = plans.plans.length ? plans.plans.slice(0, 10).map(itemHtml).join('') : '<p class="muted">No plans yet.</p>';
        document.getElementById('workpacks').innerHTML = workpacks.workpacks.length ? workpacks.workpacks.slice(0, 10).map(itemHtml).join('') : '<p class="muted">No workpacks yet.</p>';
        document.getElementById('use-workspace').disabled = false;
      }

      document.getElementById('use-workspace').addEventListener('click', async () => {
        if (!selectedProject) return;
        const response = await fetch(`/api/projects/${encodeURIComponent(selectedProject)}/default`, {method: 'POST'});
        if (!response.ok) throw new Error(await response.text());
        localStorage.setItem('godotter:selectedProject', selectedProject);
        window.location.href = '/';
      });

      document.getElementById('create-project').addEventListener('submit', async (event) => {
        event.preventDefault();
        const nameInput = document.getElementById('new-project-name');
        const status = document.getElementById('create-status');
        const name = nameInput.value.trim();
        if (!name) {
          status.textContent = 'Project name is required.';
          return;
        }
        status.textContent = 'Creating...';
        try {
          const created = await fetch('/api/projects', {
            method: 'POST',
            headers: {'content-type': 'application/json'},
            body: JSON.stringify({name, workspace_root: ''}),
          });
          if (!created.ok) throw new Error((await created.json()).detail || 'create failed');
          status.textContent = 'Created!';
          nameInput.value = '';
          init();
        } catch (error) {
          status.textContent = `Create failed: ${error.message}`;
        }
      });

      async function init() {
        const data = await api('/api/projects');
        const list = document.getElementById('project-list');
        list.innerHTML = '';
        for (const project of data.projects) {
          const wrapper = document.createElement('div');
          wrapper.className = 'project-row';
          const button = document.createElement('button');
          button.className = 'project-button';
          button.innerHTML = `<span>${escapeHtml(project.name)}${project.is_default ? ' - default' : ''}</span><span>${project.exists ? 'present' : 'missing'}</span>`;
          button.addEventListener('click', () => selectProject(project.name, button));
          wrapper.appendChild(button);
          const delBtn = document.createElement('button');
          delBtn.type = 'button';
          delBtn.className = 'project-delete-btn';
          delBtn.title = '删除项目';
          delBtn.textContent = '✕';
          delBtn.addEventListener('click', async (e) => {
            e.stopPropagation();
            const showDialog = () => {
              const overlay = document.createElement('div');
              overlay.className = 'modal-overlay';
              overlay.innerHTML = '<div class="modal-box">' +
                '<h3 style="margin-bottom:10px">删除项目</h3>' +
                '<p class="muted" style="margin-bottom:14px">' + escapeHtml(project.name) + '</p>' +
                '<label class="checkbox-line" style="margin-bottom:14px">' +
                '<input type="checkbox" id="delete-files-checkbox" />' +
                '<span>同时删除磁盘文件</span>' +
                '</label>' +
                '<div class="actions">' +
                '<button type="button" id="modal-cancel" class="secondary">取消</button>' +
                '<button type="button" id="modal-confirm" style="background:#ef4444;border-color:#ef4444;color:#fff">确认删除</button>' +
                '</div></div>';
              document.body.appendChild(overlay);
              overlay.addEventListener('click', (ev) => { if (ev.target === overlay) overlay.remove(); });
              document.getElementById('modal-cancel').addEventListener('click', () => overlay.remove());
              document.getElementById('modal-confirm').addEventListener('click', async () => {
                const deleteFiles = document.getElementById('delete-files-checkbox').checked;
                overlay.remove();
                try {
                  const result = await api('/api/projects/' + encodeURIComponent(project.name), {method: 'DELETE', headers: {'content-type': 'application/json'}, body: JSON.stringify({delete_files: deleteFiles})});
                  init();
                } catch (err) {
                  alert('删除失败: ' + err.message);
                }
              });
            };
            showDialog();
          });
          wrapper.appendChild(delBtn);
          list.appendChild(wrapper);
          const saved = localStorage.getItem('godotter:selectedProject');
          if (project.name === saved || (!saved && project.is_default) || data.projects.length === 1) {
            selectProject(project.name, button);
          }
        }
        if (!data.projects.length) {
          list.innerHTML = '<p class="muted">No registered projects in config/projects.toml.</p>';
        }
      }

      init().catch((error) => {
        document.getElementById('summary').innerHTML = `<pre>${escapeHtml(error.message)}</pre>`;
      });
    </script>"""
    return _secondary_page(
        title='Projects',
        eyebrow='Workspace',
        summary='Manage registered game projects and switch which one the web console treats as the active workspace.',
        current='Projects',
        body_html=body_html,
        script_html=script_html,
    )


@app.get('/design', response_class=HTMLResponse)
def design_page(request: Request) -> str:
    _require_token_if_configured(request)
    body_html = """
      <section class="panel" style="max-width:900px;margin:0 auto;">
        <div class="panel-head">
          <p class="eyebrow">Design</p>
          <h2>玩法设计助手</h2>
          <p class="subtle">与 AI 讨论游戏玩法、划分 System/Feature/GameMode、完善模板规范。无需绑定项目。</p>
        </div>
        <div class="session-bar" id="design-session-bar">
          <select id="design-session-select" aria-label="历史会话">
            <option value="">选择历史会话...</option>
          </select>
          <button type="button" class="secondary" id="design-load-session">加载</button>
          <button type="button" class="secondary" id="design-delete-session" disabled>删除</button>
          <button type="button" id="design-create-project" class="primary" disabled>创建项目</button>
          <span class="new-chat-wrap"><button type="button" id="design-new-chat">新对话</button></span>
        </div>
        <div id="design-tabs" class="design-tabs" style="display:none;margin-bottom:10px;">
          <button type="button" class="design-tab active" data-section="systems">Systems <span class="muted" id="design-count-systems">0</span></button>
          <button type="button" class="design-tab" data-section="features">Features <span class="muted" id="design-count-features">0</span></button>
          <button type="button" class="design-tab" data-section="gamemodes">GameModes <span class="muted" id="design-count-gamemodes">0</span></button>
          <button type="button" class="design-tab" data-section="events">Events <span class="muted" id="design-count-events">0</span></button>
        </div>
        <div id="design-panel" class="design-panel" style="border:1px solid var(--line);border-radius:14px;min-height:400px;max-height:60vh;overflow-y:auto;padding:14px;background:rgba(10,14,20,0.6);margin-bottom:12px;">
          <div class="bubble assistant" style="margin-bottom:10px;">
            <strong style="display:block;margin-bottom:4px;">Design</strong>
            <p class="muted" style="margin:0;">你好！我是玩法设计助手。描述你想设计的游戏玩法，我会拆解成 Systems、Features、GameModes 的结构化 JSON。</p>
          </div>
        </div>
        <form class="design-form" id="design-form" style="display:flex;gap:8px;">
          <textarea id="design-prompt" rows="2" placeholder="例如：玩家在地牢里探索，遇到怪物和宝箱，收集材料升级魔杖打怪..." style="flex:1;min-height:44px;"></textarea>
          <button type="submit" class="send-btn" style="flex-shrink:0;min-height:44px;min-width:44px;">➤</button>
        </form>
        <p class="muted" id="design-status" style="margin-top:8px;"></p>
      </section>
    """
    script_html = """<script>
      const panel = document.getElementById('design-panel');
      const form = document.getElementById('design-form');
      const prompt = document.getElementById('design-prompt');
      const status = document.getElementById('design-status');
      const sessionSelect = document.getElementById('design-session-select');
      const loadBtn = document.getElementById('design-load-session');
      const newBtn = document.getElementById('design-new-chat');
      const tabs = document.getElementById('design-tabs');
      const STORAGE_KEY = 'godotter:designSession';
      let currentDesignSession = null;
      let _designController = null;
      let _lastDesignJson = null;

      window.addEventListener('beforeunload', () => {
        if (_designController) _designController.abort();
      });

      async function fetchJson(path, options = {}) {
        const response = await fetch(path, options);
        if (!response.ok) throw new Error(await response.text());
        return response.json();
      }

      function esc(v) { return String(v ?? '').replace(/[&<>"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); }

      function extractJSON(text) {
        const m = text.match(/```json\\n?([\\s\\S]*?)\\n?```/);
        if (m) {
          try { return JSON.parse(m[1]); } catch (_) {}
        }
        try { return JSON.parse(text); } catch (_) {}
        return null;
      }

      function hasDesignKeys(obj) {
        return obj && (Array.isArray(obj.systems) || Array.isArray(obj.features) || Array.isArray(obj.gamemodes));
      }

      function renderDesignCards(container, items, section) {
        container.innerHTML = '';
        if (!items || !items.length) {
          container.innerHTML = '<p class="muted" style="text-align:center;padding:20px;">无</p>';
          return;
        }
        for (const item of items) {
          const card = document.createElement('div');
          card.className = 'design-card';
          let html = '<div class="design-card-head">';
          html += '<strong>' + esc(item.name || '') + '</strong>';
          html += ' <code class="muted">' + esc(item.directory || '') + '</code>';
          html += '</div>';
          if (item.description) html += '<p class="design-card-desc">' + esc(item.description) + '</p>';
          if (section === 'systems') {
            if (item.publishes && item.publishes.length) html += '<div class="design-tags">' + item.publishes.map(e => '<span class="tag tag-pub">' + esc(e) + '</span>').join('') + '</div>';
            if (item.subscribes && item.subscribes.length) html += '<div class="design-tags" style="margin-top:4px;">' + item.subscribes.map(e => '<span class="tag tag-sub">' + esc(e) + '</span>').join('') + '</div>';
            if (item.export_vars && item.export_vars.length) html += '<div class="design-card-meta">Export: ' + item.export_vars.map(v => esc(v.name) + ' (' + esc(v.type || '') + ')').join(', ') + '</div>';
          } else if (section === 'features') {
            if (item.uses_systems && item.uses_systems.length) html += '<div class="design-card-meta">Uses: ' + item.uses_systems.map(s => esc(s)).join(', ') + '</div>';
            if (item.subscribes && item.subscribes.length) html += '<div class="design-tags">' + item.subscribes.map(e => '<span class="tag tag-sub">' + esc(e) + '</span>').join('') + '</div>';
          } else if (section === 'gamemodes') {
            if (item.systems && item.systems.length) html += '<div class="design-card-meta">Systems: ' + item.systems.map(s => esc(s)).join(', ') + '</div>';
            if (item.features && item.features.length) html += '<div class="design-card-meta">Features: ' + item.features.map(f => esc(f)).join(', ') + '</div>';
            if (item.export_vars && item.export_vars.length) html += '<div class="design-card-meta">Export: ' + item.export_vars.map(v => esc(v.name) + ' (' + esc(v.type || '') + ')').join(', ') + '</div>';
          }
          card.innerHTML = html;
          container.appendChild(card);
        }
      }

      function renderDesign(json) {
        _lastDesignJson = json;
        document.getElementById('design-create-project').disabled = false;
        tabs.style.display = 'flex';
        document.getElementById('design-count-systems').textContent = (json.systems || []).length;
        document.getElementById('design-count-features').textContent = (json.features || []).length;
        document.getElementById('design-count-gamemodes').textContent = (json.gamemodes || []).length;
        document.getElementById('design-count-events').textContent = (json.event_types || []).length;
        const activeSection = tabs.querySelector('.design-tab.active').dataset.section;
        const container = document.createElement('div');
        container.className = 'design-section';
        if (activeSection === 'systems') renderDesignCards(container, json.systems, 'systems');
        else if (activeSection === 'features') renderDesignCards(container, json.features, 'features');
        else if (activeSection === 'gamemodes') renderDesignCards(container, json.gamemodes, 'gamemodes');
        else if (activeSection === 'events') {
          const events = json.event_types || [];
          if (!events.length) { container.innerHTML = '<p class="muted" style="text-align:center;padding:20px;">无</p>'; }
          else {
            const table = document.createElement('table');
            table.className = 'design-event-table';
            table.innerHTML = '<tr><th>Event</th><th>Description</th></tr>';
            for (const ev of events) {
              table.innerHTML += '<tr><td><code>' + esc(ev.name) + '</code></td><td>' + esc(ev.description || '') + '</td></tr>';
            }
            container.appendChild(table);
          }
        }
        panel.innerHTML = '';
        panel.appendChild(container);
        // Add copy button
        const copyBtn = document.createElement('button');
        copyBtn.type = 'button';
        copyBtn.className = 'secondary';
        copyBtn.textContent = '复制 JSON';
        copyBtn.style.cssText = 'margin-bottom:10px;font-size:0.8em;padding:4px 10px;';
        copyBtn.addEventListener('click', () => {
          navigator.clipboard.writeText(JSON.stringify(json, null, 2)).then(() => {
            copyBtn.textContent = '已复制';
            setTimeout(() => { copyBtn.textContent = '复制 JSON'; }, 1500);
          }).catch(() => {
            copyBtn.textContent = '失败';
          });
        });
        panel.insertBefore(copyBtn, panel.firstChild);
      }

      async function createProject() {
        if (!_lastDesignJson) return;
        const btn = document.getElementById('design-create-project');
        const defaultName = _lastDesignJson.name || 'design';
        const name = window.prompt('项目名:', defaultName);
        if (!name) return;
        btn.disabled = true;
        btn.textContent = '创建中...';
        try {
          // 1. Create project
          const created = await fetchJson('/api/projects', {
            method: 'POST',
            headers: {'content-type': 'application/json'},
            body: JSON.stringify({name, workspace_root: '', set_default: false}),
          });
          // 2. Scaffold from design JSON
          const scaf = await fetchJson('/api/projects/' + encodeURIComponent(name) + '/scaffold', {
            method: 'POST',
            headers: {'content-type': 'application/json'},
            body: JSON.stringify(_lastDesignJson),
          });
          // 3. Set as default workspace
          await fetch('/api/projects/' + encodeURIComponent(name) + '/default', {method: 'POST'});
          // 4. Navigate to workspace
          localStorage.setItem('godotter:selectedProject', name);
          window.location.href = '/';
        } catch (err) {
          btn.disabled = false;
          btn.textContent = '创建项目';
          alert('创建失败: ' + err.message);
        }
      }

      function handleReply(text) {
        const json = extractJSON(text);
        if (json && hasDesignKeys(json)) {
          renderDesign(json);
        } else {
          const div = document.createElement('div');
          div.className = 'bubble assistant';
          div.style.marginBottom = '10px';
          const strong = document.createElement('strong');
          strong.style.display = 'block';
          strong.style.marginBottom = '4px';
          strong.textContent = 'Design';
          const p = document.createElement('p');
          p.style.margin = '0';
          p.style.whiteSpace = 'pre-wrap';
          p.textContent = text;
          div.append(strong, p);
          panel.innerHTML = '';
          panel.appendChild(div);
          tabs.style.display = 'none';
        }
      }

      function renderMessages(messages) {
        panel.innerHTML = '';
        if (!messages.length) {
          const div = document.createElement('div');
          div.className = 'bubble assistant';
          div.style.marginBottom = '10px';
          const strong = document.createElement('strong');
          strong.style.display = 'block';
          strong.style.marginBottom = '4px';
          strong.textContent = 'Design';
          const p = document.createElement('p');
          p.style.margin = '0';
          p.className = 'muted';
          p.textContent = '你好！我是玩法设计助手。描述你想设计的游戏玩法，我会拆解成 Systems、Features、GameModes 的结构化 JSON。';
          div.append(strong, p);
          panel.appendChild(div);
          tabs.style.display = 'none';
          return;
        }
        const last = messages[messages.length - 1];
        if (last.role === 'assistant') handleReply(last.content);
      }

      for (const tab of tabs.querySelectorAll('.design-tab')) {
        tab.addEventListener('click', () => {
          for (const t of tabs.querySelectorAll('.design-tab')) t.classList.remove('active');
          tab.classList.add('active');
          if (_lastDesignJson) renderDesign(_lastDesignJson);
        });
      }

      async function loadDesignSessions() {
        try {
          const result = await fetchJson('/api/design/sessions');
          const sessions = result.sessions || [];
          sessionSelect.innerHTML = '<option value="">选择历史会话...</option>';
          for (const s of sessions) {
            const opt = document.createElement('option');
            opt.value = s.session_id;
            opt.textContent = s.title || s.session_id;
            if (currentDesignSession && s.session_id === currentDesignSession.session_id) opt.selected = true;
            sessionSelect.appendChild(opt);
          }
        } catch (_) { sessionSelect.innerHTML = '<option value="">加载失败</option>'; }
      }

      async function loadDesignSession(sessionId) {
        try {
          const detail = await fetchJson('/api/design/sessions/' + encodeURIComponent(sessionId));
          currentDesignSession = detail.session;
          localStorage.setItem(STORAGE_KEY, sessionId);
          renderMessages(detail.messages || []);
          status.textContent = '当前：' + (currentDesignSession.title || 'Design chat');
        } catch (error) { status.textContent = '加载失败：' + error.message; }
      }

      async function ensureDesignSession(initialTitle) {
        if (currentDesignSession) return currentDesignSession;
        const created = await fetchJson('/api/design/sessions', {
          method: 'POST',
          headers: {'content-type': 'application/json'},
          body: JSON.stringify({title: initialTitle.slice(0, 48)}),
        });
        currentDesignSession = created.session;
        localStorage.setItem(STORAGE_KEY, created.session.session_id);
        status.textContent = '当前：' + (currentDesignSession.title || 'Design chat');
        await loadDesignSessions();
        return currentDesignSession;
      }

      loadBtn.addEventListener('click', async () => {
        const sid = sessionSelect.value;
        if (!sid) return;
        await loadDesignSession(sid);
      });

      newBtn.addEventListener('click', async () => {
        if (_designController) _designController.abort();
        currentDesignSession = null;
        _lastDesignJson = null;
        document.getElementById('design-delete-session').disabled = true;
        panel.innerHTML = '';
        renderMessages([]);
        status.textContent = '新对话';
        localStorage.removeItem(STORAGE_KEY);
        await loadDesignSessions();
      });

      document.getElementById('design-delete-session').addEventListener('click', async () => {
        if (!currentDesignSession) return;
        const sid = currentDesignSession.session_id;
        if (!window.confirm('确定删除当前会话吗？')) return;
        try {
          await fetchJson('/api/design/sessions/' + encodeURIComponent(sid), {method: 'DELETE'});
          currentDesignSession = null;
          _lastDesignJson = null;
          panel.innerHTML = '';
          renderMessages([]);
          status.textContent = '已删除';
          localStorage.removeItem(STORAGE_KEY);
          await loadDesignSessions();
        } catch (err) {
          alert('删除失败: ' + err.message);
        }
      });

      document.getElementById('design-create-project').addEventListener('click', createProject);

      form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const text = prompt.value.trim();
        if (!text) return;
        prompt.value = '';
        status.textContent = '思考中...';
        try {
          const session = await ensureDesignSession(text);
          _designController = new AbortController();
          const resp = await fetch('/api/design/sessions/' + encodeURIComponent(session.session_id) + '/chat', {
            method: 'POST',
            headers: {'content-type': 'application/json'},
            body: JSON.stringify({message: text}),
            signal: _designController.signal,
          });
          _designController = null;
          if (!resp.ok) throw new Error(await resp.text());
          const data = await resp.json();
          currentDesignSession = data.session;
          handleReply(data.reply);
          status.textContent = '当前：' + (currentDesignSession.title || 'Design chat');
          await loadDesignSessions();
        } catch (err) {
          if (err.name === 'AbortError') return;
          status.textContent = '失败';
        }
      });

      async function initDesign() {
        const savedId = localStorage.getItem(STORAGE_KEY);
        await loadDesignSessions();
        if (savedId) {
          try {
            const detail = await fetchJson('/api/design/sessions/' + encodeURIComponent(savedId));
          currentDesignSession = detail.session;
          document.getElementById('design-delete-session').disabled = false;
            renderMessages(detail.messages || []);
            status.textContent = '当前：' + (currentDesignSession.title || 'Design chat');
          } catch (_) {
            localStorage.removeItem(STORAGE_KEY);
            renderMessages([]);
          }
        }
      }
      initDesign();
    </script>"""
    return _secondary_page(
        title='Design',
        eyebrow='Tools',
        summary='Discuss game design, split systems/features/gamemodes, refine template conventions.',
        current='Design',
        body_html=body_html,
        script_html=script_html,
    )

DESIGN_SESSIONS_DIR = Path('config') / 'design_sessions'


def _design_session_path(session_id: str) -> Path:
    return DESIGN_SESSIONS_DIR / f'{session_id}.json'


def _design_messages_path(session_id: str) -> Path:
    return DESIGN_SESSIONS_DIR / session_id / 'messages.jsonl'


def _create_design_session(*, title: str = '') -> dict[str, object]:
    session_id = _new_id('ds')
    now = _now_iso()
    session = {
        'session_id': session_id,
        'created_at': now,
        'updated_at': now,
        'title': title.strip() or 'Design chat',
    }
    _write_json(_design_session_path(session_id), session)
    msg_dir = _design_messages_path(session_id).parent
    msg_dir.mkdir(parents=True, exist_ok=True)
    _design_messages_path(session_id).write_text('', encoding='utf-8', newline='\n')
    return session


def _list_design_sessions() -> list[dict[str, object]]:
    if not DESIGN_SESSIONS_DIR.exists():
        return []
    paths = sorted(DESIGN_SESSIONS_DIR.glob('ds_*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
    sessions = []
    for path in paths:
        try:
            sessions.append(json.loads(path.read_text(encoding='utf-8')))
        except Exception:
            continue
    return sessions


def _load_design_session(session_id: str) -> dict[str, object]:
    sid = _validate_id(session_id, prefix='ds')
    path = _design_session_path(sid)
    if not path.exists():
        raise HTTPException(status_code=404, detail='design_session_not_found')
    return json.loads(path.read_text(encoding='utf-8'))


def _read_design_messages(session_id: str) -> list[dict[str, object]]:
    path = _design_messages_path(session_id)
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


def _append_design_message(session_id: str, *, role: str, content: str) -> dict[str, object]:
    session = _load_design_session(session_id)
    content = content.strip()
    if not content:
        raise HTTPException(status_code=400, detail='message_content_required')
    if role not in {'user', 'assistant'}:
        raise HTTPException(status_code=400, detail='invalid_message_role')
    message = {
        'message_id': _new_id('msg'),
        'created_at': _now_iso(),
        'role': role,
        'content': content,
    }
    path = _design_messages_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(message, ensure_ascii=False) + '\n')
    session['updated_at'] = message['created_at']
    if session.get('title') == 'Design chat' and role == 'user':
        session['title'] = content[:48]
    _write_json(_design_session_path(session_id), session)
    return message


@app.get('/api/design/sessions')
def design_sessions_list(request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    return {'ok': True, 'sessions': _list_design_sessions()}


@app.post('/api/design/sessions')
async def design_sessions_create(request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    title = str(payload.get('title', '')).strip()
    session = _create_design_session(title=title)
    return {'ok': True, 'session': session, 'messages': []}


@app.get('/api/design/sessions/{session_id}')
def design_sessions_get(session_id: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    session = _load_design_session(session_id)
    return {
        'ok': True,
        'session': session,
        'messages': _read_design_messages(session_id),
    }


@app.delete('/api/design/sessions/{session_id}')
def design_sessions_delete(session_id: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    sid = _validate_id(session_id, prefix='ds')
    meta = _design_session_path(sid)
    data_dir = meta.parent / sid
    removed = []
    if meta.exists():
        meta.unlink()
        removed.append(meta.name)
    if data_dir.exists():
        import shutil
        shutil.rmtree(data_dir)
        removed.append(data_dir.name)
    return {'ok': True, 'removed': removed, 'session_id': sid}


def _build_design_system_prompt() -> str:
    template_dir = Path(__file__).resolve().parents[2] / 'templates' / 'godotter_game_template'
    conventions_path = template_dir / 'Docs' / 'Conventions.md'

    conventions_text = ""
    if conventions_path.exists():
        conventions_text = conventions_path.read_text(encoding='utf-8')

    schema_text = '''{
  "name": "PacMan",
  "systems": [
    {
      "name": "pacman",
      "directory": "game/systems/pacman/",
      "description": "Manages Pac-Man grid position, direction, movement tick, and alive/dead state.",
      "publishes": ["PACMAN_MOVED", "PACMAN_DIED"],
      "subscribes": ["PACMAN_DIRECTION_REQUESTED"],
      "export_vars": [{"name": "move_speed", "type": "float", "default": "0.15"}]
    }
  ],
  "features": [
    {
      "name": "eat_dot",
      "directory": "game/features/eat_dot/",
      "description": "Detects Pac-Man on a dot tile, removes the dot and adds score.",
      "uses_systems": ["pacman"],
      "subscribes": ["PACMAN_MOVED"]
    }
  ],
  "gamemodes": [
    {
      "name": "pacman",
      "directory": "game/gamemodes/pacman/",
      "description": "Classic Pac-Man game loop with maze, dots, ghosts, power pellets, and lives.",
      "systems": ["pacman", "maze", "ghost", "dot", "score"],
      "features": ["eat_dot", "eat_power_pellet", "ghost_collision"],
      "export_vars": [{"name": "level_data_path", "type": "String", "default": ""}]
    }
  ],
  "event_types": [
    {"name": "PACMAN_MOVED", "description": "Published by pacman_system after each grid move."},
    {"name": "PACMAN_DIED", "description": "Published by pacman_system when lives reach zero."}
  ]
}'''

    return f"""You are a game design assistant for the Godotter project.
Your workspace is the Godotter game template at: {template_dir}

## Before each response, use tools to:
- Read Docs/Conventions.md at {template_dir}/Docs/Conventions.md for the full architectural rules
- Use list_files to explore game/systems/ game/features/ game/gamemodes/ in the template

## Your Job
Decompose game ideas into Systems, Features, and GameModes per the conventions.

## Output Format — STRICT

When the user asks you to decompose a game idea, your response MUST be ONLY this:

```json
{schema_text}
```

RULES:
- Your ENTIRE response must be a single ```json code block.
- NO markdown sections, NO tables, NO bullet lists, NO explanations before or after.
- If the user asks a clarifying question, output the JSON with your best guess. If you truly cannot decompose without more info, output ONE SHORT question in a single plain-text sentence — then wait.
- NEVER write more than one sentence of plain text.

NAMING RULES (from template conventions):
- system name "pacman" → main file: game/systems/pacman/pacman_system.gd
- feature name "eat_dot" → main file: game/features/eat_dot/eat_dot_feature.gd
- gamemode name "pacman" → main file: game/gamemodes/pacman/pacman_mode.gd
- Directory name is short (no suffix). Suffix is on the main file only.
- The "name" field uses the short name (e.g., "pacman", not "pacman_system").
- uses_systems arrays reference short names (e.g., "pacman", not "pacman_system").
- uses_features arrays reference short names (e.g., "eat_dot", not "eat_dot_feature").

# Current Template Conventions

{conventions_text}"""


@app.post('/api/design/sessions/{session_id}/chat')
async def design_sessions_chat(session_id: str, request: Request) -> dict[str, object]:
    _require_token_if_configured(request)
    session = _load_design_session(session_id)

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail='invalid_json')

    message = str(payload.get('message', '')).strip()
    if not message:
        raise HTTPException(status_code=400, detail='message_required')

    user_msg = _append_design_message(session_id, role='user', content=message)

    settings = get_settings()
    brain_name = settings.resolved_design_brain
    template_dir = Path(__file__).resolve().parents[2] / 'templates' / 'godotter_game_template'

    from godotter.tools.files import ReadFile, ListFiles
    registry = ToolRegistry([ReadFile(), ListFiles()])
    template_settings = settings.model_copy(update={'workspace_root': template_dir})
    memory = Memory(template_settings.resolved_memory_path)
    brain = create_brain(template_settings, brain_name, model_override=getattr(settings, 'design_model', None))

    agent = Agent(
        brain=brain,
        settings=template_settings,
        registry=registry,
        memory=memory,
        mode='act',
        brain_name=brain_name,
        project_summary=_build_design_system_prompt(),
    )
    agent.expose_tool_output = False
    if hasattr(agent.brain, 'tool_choice'):
        setattr(agent.brain, 'tool_choice', 'auto')

    recent = _read_design_messages(session_id)
    for msg in recent[-10:]:
        agent.conversation.append({'role': msg['role'], 'content': msg['content']})
    agent.conversation.insert(0, {'role': 'system', 'content': _build_design_system_prompt()})

    try:
        reply = agent._agentic_loop()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f'design_agent_failed: {exc}')

    assistant_msg = _append_design_message(session_id, role='assistant', content=reply)

    return {
        'ok': True,
        'message': user_msg,
        'reply': assistant_msg['content'],
        'session': _load_design_session(session_id),
    }


@app.get('/settings', response_class=HTMLResponse)
def settings_page(request: Request) -> str:
    _require_token_if_configured(request)
    body_html = """
      <section class="panel">
        <div class="panel-head">
          <p class="eyebrow">Settings</p>
          <h2>全局配置</h2>
        </div>
        <div class="card-grid">
          <section class="card">
            <h3 style="margin-bottom:10px">API Keys</h3>
            <div id="api-keys-grid"></div>
          </section>
          <section class="card">
            <h3 style="margin-bottom:10px">Agent</h3>
            <p class="muted" style="margin-bottom:10px">为 Chat、Plan、Run 分别选择提供商和模型。</p>
            <div class="agent-table" id="settings-agent-table"></div>
          </section>
          <section class="card" style="overflow:hidden">
            <h3 style="margin-bottom:10px">导出环境</h3>
            <p class="muted" style="margin-bottom:8px">选择一个项目以诊断导出配置。</p>
            <div class="settings-inline">
              <select id="settings-doctor-project"><option value="">选择项目...</option></select>
              <button type="button" id="settings-doctor-run">诊断</button>
              <span class="muted" id="settings-doctor-status"></span>
            </div>
            <div id="settings-env-body" style="margin-top:8px;overflow:hidden"></div>
          </section>
          <section class="card">
            <h3 style="margin-bottom:10px">项目</h3>
            <div class="settings-inline">
              <span class="muted">项目根目录:</span>
              <code id="settings-projects-root">...</code>
              <button type="button" id="settings-project-root-edit">修改</button>
            </div>
          </section>
        </div>
      </section>
    """
    script_html = """
      <script>
        async function api(path, opts) {
          const response = await fetch(path, opts);
          if (!response.ok) throw new Error(await response.text());
          return response.json();
        }
        function esc(v) {
          return String(v ?? '').replace(/[&<>\"']/g, (ch) => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[ch]));
        }

        async function loadApiKeys() {
          const grid = document.getElementById('api-keys-grid');
          try {
            const data = await api('/api/keys');
            const keys = data.keys || {};
            const providers = ['deepseek', 'siliconflow', 'alibaba', 'moonshot'];
            grid.innerHTML = providers.map((p) => {
              const status = keys[p] ? 'configured' : 'not set';
              const masked = keys[p] ? keys[p].slice(0,4) + '...' + keys[p].slice(-4) : '';
              return `<div class=\"settings-row\">
                <span class=\"settings-label\">${p}</span>
                <code>${status === 'configured' ? masked : '(not set)'}</code>
                <button type=\"button\" class=\"env-set-btn\" data-provider=\"${p}\" data-value=\"${esc(keys[p] || '')}\">设置</button>
              </div>`;
            }).join('');
            for (const btn of grid.querySelectorAll('.env-set-btn')) {
              btn.addEventListener('click', () => {
                const p = btn.dataset.provider;
                const val = prompt('输入 ' + p.toUpperCase() + ' API Key:', btn.dataset.value || '');
                if (val === null) return;
                fetch('/api/keys', {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({provider:p,key:val})})
                  .then(() => loadApiKeys());
              });
            }
          } catch (_) { grid.innerHTML = '<p class=\"muted\">加载失败</p>'; }
        }

        async function loadAgentSettings() {
          const table = document.getElementById('settings-agent-table');
          if (!table) return;
          try {
            const cfg = (await api('/api/config-state')).config || {};
            const providerModels = cfg.provider_models || {};
            const providers = ['stub','deepseek','siliconflow','alibaba','moonshot'];
            const tasks = [
              {key:'chat',label:'Chat',brain:cfg.chat_brain||cfg.default_brain||'stub',model:cfg.chat_model},
              {key:'plan',label:'Plan',brain:cfg.plan_brain||cfg.default_brain||'stub',model:cfg.plan_model},
              {key:'act',label:'Run',brain:cfg.act_brain||cfg.default_brain||'stub',model:cfg.act_model},
              {key:'design',label:'Design',brain:cfg.design_brain||cfg.default_brain||'stub',model:cfg.design_model},
            ];
            table.innerHTML = tasks.map((t) => {
              const brain = t.brain||'stub';
              const effModel = t.model || providerModels[brain] || '';
              return `<div class=\"agent-row\" data-task=\"${t.key}\">
                <div class=\"agent-task-label\">${t.label}</div>
                <div class=\"agent-selects\">
                  <label class=\"agent-inline-label\">提供商</label>
                  <select class=\"agent-brain-select\" data-task=\"${t.key}\">
                    ${providers.map((p) => `<option value=\"${p}\" ${p===brain?'selected':''}>${p}</option>`).join('')}
                  </select>
                  <label class=\"agent-inline-label\">模型</label>
                  <select class=\"agent-model-select\" data-task=\"${t.key}\">
                    ${effModel ? `<option value=\"${esc(effModel)}\" selected>${esc(effModel)}</option>` : '<option value=\"\">(使用默认)</option>'}
                  </select>
                </div>
              </div>`;
            }).join('');
            // Wire brain change
            for (const sel of table.querySelectorAll('.agent-brain-select')) {
              sel.addEventListener('change', async () => {
                const task = sel.dataset.task;
                const key = 'GODOTTER_' + task.toUpperCase() + '_BRAIN';
                await fetch('/api/config', {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({key,value:sel.value})});
                loadModelsForTask(task, sel.value);
              });
            }
            // Wire model change
            for (const sel of table.querySelectorAll('.agent-model-select')) {
              sel.addEventListener('change', async () => {
                const task = sel.dataset.task;
                const key = 'GODOTTER_' + task.toUpperCase() + '_MODEL';
                await fetch('/api/config', {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({key,value:sel.value})});
              });
            }
          } catch (_) { table.innerHTML = '<p class=\"muted\">加载失败</p>'; }
        }

        async function loadModelsForTask(taskKey, provider) {
          const table = document.getElementById('settings-agent-table');
          const select = table?.querySelector(`.agent-model-select[data-task=\"${taskKey}\"]`);
          if (!select) return;
          if (provider === 'stub') { select.innerHTML = '<option value=\"stub\" selected>stub</option>'; return; }
          const cur = select.value;
          select.innerHTML = '<option value=\"\">加载中...</option>';
          try {
            const r = await api('/api/models?provider=' + encodeURIComponent(provider));
            select.innerHTML = '<option value=\"\">(使用默认)</option>';
            for (const m of r.models||[]) {
              const opt = document.createElement('option');
              opt.value = m; opt.textContent = m;
              if (m === cur) opt.selected = true;
              select.appendChild(opt);
            }
          } catch (_) { select.innerHTML = '<option value=\"\">获取失败</option>'; }
        }

        async function loadProjectsRoot() {
          try {
            const data = await api('/api/projects-root');
            document.getElementById('settings-projects-root').textContent = data.path;
          } catch (_) {}
        }
        document.getElementById('settings-project-root-edit').addEventListener('click', async () => {
          const cur = document.getElementById('settings-projects-root').textContent;
          const path = prompt('项目根目录路径:', cur);
          if (!path || path === cur) return;
          await fetch('/api/projects-root', {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({path})});
          loadProjectsRoot();
        });

        async function loadBuildDoctor() {
          const project = document.getElementById('settings-doctor-project').value;
          const body = document.getElementById('settings-env-body');
          const status = document.getElementById('settings-doctor-status');
          if (!project) { body.innerHTML = '<p class=\"muted\">请先选择一个项目。</p>'; return; }
          status.textContent = '诊断中...';
          try {
            const r = await api('/api/projects/' + encodeURIComponent(project) + '/builds/doctor');
            const d = r.doctor || {};
            const s = r.suggestions || {};
            const rows = [
              ['Godot', d.godot_version||'(未检测)', d.godot_path_exists],
              ['导出模板', d.templates_detected ? (d.templates_root||'已检测') : '未检测到', d.templates_detected],
              ['Android SDK', d.android_sdk_valid ? d.android_sdk_path + ' ('+(d.android_build_tools_version||'')+')' : (d.android_sdk_path||'未设置'), d.android_sdk_valid],
              ['JDK', d.java_valid ? d.java_home + ' ('+(d.java_version||'')+')' : (d.java_home||'未设置'), d.java_valid],
              ['Keystore', d.keystore_valid ? d.keystore_path : (d.keystore_path||'未设置'), d.keystore_valid],
              ['Android 模板', d.android_template_installed ? '已安装' : '未安装', d.android_template_installed],
            ];
            status.textContent = '';
            body.innerHTML = rows.map((r) => {
              const ok = r[2] ? '<span style=\"color:#34d399\">✓</span>' : '<span style=\"color:#f59e0b\">✗</span>';
              const setBtn = ['安卓SDK','JDK','Keystore'].includes(r[0])
                ? `<button class=\"env-set-btn\" data-key=\"GODOTTER_ANDROID_${r[0]=='Keystore'?'KEYSTORE':'SDK'}_PATH\" style=\"font-size:0.7rem\">设置</button>`
                : '';
              return `<div class=\"settings-row\"><span class=\"settings-label\">${r[0]}</span>${ok}<code>${esc(String(r[1]))}</code>${setBtn}</div>`;
            }).join('');
            for (const btn of body.querySelectorAll('.env-set-btn')) {
              btn.addEventListener('click', () => {
                const key = btn.dataset.key;
                const val = prompt('设置 ' + key + ':', s[key.replace('GODOTTER_','').toLowerCase()+'_path'] || '');
                if (val === null) return;
                fetch('/api/config', {method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({key,value:val})})
                  .then(() => loadBuildDoctor());
              });
            }
          } catch (_) { body.innerHTML = '<p class=\"muted\">诊断失败</p>'; status.textContent = ''; }
        }

        document.getElementById('settings-doctor-run').addEventListener('click', loadBuildDoctor);

        async function loadProjects() {
          try {
            const data = await api('/api/projects');
            const sel = document.getElementById('settings-doctor-project');
            sel.innerHTML = '<option value=\"\">选择项目...</option>';
            for (const p of data.projects||[]) {
              const opt = document.createElement('option');
              opt.value = p.name; opt.textContent = p.name;
              sel.appendChild(opt);
            }
          } catch (_) {}
        }

        loadApiKeys();
        loadAgentSettings();
        loadProjectsRoot();
        loadProjects();
      </script>
    """
    return _secondary_page(
        title='Settings - Godotter Web Console',
        eyebrow='Settings',
        summary='Configure API keys, Agent providers, export environment, and project settings.',
        current='Settings',
        body_html=body_html,
        script_html=script_html,
    )


@app.get('/config', response_class=HTMLResponse)
def config_page(request: Request) -> str:
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url='/settings', status_code=302)


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
    body_html = f"""
      <section class="panel">
        <div class="panel-head">
          <p class="eyebrow">Environment</p>
          <h2>.env editor</h2>
          <p class="subtle"><code>{escaped_path}</code> - {escaped_hint}</p>
        </div>
        <div class="card-grid">
          <section class="card">
            <div class="panel-stack">
              <div>
                <h3>Question-driven fields</h3>
                <p class="muted">Common keys are surfaced as form fields. Unknown keys remain preserved in the raw text below.</p>
              </div>
              <div class="qa" id="qa"></div>
            </div>
          </section>
          <section class="card">
            <div class="panel-stack">
              <div>
                <h3>Raw .env text</h3>
                <p class="muted">Advanced edits still happen directly in the source text. Saving merges the structured answers back into this block.</p>
              </div>
              <textarea id="text" spellcheck="false">{escaped_content}</textarea>
              <div class="actions">
                <button id="save">Save config</button>
                <button id="sync" class="secondary">Reload question fields</button>
                <span id="status" class="status muted"></span>
              </div>
            </div>
          </section>
        </div>
      </section>
    """
    script_html = """<script>
      const knownFields = [
        { key: 'GODOTTER_DEFAULT_BRAIN', q: 'Default provider / brain?', hint: 'For example: deepseek / moonshot / alibaba / siliconflow.' },
        { key: 'DEEPSEEK_API_KEY', q: 'DeepSeek API key?', hint: 'Leave empty if you do not use DeepSeek.', secret: true },
        { key: 'MOONSHOT_API_KEY', q: 'Moonshot API key?', hint: 'Leave empty if you do not use Moonshot.', secret: true },
        { key: 'ALIBABA_API_KEY', q: 'Alibaba API key?', hint: 'Leave empty if you do not use Alibaba.', secret: true },
        { key: 'SILICONFLOW_API_KEY', q: 'SiliconFlow API key?', hint: 'Leave empty if you do not use SiliconFlow.', secret: true },
        { key: 'GODOTTER_WEB_TOKEN', q: 'Web access token?', hint: 'When configured, API requests require x-godotter-token.', secret: true },
        { key: 'GODOT_PATH', q: 'Godot executable path?', hint: 'Example: D:/Godots/Engines/Godot_v4.6.1-stable_win64/...console.exe.' },
      ];

      const btn = document.getElementById('save');
      const status = document.getElementById('status');
      const text = document.getElementById('text');
      const qa = document.getElementById('qa');
      const sync = document.getElementById('sync');

      function parseEnv(raw) {
        const result = {};
        for (const line of raw.split(/\r?\n/)) {
          const trimmed = line.trim();
          if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) continue;
          const idx = trimmed.indexOf('=');
          result[trimmed.slice(0, idx)] = trimmed.slice(idx + 1);
        }
        return result;
      }

      function mergeEnv(raw, values) {
        const seen = new Set();
        const lines = raw.split(/\r?\n/).map((line) => {
          const trimmed = line.trim();
          if (!trimmed || trimmed.startsWith('#') || !trimmed.includes('=')) return line;
          const idx = trimmed.indexOf('=');
          const key = trimmed.slice(0, idx);
          if (!(key in values)) return line;
          seen.add(key);
          return `${key}=${values[key]}`;
        });
        for (const field of knownFields) {
          if (!seen.has(field.key) && values[field.key]) {
            lines.push(`${field.key}=${values[field.key]}`);
          }
        }
        return lines.join('
').replace(/
*$/, '
');
      }

      function renderQA() {
        qa.innerHTML = '';
        const values = parseEnv(text.value);
        for (const field of knownFields) {
          const item = document.createElement('div');
          item.className = 'qa-item';
          const label = document.createElement('label');
          label.setAttribute('for', `env-${field.key}`);
          label.textContent = field.q;
          const hint = document.createElement('p');
          hint.className = 'muted';
          hint.textContent = `${field.key} - ${field.hint}`;
          const input = document.createElement('input');
          input.id = `env-${field.key}`;
          input.dataset.key = field.key;
          input.type = field.secret ? 'password' : 'text';
          input.autocomplete = 'off';
          input.value = values[field.key] || '';
          item.append(label, hint, input);
          qa.appendChild(item);
        }
      }

      function syncToText() {
        const values = {};
        for (const input of qa.querySelectorAll('input[data-key]')) {
          values[input.dataset.key] = input.value.trim();
        }
        text.value = mergeEnv(text.value, values);
      }

      btn.addEventListener('click', async () => {
        syncToText();
        status.textContent = 'Saving...';
        const resp = await fetch('/api/env', {
          method: 'PUT',
          headers: { 'content-type': 'text/plain' },
          body: text.value
        });
        if (resp.ok) {
          status.textContent = 'Saved.';
        } else {
          const t = await resp.text();
          status.textContent = 'Error: ' + t;
        }
      });
      sync.addEventListener('click', renderQA);
      renderQA();
    </script>"""
    return _secondary_page(
        title='Environment',
        eyebrow='System',
        summary='Edit provider keys, default brain selection, and other machine-local environment variables in one place.',
        current='Env',
        body_html=body_html,
        script_html=script_html,
    )


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
