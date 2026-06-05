# Godotter 模板项目（Template Project）

目标：当 Godot 项目规范较多时（Managers + EventBus + 分层结构），通过模板项目实现“一键复制即用”，让 agent 与 CI 在同一套约定下工作。

## 模板位置

Godotter 仓库内置模板目录：
- `templates/godotter_game_template/`

该模板会被 `godotter project new` 优先使用（复制到目标目录后替换占位符）。

## 项目布局（推荐）

顶层三目录同级：
- `game/`：游戏逻辑与内容（core/systems/features/content/levels）
- `ui/`：表现层 UI（views/scripts/themes 等）
- `tests/`：测试与 harness（core/systems/features/integration/levels/e2e）

依赖方向建议固定为：
- `ui -> game`
- `tests -> (game, ui)`
- `game` 不反向依赖 `ui` 或 `tests`

## 占位符

模板中可使用以下占位符，创建时自动替换：
- `{{PROJECT_NAME}}`：项目名（目录名）
- `{{UID_MAIN_SCENE}}`：主场景 UID（随机生成）

## 推荐工作方式

- 新建项目：`godotter project new <YourProjectName>`
- 新增 system/feature：
  - `res://game/systems/<name>/scripts/`
  - `res://game/features/<name>/scripts/`
  - 单元测试放到：`res://tests/systems/<name>/` / `res://tests/features/<name>/`
  - 跨模块测试放到：`res://tests/integration/<scenario>/`
  - 玩家流程测试放到：`res://tests/e2e/<flow>/`
