# Source Layout

The source tree now uses a standard Python package layout under `src/godotter/`.

Current goal:

- keep domain boundaries explicit
- make the project installable and runnable early
- leave room for workflow and runtime growth without another package move

Package ownership:

- `godotter.agent`: orchestration entry logic
- `godotter.workflows`: executable workflow definitions
- `godotter.runtime`: Godot and process runtime adapters
- `godotter.tools`: structured tools exposed to orchestration
- `godotter.context`: repository and scene context building
- `godotter.policies`: safety and execution policy checks
- `godotter.git`: checkpoint and rollback helpers
- `godotter.llm`: model runtime abstractions
- `godotter.interfaces`: CLI and future external interfaces
- `godotter.utils`: shared helpers