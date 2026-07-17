from __future__ import annotations

import json
import secrets
from pathlib import Path

from godotter.agent import Agent
from godotter.config import get_settings
from godotter.context import Memory, build_project_summary, render_project_summary
from godotter.llm import create_brain
from godotter.operations import build_default_operations
from godotter.services.chat import ChatSessionRepository, SessionService
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


class PlanReviewError(ValueError):
    def __init__(self, detail: str, *, status_code: int = 502) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def parse_planner_json(raw: str, workspace_root: Path) -> dict[str, object]:
    raw_stripped = raw.strip()
    try:
        parsed = json.loads(raw_stripped)
    except Exception:
        end = raw_stripped.rfind('}')
        if end == -1:
            debug_path = _write_debug_planner_output(workspace_root, raw_stripped)
            raise PlanReviewError(f'planner_did_not_return_json saved={debug_path.as_posix()}')
        tasks_pos = raw_stripped.rfind('"tasks"', 0, end)
        if tasks_pos != -1:
            start = raw_stripped.rfind('{', 0, tasks_pos)
        else:
            start = raw_stripped.rfind('{', 0, end)
        if start == -1 or end <= start:
            debug_path = _write_debug_planner_output(workspace_root, raw_stripped)
            raise PlanReviewError(f'planner_did_not_return_json saved={debug_path.as_posix()}')
        try:
            parsed = json.loads(raw_stripped[start : end + 1])
        except Exception as exc:
            debug_path = _write_debug_planner_output(workspace_root, raw_stripped)
            raise PlanReviewError(f'planner_json_parse_failed: {exc} saved={debug_path.as_posix()}') from exc
    if not isinstance(parsed, dict):
        raise PlanReviewError('planner_json_root_must_be_object')
    return parsed


def plan_tasks_from_json(parsed: dict[str, object]) -> list[PlanTask]:
    raw_tasks = parsed.get('tasks', [])
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise PlanReviewError('planner_json_missing_tasks')

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
        raise PlanReviewError(str(exc)) from exc
    return tasks


def generate_planpack(
    workspace_root: Path,
    goal: str,
    *,
    brain_name: str | None = None,
) -> tuple[PlanPack, Path]:
    base_settings = get_settings()
    settings = base_settings.model_copy(update={'workspace_root': workspace_root})
    memory = Memory(settings.resolved_memory_path)
    registry = build_default_operations()
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
    agent.brain.tools = []
    if hasattr(agent.brain, 'tool_choice'):
        setattr(agent.brain, 'tool_choice', 'none')

    scout = scout_workspace(workspace_root, goal, max_files=40)
    prompt, constraints = build_plan_prompt(
        goal,
        [ScoutPromptRef(path=ref.path, reason=ref.reason) for ref in scout.relevant_files],
    )
    raw = agent.handle_input(prompt)
    parsed = parse_planner_json(raw, workspace_root)
    tasks = plan_tasks_from_json(parsed)
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


def create_plan_review(
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
    repository = ChatSessionRepository(workspace_root)
    service = SessionService(get_settings(), repository)
    service.set_latest_review(repository.load_session(session_id), review_id, status='reviewing')
    return review


def update_review_status(review: dict[str, object]) -> None:
    items = review.get('items', [])
    if not isinstance(items, list) or not items:
        review['status'] = 'draft'
        return
    statuses = {str(item.get('status') or '') for item in items if isinstance(item, dict)}
    if statuses == {'approved'}:
        review['status'] = 'approved'
    elif 'approved' in statuses:
        review['status'] = 'partially_approved'
    elif 'needs_revision' in statuses:
        review['status'] = 'needs_revision'
    elif statuses == {'rejected'}:
        review['status'] = 'rejected'
    else:
        review['status'] = 'in_review'


def _reviews_dir(workspace_root: Path, session_id: str) -> Path:
    return workspace_root / '.godotter' / 'sessions' / session_id / 'reviews'


def _review_path(workspace_root: Path, session_id: str, review_id: str) -> Path:
    return _reviews_dir(workspace_root, session_id) / f'{review_id}.json'


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8', newline='\n')


def _write_debug_planner_output(workspace_root: Path, text: str) -> Path:
    debug_path = workspace_root / '.godotter' / 'plans' / 'last_planner_output.txt'
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug_path.write_text(text, encoding='utf-8', newline='\n')
    return debug_path


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec='seconds')


def _new_id(prefix: str) -> str:
    return f'{prefix}_{secrets.token_hex(8)}'

