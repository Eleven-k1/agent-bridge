# Agent Bridge

本项目是运行在 Windows 本机的多智能体桥接原型，包含 Python 后端、Electron 桌面端和独立 Web UI。公共协议基线是 M0 v0.5.1，正式事件保存在 SQLite，模型临时流帧只用于即时显示。

## 启动入口

桌面端可输入自定义任务内容，选择已发现的模型或手动填写模型名后提交真实任务。真实请求能否完成还取决于本机 Relay 的认证配置；当前状态见 `docs/current-status.md`。

- Web UI：双击 `web-ui/start-web.vbs`，访问 `http://127.0.0.1:5173`；双击 `web-ui/stop-web.vbs` 停止。启动脚本会复用已经健康的本项目服务。
- 桌面端：在 `desktop/` 执行 `npm.cmd install` 和 `npm.cmd run dev`。桌面端主进程会启动 `python -m agent_bridge.app`，需要 Python 后端环境位于 PATH 或使用项目虚拟环境。界面采用三栏工作台：中央执行区负责任务输入和结果流，左侧提供工作区与 Fake 沙盒入口，右侧检查栏集中模型、命令状态、事件计数和错误信息。
- 后端：在项目根目录执行 `backend\.venv\Scripts\python.exe -m agent_bridge.app`。

Web UI 和桌面端只连接本机回环地址。真实模型固定经 `http://127.0.0.1:8080` 中转；浏览器和 renderer 不接触 API Key、TCP 凭据、项目文件或 Shell 接口。

当前状态、测试证据和已知限制见 `docs/current-status.md`。历史设计和实施计划位于 `docs/superpowers/`，不作为当前运行规则。
