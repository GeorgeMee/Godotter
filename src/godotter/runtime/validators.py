from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from godotter.runtime.scene_parser import parse_scene
from godotter.utils.textio import read_text_utf8, write_text_utf8


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

    def info(self, code: str, message: str) -> None:
        self.issues.append(ValidationIssue(code=code, message=message))


@dataclass(slots=True)
class PathFix:
    file_path: Path
    old: str
    new: str
    message: str


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


def validate_nodepaths(workspace_root: Path, *, scenes_root: Path | None = None) -> ValidationReport:
    report = ValidationReport(ok=True)
    root = scenes_root or (workspace_root / "game" / "levels")
    if not root.exists():
        report.add("missing_scenes_root", f"scenes root does not exist: {root.as_posix()}")
        return report

    scenes = sorted(root.rglob("*.tscn"))
    if not scenes:
        report.add("missing_scenes", f"no .tscn scenes found under: {root.as_posix()}")
        return report

    for scene_path in scenes:
        parsed = parse_scene(scene_path)
        node_paths = _build_node_paths(parsed.nodes)
        existing = set(node_paths.values())
        for node in parsed.nodes:
            source_path = node_paths.get(id(node))
            if not source_path:
                continue
            for prop in node.properties:
                if not prop.key.endswith("_path"):
                    continue
                target = _extract_nodepath(prop.value)
                if target is None or target == "":
                    continue
                resolved = _resolve_nodepath(source_path, target)
                if resolved in existing:
                    canonical = _relative_nodepath(source_path, resolved)
                    if target != canonical and not target.startswith("/"):
                        rel = scene_path.relative_to(workspace_root).as_posix()
                        report.add(
                            "noncanonical_nodepath",
                            f"{rel}: node={source_path} property={prop.key} target={target} resolved={resolved} suggested={canonical}",
                        )
                    continue
                if resolved not in existing:
                    suggested = _suggest_nodepath(source_path, prop.key, target, list(existing))
                    suggestion_text = f" suggested={suggested}" if suggested else ""
                    rel = scene_path.relative_to(workspace_root).as_posix()
                    report.add(
                        "unresolved_nodepath",
                        f"{rel}: node={source_path} property={prop.key} target={target} resolved={resolved}{suggestion_text}",
                    )

    return report


def validate_paths(workspace_root: Path, *, fix: bool = False) -> ValidationReport:
    if fix:
        fix_report = ValidationReport(ok=True)
        fixes = _collect_path_fixes(workspace_root)
        _apply_path_fixes(fixes)
        final_report = validate_paths(workspace_root, fix=False)
        for path_fix in fixes:
            rel = path_fix.file_path.relative_to(workspace_root).as_posix()
            fix_report.info("fixed_path", f"{rel}: {path_fix.message}")
        for issue in final_report.issues:
            fix_report.add(issue.code, issue.message)
        return fix_report

    report = ValidationReport(ok=True)
    _merge_report(report, validate_nodepaths(workspace_root))
    _validate_scene_resource_paths(workspace_root, report)
    _validate_script_resource_paths(workspace_root, report)
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


_NODEPATH_RE = re.compile(r'NodePath\("([^"]*)"\)')
_RES_PATH_RE = re.compile(r'''["'](res://[^"']+)["']''')


def _extract_nodepath(raw_value: str) -> str | None:
    value = raw_value.strip()
    match = _NODEPATH_RE.fullmatch(value)
    if match:
        return match.group(1)
    if value.startswith('^"') and value.endswith('"'):
        return value[2:-1]
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1]
    return None


def _build_node_paths(nodes) -> dict[int, str]:
    paths: dict[int, str] = {}
    root_names = {node.name for node in nodes if node.parent in (None, ".")}
    for node in nodes:
        if node.parent in (None, "."):
            paths[id(node)] = node.name
        elif node.parent == "":
            paths[id(node)] = node.name
        elif node.parent in root_names:
            paths[id(node)] = f"{node.parent}/{node.name}"
        else:
            paths[id(node)] = f"{node.parent}/{node.name}"
    return paths


def _resolve_nodepath(source_path: str, target: str) -> str:
    if target.startswith("/"):
        return target.strip("/")

    parts = [part for part in source_path.split("/") if part]
    if target in ("", "."):
        return "/".join(parts)

    # In Godot, relative NodePath starts from the node that owns the property.
    current = parts[:]
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if current:
                current.pop()
            continue
        current.append(part)
    return "/".join(current)


def _merge_report(target: ValidationReport, source: ValidationReport) -> None:
    for issue in source.issues:
        target.add(issue.code, issue.message)


def _suggest_nodepath(source_path: str, prop_key: str, target: str, existing_paths: list[str]) -> str | None:
    candidates: list[str] = []
    key = prop_key.lower()
    if key == "event_bus_path" and "Managers/EventBus" in existing_paths:
        candidates = ["Managers/EventBus"]
    else:
        target_name = _nodepath_tail(target)
        if target_name:
            candidates = [path for path in existing_paths if path.rsplit("/", 1)[-1].lower() == target_name.lower()]
        if not candidates and key.endswith("_path"):
            expected_name = _path_property_to_node_name(key)
            if expected_name:
                candidates = [path for path in existing_paths if path.rsplit("/", 1)[-1].lower() == expected_name.lower()]

    unique_candidates = sorted(set(candidates))
    if len(unique_candidates) != 1:
        return None
    return _relative_nodepath(source_path, unique_candidates[0])


def _nodepath_tail(value: str) -> str:
    parts = [part for part in value.split("/") if part and part not in (".", "..")]
    return parts[-1] if parts else ""


def _path_property_to_node_name(prop_key: str) -> str:
    stem = prop_key.removesuffix("_path")
    parts = [part for part in stem.split("_") if part]
    if not parts:
        return ""
    return "".join(part[:1].upper() + part[1:] for part in parts)


def _relative_nodepath(source_path: str, target_path: str) -> str:
    source_parts = [part for part in source_path.split("/") if part]
    target_parts = [part for part in target_path.split("/") if part]
    common = 0
    for source_part, target_part in zip(source_parts, target_parts):
        if source_part != target_part:
            break
        common += 1
    up = [".."] * (len(source_parts) - common)
    down = target_parts[common:]
    return "/".join(up + down) or "."


def _validate_scene_resource_paths(workspace_root: Path, report: ValidationReport) -> None:
    for scene_path in sorted((workspace_root / "game").rglob("*.tscn")) if (workspace_root / "game").exists() else []:
        parsed = parse_scene(scene_path)
        rel_scene = scene_path.relative_to(workspace_root).as_posix()
        for resource in parsed.ext_resources:
            if not resource.path.startswith("res://"):
                continue
            if not _res_path_exists(workspace_root, resource.path):
                suggested = _suggest_res_path(workspace_root, resource.path)
                suggestion_text = f" suggested={suggested}" if suggested else ""
                report.add(
                    "unresolved_resource_path",
                    f"{rel_scene}: ext_resource id={resource.id} path={resource.path}{suggestion_text}",
                )


def _validate_script_resource_paths(workspace_root: Path, report: ValidationReport) -> None:
    for script_path in sorted((workspace_root / "game").rglob("*.gd")) if (workspace_root / "game").exists() else []:
        rel_script = script_path.relative_to(workspace_root).as_posix()
        try:
            content = script_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            content = script_path.read_text(encoding="utf-8-sig")
        for match in _RES_PATH_RE.finditer(content):
            res_path = match.group(1)
            if _res_path_exists(workspace_root, res_path):
                continue
            suggested = _suggest_res_path(workspace_root, res_path)
            suggestion_text = f" suggested={suggested}" if suggested else ""
            report.add("unresolved_resource_path", f"{rel_script}: path={res_path}{suggestion_text}")


def _collect_path_fixes(workspace_root: Path) -> list[PathFix]:
    fixes: list[PathFix] = []
    for scene_path in sorted((workspace_root / "game").rglob("*.tscn")) if (workspace_root / "game").exists() else []:
        parsed = parse_scene(scene_path)
        node_paths = _build_node_paths(parsed.nodes)
        existing = set(node_paths.values())
        for node in parsed.nodes:
            source_path = node_paths.get(id(node))
            if not source_path:
                continue
            for prop in node.properties:
                if not prop.key.endswith("_path"):
                    continue
                target = _extract_nodepath(prop.value)
                if target is None or target == "":
                    continue
                resolved = _resolve_nodepath(source_path, target)
                if resolved in existing:
                    canonical = _relative_nodepath(source_path, resolved)
                    if target != canonical and not target.startswith("/"):
                        fixes.append(
                            PathFix(
                                file_path=scene_path,
                                old=target,
                                new=canonical,
                                message=f"node={source_path} property={prop.key} {target} -> {canonical}",
                            )
                        )
                    continue
                suggested = _suggest_nodepath(source_path, prop.key, target, list(existing))
                if suggested:
                    fixes.append(
                        PathFix(
                            file_path=scene_path,
                            old=target,
                            new=suggested,
                            message=f"node={source_path} property={prop.key} {target} -> {suggested}",
                        )
                    )

        for resource in parsed.ext_resources:
            if not resource.path.startswith("res://") or _res_path_exists(workspace_root, resource.path):
                continue
            suggested = _suggest_res_path(workspace_root, resource.path)
            if suggested:
                fixes.append(
                    PathFix(
                        file_path=scene_path,
                        old=resource.path,
                        new=suggested,
                        message=f"ext_resource id={resource.id} {resource.path} -> {suggested}",
                    )
                )

    for script_path in sorted((workspace_root / "game").rglob("*.gd")) if (workspace_root / "game").exists() else []:
        content = read_text_utf8(script_path)
        for match in _RES_PATH_RE.finditer(content):
            res_path = match.group(1)
            if _res_path_exists(workspace_root, res_path):
                continue
            suggested = _suggest_res_path(workspace_root, res_path)
            if suggested:
                fixes.append(
                    PathFix(
                        file_path=script_path,
                        old=res_path,
                        new=suggested,
                        message=f"{res_path} -> {suggested}",
                    )
                )
    return _dedupe_fixes(fixes)


def _apply_path_fixes(fixes: list[PathFix]) -> None:
    by_file: dict[Path, list[PathFix]] = {}
    for path_fix in fixes:
        by_file.setdefault(path_fix.file_path, []).append(path_fix)
    for file_path, file_fixes in by_file.items():
        content = read_text_utf8(file_path)
        updated = content
        for path_fix in file_fixes:
            updated = updated.replace(path_fix.old, path_fix.new)
        if updated != content:
            write_text_utf8(file_path, updated)


def _dedupe_fixes(fixes: list[PathFix]) -> list[PathFix]:
    seen: set[tuple[Path, str, str]] = set()
    deduped: list[PathFix] = []
    for path_fix in fixes:
        key = (path_fix.file_path, path_fix.old, path_fix.new)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path_fix)
    return deduped


def _res_path_exists(workspace_root: Path, res_path: str) -> bool:
    if not res_path.startswith("res://"):
        return True
    relative = res_path.removeprefix("res://")
    return (workspace_root / relative).is_file()


def _suggest_res_path(workspace_root: Path, missing_res_path: str) -> str | None:
    missing_relative = missing_res_path.removeprefix("res://")
    missing_name = Path(missing_relative).name.lower()
    if not missing_name:
        return None

    candidates = [
        path
        for path in workspace_root.rglob(missing_name)
        if path.is_file() and ".godotter" not in path.parts and ".venv" not in path.parts
    ]
    if len(candidates) != 1:
        return None
    return "res://" + candidates[0].relative_to(workspace_root).as_posix()
