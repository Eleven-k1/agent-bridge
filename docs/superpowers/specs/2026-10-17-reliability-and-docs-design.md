# 本地桥接可靠性与文档一致性设计

## 目标

修复当前本地 Agent Bridge 的事件发布恢复、任务状态恢复、进程启动错误处理、Web UI 重复启动和契约文档不一致问题，使现有 Fake 与固定本地 OpenAI Relay 链路在异常退出和重启后仍可解释、可恢复、可验证。

本轮不引入真实文件/Shell/Git 工具、不增加远程 Provider、不改变 M0 v0.5.1 的公共语义。

## 现状问题

- M5 已保存 `published_at`，但集成发布路径没有在广播成功后标记，启动时也没有补发待发布事件。
- M1 `TaskState` 只存在内存字典，服务重启后无法根据正式事件恢复。
- Electron 主进程依赖 PATH 中的 `python`，spawn 失败时可能表现为启动超时或未处理异常。
- Web UI 启动脚本不检查已有进程和端口，重复启动会留下端口占用日志和失效 PID。
- 生成 JSON Schema 没有充分表达作用域专属字段；模块文档仍混有旧的 Fake Provider 描述。

## 设计

### 事件发布与恢复

`SQLiteEventStore.append()` 继续负责事务内持久化。`LoopbackServer.publish_event()` 在事件广播成功后调用 `mark_published()`；广播失败时保留 `published_at=NULL`。服务启动完成后，集成应用按作用域读取 `pending_publications()`，从 SQLite 重建完整事件并通过现有广播入口补发，补发成功后标记已发布。补发失败保留记录，等待下一次启动或客户端 resume。

事件发布操作必须保持“先落盘、后广播、成功才标记”的顺序，不能用内存队列替代 SQLite 事实来源。测试覆盖正常发布、广播失败、重启补发和补发幂等。

### M1 状态恢复

为 `Orchestrator` 增加从正式事件重放构建 `TaskState` 的入口。恢复只消费任务作用域正式事件，按 `task_event_sequence` 顺序应用已有生命周期事件；未知或不一致事件返回明确恢复错误，不静默创建状态。`IntegratedApp.start()` 在接受命令前完成任务状态恢复，恢复失败阻止服务进入可用状态并保留可诊断错误。

恢复逻辑复用现有状态转换规则，避免为重放建立第二套状态机。新增测试覆盖创建、启动、模型完成、停止处置、恢复失败和重启后的继续处理。

### 进程与 Web UI 启动

`PythonServiceProcess.start()` 为 child process 的 `error` 事件提供单次 reject/清理路径，错误信息包含实际命令和 stderr 尾部；调用方可以将其显示为启动失败，而不是等待固定超时。保留当前正常 stdout listening 通知和优雅停止流程。

Web UI launcher 启动前读取 PID 文件并检查目标进程仍是本项目 Python 启动器；已有有效服务时直接复用，失效 PID 自动清理。端口冲突或健康检查失败写入诊断日志并退出，不覆盖已有服务。停止脚本只终止经过项目标识校验的进程。

### 契约与文档

更新 JSON Schema，使 TASK 命令要求 `task_id`/`run_id`，WORKSPACE 命令要求 `workspace_identity`，正式事件要求对应作用域序号；运行时 dataclass 仍是最终校验来源。同步 `README.md`、`web-ui/README.md`、`docs/current-status.md` 和当前模块说明，明确固定本地 Relay、测试命令、未完成能力和历史计划文档的定位。

## 测试策略

- 先为每个行为写失败测试，再实现最小改动。
- 后端：事件发布标记、待发布补发、任务状态重放、恢复失败和 Schema 必填字段。
- 桌面端：spawn 错误、可读启动失败、正常启动/停止回归。
- Web UI：有效 PID 复用、失效 PID 清理、端口占用和停止校验。
- 最终运行后端 pytest、Web UI pytest、桌面端 Vitest、TypeScript 类型检查和 Electron 构建。

## 约束

- 不改变 M0 v0.5.1 的字段名称、事件语义或作用域分类。
- 不把临时模型流帧当作任务恢复依据。
- 不删除现有 SQLite 数据、构建产物或诊断日志；仅清理明确属于本轮验证的临时残留。
- 当前目录没有可用 Git 仓库，因此设计和实现通过文件差异与测试输出验证，无法创建提交。
