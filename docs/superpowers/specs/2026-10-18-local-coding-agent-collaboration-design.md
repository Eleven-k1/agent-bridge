# 本地编程 Agent 协作设计

**日期：** 2026-10-18
**状态：** 用户已确认方向，当前实现依据
**界面：** Windows 本机浏览器 Web UI

## 目标

让 Codex CLI、Claude Code 等真实本地编程 Agent 参与同一软件开发任务。Agent 必须由本机工具链启动，使用其官方命令行协议和已登录会话；多个 Agent 的分工、实时输出、结论、交接、执行者和独立审查在 Web UI 可见。普通模型 API 的多个响应、Fake Provider 和静态模拟消息不算协作。

## 已确认的产品行为

1. Web UI 检测本机 Agent 的实际安装和可调用状态，用户可以选择本次参与者及角色。UI 不宣称未安装或未适配的工具已经接入。
2. 参与 Agent 并行执行只读分析，并分别公开输出。协调 Agent 汇总可执行方案、分歧和风险，正式事件记录各阶段及参与者。
3. 用户批准方案后，系统才启动唯一写入 Agent。执行结束后由不同的只读 Agent 独立审查；用户可查看审查发现与 Git diff。
4. 任一运行可取消。服务关闭、超时或 Agent 异常退出时，终止对应子进程树、记录失败并释放资源；重连按 SQLite 事件游标恢复协作时间线。任务终态时，Web UI 汇总各 Agent 的分析结论、协调方案、执行分支与 Git diff、独立审查摘要及失败诊断；整页重载通过回放正式 TASK 事件重建汇报，临时流式输出不纳入历史汇报。
5. Agent 子进程只由 Python 后端启动。写入任务须由服务端在项目 Git 仓库中创建专属 worktree，固定基线为开始时的 `HEAD`，并在该 worktree 中执行；不把主工作区未提交内容隐式复制给 Agent。任务结束后保留分支和 worktree，UI 显示基线、分支、状态及差异。浏览器不能指定任意命令、可执行文件、参数、环境变量或工作目录。
6. Web UI 不接触、不保存 Agent 的登录令牌或 API 凭据。所有进程参数使用 argv，不拼接 Shell 命令字符串。读操作阶段使用 Agent 自身的只读权限配置；Claude Code 仅开放 `Read`、`Glob`、`Grep` 等读取工具、禁用 MCP，并在目标版本做写入拒绝验收。该权限配置不是操作系统级文件隔离。Agent 可读取所选工作区中的项目文件，启动前 UI 必须明确提示不要将密钥、凭据或不应交给模型的文件纳入工作区。
7. Windows 下不得通过 PowerShell 函数、shell alias 或 `.cmd` 命令字符串启动 Agent。后端需将受支持的 npm shim 解析为已验证的原生 Node 可执行文件与固定 CLI 脚本，并使用操作系统进程组/Job Object 管理整棵子进程树；无法安全解析时将 Agent 标记为不可用。

## 首版 Agent 支持

采用独立的 provider adapter 注册表，不把 CLI Agent 塞进单模型 `ModelGateway`。

- Codex CLI：使用 `codex exec --json` 的 JSONL 事件流；只读分析使用 `--sandbox read-only`，执行使用 `--sandbox workspace-write`。
- Claude Code：使用 `claude -p --output-format stream-json --verbose --include-partial-messages` 的 JSON 事件流；只读分析使用 `dontAsk` 和显式只读工具白名单，禁用 MCP；不把该 CLI 配置宣称为 OS 级沙盒。Claude 写入适配首版关闭。
- OpenCode、DeepSeek Harness 和 ZCode 显示为未适配或未安装状态。DeepSeek Harness 提供 SDK runtime，不视为兼容简单 CLI 的同一种执行器；后续适配必须通过独立实现和真实契约测试。

各 Agent 特有事件归一化为内部事件类型和输出片段；保留 provider 原始会话 ID 与退出码，但不持久化认证信息。正式任务使用开始时 `HEAD` 创建的独立分支/worktree；主工作区保持原样，主工作区既有未提交改动不包含在执行基线内。worktree 与分支默认保留，用户可在 UI 查看差异并自行决定后续处置。真实运行时发现、调用和事件格式必须在目标机器验证，不得用 fixture 结果冒充真实 Agent 验收。

## 事件与恢复

Agent 协作事件存为 `TASK` 作用域 SQLite 正式事件，包括会话创建、参与者检测、阶段启动、Agent 状态、方案准备/批准、执行结果、审查结论、取消和任务终态。Web UI 使用这些事件重建当前任务汇报，展示分析摘要、协调方案、执行与 Git diff、审查结果及失败原因；页面刷新后从正式事件游标恢复。实时 stdout/stderr 片段作为临时传输消息，不写入事件日志，也不作为汇报历史；正式摘要和错误会脱敏、限长后持久化。M1 重放必须明确识别新增 Agent 事件，不得因未知 Agent 事件阻断恢复。

## 失败处理

- 找不到可安全启动的二进制、CLI 未登录、协议不兼容或项目不是 Git 仓库：在开始运行前显示该 Agent/工作区不可用及可行动诊断；不得悄悄回退到模型 API。
- 子进程非零退出、无效 JSONL、启动超时、运行超时：对应 Agent 标记失败，其他只读分析可以完成；没有协调结果时不进入批准阶段。
- 用户取消、服务退出：尝试优雅终止，超时后终止进程树，正式事件记录实际结果。
- Agent 输出中可能含敏感信息：stdout 展示和持久化摘要经过现有 `Redactor`，每条片段和正式载荷设字节上限。

## 明确不包含

- 伪造客户端或驱动桌面 GUI、复用其他应用内存/会话文件、读取 API key 或 Cookie。
- 本地 CLI 的通用插件生态、远端 Agent、团队多用户、云端调度和多项目并发。
- 第一版自动提交 Git、推送远端或绕过 UI 批准。
- 未实际安装的 OpenCode、DeepSeek Harness 或 ZCode 的完成态声明。

## 官方接口依据

- Claude Code headless：<https://code.claude.com/docs/en/headless>；CLI reference：<https://code.claude.com/docs/en/cli-reference>
- Codex CLI reference：<https://developers.openai.com/codex/cli/reference>
- Agent Client Protocol repository/license/status：<https://github.com/agentclientprotocol/agent-client-protocol>
- OpenCode CLI：<https://opencode.ai/docs/cli/>；官方权限模型：<https://opencode.ai/docs/permissions/>
- DeepSeek Harness SDK：<https://github.com/deepseek-ai/deepseek-harness>；<https://deepseek-harness.github.io/deepseek-harness/guide/python-sdk>
- ZCode repository：<https://github.com/zai-org/ZCode>

本机检查到 `codex-cli 0.163.0` 和 `Claude Code 2.1.287`；当前没有找到 OpenCode、DeepSeek Harness 或 ZCode 可执行入口。正式发布时应按实际安装版本重新核对接口。
