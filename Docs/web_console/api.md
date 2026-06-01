# API（草案）

说明：此处仅定义最小可用 API，用于快速打通「plan-only → approve → run → logs → notify」。

## Auth

第一版建议：
- 简单 Token（HTTP Header）
- 或仅绑定内网/SSH 隧道访问

## Endpoints（示例）

### Plans

- `POST /api/plans`
  - body: `{ goal, workspace_root?, scout_max_files?, planner_provider? }`
  - returns: `{ plan_id, plan_path, summary }`

- `GET /api/plans/{plan_id}`
  - returns: plan JSON + item statuses + comments

- `POST /api/plans/{plan_id}/items/{item_id}/approve`
- `POST /api/plans/{plan_id}/items/{item_id}/revise` (comment required)

### Runs

- `POST /api/runs`
  - body: `{ plan_id, item_ids?, executor_provider?, max_attempts?, same_failure_limit? }`
  - returns: `{ run_id }`

- `GET /api/runs/{run_id}`
  - returns: `{ status, attempts, artifacts }`

- `GET /api/runs/{run_id}/logs`（SSE）
  - streams: log lines + status updates

### Approvals

- `GET /api/approvals`
- `POST /api/approvals/{approval_id}/approve` (optional comment)
- `POST /api/approvals/{approval_id}/reject` (comment required)

