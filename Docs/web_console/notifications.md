# Notifications（草案）

目标：避免“通知爆炸”，默认只推关键事件，并支持聚合成单条通知（类似 QQ 未读数）。

## 事件分类

仅三类默认开启：
- `approval_required`
- `run_finished`（ok/fail）
- `plan_ready`

禁止默认推送：
- step-by-step 日志
- 每个 task 的进度

## 聚合策略（推荐默认）

维护一个 Inbox 计数：
- pending_approvals
- pending_reviews
- failed_runs

推送始终更新同一条通知（固定 tag/group）：
- `Godotter：待处理 2（需审批 1）`

## 通知后端

### Web Push（PWA）
- 需要 HTTPS 域名（Let’s Encrypt/Cloudflare）
- 用 tag/group key 更新同一条通知

### ntfy
- 同 topic 发送时控制频率与聚合
- 仅发送摘要（不发长日志）

