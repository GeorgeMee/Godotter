from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import json
import re
import secrets


@dataclass(slots=True)
class WorkPackFileRef:
    path: str
    reason: str = ""
    priority: int = 100


@dataclass(slots=True)
class WorkPack:
    task_id: str
    created_at: str
    workspace_root: str
    goal: str
    constraints: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    relevant_files: list[WorkPackFileRef] = field(default_factory=list)
    execution_plan: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["relevant_files"] = [asdict(ref) for ref in self.relevant_files]
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def default_workpack_dir(workspace_root: Path) -> Path:
    return workspace_root / ".godotter" / "workpacks"


def ensure_workpack_dir(workspace_root: Path) -> Path:
    target = default_workpack_dir(workspace_root)
    target.mkdir(parents=True, exist_ok=True)
    return target


def build_workpack_filename(goal: str, created_at: datetime | None = None) -> str:
    timestamp = (created_at or datetime.now()).strftime("%Y%m%d_%H%M%S")
    slug = _slugify(goal)[:40] or "task"
    shortid = secrets.token_hex(3)
    return f"{timestamp}_{slug}_{shortid}.json"


def write_workpack(workspace_root: Path, pack: WorkPack, *, filename: str | None = None) -> Path:
    out_dir = ensure_workpack_dir(workspace_root)
    out_path = out_dir / (filename or build_workpack_filename(pack.goal))
    out_path.write_text(pack.to_json(), encoding="utf-8", newline="\n")
    (out_dir / "latest.json").write_text(pack.to_json(), encoding="utf-8", newline="\n")
    return out_path


def load_workpack(path: Path) -> WorkPack:
    data = json.loads(path.read_text(encoding="utf-8"))
    return WorkPack(
        task_id=str(data.get("task_id", "")),
        created_at=str(data.get("created_at", "")),
        workspace_root=str(data.get("workspace_root", "")),
        goal=str(data.get("goal", "")),
        constraints=list(data.get("constraints", [])),
        assumptions=list(data.get("assumptions", [])),
        relevant_files=[
            WorkPackFileRef(**ref) for ref in data.get("relevant_files", []) if isinstance(ref, dict)
        ],
        execution_plan=list(data.get("execution_plan", [])),
        verification=list(data.get("verification", [])),
    )


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = _SLUG_RE.sub("-", value).strip("-")
    return value

