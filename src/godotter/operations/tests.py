from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from godotter.runtime.scene_parser import filename_to_node_name, generate_uid
from godotter.utils.textio import write_text_utf8


SUPPORTED_TEST_KINDS = {"system", "feature", "integration", "level-smoke", "e2e"}


@dataclass(slots=True)
class TestScaffoldResult:
    scene_path: Path
    script_path: Path
    kind: str
    uid: str


def scaffold_test(
    *,
    workspace_root: Path,
    name: str,
    kind: str,
    force: bool = False,
) -> TestScaffoldResult:
    kind_norm = _normalize_kind(kind)
    name_slug = _slugify_name(name)
    scene_path = _test_scene_path(workspace_root, name_slug, kind_norm)
    script_path = _test_script_path(workspace_root, name_slug, kind_norm)

    if scene_path.exists() and not force:
        raise ValueError(f"File already exists: {scene_path.relative_to(workspace_root).as_posix()}")
    if script_path.exists() and not force:
        raise ValueError(f"File already exists: {script_path.relative_to(workspace_root).as_posix()}")

    uid = generate_uid()
    script_res = f"res://{script_path.relative_to(workspace_root).as_posix()}"
    write_text_utf8(script_path, _generate_test_script(name_slug, kind_norm))
    write_text_utf8(scene_path, _generate_test_scene(uid=uid, root_name=_root_name(name_slug, kind_norm), script_res=script_res))
    return TestScaffoldResult(scene_path=scene_path, script_path=script_path, kind=kind_norm, uid=uid)


def test_kind_pattern(kind: str) -> str:
    kind_norm = _normalize_runtime_kind(kind)
    if kind_norm == "all":
        return "*_harness.tscn;*_smoke.tscn;*_e2e.tscn"
    if kind_norm == "unit":
        return "systems/**/*_harness.tscn;features/**/*_harness.tscn"
    if kind_norm == "system":
        return "systems/**/*_harness.tscn"
    if kind_norm == "feature":
        return "features/**/*_harness.tscn"
    if kind_norm == "integration":
        return "integration/**/*_harness.tscn"
    if kind_norm == "level-smoke":
        return "levels/**/*_smoke.tscn"
    if kind_norm == "e2e":
        return "e2e/**/*_e2e.tscn"
    raise ValueError(f"Unsupported test kind: {kind}")


def infer_test_kinds_for_paths(paths: list[str] | set[str]) -> list[str]:
    kinds: list[str] = []
    normalized = [path.replace("\\", "/") for path in paths]
    if any(path.startswith("game/systems/") for path in normalized):
        kinds.append("system")
    if any(path.startswith("game/features/") for path in normalized):
        kinds.append("feature")
    if _touches_multiple_game_modules(normalized):
        kinds.append("integration")
    if any(path.startswith("game/levels/") for path in normalized):
        kinds.append("level-smoke")
    if any(path.startswith("ui/views/") or path.startswith("game/ui/") for path in normalized):
        kinds.append("level-smoke")
        kinds.append("e2e")
    return _dedupe(kinds)


def expected_test_dirs_for_paths(paths: list[str] | set[str]) -> list[str]:
    dirs: list[str] = []
    normalized = [path.replace("\\", "/") for path in paths]
    for path in normalized:
        system_name = _module_name_after(path, "game/systems/")
        if system_name:
            dirs.append(f"tests/systems/{system_name}/")
    for path in normalized:
        feature_name = _module_name_after(path, "game/features/")
        if feature_name:
            dirs.append(f"tests/features/{feature_name}/")
    if _touches_multiple_game_modules(normalized):
        dirs.append("tests/integration/")
    if any(path.startswith("game/levels/") for path in normalized):
        dirs.append("tests/levels/")
    if any(path.startswith("ui/views/") or path.startswith("game/ui/") for path in normalized):
        dirs.append("tests/levels/")
        dirs.append("tests/e2e/")
    return _dedupe(dirs)


def _normalize_kind(kind: str) -> str:
    value = (kind or "").strip().lower().replace("_", "-")
    if value not in SUPPORTED_TEST_KINDS:
        raise ValueError(f"Unsupported test kind: {kind}")
    return value


def _normalize_runtime_kind(kind: str) -> str:
    value = (kind or "").strip().lower().replace("_", "-")
    if value == "":
        raise ValueError("test kind is empty")
    allowed = SUPPORTED_TEST_KINDS | {"unit", "all"}
    if value not in allowed:
        raise ValueError(f"Unsupported test kind: {kind}")
    return value


def _touches_multiple_game_modules(paths: list[str]) -> bool:
    modules: set[str] = set()
    for path in paths:
        for prefix in ("game/systems/", "game/features/"):
            name = _module_name_after(path, prefix)
            if name:
                modules.add(f"{prefix}{name}")
    return len(modules) >= 2


def _module_name_after(path: str, prefix: str) -> str:
    if not path.startswith(prefix):
        return ""
    rest = path[len(prefix) :]
    return rest.split("/", 1)[0] if rest else ""


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _slugify_name(name: str) -> str:
    value = (name or "").strip().strip('"').strip("'")
    if not value:
        raise ValueError("test name is empty")
    value = value.replace("\\", "/").rsplit("/", 1)[-1]
    value = re.sub(r"[^A-Za-z0-9_ -]+", "_", value)
    value = re.sub(r"[-\s]+", "_", value).strip("_").lower()
    if not value:
        raise ValueError("test name is empty")
    return value


def _test_scene_path(workspace_root: Path, name: str, kind: str) -> Path:
    if kind == "system":
        return (workspace_root / "tests" / "systems" / name / f"{name}_harness.tscn").resolve()
    if kind == "feature":
        return (workspace_root / "tests" / "features" / name / f"{name}_harness.tscn").resolve()
    if kind == "integration":
        return (workspace_root / "tests" / "integration" / name / f"{name}_harness.tscn").resolve()
    if kind == "level-smoke":
        return (workspace_root / "tests" / "levels" / f"{name}_smoke.tscn").resolve()
    if kind == "e2e":
        return (workspace_root / "tests" / "e2e" / name / f"{name}_e2e.tscn").resolve()
    raise ValueError(f"Unsupported test kind: {kind}")


def _test_script_path(workspace_root: Path, name: str, kind: str) -> Path:
    if kind in {"system", "feature", "integration"}:
        return _test_scene_path(workspace_root, name, kind).with_name(f"test_{name}.gd")
    if kind == "level-smoke":
        return (workspace_root / "tests" / "levels" / f"test_{name}_smoke.gd").resolve()
    if kind == "e2e":
        return _test_scene_path(workspace_root, name, kind).with_name(f"test_{name}_e2e.gd")
    raise ValueError(f"Unsupported test kind: {kind}")


def _root_name(name: str, kind: str) -> str:
    suffix = {
        "system": "Harness",
        "feature": "Harness",
        "integration": "Harness",
        "level-smoke": "Smoke",
        "e2e": "E2E",
    }[kind]
    return f"{filename_to_node_name(name)}{suffix}"


def _generate_test_scene(*, uid: str, root_name: str, script_res: str) -> str:
    return "\n".join(
        [
            f'[gd_scene load_steps=2 format=3 uid="{uid}"]',
            "",
            f'[ext_resource type="Script" path="{script_res}" id="1_script"]',
            "",
            f'[node name="{root_name}" type="Node"]',
            'script = ExtResource("1_script")',
            "",
        ]
    )


def _generate_test_script(name: str, kind: str) -> str:
    label = f"{kind}:{name}"
    guidance = _kind_guidance(kind)
    return "\n".join(
        [
            "extends Node",
            "",
            "const TIMEOUT_SECONDS := 2.0",
            "",
            "var _started_ms := 0",
            "",
            "",
            "func _ready() -> void:",
            "\t_started_ms = Time.get_ticks_msec()",
            f"\t# {guidance}",
            "\tawait get_tree().process_frame",
            f'\t_pass("{label}")',
            "",
            "",
            "func _process(_delta: float) -> void:",
            "\tif Time.get_ticks_msec() - _started_ms > int(TIMEOUT_SECONDS * 1000.0):",
            '\t\t_fail("timeout")',
            "",
            "",
            "func _assert(condition: bool, message: String) -> void:",
            "\tif not condition:",
            "\t\t_fail(message)",
            "",
            "",
            "func _pass(label: String) -> void:",
            '\tprint("PASS: %s" % label)',
            "\tget_tree().quit(0)",
            "",
            "",
            "func _fail(message: String) -> void:",
            '\tprinterr("FAIL: %s" % message)',
            "\tget_tree().quit(1)",
            "",
        ]
    )


def _kind_guidance(kind: str) -> str:
    if kind == "system":
        return "System unit test: instantiate the target manager/service and call public methods."
    if kind == "feature":
        return "Feature unit test: use a minimal harness and verify public API/EventBus behavior."
    if kind == "integration":
        return "Integration test: combine real systems/features and verify their event flow."
    if kind == "level-smoke":
        return "Level smoke test: load the real level scene and assert key nodes are wired."
    if kind == "e2e":
        return "E2E test: drive the flow via InputSim/InputMap/UI signals, not private methods."
    return "Godotter test."
