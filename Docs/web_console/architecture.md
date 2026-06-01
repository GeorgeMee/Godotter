# Architecture

## 目标形态

云端常驻服务（daemon）+ Web UI +（可选）通知适配器。

核心诉求：
- 异步：用户离线也能跑
- 可审批：计划逐项审批、危险操作审批
- 可观测：logs/diff/tests/attempts
- 可回放：同 plan/run 可复跑/对比

## 组件

### 1) `godotterd`（云端 daemon，核心）

职责：
- 任务/计划/执行的生命周期管理
- WorkPack/PlanPack 的落盘与索引（建议 SQLite + 文件系统 artifacts）
- 执行引擎：调用现有 `godotter plan/task/runtime` 能力（复用自愈逻辑）
- 输出：事件流（run 状态、审批请求、通知摘要）

建议接口：
- HTTP API（REST）+ SSE（日志/状态流）
- 可选 WebSocket（更实时，但 SSE 更简单可靠）

### 2) Web Console（前端）

页面最小集合：
- Inbox（待审批/待处理/已完成）
- Plan Review（逐项 approve/revise/comment）
- Run Detail（logs/diff/tests/attempts，支持 retry）

### 3) Notifier（可选）

统一事件→通知策略后，支持多后端：
- Web Push（PWA；需要 HTTPS）
- ntfy（快速落地）
- Feishu（增强：卡片/按钮/链接；不作为唯一通道）

## 目录与工件

建议继续沿用项目内 `.godotter/`：
- `.godotter/plans/`：PlanPack 与状态
- `.godotter/workpacks/`：WorkPack 与执行记录
- `.godotter/artifacts/`：logs/diff/attachments（可新增）

