from __future__ import annotations

import json
from dataclasses import dataclass

from godotter.tasks.planpack import PlanTask


PLAN_CONSTRAINTS = [
    'Plan stage may investigate and decide internally, but task output must be executable work only.',
    'Do not create pure investigation, pure decision, or manual-only smoke-test tasks.',
    'Each task must be a closed loop: implement/fix/update plus its own automated verification.',
    'Merge discovery and verification into the implementation task that needs them.',
    'For a small bug or concrete error, prefer 1-3 tasks total.',
    'Each task must declare scope, acceptance, verification commands, and dependencies.',
    'Prefer changing one system/feature per task.',
    'If task changes game/features or game/systems, include tests changes in the same task.',
    'Choose the smallest required test kind: system, feature, integration, level-smoke, or e2e.',
]

NON_EXECUTABLE_STARTS = (
    'locate ',
    'find ',
    'identify ',
    'inspect ',
    'investigate ',
    'analyze ',
    'analyse ',
    'determine ',
    'decide ',
    'choose ',
    'research ',
    'perform smoke',
    'manual smoke',
    'manually run',
)

IMPLEMENTATION_TERMS = (
    'fix',
    'implement',
    'update',
    'add',
    'remove',
    'replace',
    'wire',
    'connect',
    'create',
    'repair',
    'refactor',
    'test',
    'verify',
)

COMMAND_TERMS = (
    'uv run ',
    'godotter ',
    'pytest',
    'godot',
    'python',
)

COMMAND_PREFIXES = (
    'uv run ',
    'godotter ',
    'python ',
    'pytest ',
    'godot ',
)


@dataclass(frozen=True)
class ScoutPromptRef:
    path: str
    reason: str = ''


def build_plan_prompt(goal: str, scout_refs: list[ScoutPromptRef]) -> tuple[str, list[str]]:
    prompt = '\n'.join(
        [
            'Create an executable implementation plan as JSON ONLY (no markdown, no backticks, no commentary).',
            'Output must be a single JSON object with keys: name (string, short concise summary), tasks (array).',
            'Each task must have: title, goal, scope (array of path prefixes), acceptance (array), verification (array), depends_on (array).',
            'Only output tasks that an executor should run. Do not output scout/analysis/decision/manual-smoke tasks.',
            'Every task must include code/content changes and automated verification in the same task.',
            'Do not write verification as prose such as "Run the game" or "Test that ...".',
            'For bug fixes, use 1-3 tasks unless multiple independent systems must change.',
            '',
            'Bad task examples: "Locate references", "Determine best fix", "Perform smoke test".',
            'Good task examples: "Fix double_down input action and add gameplay harness coverage", "Update renderer palette and run UI smoke tests".',
            '',
            'Verification commands must use EXACTLY one of these forms (do not invent subcommands):',
            '  uv run godotter runtime validate-structure --workspace .',
            '  uv run godotter runtime validate-managers --workspace .',
            '  uv run godotter runtime validate-paths --workspace .',
            '  uv run godotter runtime lint --project . --headless',
            '  uv run godotter runtime test --project . --kind <KIND> --timeout 30',
            '  uv run godotter runtime run --project . --scene res://<PATH> --timeout 30',
            '  uv run godotter runtime verify --workspace . --kind <KIND> --timeout 60',
            '  uv run pytest tests/ -x -q',
            'Valid KIND values: system, feature, integration, level-smoke, e2e, all.',
            'If a task edits .tscn scenes or res:// references, include "uv run godotter runtime validate-paths".',
            'Use --kind level-smoke for scene smoke tests, --kind feature for feature tests.',
            '',
            f'goal={goal}',
            '',
            'Relevant files (scout context, do not turn these into separate tasks):',
            *[f'- {ref.path} {ref.reason}'.rstrip() for ref in scout_refs[:20]],
        ]
    )
    return prompt, PLAN_CONSTRAINTS.copy()


def normalize_plan_dependencies(tasks: list[PlanTask]) -> None:
    task_ids = {task.id for task in tasks}
    title_to_id: dict[str, str] = {}
    for task in tasks:
        key = task.title.strip().lower()
        if key and key not in title_to_id:
            title_to_id[key] = task.id

    for task in tasks:
        normalized: list[str] = []
        for dep in task.depends_on:
            dep_norm = dep.strip()
            if not dep_norm:
                continue
            if dep_norm in task_ids:
                normalized.append(dep_norm)
                continue
            if dep_norm.isdigit():
                numbered_id = f't{dep_norm}'
                if numbered_id in task_ids:
                    normalized.append(numbered_id)
                    continue
            mapped = title_to_id.get(dep_norm.lower())
            normalized.append(mapped or dep_norm)
        task.depends_on = normalized


def validate_plan_tasks(tasks: list[PlanTask]) -> None:
    if not tasks:
        raise ValueError('planner_json_missing_tasks')

    task_ids = {task.id for task in tasks}
    missing_deps = sorted({dep for task in tasks for dep in task.depends_on if dep not in task_ids})
    if missing_deps:
        raise ValueError(f'planner_unknown_dependencies: {missing_deps}')

    failures: list[str] = []
    for task in tasks:
        title = task.title.strip()
        goal = task.goal.strip()
        title_lower = title.lower()
        goal_lower = goal.lower()
        starts_non_executable = title_lower.startswith(NON_EXECUTABLE_STARTS) or goal_lower.startswith(
            NON_EXECUTABLE_STARTS
        )
        has_implementation_term = any(term in f'{title_lower} {goal_lower}' for term in IMPLEMENTATION_TERMS)
        if starts_non_executable and not has_implementation_term:
            failures.append(f'{task.id}: non_executable_task title={title!r}')
        has_executable_verification = any(is_executable_verification(item) for item in task.verification)
        mentions_manual = any(
            term in title_lower or term in goal_lower or any(term in item.lower() for item in task.verification)
            for term in ('manual', 'manually')
        )
        if mentions_manual and not has_executable_verification:
            failures.append(f'{task.id}: manual_only_or_manual_smoke_not_allowed')
        if not task.scope:
            failures.append(f'{task.id}: missing_scope')
        if not task.acceptance:
            failures.append(f'{task.id}: missing_acceptance')
        if not task.verification:
            failures.append(f'{task.id}: missing_verification')
        elif not has_executable_verification:
            failures.append(f'{task.id}: verification_must_contain_executable_command')

    if failures:
        raise ValueError('planner_quality_gate_failed: ' + '; '.join(failures))


def is_executable_verification(value: str) -> bool:
    raw = str(value).strip()
    if not raw:
        return False
    if raw.startswith('`') and raw.endswith('`') and len(raw) > 2:
        raw = raw[1:-1].strip()
    lower = raw.lower()
    if lower.startswith(COMMAND_PREFIXES):
        return True
    if 'headless_run' in lower or 'script_lint' in lower:
        return True
    return False


def build_revise_prompt(task: PlanTask, feedback: str, goal: str) -> tuple[str, list[str]]:
    prompt = '\n'.join(
        [
            'A task in an implementation plan failed its verification. Revise it based on reviewer feedback.',
            '',
            'Output ONLY valid JSON. No markdown, no backticks, no commentary.',
            '{',
            '  "title": "...",',
            '  "goal": "...",',
            '  "scope": [...],',
            '  "acceptance": [...],',
            '  "verification": [...],',
            '  "depends_on": [...]',
            '}',
            '',
            'Verification commands must use EXACTLY one of these forms (do not invent subcommands):',
            '  uv run godotter runtime validate-structure --workspace .',
            '  uv run godotter runtime validate-managers --workspace .',
            '  uv run godotter runtime validate-paths --workspace .',
            '  uv run godotter runtime lint --project . --headless',
            '  uv run godotter runtime test --project . --kind <KIND> --timeout 30',
            '  uv run godotter runtime run --project . --scene res://<PATH> --timeout 30',
            '  uv run godotter runtime verify --workspace . --kind <KIND> --timeout 60',
            '  uv run pytest tests/ -x -q',
            'Valid KIND values: system, feature, integration, level-smoke, e2e, all.',
            '',
            f'Original plan goal: {goal}',
            '',
            f'Original task title: {task.title}',
            f'Original task goal: {task.goal}',
            f'Original scope: {json.dumps(task.scope, ensure_ascii=False)}',
            f'Original acceptance: {json.dumps(task.acceptance, ensure_ascii=False)}',
            f'Original verification: {json.dumps(task.verification, ensure_ascii=False)}',
            f'Original dependencies: {json.dumps(task.depends_on, ensure_ascii=False)}',
            '',
            f'Reviewer feedback — you MUST address this:',
            feedback,
        ]
    )
    return prompt, PLAN_CONSTRAINTS.copy()


DESIGN_EXAMPLE = json.dumps({
    "name": "Sokoban",
    "systems": [
        {
            "name": "grid_system",
            "directory": "game/systems/grid/",
            "description": "Manages grid cell states (wall, floor, box, goal, player). Initializes from level data.",
            "publishes": ["GRID_CHANGED"],
            "subscribes": [],
            "export_vars": [{"name": "grid_width", "type": "int", "default": "10"}, {"name": "grid_height", "type": "int", "default": "10"}],
        },
        {
            "name": "movement_system",
            "directory": "game/systems/movement/",
            "description": "Processes player input, validates moves, detects box pushes.",
            "publishes": ["PLAYER_MOVED"],
            "subscribes": ["GRID_CHANGED"],
            "export_vars": [],
        },
    ],
    "features": [
        {
            "name": "push_box_feature",
            "directory": "game/features/push_box/",
            "description": "Wires movement to box detection and goal checking.",
            "uses_systems": ["movement_system", "box_system", "goal_system"],
            "subscribes": ["PLAYER_MOVED"],
        },
    ],
    "gamemodes": [
        {
            "name": "SokobanGameMode",
            "directory": "game/gamemodes/sokoban/",
            "description": "Standard Sokoban level runner.",
            "systems": ["grid_system", "movement_system", "box_system", "goal_system"],
            "features": ["push_box_feature"],
            "export_vars": [{"name": "level_data_path", "type": "String", "default": ""}],
        },
    ],
    "event_types": [
        {"name": "GRID_CHANGED", "description": "Emitted when any cell in the grid changes state."},
        {"name": "PLAYER_MOVED", "description": "Emitted after player movement is processed."},
    ],
}, indent=2, ensure_ascii=False)


DESIGN_CONSTRAINTS = [
    "Systems are self-contained: they publish events, subscribe to events, and know nothing about features, gamemodes, or levels.",
    "Features wire systems together: they subscribe from multiple systems and coordinate game rules.",
    "GameModes are the single orchestrator per level: they create systems and features as children in _ready().",
    "System publish/subscribe references MUST point to named event_types.",
    "Feature uses_systems MUST reference existing system names.",
    "GameMode systems/features MUST reference existing system and feature names.",
    "No System may reference another System, Feature, GameMode, or Level.",
    "No Feature may reference another Feature or GameMode.",
]


def build_design_prompt(description: str) -> tuple[str, list[str]]:
    prompt = '\n'.join(
        [
            'You are a game design assistant for the Godotter template conventions.',
            '',
            'Decompose the game idea below into Systems, Features, GameModes, and EventTypes.',
            '',
            'Output ONLY valid JSON. No markdown, no backticks, no commentary, no explanation.',
            '',
            '## Architecture Hierarchy',
            '',
            'References between layers MUST follow this strict order:',
            '  game/levels/     -> game/gamemodes/',
            '  game/gamemodes/  -> game/features/, game/systems/, game/core/',
            '  game/features/   -> game/systems/, game/core/',
            '  game/systems/    -> game/core/ ONLY',
            '  game/core/       -> nothing in game/',
            '',
            'Systems are self-contained logic units. Each system:',
            '  - owns ONE domain (grid, movement, monster, inventory, etc.)',
            '  - has its own Timer-driven tick loop',
            '  - publishes events when state changes',
            '  - subscribes to events from core/ only',
            '  - implements configure(bus) to receive EventBus reference',
            '  - is a Node (never Node2D or Control)',
            '',
            'Features wire systems into gameplay rules. Each feature:',
            '  - subscribes to events from system(s)',
            '  - coordinates multiple systems to implement a game rule',
            '  - can be enabled/disabled independently',
            '  - does NOT own systems (systems are owned by GameMode)',
            '',
            'GameModes bundle systems and features into a level type. Each GameMode:',
            '  - creates all systems and features as children in _ready()',
            '  - is the single orchestrator per level scene',
            '  - has exported parameters for level-specific config',
            '  - multiple levels can reuse the same GameMode',
            '',
            '## Example',
            '',
            'Input: "player pushes boxes onto targets, with undo and step counter"',
            '',
            'Output:',
            DESIGN_EXAMPLE,
            '',
            '## Output Schema',
            '',
            '{',
            '  "name": "your_game_name",',
            '  "systems": [',
            '    {',
            '      "name": "...",',
            '      "directory": "game/systems/.../",',
            '      "description": "...",',
            '      "publishes": ["EVENT1", "EVENT2"],',
            '      "subscribes": ["EVENT3"],',
            '      "export_vars": [{"name": "...", "type": "int|float|String|bool", "default": "..."}]',
            '    }',
            '  ],',
            '  "features": [',
            '    {',
            '      "name": "...",',
            '      "directory": "game/features/.../",',
            '      "description": "...",',
            '      "uses_systems": ["system_name_1", "system_name_2"],',
            '      "subscribes": ["EVENT1"]',
            '    }',
            '  ],',
            '  "gamemodes": [',
            '    {',
            '      "name": "...",',
            '      "directory": "game/gamemodes/.../",',
            '      "description": "...",',
            '      "systems": ["system_name_1"],',
            '      "features": ["feature_name_1"],',
            '      "export_vars": [{"name": "...", "type": "String", "default": ""}]',
            '    }',
            '  ],',
            '  "event_types": [',
            '    { "name": "EVENT1", "description": "..." },',
            '    { "name": "EVENT2", "description": "..." }',
            '  ]',
            '}',
            '',
            'Game idea to decompose:',
            description,
        ]
    )
    return prompt, DESIGN_CONSTRAINTS.copy()


def validate_design_json(data: dict) -> list[str]:
    errors: list[str] = []
    system_names = {s["name"] for s in data.get("systems", []) if "name" in s}
    feature_names = {f["name"] for f in data.get("features", []) if "name" in f}
    event_names = {e["name"] for e in data.get("event_types", []) if "name" in e}

    for sys in data.get("systems", []):
        for ev in sys.get("publishes", []):
            if ev not in event_names:
                errors.append(f'system "{sys["name"]}" publishes unknown event "{ev}"')
        for ev in sys.get("subscribes", []):
            if ev not in event_names:
                errors.append(f'system "{sys["name"]}" subscribes to unknown event "{ev}"')

    for feat in data.get("features", []):
        for sys_name in feat.get("uses_systems", []):
            if sys_name not in system_names:
                errors.append(f'feature "{feat["name"]}" references unknown system "{sys_name}"')
        for ev in feat.get("subscribes", []):
            if ev not in event_names:
                errors.append(f'feature "{feat["name"]}" subscribes to unknown event "{ev}"')

    for gm in data.get("gamemodes", []):
        for sys_name in gm.get("systems", []):
            if sys_name not in system_names:
                errors.append(f'gamemode "{gm["name"]}" references unknown system "{sys_name}"')
        for feat_name in gm.get("features", []):
            if feat_name not in feature_names:
                errors.append(f'gamemode "{gm["name"]}" references unknown feature "{feat_name}"')

    return errors
