# Godotter 开发模式（项目内副本）

本项目采用 Godotter 的开发模式约定：
- `Managers`（每个 Level 根节点一个）
- 结构化事件（`EventBus`）
- 分层：`game/`、`ui/`、`tests/`

详细说明请参考 Godotter 仓库内的文档：
- `Docs/godotter_dev_mode_project_structure.md`

建议将该文档随项目迭代持续更新，并在各个 `feature/system` 的 `README.md` 中补充事件与依赖。

模板内已包含一个最小事件流样板：
- `game/features/item_pickup/`
- `game/systems/inventory/`
- `tests/features/item_pickup/`

建议新 feature 先参考这条闭环（feature + system + harness），再扩展自己的事件、manager 与测试。

