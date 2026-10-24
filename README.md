# Agent Bridge

Agent Bridge 是一个运行在 Windows 本机上的多智能体编程工作台。它把本机安装的 Codex CLI、Claude Code 等编程 Agent 接入同一个浏览器 Web UI，让用户可以查看分析、批准方案、监督写入、阅读 Git diff，并在任务结束后获得一份可恢复的正式汇报。

项目的产品入口是 `http://127.0.0.1:5173/`。`desktop/` 目录保留早期 Electron 原型，不属于当前支持的产品工作流。

## 能力概览

- 发现本机 CLI Agent，显示安装状态、适配状态和可用角色。
- 使用两个到四个只读 Agent 并行分析同一项目。
- 由协调 Agent 汇总分析结果，生成需要用户批准的执行方案。
- 只允许 Codex 在独立 Git worktree 中写入，主工作区不会被隐式修改。
- 使用不同的只读 Agent 独立审查 writer 产生的差异。
- 在 Web UI 中实时显示临时 Agent 输出和正式事件时间线。
- 任务完成、失败或取消后显示持久化任务汇报：分析结论、协调方案、分支、worktree、Git diff、审查结论和失败诊断。
- 页面刷新或 WebSocket 重连后，从 SQLite 正式 TASK 事件恢复汇报；临时流式输出不会伪装成历史记录。
- 保留本地模型 Fake Provider 工作流，作为独立的开发与协议验证入口。

## 工作流

```text
浏览器 Web UI
      | loopback HTTP + WebSocket
      v
Python Web 服务与 BridgeSession
      | authenticated NDJSON
      v
Agent Bridge 后端
      +--> SQLite 正式事件存储
      +--> Codex CLI / Claude Code CLI
      +--> Git worktree 与 diff
```

一次协作任务依次执行：发现 Agent；并行只读分析；生成并批准方案；创建独立 worktree；Codex 写入；计算脱敏 diff；不同 Agent 审查；写入终态事件并在 Web UI 汇报。

## 支持矩阵

| Agent | 发现 | 分析 | 协调 | 写入 | 审查 | 当前说明 |
| --- | --- | --- | --- | --- | --- | --- |
| Codex CLI | 支持 | 支持 | 支持 | 支持 | 支持 | Windows 下直接启动已验证的 Node 与官方 JS 入口 |
| Claude Code | 支持 | 支持 | 支持 | 不支持 | 支持 | 只读工具白名单；写入适配尚未开放 |
| OpenCode | 显示 | 不支持 | 不支持 | 不支持 | 不支持 | 尚无专用适配器 |
| DeepSeek Harness | 显示 | 不支持 | 不支持 | 不支持 | 不支持 | SDK/runtime 不能直接当作 CLI 适配 |
| ZCode | 显示 | 不支持 | 不支持 | 不支持 | 不支持 | 尚无专用适配器 |

“显示”表示 UI 会诚实报告未安装或未适配，不表示该 Agent 已经参与协作。

## 快速开始

### 环境要求

- Windows 10/11、Python 3.11+、Node.js 22+ 和 Git。
- 目标项目必须是 Git 仓库且存在 `HEAD`。
- 至少两个已登录且已适配的 CLI Agent；完整协作需要 Codex 作为 writer，以及另一个不同的只读 Agent 作为 reviewer（当前可用组合为 Codex + Claude Code）。

### 安装与启动

```powershell
cd web-ui\client
npm.cmd ci
npm.cmd run build
cd ..\..
wscript.exe .\web-ui\start-web.vbs
```

打开 `http://127.0.0.1:5173/`。停止服务：

```powershell
wscript.exe .\web-ui\stop-web.vbs
```

后端测试环境使用 `backend\.venv`。默认数据库是 `web-ui\agent-bridge.sqlite3`，可用 `AGENT_BRIDGE_DB_PATH` 覆盖。

## Web UI 使用

1. 在“多 Agent 协作”中刷新 Agent 库，确认安装和适配状态。
2. 输入任务目标，选择至少两个分析 Agent、一个协调 Agent、Codex writer 和不同的 reviewer。
3. 确认允许 Agent 读取项目文件，启动协作。
4. 阅读临时输出和正式事件。出现协调方案后，检查步骤、风险和验证方式。
5. 批准方案后才会创建 worktree 并启动 Codex；不接受方案时取消协作。
6. 终态出现后，在“任务完成汇报”中查看分析、方案、执行分支、Git diff、审查和失败诊断。

任务汇报只使用正式 TASK 事件构建。页面刷新后按游标恢复事件，前端按作用域序号缓冲乱序消息；低序号事件补齐前不会显示不完整终态汇报。临时 stdout/stderr 只用于当前连接，不写入历史汇报。

## Git 与 worktree 规则

- 所有代码、文档和配置通过 Git 管理。
- writer 以开始时的 `HEAD` 为基线，在 `agent-bridge/<task>-<run>-<suffix>` 分支和独立 worktree 中工作。
- 主工作区既有未提交改动不会复制给 writer。
- writer 不自动 commit、不 push；Web UI 展示分支、基线、worktree 和脱敏 diff，后续处置由用户决定。
- 修改完成后运行测试、`git diff --check` 和临时残留检查，再提交清晰的 Git 版本。

## 安全边界

- 浏览器不能指定任意命令、可执行文件、参数、环境变量或工作目录。
- Agent 子进程只由 Python 后端通过 argv 启动。
- 分析和审查使用只读 CLI 配置；Claude Code 使用 `Read`、`Glob`、`Grep`、`--safe-mode` 和严格 MCP 配置。
- Web UI 不读取或保存 CLI 登录令牌、API key 或 Cookie。
- 正式摘要、错误和 diff 经过脱敏并限制长度；取消、超时和服务关闭会尝试终止受管理的子进程树。

这些是应用层约束，不等同于操作系统级沙盒。不要把密钥、凭据或不应交给模型的文件放入可读工作区。

## 开发与验证

```powershell
cd web-ui\client
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
cd ..\..
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=backend\.pytest-run-tmp
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=backend\.pytest-run-web-server web-ui\tests
git diff --check
```

当前结果、真实 CLI smoke check 和未完成验收见 [docs/current-status.md](docs/current-status.md)。架构见 [docs/architecture.md](docs/architecture.md)，操作排障见 [docs/operations.md](docs/operations.md)。

## 项目文档

- [当前状态与验收记录](docs/current-status.md)
- [系统架构与数据流](docs/architecture.md)
- [运行、配置与故障排查](docs/operations.md)
- [贡献指南](CONTRIBUTING.md)
- [安全策略](SECURITY.md)
- [许可证](LICENSE)
- [第三方依赖声明](THIRD-PARTY-NOTICES.md)
- [本地 CLI 协作设计](docs/superpowers/specs/2026-10-18-local-coding-agent-collaboration-design.md)
- [Web UI 说明](web-ui/README.md)
- [公共协议与数据契约](M0-公共协议与数据契约-v0.5.1-最终版.txt)

## 许可证、依赖与当前限制

本项目使用 MIT 许可证，详见 [LICENSE](LICENSE)。直接依赖和第三方软件说明见 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。Claude Code 最近一次真实请求收到上游 HTTP 503，完整双 Agent 端到端验收需要在上游可用时重跑。OpenCode、DeepSeek Harness、ZCode 需要独立 adapter；当前 writer 固定为 Codex，不提供自动 commit、远程 push、团队多用户、云端调度或多项目并发。
