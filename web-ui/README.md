# 本机 Web UI

当前唯一支持的用户界面是浏览器 Web UI。首次运行或客户端源码有更新后，在 `client/` 执行 `npm.cmd install` 和 `npm.cmd run build`，构建产物写入 `dist/`。双击 `start-web.vbs` / `start-web.bat` 启动服务，再用浏览器访问 `http://127.0.0.1:5173`。脚本使用项目虚拟环境的 `pythonw.exe`，不会弹出控制台；已有健康服务会直接复用。

双击 `stop-web.vbs` / `stop-web.bat` 停止经过项目身份校验的服务。工作台支持自定义任务内容、模型发现和手动模型名称、真实模型任务、Fake 模型任务和 Fake Tool。流式帧与持久化正式事件分开展示；连接恢复后按事件游标补发 TASK 和 LOCAL_CONFIG 事件。真实模型固定使用 `http://127.0.0.1:8080` 中转。

客户端测试和构建：在 `client/` 执行 `npm.cmd test`、`npm.cmd run typecheck`、`npm.cmd run build`。Web 服务测试：在仓库根目录执行 `backend\.venv\Scripts\python.exe -m pytest web-ui/tests -q -p no:cacheprovider`。

构建产物位于 `web-ui/dist/`。页面 HTML 使用 `Cache-Control: no-store`；更新后重新加载即可获取新的哈希资源。当前仍不执行真实文件、Shell 或 Git 操作。
