# Source Layout

The source tree now uses a standard Python package layout under `src/godotter/`.

Current goal:

- keep domain boundaries explicit
- make the project installable and runnable early
- keep public actions separate from grouped service capabilities

Package ownership:

- `godotter.agent`: orchestration entry logic
- `godotter.config`: settings and application logging setup
- `godotter.operations`: public action contracts, schemas, registry, and operation handlers
- `godotter.services`: business services and lower-level capability modules
- `godotter.services.godot`: Godot runner, project inspection, validation, export, UID, LSP, and Godot-facing services
- `godotter.services.project`: workspace file/git/patch services and project/scene/test scaffolding
- `godotter.services.llm`: provider configuration helpers
- `godotter.context`: execution context, memory, project summaries, and scout context
- `godotter.llm`: model runtime abstractions
- `godotter.interfaces`: human and machine CLI entry points
- `godotter.tasks`: plan/work/run state models that have not yet moved into services
- `godotter.utils`: shared helpers
