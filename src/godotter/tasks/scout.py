from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from godotter.tasks.workpack import WorkPackFileRef


@dataclass(slots=True)
class ScoutResult:
    relevant_files: list[WorkPackFileRef]
    keywords: list[str]


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
    keywords = extract_keywords(goal)
    if not keywords:
        return ScoutResult(relevant_files=[], keywords=[])

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
    for score, path, reason in top:
        rel = path.relative_to(workspace_root).as_posix()
        priority = max(1, 200 - score)
        refs.append(WorkPackFileRef(path=rel, reason=reason, priority=priority))

    return ScoutResult(relevant_files=refs, keywords=keywords)


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

