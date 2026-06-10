from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import json
import secrets


@dataclass(slots=True)
class PlanTask:
    id: str
    title: str
    goal: str
    depends_on: list[str] = field(default_factory=list)
    scope: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PlanPack:
    plan_id: str
    created_at: str
    workspace_root: str
    goal: str
    name: str = ""
    global_constraints: list[str] = field(default_factory=list)
    tasks: list[PlanTask] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["tasks"] = [asdict(task) for task in self.tasks]
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass(slots=True)
class PlanState:
    plan_id: str
    updated_at: str
    task_status: dict[str, str] = field(default_factory=dict)  # id -> pending|running|pass|fail|skipped
    task_artifacts: dict[str, dict] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


def default_plan_dir(workspace_root: Path) -> Path:
    return workspace_root / ".godotter" / "plans"


def ensure_plan_dir(workspace_root: Path) -> Path:
    out = default_plan_dir(workspace_root)
    out.mkdir(parents=True, exist_ok=True)
    return out


def build_plan_filename(goal: str, created_at: datetime | None = None) -> str:
    ts = (created_at or datetime.now()).strftime("%Y%m%d_%H%M%S")
    short = secrets.token_hex(3)
    return f"{ts}_plan_{short}.json"


def write_planpack(workspace_root: Path, pack: PlanPack, *, filename: str | None = None) -> Path:
    out_dir = ensure_plan_dir(workspace_root)
    out_path = out_dir / (filename or build_plan_filename(pack.goal))
    out_path.write_text(pack.to_json(), encoding="utf-8", newline="\n")
    (out_dir / "latest.json").write_text(pack.to_json(), encoding="utf-8", newline="\n")
    return out_path


def load_planpack(path: Path) -> PlanPack:
    data = json.loads(path.read_text(encoding="utf-8"))
    tasks = [PlanTask(**raw) for raw in data.get("tasks", []) if isinstance(raw, dict)]
    return PlanPack(
        plan_id=str(data.get("plan_id", "")),
        created_at=str(data.get("created_at", "")),
        workspace_root=str(data.get("workspace_root", "")),
        name=str(data.get("name", "")),
        goal=str(data.get("goal", "")),
        global_constraints=list(data.get("global_constraints", [])),
        tasks=tasks,
    )


def plan_state_path(plan_path: Path) -> Path:
    return plan_path.with_suffix(".state.json")


def load_planstate(path: Path) -> PlanState:
    data = json.loads(path.read_text(encoding="utf-8"))
    return PlanState(
        plan_id=str(data.get("plan_id", "")),
        updated_at=str(data.get("updated_at", "")),
        task_status=dict(data.get("task_status", {})),
        task_artifacts=dict(data.get("task_artifacts", {})),
    )


def write_planstate(path: Path, state: PlanState) -> None:
    path.write_text(state.to_json(), encoding="utf-8", newline="\n")


def new_plan_id() -> str:
    return f"pp_{secrets.token_hex(6)}"


def new_task_id() -> str:
    return f"t_{secrets.token_hex(4)}"

