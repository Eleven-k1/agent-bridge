# Zcode 负责模块：M2 模型网关、M6 本地通信、M7 桌面工作台

负责人：AI-C（Zcode）  
状态：固定本地 OpenAI Relay 已接入 M2/M6/M7 正式应用链路  
协议依据：M0 v0.5.1 最终版

## 模块结构

```text
backend/src/agent_bridge/
├── app.py                  # 正式装配和 CLI 入口
├── model_gateway/          # Provider 抽象、FakeProvider、超时/取消/错误归一化
└── transport/              # NDJSON 回环服务、鉴权、命令登记、发布与补发

desktop/
├── src/main/               # Python 子进程、TCP 连接、Electron 生命周期、窄 IPC
├── src/preload/            # renderer 可用的最小接口
└── src/renderer/           # React 工作台与事件驱动状态投影
```

`agent_bridge.transport.demo` 只保留作 M2/M6 独立测试入口。Electron 正式启动模块是 `agent_bridge.app`。

## 启动

```powershell
cd desktop
npm install
npm run dev
```

Electron main 进程启动 `python -m agent_bridge.app`，从 stdout 首行读取动态端口和临时会话凭据，再连接 `127.0.0.1`。凭据只存在于 main 进程内存，不进入 renderer，也不落盘。窗口关闭时先通过 stdin EOF 请求后端优雅退出，超时后才清理进程树。

正式后端也可单独启动：

```powershell
cd backend\src
python -m agent_bridge.app
```

默认数据库为 `%LOCALAPPDATA%\AgentBridge\agent-bridge.sqlite3`；设置 `AGENT_BRIDGE_DB_PATH` 可覆盖路径。

## M2 模型网关

`ModelProvider` 统一 `stream()`、`request_cancel()` 与 `close()`。`ModelGateway` 支持三段超时、首字节前有限重试、取消后丢弃增量、供应商错误归一化和凭据样式消息脱敏。

M2 原始网关对象保留便于内部测试的诊断扩展。正式 `app.py` 在 M6 边界只转发冻结的八个 `ModelStreamFrame` 字段；即时显示文本放在传输消息顶层 `content_delta`。M7 优先读取正式字段，同时兼容旧 demo 的帧内文本。

当前 Provider 包含 `FakeProvider` 和固定本地 `OpenAICompatibleProvider`；不支持远程 Provider、Anthropic 或 Z.AI。

## M6 本地通信

`LoopbackServer` 使用 NDJSON over TCP，只监听 `127.0.0.1` 和动态端口。连接首条消息必须携带临时凭据。命令经过基础格式校验和 M5 持久化幂等登记后才交给应用路由器；M6 不解释业务命令。

正式事件先经 M5 持久化再发布。`resume` 只按作用域序号补发正式事件，不补发临时流帧。正式线消息包括 auth、command、command_result、event、model_frame、resume 与连接状态。

## M7 桌面端

renderer 只能使用 preload 暴露的 `runFakeTask`、`runFakeTool`、`getServiceInfo`、`onMessage` 和 `onStateChange`，不能访问凭据、TCP、Python 子进程、数据库、Shell 或项目文件。

状态投影分成两层：`streamedText` 是临时显示层，重连时清空；`recordedText` 与终态来自 M5 正式事件，作为恢复依据。终态只能由 `MODEL_OPERATION_COMPLETED`、`FAILED` 或 `CANCELLED` 正式事件设置。

## 测试

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests\model_gateway tests\transport -q -p no:cacheprovider

cd ..\desktop
npm test
npm run typecheck
npm run build
```

桌面端端到端连接测试明确启动 `agent_bridge.app`，验证正式帧形状、顶层流式文本、鉴权、命令回执、M5 正式事件恢复、游标重连和临时帧不补发。

## 限制

- 真实 Provider 仅支持固定本地 OpenAI 兼容中转，不支持远程地址、HTTPS、API Key、Anthropic 或 Z.AI。
- 未实现协议版本协商和不兼容阻止页。
- 未实现真实文件、Shell、Git 工具和多智能体任务编排。
- 未做真实 Electron 窗口人工冒烟及 Windows 安装包验收。
- `transport.demo` 的内存存储和扩展帧只服务模块测试，不代表正式应用协议。

## 固定本地 OpenAI Relay（当前实现）

当前真实 Provider 为 `OpenAICompatibleProvider`，固定连接 `http://127.0.0.1:8080`。应用支持 `LIST_LOCAL_MODELS` 和 `REAL_MODEL_RUN`：前者读取 `/v1/models`，失败时允许手动填写模型名；后者请求 `/v1/chat/completions`，支持 SSE 与非流式响应。请求不携带 Authorization，Provider 不写数据库、不访问项目文件。正式事件使用 SQLite 持久化，服务重启时恢复 M1 任务状态并补发未发布事件。

当前验证以项目虚拟环境执行：后端 233 项、Web UI 6 项、桌面端 23 项测试通过，TypeScript 类型检查和 Electron 构建通过。
