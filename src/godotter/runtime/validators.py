from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from godotter.runtime.scene_parser import parse_scene


@dataclass(slots=True)
class ValidationIssue:
    code: str
    message: str


@dataclass(slots=True)
class ValidationReport:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    def add(self, code: str, message: str) -> None:
        self.issues.append(ValidationIssue(code=code, message=message))
        self.ok = False


def validate_structure(workspace_root: Path) -> ValidationReport:
    report = ValidationReport(ok=True)

    required_dirs = [
        workspace_root / "game" / "core",
        workspace_root / "game" / "systems",
        workspace_root / "game" / "features",
        workspace_root / "game" / "content",
        workspace_root / "game" / "levels",
        workspace_root / "tests" / "core",
        workspace_root / "tests" / "systems",
        workspace_root / "tests" / "features",
        workspace_root / "tests" / "levels",
    ]
    for directory in required_dirs:
        if not directory.is_dir():
            report.add("missing_dir", f"missing directory: {directory.as_posix()}")

    required_files = [
        workspace_root / "project.godot",
        workspace_root / ".gitignore",
        workspace_root / "icon.svg",
    ]
    for file_path in required_files:
        if not file_path.is_file():
            report.add("missing_file", f"missing file: {file_path.as_posix()}")

    return report


_QUOTED_STRING_RE = re.compile(r'"([^"]+)"')


def validate_managers(workspace_root: Path, *, levels_root: Path | None = None) -> ValidationReport:
    report = ValidationReport(ok=True)
    root = levels_root or (workspace_root / "game" / "levels")
    if not root.exists():
        report.add("missing_levels_root", f"levels root does not exist: {root.as_posix()}")
        return report

    scenes = sorted(root.rglob("*.tscn"))
    if not scenes:
        report.add("missing_scenes", f"no .tscn scenes found under: {root.as_posix()}")
        return report

    for scene_path in scenes:
        parsed = parse_scene(scene_path)
        issues = _validate_scene_managers(parsed.nodes)
        for code, msg in issues:
            rel = scene_path.relative_to(workspace_root).as_posix()
            report.add(code, f"{rel}: {msg}")

    return report


def _validate_scene_managers(nodes) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []

    managers_nodes = [node for node in nodes if node.name == "Managers" and (node.parent in (".", None))]
    if not managers_nodes:
        issues.append(("missing_managers", "missing root-level Managers node"))
        return issues
    if len(managers_nodes) > 1:
        issues.append(("duplicate_managers", "multiple root-level Managers nodes"))

    eventbus_nodes = [node for node in nodes if node.name == "EventBus" and node.parent == "Managers"]
    if not eventbus_nodes:
        issues.append(("missing_event_bus", "missing Managers/EventBus node"))

    # Enforce uniqueness for mgr:* groups if present.
    mgr_group_to_node: dict[str, str] = {}
    for node in nodes:
        groups = _extract_groups(node.properties)
        for group in groups:
            if not group.startswith("mgr:"):
                continue
            if group in mgr_group_to_node:
                issues.append(
                    (
                        "duplicate_mgr_group",
                        f'duplicate group "{group}" on nodes "{mgr_group_to_node[group]}" and "{node.name}"',
                    )
                )
            else:
                mgr_group_to_node[group] = node.name

    return issues


def _extract_groups(properties) -> list[str]:
    for prop in properties:
        if prop.key != "groups":
            continue
        # Godot serializes as: groups=["a", "b"] (order/spacing may vary)
        return _QUOTED_STRING_RE.findall(prop.value)
    return []
