from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import json
import secrets


@dataclass(slots=True)
class RunAttempt:
    index: int
    status: str = "running"
    started_at: str = ""
    updated_at: str = ""
    agent_output: str = ""
    changed_files: list[str] = field(default_factory=list)
    failure_report: str | None = None
    verify_report: str | None = None


@dataclass(slots=True)
class RunState:
    run_id: str
    workpack_path: str
    task_id: str
    goal: str
    workspace_root: str
    mode: str
    status: str = "running"
    started_at: str = ""
    updated_at: str = ""
    attempts: list[RunAttempt] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def new_run_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"run_{timestamp}_{secrets.token_hex(3)}"


def default_run_dir(workspace_root: Path) -> Path:
    return workspace_root / ".godotter" / "runs"


def runstate_path(workspace_root: Path, run_id: str) -> Path:
    return default_run_dir(workspace_root) / f"{run_id}.json"


def latest_runstate_path(workspace_root: Path) -> Path:
    return default_run_dir(workspace_root) / "latest.json"


def create_runstate(
    *,
    workspace_root: Path,
    workpack_path: Path,
    task_id: str,
    goal: str,
    mode: str,
) -> tuple[RunState, Path]:
    now = datetime.now().isoformat(timespec="seconds")
    state = RunState(
        run_id=new_run_id(),
        workpack_path=workpack_path.as_posix(),
        task_id=task_id,
        goal=goal,
        workspace_root=workspace_root.as_posix(),
        mode=mode,
        started_at=now,
        updated_at=now,
    )
    path = runstate_path(workspace_root, state.run_id)
    write_runstate(workspace_root, state)
    return state, path


def append_attempt(state: RunState, index: int) -> RunAttempt:
    now = datetime.now().isoformat(timespec="seconds")
    attempt = RunAttempt(index=index, started_at=now, updated_at=now)
    state.attempts.append(attempt)
    state.updated_at = now
    return attempt


def finish_attempt(
    state: RunState,
    attempt: RunAttempt,
    *,
    status: str,
    failure_report: str | None = None,
    verify_report: str | None = None,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    attempt.status = status
    attempt.updated_at = now
    attempt.failure_report = failure_report
    attempt.verify_report = verify_report
    state.updated_at = now


def finish_runstate(state: RunState, *, status: str) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    state.status = status
    state.updated_at = now


def write_runstate(workspace_root: Path, state: RunState) -> Path:
    out_dir = default_run_dir(workspace_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = runstate_path(workspace_root, state.run_id)
    payload = state.to_json()
    path.write_text(payload, encoding="utf-8", newline="\n")
    latest_runstate_path(workspace_root).write_text(payload, encoding="utf-8", newline="\n")
    return path


def load_runstate(path: Path) -> RunState:
    data = json.loads(path.read_text(encoding="utf-8"))
    return RunState(
        run_id=str(data.get("run_id", "")),
        workpack_path=str(data.get("workpack_path", "")),
        task_id=str(data.get("task_id", "")),
        goal=str(data.get("goal", "")),
        workspace_root=str(data.get("workspace_root", "")),
        mode=str(data.get("mode", "")),
        status=str(data.get("status", "")),
        started_at=str(data.get("started_at", "")),
        updated_at=str(data.get("updated_at", "")),
        attempts=[
            RunAttempt(
                index=int(raw.get("index", 0)),
                status=str(raw.get("status", "")),
                started_at=str(raw.get("started_at", "")),
                updated_at=str(raw.get("updated_at", "")),
                agent_output=str(raw.get("agent_output", "")),
                changed_files=list(raw.get("changed_files", [])),
                failure_report=raw.get("failure_report"),
                verify_report=raw.get("verify_report"),
            )
            for raw in data.get("attempts", [])
            if isinstance(raw, dict)
        ],
    )
