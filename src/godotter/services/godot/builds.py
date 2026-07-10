from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
import json
import os
import re
import secrets
import subprocess
import time


@dataclass(slots=True)
class BuildArtifact:
    path: str
    name: str
    size_bytes: int


@dataclass(slots=True)
class BuildReport:
    build_id: str
    created_at: str
    workspace_root: str
    preset: str
    output_path: str
    status: str
    exit_code: int
    timed_out: bool
    duration_ms: int
    stdout: str = ""
    stderr: str = ""
    artifacts: list[BuildArtifact] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


@dataclass(slots=True)
class ExportPreset:
    index: int
    name: str
    platform: str


@dataclass(slots=True)
class ExportDoctorReport:
    workspace_root: str
    project_exists: bool
    export_presets_exists: bool
    presets: list[ExportPreset]
    godot_configured: bool
    godot_path_exists: bool
    godot_version: str | None
    templates_root: str | None
    templates_detected: bool
    android_sdk_path: str | None = None
    android_sdk_valid: bool = False
    android_build_tools_version: str | None = None
    android_adb_exists: bool = False
    java_home: str | None = None
    java_valid: bool = False
    java_version: str | None = None
    keystore_path: str | None = None
    keystore_valid: bool = False
    android_template_installed: bool = False
    ok: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def new_build_id() -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"build_{timestamp}_{secrets.token_hex(3)}"


def default_builds_dir(workspace_root: Path) -> Path:
    return workspace_root / ".godotter" / "builds"


def build_dir(workspace_root: Path, build_id: str) -> Path:
    return default_builds_dir(workspace_root) / build_id


def latest_build_report_path(workspace_root: Path) -> Path:
    return default_builds_dir(workspace_root) / "latest.json"


def build_report_path(workspace_root: Path, build_id: str) -> Path:
    return build_dir(workspace_root, build_id) / "build_report.json"


def default_export_output(workspace_root: Path, build_id: str, preset: str) -> Path:
    out_dir = build_dir(workspace_root, build_id)
    slug = _slugify(preset) or "game"
    lower = preset.lower()
    if "web" in lower or "html" in lower:
        return out_dir / "index.html"
    if "android" in lower:
        return out_dir / f"{slug}.apk"
    if "windows" in lower or "win" in lower:
        return out_dir / f"{slug}.exe"
    if "linux" in lower:
        return out_dir / f"{slug}.x86_64"
    return out_dir / f"{slug}.pck"


def run_export_build(
    *,
    godot_path: str,
    workspace_root: Path,
    preset: str,
    output: Path | None = None,
    release: bool = True,
    timeout: int = 1800,
) -> tuple[BuildReport, Path]:
    build_id = new_build_id()
    root = workspace_root.resolve()
    output_path = _resolve_output(root, output) if output is not None else default_export_output(root, build_id, preset)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Auto-install Android build template if missing
    if "android" in preset.lower():
        aar = root / "android" / "libs" / "release" / "godot-lib.template_release.aar"
        if not aar.exists():
            subprocess.run(
                [godot_path, "--headless", "--path", root.as_posix(), "--install-android-build-template"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=120,
            )

    command = [
        godot_path,
        "--headless",
        "--export-release" if release else "--export-debug",
        preset,
        output_path.as_posix(),
    ]
    start = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            timeout=None if timeout <= 0 else timeout,
        )
        timed_out = False
        exit_code = int(completed.returncode)
        stdout = _decode(completed.stdout)
        stderr = _decode(completed.stderr)
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -1
        stdout = _decode(exc.stdout)
        stderr = _decode(exc.stderr)
    duration_ms = int((time.perf_counter() - start) * 1000)
    status = "passed" if exit_code == 0 and not timed_out else "failed"
    report = BuildReport(
        build_id=build_id,
        created_at=datetime.now().isoformat(timespec="seconds"),
        workspace_root=root.as_posix(),
        preset=preset,
        output_path=output_path.as_posix(),
        status=status,
        exit_code=exit_code,
        timed_out=timed_out,
        duration_ms=duration_ms,
        stdout=stdout,
        stderr=stderr,
        artifacts=_collect_artifacts(root, output_path),
    )
    path = write_build_report(root, report)
    return report, path


def list_build_reports(workspace_root: Path) -> list[dict[str, object]]:
    root = default_builds_dir(workspace_root)
    if not root.exists():
        return []
    reports: list[dict[str, object]] = []
    for path in sorted(root.glob("build_*/build_report.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            reports.append(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
    return reports


def run_export_doctor(
    *,
    workspace_root: Path,
    godot_path: str | None = None,
    timeout: int = 15,
    templates_path: str | None = None,
    android_sdk_path: str | None = None,
    java_home: str | None = None,
    keystore_path: str | None = None,
) -> ExportDoctorReport:
    root = workspace_root.resolve()
    project_exists = (root / "project.godot").exists()
    presets_path = root / "export_presets.cfg"
    export_presets_exists = presets_path.exists()
    presets = parse_export_presets(presets_path) if export_presets_exists else []
    godot_configured = bool(godot_path)
    godot_path_exists = bool(godot_path and Path(godot_path).exists())
    godot_version = _detect_godot_version(godot_path, timeout=timeout) if godot_path_exists else None
    templates_root = find_export_templates_root(godot_version, templates_path=templates_path)
    templates_detected = bool(templates_root and Path(templates_root).exists() and any(Path(templates_root).rglob("*")))

    # Android SDK
    sdk_path = android_sdk_path or os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    sdk_valid = False
    build_tools_ver: str | None = None
    adb_exists = False
    if sdk_path:
        sdk = Path(sdk_path)
        adb = sdk / "platform-tools" / ("adb.exe" if os.name == "nt" else "adb")
        adb_exists = adb.exists()
        bt_dir = sdk / "build-tools"
        if bt_dir.exists():
            for child in sorted(bt_dir.iterdir(), reverse=True):
                if child.is_dir() and (child / ("apksigner.bat" if os.name == "nt" else "apksigner")).exists():
                    build_tools_ver = child.name
                    break
        sdk_valid = adb_exists and build_tools_ver is not None

    # JDK
    jdk_path = java_home or os.environ.get("JAVA_HOME")
    jdk_valid = False
    jdk_version: str | None = None
    if jdk_path:
        java_bin = Path(jdk_path) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if java_bin.exists():
            try:
                completed = subprocess.run(
                    [str(java_bin), "-version"],
                    capture_output=True, text=True, timeout=10,
                )
                out = (completed.stdout + completed.stderr).strip()
                jdk_valid = True
                for line in out.splitlines():
                    line = line.strip()
                    if "version" in line.lower() and line.startswith(("openjdk", "java")):
                        jdk_version = line.split('"')[1] if '"' in line else line
                        break
                    elif "Runtime" in line or "build" in line.lower():
                        continue
                if jdk_version is None:
                    jdk_version = out.splitlines()[0].strip()
            except Exception:
                jdk_version = "detected"

    # Keystore
    ks_path = keystore_path
    ks_valid = False
    if ks_path and Path(ks_path).exists():
        ks_valid = True

    # Android build template
    aar_release = root / "android" / "libs" / "release" / "godot-lib.template_release.aar"
    aar_debug = root / "android" / "libs" / "debug" / "godot-lib.template_debug.aar"
    android_template_installed = aar_release.exists() and aar_debug.exists()

    errors: list[str] = []
    warnings: list[str] = []
    if not project_exists:
        errors.append("project.godot is missing")
    if not export_presets_exists:
        errors.append("export_presets.cfg is missing")
    elif not presets:
        warnings.append("export_presets.cfg exists but no preset names were detected")
    if not godot_configured:
        errors.append("GODOT_PATH is not configured")
    elif not godot_path_exists:
        errors.append("GODOT_PATH does not point to an existing executable")
    if godot_path_exists and not templates_detected:
        warnings.append("Godot export templates were not detected in the standard user template directory")
    if not sdk_path:
        warnings.append("Android SDK path is not configured (set GODOTTER_ANDROID_SDK_PATH or ANDROID_HOME)")
    elif not sdk_valid:
        warnings.append("Android SDK is incomplete: missing platform-tools or build-tools")
    if not jdk_path:
        warnings.append("Java JDK path is not configured (set GODOTTER_JAVA_HOME or JAVA_HOME)")
    elif not jdk_valid:
        warnings.append("Java JDK binary not found at configured path")
    if not ks_path:
        warnings.append("Android keystore is not configured (set GODOTTER_ANDROID_KEYSTORE_PATH)")
    elif not ks_valid:
        errors.append("Android keystore file not found at configured path")

    ok = (
        project_exists
        and export_presets_exists
        and bool(presets)
        and godot_configured
        and godot_path_exists
    )
    return ExportDoctorReport(
        workspace_root=root.as_posix(),
        project_exists=project_exists,
        export_presets_exists=export_presets_exists,
        presets=presets,
        godot_configured=godot_configured,
        godot_path_exists=godot_path_exists,
        godot_version=godot_version,
        templates_root=templates_root,
        templates_detected=templates_detected,
        android_sdk_path=sdk_path,
        android_sdk_valid=sdk_valid,
        android_build_tools_version=build_tools_ver,
        android_adb_exists=adb_exists,
        java_home=jdk_path,
        java_valid=jdk_valid,
        java_version=jdk_version,
        keystore_path=ks_path,
        keystore_valid=ks_valid,
        android_template_installed=android_template_installed,
        ok=ok,
        errors=errors,
        warnings=warnings,
    )


def parse_export_presets(path: Path) -> list[ExportPreset]:
    text = path.read_text(encoding="utf-8", errors="replace")
    sections: dict[int, dict[str, str]] = {}
    current_index: int | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = re.fullmatch(r"\[preset\.(\d+)\]", line)
        if match:
            current_index = int(match.group(1))
            sections.setdefault(current_index, {})
            continue
        if current_index is None or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"')
        if key.strip() in {"name", "platform"}:
            sections[current_index][key.strip()] = value
    return [
        ExportPreset(index=index, name=data.get("name", ""), platform=data.get("platform", ""))
        for index, data in sorted(sections.items())
        if data.get("name")
    ]


def find_export_templates_root(godot_version: str | None = None, *, templates_path: str | None = None) -> str | None:
    if templates_path:
        return templates_path
    appdata = os.environ.get("APPDATA")
    if not appdata:
        appdata = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if not appdata:
        return None
    # On Linux/macOS, templates typically live under ~/.local/share/godot/export_templates
    base = Path(appdata) / "Godot" / "export_templates"
    if not base.exists():
        alt = Path(appdata) / ".local" / "share" / "godot" / "export_templates"
        if alt.exists():
            base = alt
    if not base.exists():
        return base.as_posix()
    version_keys = _template_version_keys(godot_version)
    for version_key in version_keys:
        candidate = base / version_key
        if candidate.exists():
            return candidate.as_posix()
    return base.as_posix()


def write_build_report(workspace_root: Path, report: BuildReport) -> Path:
    path = build_report_path(workspace_root, report.build_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_json()
    path.write_text(payload, encoding="utf-8", newline="\n")
    latest_build_report_path(workspace_root).write_text(payload, encoding="utf-8", newline="\n")
    return path


def _resolve_output(workspace_root: Path, output: Path) -> Path:
    if output.is_absolute():
        return output
    return workspace_root / output


def _collect_artifacts(workspace_root: Path, output_path: Path) -> list[BuildArtifact]:
    candidates: list[Path] = []
    if output_path.exists() and output_path.is_file():
        candidates.append(output_path)
    if output_path.parent.exists():
        candidates.extend(path for path in output_path.parent.iterdir() if path.is_file() and path not in candidates)
    artifacts = []
    for path in sorted(candidates, key=lambda item: item.name):
        try:
            rel = path.relative_to(workspace_root).as_posix()
        except ValueError:
            rel = path.as_posix()
        artifacts.append(BuildArtifact(path=rel, name=path.name, size_bytes=path.stat().st_size))
    return artifacts


def _decode(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def _detect_godot_version(godot_path: str | None, *, timeout: int) -> str | None:
    if not godot_path:
        return None
    try:
        completed = subprocess.run(
            [godot_path, "--version"],
            capture_output=True,
            timeout=timeout,
        )
    except Exception:
        return None
    output = _decode(completed.stdout).strip() or _decode(completed.stderr).strip()
    return output or None


def _template_version_keys(godot_version: str | None) -> list[str]:
    if not godot_version:
        return []
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)[.\-]([A-Za-z0-9_]+)", godot_version)
    if not match:
        return []
    version = match.group(1)
    channel = match.group(2)
    return [f"{version}.{channel}", f"{version}.{channel}.official"]
