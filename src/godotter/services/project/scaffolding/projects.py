from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import re

from godotter.services.godot.scene_parser import generate_minimal_scene, generate_uid
from godotter.utils.textio import write_text_utf8


GODOT_GITIGNORE = '''# Godot 4+ specific ignores
.godot/
.import/
export/
export_presets.cfg

# Godot-specific ignores
*.translation

# Mono-specific ignores
.mono/
data_*/
mono_crash.*.json

# System/tool-specific ignores
.DS_Store
Thumbs.db
*.tmp
*.temp
*.log

# IDE
.vscode/
.idea/
*.code-workspace

# Godotter
.godotter/
'''

ICON_SVG_TEMPLATE = '''<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128" fill="none">
  <rect width="128" height="128" rx="24" fill="#1F4ACC"/>
  <path d="M64 24L96 42.5V85.5L64 104L32 85.5V42.5L64 24Z" fill="#F2F5FF"/>
  <circle cx="64" cy="64" r="14" fill="#1F4ACC"/>
</svg>
'''

PROJECT_GODOT_TEMPLATE = '''; Engine configuration file.
; It's best edited using the editor and not directly.

config_version=5

[application]
config/name="{project_name}"
run/main_scene="res://scenes/main.tscn"
config/icon="res://icon.svg"
'''


@dataclass(slots=True)
class ProjectScaffoldResult:
    project_name: str
    project_path: Path
    git_initialized: bool
    files_created: list[Path]
    directories_created: list[Path]


def scaffold_godot_project(name: str, *, no_git: bool = False, base_dir: Path | None = None) -> ProjectScaffoldResult:
    project_path, project_name = _resolve_project_target(name, base_dir=base_dir)
    if project_path.exists() and any(project_path.iterdir()):
        raise ValueError(f'Directory "{project_path}" already exists and is not empty.')

    project_path.mkdir(parents=True, exist_ok=True)

    files_created: list[Path] = []
    directories_created: list[Path] = []

    template_dir = _find_project_template_dir()
    if template_dir.exists():
        created_files, created_dirs = _copy_project_template(
            template_dir=template_dir,
            project_path=project_path,
            project_name=project_name,
        )
        files_created.extend(created_files)
        directories_created.extend(created_dirs)
    else:
        directories = [project_path / part for part in ('scenes', 'scripts', 'assets', 'resources')]
        for directory in directories:
            directory.mkdir(exist_ok=True)
        directories_created.extend(directories)

        project_godot = project_path / 'project.godot'
        write_text_utf8(project_godot, PROJECT_GODOT_TEMPLATE.format(project_name=project_name))
        files_created.append(project_godot)

        gitignore = project_path / '.gitignore'
        write_text_utf8(gitignore, GODOT_GITIGNORE)
        files_created.append(gitignore)

        icon_path = project_path / 'icon.svg'
        write_text_utf8(icon_path, ICON_SVG_TEMPLATE)
        files_created.append(icon_path)

        scene_path = project_path / 'scenes' / 'main.tscn'
        scene_uid = generate_uid()
        scene_content = generate_minimal_scene('Node', 'Main', scene_uid)
        write_text_utf8(scene_path, scene_content)
        files_created.append(scene_path)

    git_initialized = False
    if not no_git:
        git_initialized = _initialize_git_repo(project_path)

    return ProjectScaffoldResult(
        project_name=project_name,
        project_path=project_path,
        git_initialized=git_initialized,
        files_created=files_created,
        directories_created=directories_created,
    )


def render_project_scaffold_summary(result: ProjectScaffoldResult, *, no_git: bool) -> str:
    lines = [
        f'Created Godot project: {result.project_name}',
        f'Path: {result.project_path}',
        '',
        'Created directories:',
    ]
    for directory in sorted(set(result.directories_created)):
        try:
            lines.append(f'  - {directory.relative_to(result.project_path).as_posix()}/')
        except ValueError:
            lines.append(f'  - {directory.as_posix()}/')
    lines.append('Created files:')
    for file_path in sorted(set(result.files_created)):
        try:
            lines.append(f'  - {file_path.relative_to(result.project_path).as_posix()}')
        except ValueError:
            lines.append(f'  - {file_path.as_posix()}')
    lines.append(f'Git initialized: {"yes" if result.git_initialized else "no"}')
    if no_git:
        lines.append('Git init skipped by --no-git.')
    lines.extend(
        [
            '',
            'Next steps:',
            f'  cd {result.project_path.name}',
            '  godotter runtime doctor',
            '  godotter runtime run',
        ]
    )
    return '\n'.join(lines)


def _resolve_project_target(name: str, *, base_dir: Path | None) -> tuple[Path, str]:
    root = (base_dir or Path.cwd()).resolve()
    if name == '.':
        return root, root.name
    target = Path(name)
    project_path = target.resolve() if target.is_absolute() else (root / target).resolve()
    return project_path, project_path.name


def _initialize_git_repo(project_path: Path) -> bool:
    try:
        subprocess.run(['git', 'init'], cwd=project_path, capture_output=True, check=True, text=True)
        subprocess.run(['git', 'add', '.'], cwd=project_path, capture_output=True, check=True, text=True)
        subprocess.run(
            ['git', 'commit', '-m', 'Initial commit: Godot project setup'],
            cwd=project_path,
            capture_output=True,
            check=True,
            text=True,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def _find_project_template_dir() -> Path:
    for parent in Path(__file__).resolve().parents:
        candidate = parent / 'templates' / 'godotter_game_template'
        if candidate.exists():
            return candidate
    return Path('__missing_godotter_game_template__')


def _copy_project_template(
    *, template_dir: Path, project_path: Path, project_name: str
) -> tuple[list[Path], list[Path]]:
    created_files: list[Path] = []
    created_dirs: list[Path] = []

    for source_path in template_dir.rglob('*'):
        rel = source_path.relative_to(template_dir)
        if rel.parts and rel.parts[0] == '.godotter':
            continue
        target_path = project_path / rel

        if source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True)
            created_dirs.append(target_path)
            continue

        target_path.parent.mkdir(parents=True, exist_ok=True)
        if source_path.suffix.lower() in {'.gd', '.tscn', '.tres', '.cfg', '.md', '.txt', '.godot', '.gitignore'}:
            content = source_path.read_text(encoding='utf-8')
            content = content.replace('{{PROJECT_NAME}}', project_name)
            content = _replace_uid_placeholders(content)
            write_text_utf8(target_path, content)
        else:
            shutil.copy2(source_path, target_path)
        created_files.append(target_path)

    return created_files, created_dirs


_UID_PLACEHOLDER_RE = re.compile(r"\{\{UID_[A-Z0-9_]+\}\}")


def _replace_uid_placeholders(content: str) -> str:
    def _replace(_: re.Match[str]) -> str:
        return generate_uid()

    return _UID_PLACEHOLDER_RE.sub(_replace, content)
