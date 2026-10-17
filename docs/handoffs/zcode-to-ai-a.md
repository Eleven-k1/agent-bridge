# Zcode -> AI-A 移交单（M2 / M6 / M7 第一轮 Fake 闭环）

> 状态：已完成交接，保留作历史记录。正式入口已切换为 `agent_bridge.app`；当前实现和限制以根目录 `README.md`、`docs/current-status.md` 及 `docs/modules/` 为准。

日期：2026-08-30
来源：AI-C（Zcode），负责 M2 / M6 / M7
性质：接口连接请求 + 契约变更申请 + 集成说明。未修改任何 M0 契约、公共 Schema 或他人模块。

## 1. 我已交付的内容（一句话版）

M2 统一 Provider 接口 + FakeProvider + 帧生成/取消门/三段超时/错误归一化；
M6 回环 NDJSON 服务（鉴权/幂等登记/帧转发/先持久化后发布/resume 补发）；
M7 Electron + React 最小工作台（Python 子进程管理、临时凭据、双层状态投影、断线恢复）。
后端 60 测试 + 桌面端 18 测试全绿。详见 `docs/modules/zcode-m2-m6-m7.md`。

## 2. 需要 AI-A 连接的接口

### 2.1 [必需] 契约类型切换点

`backend/src/agent_bridge/contracts/` 已出现后，我的模块仍在使用窄接口隔离（dict 传递 + 自带字段校验），未直接 import contracts。切换点如下，均只影响构造处，业务逻辑不变：

| 我的代码位置 | 当前做法 | 切换动作 |
|---|---|---|
| `model_gateway/gateway.py::_frame` | 手工组装帧 dict（M0 §6 八字段 + 扩展） | 改用 `contracts.ModelStreamFrame(...).to_dict()` 后叠加扩展字段（见 3.1） |
| `transport/memory.py::InMemoryEventStore.append` | 手工补全 event_id / 序号 / digest / integrity_ref | 改用 `contracts.EventEnvelope` 校验补全结果；摘要计算换成 `contracts.canonical_digest`（见 3.2） |
| `transport/validation.py::validate_command` | 自带字段存在性检查 | 改用 `contracts.CommandEnvelope` 构造校验（错误信息需保留字段名以便回执 INVALID_COMMAND 的 errors 列表） |
| `transport/interfaces.py` | CommandRouter / CommandRegistry / EventStore 端口 | 端口签名不变；orchestrator.router 实现 CommandRouter 即可插入 LoopbackServer |

我不自行切换的原因：contracts 与 orchestrator 仍在并行变更中，双向耦合会让两边震荡。等你的实现稳定后，切换是机械动作，我随时可做。

### 2.2 [必需] 正式应用入口替换 demo

`python -m agent_bridge.transport.demo` 是我的联调入口（FakeModelRouter + 内存存储）。你的 `app.py` / M1 编排就位后，桌面端只需换 `desktop/src/main/index.ts` 里 `PythonServiceProcess` 的 `module` 参数。**stdout 首行 JSON 协议请保持**：`{"event": "listening", "port": <int>, "token": "<str>"}`——这是 Electron main 获取端口与临时凭据的唯一通道（凭据不落盘、不进 renderer）。

### 2.3 [必需] CommandRouter 的实现归属

M0 §5 的「应用命令路由器」在我的 transport 里是 `CommandRouter` 端口（`interfaces.py`）：`async handle_command(command) -> None` + `async shutdown()`。语义约定：
- transport 只做鉴权、传输校验、基础格式校验、幂等登记，然后把 dict 原样转交；
- 路由器执行命令的一切可见结果必须走事件端口（`server.publish_event`）或帧端口（`server.broadcast_frame`）；`command_result` 回执只表达登记结果（NEW/REPLAY/CONFLICT/INVALID），不承载业务进展。

你的 orchestrator/router.py 若已实现等价语义，请以它为准替换 FakeModelRouter。

## 3. 契约变更申请（接口变更单）

### 3.1 [阻塞 M7 流式显示] ModelStreamFrame 缺内联显示字段

**问题**：contracts 的 `ModelStreamFrame` 只有 `content_delta_ref`（引用 id），无内联文本。M0 §6 说临时帧「仅用于 UI 即时显示」，但 M7 没有任何通道凭 `content:op-1:3` 这样的 ref 取回文本（内容块在 M5，正式事件 `MODEL_CONTENT_CHUNK_RECORDED` 只在事后到达）。若严格执行当前 Schema，流式显示层不可能实现。

**建议语义**（二选一，均为一句话级修改）：
- a. `ModelStreamFrame` 增加 `content_delta: str | None = None`（我的当前实现方式：帧 dict 在契约字段外携带该扩展字段）；
- b. 明确 `content_delta_ref` 的取值语义为内联文本本身（自包含引用）。

**不改的风险**：M7 要么无法即时显示流式内容（违反「仅用于 UI 即时显示」的契约目的），要么私改字段语义（违反公共契约）。

### 3.2 [阻塞事件互换] canonical digest 两套实现不一致

我的 `transport/memory.py` 摘要：`json.dumps(..., ensure_ascii=False, separators=(",",":"), sort_keys=True)`，前缀 `"sha256:"`。
你的 `contracts.canonical_digest`：`ensure_ascii=True, separators=(",",":"), sort_keys=True, default=str`，无前缀。

Fake 闭环内各自自洽；一旦两侧事件互换（或契约测试跨模块断言摘要），同 candidate 会得到不同值。**申请**：统一以 contracts 的 `canonical_digest` 为准，我切换时同步去掉前缀并保持摘要覆盖范围（排除 event_id/序号/occurred_at/integrity_ref）不变。契约侧最好把「摘要覆盖字段清单」写进 schema 注释，避免各模块各猜。

### 3.3 [依赖申请] transport 正式传输库

`backend/pyproject.toml` 我无权修改，当前 transport 用标准库 asyncio TCP + NDJSON（零新依赖）。调研结论（记录于 `docs/modules/zcode-m2-m6-m7.md` 与全局调研文档）：
- `websockets`（BSD-2-Clause，v17.x 活跃，精简 ~4k LOC）：若正式协议走 WebSocket，推荐；
- `aiohttp`（Apache-2.0/MIT，aio-libs 组织维护，近期有 CVE 修复记录）：若与 HTTP 服务栈统一，可选；
- FastAPI/Uvicorn：你侧调研的首选，若 app.py 采用，transport 可保留 NDJSON 通道或迁移，需要你定调。

**申请**：在 pyproject 中为 transport 声明所选依赖（或确认维持零依赖）。我的传输层把「协议逻辑」（鉴权/幂等/帧/补发）与「传输机制」分层，切换成本可控。

### 3.4 [澄清] 事件候选的 digest 归属

contracts 的 `EventEnvelope` 含 `candidate_event_digest`，但「谁计算摘要」未写明。我的 Fake EventStore 在 append 时计算（M5 侧）。若按 Claude B1 意见（生产者计算、M5 重算校验），请明确写入契约；两种都可实现，但需要唯一答案。

## 4. 观察与提醒（不阻塞）

1. **工作区 .git 目录已消失**（我开工时存在且有基线提交 `8e28602`）。我未执行任何 git 命令（按指令禁止 add/commit/checkout/reset/clean）。若是你的操作请忽略；若不是，建议用户确认——当前所有产出（含你的 contracts/orchestrator/security/storage）都处于无版本控制状态。
2. `backend/tests/contracts/` 你的 3 个测试文件在我最后一次全量运行时未执行（我只跑 `tests/model_gateway tests/transport`，按指令限定自己范围）。两侧测试目录无冲突。
3. 我创建了 `backend/src/agent_bridge/__init__.py`（包根空文档）——你的 contracts 也需要它；内容只有 docstring，可直接扩展。
4. desktop 端 `window.bridge` 是 preload 窄 IPC；若 M1 需要更多命令入口（暂停/停止/审批决定），走同样模式扩展 ipcMain.handle，不要让 renderer 直接组包信封。

## 5. 我的下一步（等待你或用户输入前不做）

- 接 3.1 裁决后切换帧构造到 contracts.ModelStreamFrame；
- 接 2.2 后把桌面端切到正式 app 入口并联调 M1 真实编排；
- 真实适配器（OpenAI 兼容 / Anthropic / Z.AI）待 Fake 闭环与 M1 集成验证后开工。
