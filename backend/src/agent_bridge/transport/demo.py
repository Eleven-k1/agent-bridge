"""Fake 闭环组装与开发联调入口。

组装：FakeProvider + ModelGateway（M2）+ LoopbackServer / 内存登记与事件库（M6）。
命令 FAKE_MODEL_RUN / FAKE_MODEL_CANCEL 是本 Fake 路由器定义的测试语义，
属于「应用命令路由器」位置的业务组件；transport 服务本身不解释它们。
AI-A 的正式编排入口（app.py / M1）就位后，桌面端切换到正式入口，
本模块保留为协议联调工具。

CLI 用法（桌面端 Electron main 调用）：
    python -m agent_bridge.transport.demo
启动后向 stdout 输出一行 JSON：
    {"event": "listening", "port": <int>, "token": "<临时会话凭据>", ...}
父进程（Electron）从 stdout 读取端口与凭据；父进程退出导致 stdin EOF 时，
本进程安全关闭服务并退出，不遗留监听端口。
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from typing import Any
from uuid import uuid4

from agent_bridge.model_gateway.fake import FakeProvider, FakeScenario
from agent_bridge.model_gateway.gateway import GatewayCandidate, GatewayFrame, ModelGateway
from agent_bridge.model_gateway.types import ModelRequest, TimeoutPolicy
from agent_bridge.transport.memory import InMemoryCommandRegistry, InMemoryEventStore
from agent_bridge.transport.server import LoopbackServer

#: demo 使用的固定任务上下文（测试与联调约定）
DEMO_TASK_ID = "task-1"
DEMO_RUN_ID = "run-1"

_DEFAULT_TIMEOUT = TimeoutPolicy(
    connect_timeout_s=5.0,
    first_token_timeout_s=5.0,
    stream_idle_timeout_s=10.0,
)


class _ServerPorts:
    """把 server 的广播端口后置绑定给路由器，避免构造期循环依赖。"""

    def __init__(self) -> None:
        self._server: LoopbackServer | None = None

    def bind(self, server: LoopbackServer) -> None:
        self._server = server

    async def broadcast_frame(self, frame: dict[str, Any]) -> None:
        if self._server is not None:
            await self._server.broadcast_frame(frame)

    async def publish_event(self, candidate: dict[str, Any]) -> dict[str, Any] | None:
        if self._server is not None:
            return await self._server.publish_event(candidate)
        return None


class FakeModelRouter:
    """Fake 应用命令路由器：仅识别测试语义的两条命令。

    - FAKE_MODEL_RUN：按 payload 驱动一次 FakeProvider 模型操作；
    - FAKE_MODEL_CANCEL：请求取消指定 operation_id。
    其余命令静默忽略（真实实现按 scope_type 转交 M1 / M2 配置服务 /
    角色与设置配置服务）。
    """

    def __init__(self) -> None:
        self._ports = _ServerPorts()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._gateways: dict[str, ModelGateway] = {}
        self.last_operation_id: str | None = None

    def bind(self, server: LoopbackServer) -> None:
        self._ports.bind(server)

    async def handle_command(self, command: dict[str, Any]) -> None:
        command_type = command.get("command_type")
        if command_type == "FAKE_MODEL_RUN":
            self._start_model_run(command)
        elif command_type == "FAKE_MODEL_CANCEL":
            operation_id = (command.get("payload") or {}).get("operation_id")
            if operation_id:
                gateway = self._gateways.get(operation_id)
                if gateway is not None:
                    await gateway.cancel(operation_id)
        # 其他命令：Fake 路由器不处理

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._gateways.clear()

    def _start_model_run(self, command: dict[str, Any]) -> None:
        payload = command.get("payload") or {}
        text_chunks = payload.get("text_chunks") or ["这是一段默认的假模型输出。"]
        chunk_delay_s = float(payload.get("chunk_delay_s", 0.01))

        operation_id = f"op-{uuid4().hex[:12]}"
        self.last_operation_id = operation_id
        gateway = ModelGateway(
            FakeProvider(
                FakeScenario(text_chunks=list(text_chunks), chunk_delay_s=chunk_delay_s)
            )
        )
        self._gateways[operation_id] = gateway

        request = ModelRequest(
            operation_id=operation_id,
            model="fake-model",
            messages=[{"role": "user", "content": "fake task"}],
            system=None,
            timeout=_DEFAULT_TIMEOUT,
            task_id=command.get("task_id") or DEMO_TASK_ID,
            run_id=command.get("run_id") or DEMO_RUN_ID,
            agent_id="fake-agent",
            max_retries=1,
        )
        task = asyncio.create_task(self._drive_operation(operation_id, gateway, request))
        self._tasks[operation_id] = task

    async def _drive_operation(
        self, operation_id: str, gateway: ModelGateway, request: ModelRequest
    ) -> None:
        try:
            async for item in gateway.execute(request):
                if isinstance(item, GatewayFrame):
                    await self._ports.broadcast_frame(item.frame)
                elif isinstance(item, GatewayCandidate):
                    await self._ports.publish_event(item.event)
        finally:
            self._tasks.pop(operation_id, None)
            self._gateways.pop(operation_id, None)


class DemoApp:
    """Fake 闭环应用：供集成测试与桌面端联调使用。"""

    def __init__(self) -> None:
        self.router = FakeModelRouter()
        self.registry = InMemoryCommandRegistry()
        self.event_store = InMemoryEventStore()
        self.server = LoopbackServer(
            router=self.router,
            registry=self.registry,
            event_store=self.event_store,
        )
        self.router.bind(self.server)

    async def start(self) -> None:
        await self.server.start()

    async def aclose(self) -> None:
        await self.server.aclose()

    @property
    def port(self) -> int:
        return self.server.port

    @property
    def token(self) -> str:
        return self.server.token

    @property
    def last_operation_id(self) -> str | None:
        return self.router.last_operation_id


def build_demo_app() -> DemoApp:
    """构造一个未启动的 Fake 闭环应用。"""
    return DemoApp()


async def _amain() -> None:
    app = build_demo_app()
    await app.start()
    notice = {
        "event": "listening",
        "port": app.port,
        "token": app.token,
        "transport": "ndjson-tcp-v1",
    }
    sys.stdout.write(json.dumps(notice) + "\n")
    sys.stdout.flush()

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _watch_stdin_eof() -> None:
        try:
            sys.stdin.buffer.read()
        except Exception:
            pass
        loop.call_soon_threadsafe(stop.set)

    threading.Thread(target=_watch_stdin_eof, daemon=True).start()
    try:
        await stop.wait()
    finally:
        await app.aclose()


def main() -> None:
    asyncio.run(_amain())


if __name__ == "__main__":
    main()
