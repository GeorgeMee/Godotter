# State Machine（草案）

## PlanPack / PlanItem

`PlanPack.status`：
- `draft`：仅生成草案
- `in_review`：等待用户逐项处理
- `approved`：至少有可执行的 items
- `archived`

`PlanItem.status`：
- `needs_review`（默认）
- `approved`
- `needs_revision`（带 comment，要求 planner 返工该 item）
- `rejected`

关键点：
- 计划“逐项审批”，允许只执行部分 items。
- `needs_revision` 必须保留 comment，并在重新生成时引用。

## Run / Attempt

`Run.status`：
- `queued`
- `running`
- `blocked_for_approval`（危险操作/高风险变更）
- `passed`
- `failed`
- `canceled`

`Attempt`：
- attempt_index
- failure_signature（同错去重/提前退出）
- verification_results（每条命令 stdout/stderr/exit）

## Approval

`ApprovalRequest`：
- kind: `dangerous_change | delete | overwrite | external_command | plan_item_execute | other`
- payload: 结构化信息（文件列表、命令、diff 摘要）
- comment: 用户输入（可选）

