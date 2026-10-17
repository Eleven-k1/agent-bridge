# 本地桥接可靠性与文档一致性实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复事件发布恢复、M1 任务状态重放、桌面端启动错误、Web UI 重复启动和契约文档不一致问题。

**Architecture:** 保留 M0 v0.5.1、M5 SQLite 和 M6 loopback 的现有边界。事件以 SQLite 为事实来源，采用“持久化 -> 广播 -> 标记已发布”顺序；M1 从正式 TASK 事件重放状态；桌面与 Web UI 只改启动与错误处理。

**Tech Stack:** Python 3.11+、SQLite、pytest、Electron 44、React 19、TypeScript、Vitest、PowerShell/VBS 启动脚本。

## Global Constraints

- 不改变 M0 v0.5.1 的字段名称、作用域分类和临时模型流语义。
- 不实现真实文件、Shell、Git 工具，不接入远程 Provider，不增加外部依赖。
- 所有行为修改先写失败测试，再写最小实现。
- 不删除现有数据库、构建产物和诊断日志；只清理本轮验证产生的临时残留。
- 当前目录没有 Git 仓库，不执行 commit。

---

### Task 1: 事件发布标记与未发布事件补发

**Files:**
- Modify: `backend/src/agent_bridge/app.py`
- Modify: `backend/src/agent_bridge/transport/server.py`
- Test: `backend/tests/integration/test_integrated_app.py`
- Test: `backend/tests/storage/test_event_store.py`

**Interfaces:**
- `EventStore.pending_publications(scope_type, scope_id) -> list[int]`
- `EventStore.mark_published(scope_type, scope_id, sequence) -> None`
- `LoopbackServer.publish_event(candidate) -> dict[str, Any]`

- [x] **Step 1: Write failing tests** for successful publish marking and startup replay of pending events.
- [x] **Step 2: Run** the focused integration, storage, and orchestration tests; the new tests failed because published rows remained pending and startup did not replay them.
- [x] **Step 3: Implement** mark-after-broadcast in the transport publish path and add an integration startup drain that reads persisted event envelopes and publishes them without re-appending.
- [x] **Step 4: Run** the focused tests again; `7 passed` and normal integration events no longer remain pending.

### Task 2: M1 TaskState event replay

**Files:**
- Modify: `backend/src/agent_bridge/orchestrator/core.py`
- Modify: `backend/src/agent_bridge/app.py`
- Test: `backend/tests/orchestrator/test_state_machine.py`
- Test: `backend/tests/integration/test_integrated_app.py`

**Interfaces:**
- Add `Orchestrator.restore_task_events(events: Iterable[dict[str, Any]]) -> set[tuple[str, str]]`.
- Add an internal event-to-transition adapter that reuses existing transition validation.

- [x] **Step 1: Write failing tests** for restoring a created/started task and rejecting an unknown or out-of-order event.
- [x] **Step 2: Run** the focused tests; the new tests failed because the replay API was missing.
- [x] **Step 3: Implement** ordered TASK event replay and invoke it during integrated app startup before accepting commands.
- [x] **Step 4: Run** focused tests; `7 passed` and restored state matches a continuously running orchestrator.

### Task 3: Desktop Python spawn errors

**Files:**
- Modify: `desktop/src/main/service-process.ts`
- Test: `desktop/tests/service-process.test.ts`

- [x] **Step 1: Write a failing test** that injects a nonexistent Python executable and expects a rejected start with the executable name rather than a 15-second timeout or uncaught error.
- [x] **Step 2: Run** the focused Vitest; it failed with the expected timeout and unhandled `ENOENT`.
- [x] **Step 3: Implement** one-shot reject/cleanup for `ChildProcess.error`, preserve stderr-tail diagnostics, and avoid double settlement from timeout/exit/error.
- [x] **Step 4: Run** the desktop suite with the project venv on PATH; all `23` tests pass.

### Task 4: Web UI launcher duplicate-start guard

**Files:**
- Modify: `web-ui/server/launcher.py`
- Modify: `web-ui/start-web.bat`
- Modify: `web-ui/start-web.vbs`
- Modify: `web-ui/stop-web.bat`
- Modify: `web-ui/stop-web.vbs`
- Test: `web-ui/tests/test_web_server.py`
- Create: `web-ui/tests/test_launcher.py`

- [x] **Step 1: Write failing tests** for stale PID cleanup, valid project PID reuse, and refusal to stop an unrelated process identity.
- [x] **Step 2: Run** the new tests; they failed because launcher helpers did not exist.
- [x] **Step 3: Implement** small testable PID/process identity helpers, reuse an existing healthy service, and make stop scripts call the guarded path.
- [x] **Step 4: Run** all Web UI tests; `6 passed` and no live project process or PID file was created by unit tests.

### Task 5: Contract schema and documentation synchronization

**Files:**
- Modify: `backend/src/agent_bridge/contracts/schema_export.py`
- Modify: `contracts/generated/command-envelope-v0.5.1.json`
- Modify: `contracts/generated/event-envelope-v0.5.1.json`
- Modify: `README.md`
- Modify: `web-ui/README.md`
- Modify: `docs/current-status.md`
- Modify: `docs/modules/zcode-m2-m6-m7.md`
- Test: `backend/tests/contracts/test_schema_export.py`

- [x] **Step 1: Write failing schema tests** asserting TASK/WORKSPACE conditional required fields and the correct event sequence field.
- [x] **Step 2: Run** the contract tests; they failed because generated schemas lacked `allOf` scope requirements.
- [x] **Step 3: Implement** JSON Schema `if/then` constraints, regenerate checked-in schemas, and rewrite current docs around the fixed Relay state while labeling old plans as historical.
- [x] **Step 4: Run** contract tests; `4 passed` and generated JSON contains the protocol and scope constraints.

### Task 6: Full verification and residue review

**Files:**
- No production files unless a focused test exposes a regression.

- [x] **Step 1: Run** `backend\.venv\Scripts\python.exe -m pytest backend/tests -q -p no:cacheprovider`; `233 passed`.
- [x] **Step 2: Run** `backend\.venv\Scripts\python.exe -m pytest web-ui/tests -q -p no:cacheprovider`; `6 passed`.
- [x] **Step 3: Run** desktop Vitest with `backend\.venv\Scripts` prepended to PATH, then `npm.cmd run typecheck` and `npm.cmd run build`; `23 passed`, typecheck and build passed.
- [x] **Step 4: Scan** for new `__pycache__`, `.pid`, debug output, temporary scripts, or test databases; removed generated Python caches and the stale PID file, retaining the diagnostic log and SQLite runtime data.
- [x] **Step 5: Re-read `README.md` and `docs/current-status.md`** and recorded remaining limitations without claiming unverified behavior.
