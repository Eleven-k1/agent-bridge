# AI-A M0/M1 模块说明

## 范围

本模块实现 M0 v0.5.1 的 Python 公共契约，以及 M1 的第一阶段任务状态机和命令路由端口。模块本身不实现模型、工具、数据库、传输或 UI；正式装配层已把它连接到 M2、M5 和 M6。

## 公共接口

`agent_bridge.contracts.envelopes`：

- `CommandEnvelope`：TASK、WORKSPACE、LOCAL_CONFIG 三种作用域命令；TASK 必须有 `task_id`/`run_id`，WORKSPACE 必须有 `workspace_identity`，LOCAL_CONFIG 不要求工作区。
- `EventEnvelope`：正式、可持久化事件。按作用域使用且只使用 `task_event_sequence`、`workspace_event_sequence` 或 `local_config_event_sequence` 中之一。
- `ModelStreamFrame`：M2 到 M6 再到 M7 的临时模型帧，包含 `operation_id` 和 `model_fragment_sequence`，没有正式事件 ID 或作用域全局事件序号，不能改变任务状态。
- `InMemoryIdempotencyRegistry`：仅作为 M0 三类幂等语义的参考实现。命令用 `command_idempotency_key`，操作用 `operation_id`，事件用 `event_deduplication_key`；相同身份且摘要不同抛出 `IDEMPOTENCY_CONFLICT`。生产持久化权威入口属于 M5。
- `schema_export.export_schemas(destination)`：导出稳定的 v0.5.1 JSON Schema。当前生成物为 `contracts/generated/*.json`。

`agent_bridge.contracts.lifecycle`：

- `TaskState` 把业务阶段、生命周期、阻塞、工作区修改、验证状态和最终结果分层。
- 新修改通过 `with_new_workspace_modification()` 将工作区状态改为 `TASK_CHANGES_UNVERIFIED` 并将验证降级为 `NOT_RUN`。
- `TERMINATED` 禁止存在开放 blocker、有效写租约、可消费审批或未解决操作。`AWAITING_DISPOSITION` 必须保持 `OPEN` 结果和 `STOP_DISPOSITION` blocker。`RECOVERY_REQUIRED` 必须有 `RECOVERY_DECISION` blocker。

`agent_bridge.orchestrator`：

- `Orchestrator` 是 M1 内唯一改变任务状态的对象。首阶段支持创建、启动、暂停、继续、停止并等待处置、保留或恢复成功后的终止、恢复对账失败和未提交完成。
- `ApplicationCommandRouter` 只按 `scope_type` 转交：TASK 至 M1，WORKSPACE 至工作区只读服务，LOCAL_CONFIG 至模型连接或角色/设置服务；它不解释或执行任何业务命令。
- `ports.py` 定义 M1 到 M2-M5 的结构端口。M1 不导入其他模块内部实现。

## 集成方式

1. M6 完成鉴权、基础格式校验和持久化命令幂等登记后，经应用命令路由器提交 TASK 命令至 `Orchestrator.handle()`。
2. M1 产生 `CandidateFact`，只通过 M5 对应的候选事件持久化端口提交；M5 成功持久化后，M6 才能发布正式事件。
3. M2 把 `ModelStreamFrame` 交给 M6 转发 M7；M1 不将临时帧视为状态事实。
4. M3/M4 在真实副作用前后实施审批、写租约、操作生命周期与事实核验；M1 仅根据 M5 已持久化的事实结果推进更高阶段。

## 测试

预期 pytest 入口：

```powershell
cd backend
python -m pip install -e ".[test]"
python -m pytest tests/contracts tests/orchestrator -v -p no:cacheprovider --basetemp .test-tmp-gpt-m0-m1
```

契约测试覆盖三种 scope、作用域序号、临时流帧、三类幂等冲突、终止不变量和确定性 Schema。编排测试覆盖创建、启动、暂停、继续、停止处置、取消结果和恢复失败。

2026-08-30 本机验证结果：上述 pytest 入口在项目隔离环境中执行，`22 passed`，退出码 `0`。跨模块测试另验证 M1 的创建/启动事实经 M5 持久化、M6 发布，并能在断线后逐字段恢复。

## 限制

- 当前 `InMemoryIdempotencyRegistry` 不是 M5 的持久化实现，不能用于断电/重启安全结论。
- 正式装配层只接通第一阶段命令和 Fake 流程；完整的多智能体任务分派、模型互证与结果合并尚未实现。
- M1 尚未依据 M3/M4 的真实副作用事实推进完整阶段，也未实现真实 Run 恢复流程。
