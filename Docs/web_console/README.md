# Godotter Web Console（设计草案）

目标：在云端（Linux）运行 Godot + Godotter，通过网页完成「提需求 → 产计划（不执行）→ 审批/评论 → 执行（自愈）→ 通知」的闭环；适配手机使用（安卓优先）。

本目录只存放产品/系统设计与实现方案草案，便于后续迭代落地。

## 设计原则

- 默认只产计划，不执行；执行必须显式确认（Approve/Run）。
- 重要/危险操作（删除、覆盖、批量改动、破坏性命令）必须进入审批队列。
- 任务执行需具备自愈重试：失败 → 修复 → 验证，直到通过或同错重复退出。
- 通知默认克制：仅 Needs-Approval / Run-Finished / Plan-Ready，且支持聚合为单条通知。

## 文档索引

- `Docs/web_console/architecture.md`：总体架构与组件边界
- `Docs/web_console/state_machine.md`：Plan/Item/Run/Approval 状态机
- `Docs/web_console/notifications.md`：通知策略（聚合、降噪、Web Push/ntfy）
- `Docs/web_console/api.md`：后端 API 草案（HTTP + SSE/WebSocket）

