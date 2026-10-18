# Zcode 负责模块：M2 模型网关、M6 本地通信、M7 Web 工作台

负责人：AI-C（Zcode）  
状态：固定本地 OpenAI Relay 已接入 M2/M6/M7 正式应用链路  
协议依据：M0 v0.5.1 最终版

## 模块结构

```text
backend/src/agent_bridge/
├── app.py                  # 正式装配和 CLI 入口
├── model_gateway/          # Provider 抽象、FakeProvider、超时/取消/错误归一化
└── transport/              # NDJSON 回环服务、鉴权、命令登记、发布与补发

web-ui/
├── client/src/             # React 工作台、命令控制和事件状态投影
├── server/                 # HTTP/WebSocket 入口、命令校验和 M6 通信适配
└── dist/                   # Vite 生成、由本机 Web 服务提供的静态资源
```

`agent_bridge.transport.demo` 只保留作 M2/M6 独立测试入口。正式后端启动模块是 `agent_bridge.app`，受支持的用户入口是浏览器 Web UI。

## 启动

首次运行或客户端源码更新后，在 `web-ui/client/` 执行 `npm.cmd install` 和 `npm.cmd run build`。之后从项目根目录启动本机 Web UI：

```powershell
wscript.exe .\web-ui\start-web.vbs
```

浏览器访问 `http://127.0.0.1:5173/`。停止服务：

```powershell
wscript.exe .\web-ui\stop-web.vbs
```

启动脚本使用项目虚拟环境中的 `pythonw.exe`，通过项目启动器检查服务身份并复用健康实例。静态 HTML 设置 `Cache-Control: no-store`；前端源码变更需要重新构建，再刷新浏览器。

正式后端也可单独启动：

```powershell
backend\.venv\Scripts\python.exe -m agent_bridge.app
```

默认数据库为 `%LOCALAPPDATA%\AgentBridge\agent-bridge.sqlite3`；设置 `AGENT_BRIDGE_DB_PATH` 可覆盖路径。

## M2 模型网关

`ModelProvider` 统一 `stream()`、`request_cancel()` 与 `close()`。`ModelGateway` 支持三段超时、首字节前有限重试、取消后丢弃增量、供应商错误归一化和凭据样式消息脱敏。

M2 原始网关对象保留便于内部测试的诊断扩展。正式 `app.py` 在 M6 边界只转发冻结的八个 `ModelStreamFrame` 字段；即时显示文本放在传输消息顶层 `content_delta`。Web UI 分开展示临时流帧和持久化正式事件；模型终态只由正式事件确定。

当前 Provider 包含 `FakeProvider` 和固定本地 `OpenAICompatibleProvider`；不支持远程 Provider、Anthropic 或 Z.AI。

## M6 本地通信

`LoopbackServer` 使用 NDJSON over TCP，只监听 `127.0.0.1` 和动态端口。连接首条消息必须携带临时凭据。命令经过基础格式校验和 M5 持久化幂等登记后才交给应用路由器；M6 不解释业务命令。

正式事件先经 M5 持久化再发布。`resume` 只按作用域序号补发正式事件，不补发临时流帧。正式线消息包括 auth、command、command_result、event、model_frame、resume 与连接状态。

Web UI 的 Python WebSocket 适配层保持浏览器协议精简：浏览器只发送命令类型与 payload，由服务端生成后端 envelope；恢复请求只接受 `TASK` 和 `LOCAL_CONFIG` 作用域及非负整数游标。服务端持有会话凭据，浏览器不接触 M6 TCP 端口或凭据。

## M7 Web 工作台

当前 Web UI 负责任务输入、模型发现与选择、Fake 沙盒、流式输出、正式事件和运行检查。页面只通过 `web-ui/server/` 提供的回环 HTTP/WebSocket 服务通信，不直接访问 Provider 凭据、数据库、Shell 或项目文件。

状态投影分成两层：`streamedText` 是临时显示层，断线时清空；`recordedText`、任务终态和可恢复事件来自 M5 正式事件。命令 `ACCEPTED` 只表示已登记；运行控件保持禁用，直到模型/工具正式终态事件到达或命令入口返回错误。重连后，Web UI 按事件游标恢复 `TASK` 和 `LOCAL_CONFIG` 事件。

## 测试

```powershell
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider

cd web-ui\client
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
```

当前验证结果：Python 完整测试 251 项通过，Web UI 客户端测试 5 项通过，TypeScript 类型检查和 Vite 构建通过。若 Windows 默认临时目录权限导致 pytest 无法创建 `tmp_path`，可通过 `--basetemp=.pytest-tmp` 指定仓库内临时目录。

## 限制

- 真实 Provider 仅支持固定本地 OpenAI 兼容中转，不支持远程地址、HTTPS、API Key、Anthropic 或 Z.AI。
- 未实现协议版本协商和不兼容阻止页。
- 未实现真实文件、Shell、Git 工具和多智能体任务编排。
- 安装包和真实中转认证后的人工验收仍未完成。
- `transport.demo` 的内存存储和扩展帧只服务模块测试，不代表正式应用协议。

## 固定本地 OpenAI Relay

当前真实 Provider 为 `OpenAICompatibleProvider`，固定连接 `http://127.0.0.1:8080`。应用支持 `LIST_LOCAL_MODELS` 和 `REAL_MODEL_RUN`：前者读取 `/v1/models`，失败时允许手动填写模型名；后者请求 `/v1/chat/completions`，支持 SSE 与非流式响应。请求不携带 Authorization，Provider 不写数据库、不访问项目文件。正式事件使用 SQLite 持久化，服务重启时恢复 M1 任务状态并补发未发布事件。
