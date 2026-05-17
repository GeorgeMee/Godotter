# gdcli Feature Mapping for Godotter

## Goal

Translate the useful parts of `References/gdcli` into a Python-first Godotter runtime.

## P0 - Must Have

### Python-native

- `project_info`
  - Parse `project.godot`
  - Count `.gd` and `.tscn` files
  - Report main scene and autoloads
- `scene_create`
  - Generate scene UID
  - Generate minimal `.tscn`
  - Write atomically
- `scene_inspect`
  - Parse scene UID
  - Parse ext resources, sub resources, nodes, properties, connections
- `scene_validate`
  - Check missing `ext_resource` targets
  - Check malformed nodes / missing types

### Godot headless

- `script_lint`
  - Single-file `--headless -s file.gd --check-only`
  - Whole-project `--quit`
- `headless_run`
  - Run project or scene with timeout
  - Capture stdout, stderr, exit code

## P1 - Strongly Recommended

### Python-native

- `scene_list`
- `scene_edit`
  - Limited property edits first
  - Resource-aware `res://` handling
- `uid_fix`
  - Scan `.uid` files
  - Build `uid:// -> res://path` map
  - Fix stale `ext_resource` paths in `.tscn` / `.tres`

### Godot headless

- `doctor`
  - Check Godot binary
  - Check project root
  - Count `.gd` files

## P2 - Nice to Have

- `node_add/remove/reorder`
- `sub_resource_add/edit`
- `connection_add/remove`
- `sprite_load`
- `docs_lookup`
- `docs_build`
- non-blocking run sessions

## Implementation Split

### Python should own

- scene text parsing
- scene UID generation
- minimal `.tscn` generation
- project metadata extraction
- UID repair
- atomic writes

### Godot should own

- GDScript parse / compile validation
- headless runtime execution
- engine-level diagnostics

## Recommendation

Build P0 first in Python, then add Godot subprocess validation. Do not reimplement the entire `gdcli` surface before the workflow runtime exists.