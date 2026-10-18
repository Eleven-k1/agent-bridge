# Agent Bridge

本项目是运行在 Windows 本机的多智能体桥接原型，包含 Python 后端和本机浏览器 Web UI。公共协议基线是 M0 v0.5.1，正式事件保存在 SQLite，模型临时流帧只用于即时显示。当前支持的用户界面为 Web UI；`desktop/` 是早期桌面原型，不作为运行入口或后续功能开发目标。

## 启动入口

- 首次运行或前端源码更新后，在 `web-ui/client/` 执行 `npm.cmd install` 和 `npm.cmd run build`，生成服务使用的 `web-ui/dist/` 静态资源。
- Web UI：双击 `web-ui/start-web.vbs`，访问 `http://127.0.0.1:5173`；双击 `web-ui/stop-web.vbs` 停止。启动脚本会复用已经健康的本项目服务。
- Web UI 提供三栏任务工作台：中央区域提交任务并查看流式文本与正式事件，左侧提供 Fake 沙盒，右侧显示模型、连接、命令、事件计数和错误。
- 开发时可在项目根目录执行 `backend\.venv\Scripts\python.exe -m agent_bridge.app` 单独启动后端。

Web UI 只连接本机回环地址。真实模型固定经 `http://127.0.0.1:8080` 中转；浏览器不接触 API Key、TCP 凭据、项目文件或 Shell 接口。

当前状态、测试证据和已知限制见 `docs/current-status.md`。历史设计和实施计划位于 `docs/superpowers/`，不作为当前运行规则。
