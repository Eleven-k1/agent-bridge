"""transport 测试共享夹具：异步包装与 NDJSON 测试客户端。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable
from typing import Any, TypeVar

T = TypeVar("T")


def run(coro: Awaitable[T]) -> T:
    return asyncio.run(coro)


class LineClient:
    """NDJSON over TCP 测试客户端。"""

    def __init__(self, host: str, port: int, token: str) -> None:
        self.host = host
        self.port = port
        self.token = token
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def connect(self, *, authenticate: bool = True) -> list[dict[str, Any]]:
        """建立连接；默认发送 auth 并等待 auth_ok / connection_state。"""
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        received: list[dict[str, Any]] = []
        if authenticate:
            await self.send({"type": "auth", "token": self.token})
            reply = await self.recv()
            received.append(reply)
            if reply.get("type") == "auth_ok":
                # 服务端在 auth_ok 后发布连接状态通知
                state = await self.recv()
                received.append(state)
        return received

    async def send(self, message: dict[str, Any]) -> None:
        assert self._writer is not None, "client not connected"
        self._writer.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        await self._writer.drain()

    async def recv(self, timeout: float = 3.0) -> dict[str, Any]:
        assert self._reader is not None, "client not connected"
        line = await asyncio.wait_for(self._reader.readline(), timeout=timeout)
        if not line:
            raise ConnectionError("server closed the connection")
        return json.loads(line.decode("utf-8"))

    async def recv_until(
        self, predicate, timeout: float = 3.0
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """持续接收直到谓词命中；返回 (命中消息, 全部收到的消息)。"""
        seen: list[dict[str, Any]] = []
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"predicate not met; seen={seen}")
            message = await self.recv(timeout=remaining)
            seen.append(message)
            if predicate(message):
                return message, seen

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            self._writer = None
            self._reader = None


def make_command(
    *,
    command_type: str = "TEST_ECHO",
    scope_type: str = "LOCAL_CONFIG",
    command_id: str = "cmd-1",
    idempotency_key: str = "idem-1",
    digest: str = "digest-1",
    payload: dict | None = None,
    **extra,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "protocol_version": "0.5.1",
        "scope_type": scope_type,
        "scope_id": "task-1" if scope_type == "TASK" else "config-scope",
        "command_id": command_id,
        "command_idempotency_key": idempotency_key,
        "command_request_digest": digest,
        "issuer": "local-user",
        "command_type": command_type,
        "payload": payload or {},
        "issued_at": "2026-08-30T00:00:00+00:00",
    }
    if scope_type == "TASK":
        data.update(task_id="task-1", run_id="run-1")
    elif scope_type == "WORKSPACE":
        data.update(workspace_identity="workspace-1")
    data.update(extra)
    return data
