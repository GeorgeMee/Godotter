from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

from godotter.tasks.workpack import WorkPackFileRef


@dataclass(slots=True)
class ScoutResult:
    relevant_files: list[WorkPackFileRef]
    keywords: list[str]
    changed_files: list[WorkPackFileRef]


_WORD_RE = re.compile(r"[A-Za-z0-9_./:-]{2,}")


def extract_keywords(goal: str, *, max_keywords: int = 12) -> list[str]:
    goal = goal.strip()
    if not goal:
        return []

    keywords: list[str] = []
    seen: set[str] = set()

    def _add(token: str) -> None:
        token = token.strip()
        if not token or token in seen:
            return
        seen.add(token)
        keywords.append(token)

    # Add ascii-ish words.
    for token in _WORD_RE.findall(goal):
        _add(token.lower())
        if len(keywords) >= max_keywords:
            return keywords

    # Add some CJK substrings as rough keywords (2-4 chars).
    cjk = "".join(ch for ch in goal if "\u4e00" <= ch <= "\u9fff")
    for size in (4, 3, 2):
        for i in range(0, max(0, len(cjk) - size + 1)):
            _add(cjk[i : i + size])
            if len(keywords) >= max_keywords:
                return keywords

    return keywords[:max_keywords]


def scout_workspace(
    workspace_root: Path,
    goal: str,
    *,
    max_files: int = 40,
    max_file_bytes: int = 256 * 1024,
) -> ScoutResult:
    changed_files = collect_changed_files(workspace_root)
    keywords = extract_keywords(goal)
    if not keywords:
        return ScoutResult(relevant_files=changed_files[:max_files], keywords=[], changed_files=changed_files)

    candidates = _collect_candidate_files(workspace_root)
    scored: list[tuple[int, Path, str]] = []

    for path in candidates:
        try:
            score, reason = _score_file(path, keywords, max_file_bytes=max_file_bytes)
        except OSError:
            continue
        if score <= 0:
            continue
        scored.append((score, path, reason))

    scored.sort(key=lambda item: (item[0], item[1].as_posix()), reverse=True)
    top = scored[:max_files]

    refs: list[WorkPackFileRef] = []
    seen_paths = {ref.path for ref in changed_files}
    refs.extend(changed_files[:max_files])
    for score, path, reason in top:
        rel = path.relative_to(workspace_root).as_posix()
        if rel in seen_paths:
            continue
        priority = max(1, 200 - score)
        refs.append(WorkPackFileRef(path=rel, reason=reason, priority=priority))
        if len(refs) >= max_files:
            break

    return ScoutResult(relevant_files=refs, keywords=keywords, changed_files=changed_files)


def collect_changed_files(workspace_root: Path) -> list[WorkPackFileRef]:
    if not (workspace_root / ".git").exists():
        return []

    try:
        completed = subprocess.run(
            ["git", "status", "--short", "--untracked-files=all"],
            cwd=workspace_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    if completed.returncode != 0:
        return []

    refs: list[WorkPackFileRef] = []
    seen_paths: set[str] = set()
    for line in completed.stdout.splitlines():
        parsed = _parse_status_line(line, workspace_root)
        if parsed is None or parsed.path in seen_paths:
            continue
        seen_paths.add(parsed.path)
        refs.append(parsed)
    return refs


def _collect_candidate_files(workspace_root: Path) -> list[Path]:
    include_dirs = [
        workspace_root / "Docs",
        workspace_root / "src",
        workspace_root / "templates",
        workspace_root / "game",
        workspace_root / "tests",
        workspace_root / "config",
    ]
    exts = {".py", ".md", ".gd", ".tscn", ".tres", ".toml", ".json", ".yml", ".yaml", ".txt"}
    skip_dirnames = {".git", ".venv", ".godot", ".godotter", "__pycache__", ".mypy_cache", ".ruff_cache"}

    files: list[Path] = []
    for base in include_dirs:
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_dir():
                if path.name in skip_dirnames:
                    # Prune by skipping descent: rglob can't prune directly, so just ignore.
                    continue
                continue
            if path.suffix.lower() not in exts:
                continue
            files.append(path)
    return files


def _parse_status_line(line: str, workspace_root: Path) -> WorkPackFileRef | None:
    if len(line) < 4:
        return None

    status = line[:2]
    raw_path = line[3:].strip()
    if not raw_path:
        return None

    path_text = raw_path.split(" -> ", 1)[-1]
    path = (workspace_root / path_text).resolve()
    try:
        rel = path.relative_to(workspace_root).as_posix()
    except ValueError:
        return None

    reason = f"git:{_status_reason(status)}"
    priority = 5 if "?" in status else 15
    return WorkPackFileRef(path=rel, reason=reason, priority=priority)


def _status_reason(status: str) -> str:
    code = status.strip()
    if code == "??":
        return "untracked"
    if "A" in status:
        return "added"
    if "M" in status:
        return "modified"
    if "D" in status:
        return "deleted"
    if "R" in status:
        return "renamed"
    if "C" in status:
        return "copied"
    if "U" in status:
        return "unmerged"
    return "changed"


def _score_file(path: Path, keywords: list[str], *, max_file_bytes: int) -> tuple[int, str]:
    score = 0
    hits: list[str] = []

    name_lower = path.name.lower()
    for kw in keywords:
        if kw and kw.lower() in name_lower:
            score += 40
            hits.append(f"name:{kw}")

    size = path.stat().st_size
    if size <= 0 or size > max_file_bytes:
        return score, ", ".join(hits) if hits else ""

    content = path.read_text(encoding="utf-8", errors="ignore").lower()
    for kw in keywords:
        if not kw:
            continue
        count = content.count(kw.lower())
        if count:
            score += min(60, count * 8)
            hits.append(f"content:{kw}x{count}")

    reason = ", ".join(hits[:6])
    return score, reason

