# Godotter 模板项目（Template Project）

目标：当 Godot 项目规范较多时（Managers + EventBus + core/systems/features/content/levels/tests），通过模板项目实现“一键复制即用”，让 agent 与 CI 在同一套约定下工作。

## 模板位置

Godotter 仓库内置模板目录：
- `templates/godotter_game_template/`

该模板会被 `godotter project new` 优先使用（复制到目标目录后替换占位符）。

## 占位符

模板中可使用以下占位符，创建时自动替换：
- `{{PROJECT_NAME}}`：项目名（目录名）
- `{{UID_MAIN_SCENE}}`：主场景 UID（随机生成）

## 推荐的工作方式

- 新建项目：`godotter project new <YourProjectName>`
- 然后按需新增系统/玩法：
  - `res://game/systems/<name>/scripts/`
  - `res://game/features/<name>/scripts/`
  - 单元测试放到 `res://tests/systems/<name>/` / `res://tests/features/<name>/`

## 版本策略（建议）

- 模板是“起步骨架”，后续项目演进可与模板脱钩。
- 如需持续同步，可在 Godotter 中增加 `project sync-template`（后续可选）。

