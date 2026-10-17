# AI-A 至集成人员交接

> 状态：已完成交接，保留作历史记录。当前实现和限制以根目录 `README.md`、`docs/current-status.md` 及 `docs/modules/` 为准。

## 已提供接口

### 给 Claude（M3/M4/M5）

1. M5 候选事件端口需接受来自 M1/M2/M3/M4 的候选事实，原子完成事件去重、作用域序号分配和持久化。M0 `EventEnvelope` 由 M5 返回后才可由 M6 发布。
2. M1 当前以 `CandidateEventPort.append_candidate(candidate)` 表达依赖；集成时应适配为 M5 的正式候选事件追加接口，不能让 M1 直接分配序号或写审计。
3. `TaskState.assert_invariants()` 对终止、等待处置和恢复所需 blocker 提供公共检查。M3/M4/M5 仍须从权威审批、租约和操作状态核验这些不变量。
4. Stop 保留修改或全量恢复成功均终止为 `CANCELLED`；稍后处理保持 `AWAITING_DISPOSITION` 与 `OPEN`。部分恢复失败必须反馈可持久化的恢复事实，以使 M1 进入 `RECOVERY_REQUIRED`。

### 给 Zcode（M2/M6/M7）

1. `ModelStreamFrame` 为临时帧：TASK scope、`task_id`、`run_id`、`operation_id`、`model_fragment_sequence`、`frame_type`、`content_delta_ref`、`received_at`。它没有正式事件序号/ID，不改变状态。
2. M2 经 M6 向 M7 转发临时帧；重连恢复依赖 M5 持久化的 `EventEnvelope`，而非帧回放。取消确认后的 delta 不得进入正式内容。
3. M6 仅做鉴权、基础格式校验、持久化命令登记/查询和转交。经应用路由器，TASK 到 M1，WORKSPACE 到只读工作区服务，LOCAL_CONFIG 到模型连接或角色/设置服务；M6 不解释业务语义。
4. `CommandEnvelope` 已区分 `command_id`、`command_idempotency_key`、`command_request_digest`。重传同键同摘要必须返回原处理状态；同键异摘要必须拒绝为 `IDEMPOTENCY_CONFLICT`。

## 需要集成人员确认的事项

1. M5 的原子事件追加返回格式如何映射到 `EventEnvelope`，以及 M6 的按作用域补发游标如何使用三种独立序号。
2. M6 的持久化命令入口如何返回“首次已受理”与“同摘要重复命令的已有状态”，且不会重放 Pause、Stop、ApprovalDecision 或 ToolRequest。
3. M3/M4 的真实审批、WriteLease 和操作状态如何在 M1 请求状态推进前提供已持久化事实。
4. M2/M6 是否能以本模块 `ModelStreamFrame` 形状转发而不把临时帧混入正式事件流。

## 契约变更申请

本轮未发现无法实现 M0 v0.5.1 的问题，未提交契约变更申请。
