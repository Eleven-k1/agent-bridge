# 本机 Web UI

双击桌面的 `Agent Bridge Web UI` 快捷方式，或双击 `start-web.vbs` / `start-web.bat`，访问 `http://127.0.0.1:5173`。脚本使用项目虚拟环境的 `pythonw.exe`，不会弹出控制台；已有健康服务会直接复用。

双击 `stop-web.vbs` / `stop-web.bat` 停止经过项目身份校验的服务。页面只允许模型刷新、真实模型任务、Fake 模型任务和 Fake Tool；真实模型固定使用 `http://127.0.0.1:8080` 中转。

Web 服务测试：`backend\.venv\Scripts\python.exe -m pytest web-ui/tests -q -p no:cacheprovider`。

构建产物位于 `web-ui/dist/`。当前仍不执行真实文件、Shell 或 Git 操作。
