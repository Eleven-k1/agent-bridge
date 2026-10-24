# Browser Web UI

`web-ui/` 是 Agent Bridge 的正式产品入口，提供浏览器工作台、WebSocket 会话桥接和本地 Python 服务启动器。桌面 Electron 原型不参与当前运行链路。

## 运行

从仓库根目录执行：

```powershell
cd web-ui\client
npm.cmd ci
npm.cmd run build
cd ..\..
wscript.exe .\web-ui\start-web.vbs
```

访问 `http://127.0.0.1:5173/`。停止服务：

```powershell
wscript.exe .\web-ui\stop-web.vbs
```

启动器会启动 `backend\.venv\Scripts\pythonw.exe`，后端在回环地址创建认证 NDJSON 服务，Web 服务只允许本机浏览器连接。静态构建产物位于 `web-ui/dist/`，由 Git 忽略。

## 页面区域

- **工作台**：运行传统本地模型任务，展示实时流和正式事件。
- **本地 CLI Agents**：显示 Codex、Claude Code、OpenCode、DeepSeek Harness、ZCode 的发现和适配状态。
- **多 Agent 协作**：配置并行分析、协调、Codex writer 和独立 reviewer。
- **临时流式层**：展示当前连接中的 Agent 输出，断线后清空。
- **正式事件层**：展示持久化事件，支持重连恢复。
- **任务完成汇报**：在终态展示分析、方案、执行、Git diff、审查和失败诊断。

## 前端开发

```powershell
cd web-ui\client
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
```

测试位于 `client/src/app.test.tsx`，覆盖 Agent 选择、批准门禁、writer/reviewer 交接、取消、失败汇报、刷新恢复和乱序事件缓冲。

## HTTP 与 WebSocket

| 路径 | 方法 | 作用 |
| --- | --- | --- |
| `/` | GET | 返回 Vite 构建的 `index.html` |
| `/assets/*` | GET | 返回构建资源 |
| `/health` | GET | 返回 Web 服务健康状态 |
| `/ws` | WebSocket | 浏览器与后端 BridgeSession 的本地桥接 |

浏览器发送 `resume` 或受限 `command` 消息。Web 服务只接受白名单命令，并在进入后端前规范化 payload；业务路由、事件落盘和 CLI 子进程管理均由 Python 后端负责。

## 相关文档

- [项目总览](../README.md)
- [架构说明](../docs/architecture.md)
- [操作与故障排查](../docs/operations.md)
- [当前状态](../docs/current-status.md)
