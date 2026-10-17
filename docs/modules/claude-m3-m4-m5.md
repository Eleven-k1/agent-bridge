# Claude 负责模块：M3 安全、M4 工具门禁、M5 SQLite 持久化

负责人：AI-B（Claude）  
状态：Fake 第一纵向切片完成并接入正式应用  
协议依据：M0 v0.5.1 最终版

## 模块结构

`backend/src/agent_bridge/security/` 提供三类作用域能力门禁、Windows 路径分类、敏感信息脱敏、一次性审批票据和按工作区唯一的写租约。

`backend/src/agent_bridge/tools/` 提供 `ToolGate` 与 `FakeTool`。每次受控写入依次检查作用域、路径、租约和审批，在执行前持久化 `TOOL_EXECUTION_PLANNED`，执行后持久化 `COMPLETED` 或 `FAILED`。第一阶段拒绝真实 Shell、Git 和真实文件写入。

`backend/src/agent_bridge/storage/` 提供 SQLite `CommandLog` 与 `EventStore`。事件去重、作用域序号分配和正式事件写入位于同一个事务；相同去重键但摘要不同会记录冲突并拒绝。事件可按 TASK、WORKSPACE、LOCAL_CONFIG 独立序号重放，缺口不会被静默跳过。

## 正式应用连接

`backend/src/agent_bridge/app.py` 使用 M5 的 `CommandLog` 作为 M6 命令登记权威，使用 M5 的 `EventStore` 保存 M1/M2/M4 正式事件。`FAKE_TOOL_RUN` 是第一阶段验证命令：它在本地构造短期租约和绑定动作摘要的一次性票据，经 `ToolGate` 调用 `FakeTool`，再把 M5 已持久化的事件交给 M6 发布。

这条命令只用于证明 M3/M4/M5 可以协作。票据代表测试流程中的模拟批准，Fake Tool 不接触文件系统。真实工具接入时必须由用户审批界面签发票据，不得沿用自动批准流程。

## 测试

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests\security tests\tools tests\storage -q -p no:cacheprovider
```

2026-08-30 结果：`122 passed`。覆盖票据原子消费与失效、租约世代与唯一持有者、路径逃逸和敏感位置、脱敏、Fake Tool 执行顺序、命令幂等、事件去重、序号连续性、缺口报告、发布前持久化和重启恢复。

跨模块测试另验证 M3/M4 事件可由 M6 实时发布并由 M5 重放，且 Fake Tool 不创建目标文件。

## 限制

- 正式用户审批 UI、真实凭据服务和 ContextManifest 尚未接入。
- Shell、Git、下载、真实文件写入、快照和恢复没有实现。
- 当前工作区身份用于 Fake 本地流程，不代表卷序列号/文件 ID 级的生产工作区身份。
- Fake 工具流程的自动批准只允许存在于无真实副作用的第一阶段验证命令中。
