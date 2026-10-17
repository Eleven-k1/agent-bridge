# 本机 Web UI 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers'subagent-driven-development (recommended) or superpowers'executing-plans to implement this plan task-by-task.

**目标：** 将现有 React 工作台交付为只监听本机的浏览器 Web UI，并提供双击隐藏启动和停止脚本。

**架构：** 新增 Python 本机 Web 适配服务，监听 127.0.0.1:5173，提供静态 React 构建产物和 WebSocket 桥接。每个浏览器会话通过现有 TCP/NDJSON 客户端连接 agent_bridge.app，只转发允许的模型刷新、真实任务和 Fake 命令；业务状态机、ModelGateway、SQLite 和安全边界继续由后端负责。

**技术栈：** Python 3.11+、现有 agent_bridge、React 19、TypeScript、Vite、Windows 隐藏进程启动。

## 全局约束

- Web 服务只监听 127.0.0.1:5173，不接受局域网访问。
- 浏览器不接触 TCP 会话凭据、API Key、项目文件、Shell 或工具执行接口。
- 真实 Provider 固定连接 http://127.0.0.1:8080，不发送 Authorization。
- 浏览器消息只允许 LIST_LOCAL_MODELS、REAL_MODEL_RUN、FAKE_MODEL_RUN、FAKE_TOOL_RUN。
- 启动和停止脚本不得主动显示黑色控制台窗口。
- Electron 入口保留，现有回归测试必须继续通过。
- 当前目录没有 Git 仓库，不执行或声称执行 commit。

---

### 任务 1：本机 HTTP/WebSocket 适配服务

文件：新建 web-ui/server/web_server.py、web-ui/server/bridge_session.py、web-ui/server/__init__.py、web-ui/tests/test_web_server.py。

接口：WebApp(host=127.0.0.1, port=5173)；GET /health；GET /；WebSocket ws://127.0.0.1:5173/ws。

- [ ] 先写健康检查、静态首页、无效 JSON、命令白名单和断开清理的失败测试。
- [ ] 实现只绑定回环地址的 HTTP 服务和 WebSocket 会话管理。
- [ ] 每个会话连接现有 agent_bridge.app，凭据只保存在服务端内存。
- [ ] 校验浏览器命令字段，禁止任意 command_type、地址、文件路径和凭据字段透传。
- [ ] 运行聚焦测试并确认通过。

### 任务 2：浏览器 React 构建与 UI 入口

文件：新建 web-ui/client/、web-ui/client/web-bridge.ts、web-ui/client/web-app.test.tsx。

- [ ] 测试连接、刷新模型、手动模型回退、真实任务、流式帧、正式事件和断线清理。
- [ ] 实现浏览器 WebSocket 客户端和任务状态。
- [ ] 提供固定中转地址提示、刷新模型、模型选择、手动模型、真实任务和 Fake 回归按钮。
- [ ] 禁止 token、API Key、原始堆栈进入页面、URL 或 localStorage。
- [ ] 构建产物输出到 web-ui/dist。

### 任务 3：双击隐藏启动与停止

文件：新建 web-ui/start-web.bat、web-ui/stop-web.bat、web-ui/server/launcher.py、web-ui/README.md、web-ui/tests/test_launcher.py。

- [ ] 测试健康等待、重复启动、停止范围和隐藏窗口属性。
- [ ] 实现带项目标识的 launcher/PID 管理，不保存 token 或 API Key。
- [ ] start 等待 /health 后打开 http://127.0.0.1:5173。
- [ ] stop 只停止本项目服务，不影响其他 Node/Python/浏览器进程。
- [ ] 手工双击启动、验证浏览器和停止流程。

### 任务 4：回归、文档和清理

文件：修改 README.md、docs/current-status.md、docs/modules/zcode-m2-m6-m7.md、web-ui/README.md。

- [ ] 后端全量测试、桌面端测试、类型检查、Electron 构建和 Web UI 测试/构建全部使用隐藏后台方式运行。
- [ ] 更新 Web UI 启动、固定回环地址、模型发现回退和安全边界文档。
- [ ] 清理本轮临时数据库、日志、缓存、调试打印和一次性脚本。
- [ ] 搜索 API Key、Authorization、token 输出和原始响应日志。
- [ ] 最后停止项目进程，确认不再弹出黑色窗口。

## 计划自检

任务覆盖 Web 服务、浏览器 UI、启动脚本、测试、文档和清理；没有 TBD、TODO 或未定义接口；固定地址、端口、白名单命令和 UI 状态字段保持一致。
