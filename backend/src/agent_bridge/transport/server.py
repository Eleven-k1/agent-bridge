"""M6 回环服务：NDJSON over TCP，仅监听 127.0.0.1。

职责边界（M0 v0.5.1 §5/§7）：
- 鉴权：连接首条消息必须是携带临时会话凭据的 auth，失败即断开；
- 传输校验：非法 JSON 断开；
- 基础格式校验：命令信封缺字段或作用域字段不满足时拒绝，不交给路由器；
- 转发：基础校验通过且幂等登记为 NEW 的命令原样转交应用命令路由器；
  本服务不解释、不执行任何业务命令；
- 帧转发：把 M2 产生的临时模型流式帧广播给已认证客户端，不当作正式事件；
- 事件发布：只发布经过事件存储入口确认（append 返回完整信封）的正式事件；
- 断线补发：resume 只补发正式事件，不补发临时帧；
- 连接状态：向客户端发布传输层连接状态通知（不是任何作用域的正式事件）。
"""

from __future__ import annotations

import asyncio
import secrets
from dataclasses import dataclass, field
from typing import Any

from agent_bridge.transport.interfaces import (
    CommandRegistry,
    CommandRouter,
    CommandStatus,
    EventStore,
)
from agent_bridge.transport.protocol import (
    MAX_LINE_BYTES,
    MSG_AUTH,
    MSG_AUTH_FAILED,
    MSG_AUTH_OK,
    MSG_AGENT_FRAME,
    MSG_COMMAND,
    MSG_COMMAND_RESULT,
    MSG_CONNECTION_STATE,
    MSG_EVENT,
    MSG_MODEL_FRAME,
    MSG_PROTOCOL_ERROR,
    MSG_RESUME,
    MSG_RESUME_DONE,
    decode_line,
    encode_message,
)
from agent_bridge.transport.validation import validate_command


@dataclass
class _ClientConnection:
    writer: asyncio.StreamWriter
    authenticated: bool = False
    connection_id: str = field(default_factory=lambda: f"conn-{secrets.token_hex(4)}")


class LoopbackServer:
    """回环 NDJSON 服务。"""

    def __init__(
        self,
        *,
        router: CommandRouter,
        registry: CommandRegistry,
        event_store: EventStore,
        host: str = "127.0.0.1",
    ) -> None:
        self._router = router
        self._registry = registry
        self._event_store = event_store
        self._host = host
        self._token = secrets.token_urlsafe(32)
        self._server: asyncio.AbstractServer | None = None
        self._connections: list[_ClientConnection] = []
        self._router_tasks: set[asyncio.Task[None]] = set()
        self._broadcast_lock = asyncio.Lock()
        self._closed = False

    # ---- 生命周期 ----

    @property
    def token(self) -> str:
        """临时会话凭据：仅应通过本进程私有通道（stdout）交付给桌面端。"""
        return self._token

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("server not started")
        return self._server.sockets[0].getsockname()[1]

    @property
    def sockets(self):
        if self._server is None:
            raise RuntimeError("server not started")
        return self._server.sockets

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client,
            host=self._host,
            port=0,
            limit=MAX_LINE_BYTES,
        )

    async def aclose(self) -> None:
        """优雅关闭：停路由器、取消命令任务、断开客户端、释放端口。"""
        self._closed = True
        await self._router.shutdown()
        for task in self._router_tasks:
            task.cancel()
        if self._router_tasks:
            await asyncio.gather(*self._router_tasks, return_exceptions=True)
        for connection in list(self._connections):
            self._close_connection(connection)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ---- 对路由器开放的广播端口 ----

    async def broadcast_frame(
        self, frame: dict[str, Any], *, display_delta: str | None = None
    ) -> None:
        """广播 M0 临时帧；即时显示扩展位于传输消息而非帧本体。"""
        message: dict[str, Any] = {"type": MSG_MODEL_FRAME, "frame": frame}
        if display_delta is not None:
            message["content_delta"] = display_delta
        await self._broadcast(message)

    async def broadcast_agent_frame(self, frame: dict[str, Any]) -> None:
        """Broadcast transient local-agent output without persisting it as an event."""
        await self._broadcast({"type": MSG_AGENT_FRAME, **frame})

    async def publish_event(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """经事件存储确认后发布正式事件，返回完整信封。"""
        envelope = await self._event_store.append(candidate)
        if not await self._broadcast({"type": MSG_EVENT, "event": envelope}):
            return envelope
        mark_published = getattr(self._event_store, "mark_published", None)
        if mark_published is not None:
            mark_published(envelope)
        return envelope

    async def publish_persisted_event(self, envelope: dict[str, Any]) -> None:
        """发布由 M5 其他受控入口已经落盘并返回的正式事件。"""
        if not await self._broadcast({"type": MSG_EVENT, "event": envelope}):
            return
        mark_published = getattr(self._event_store, "mark_published", None)
        if mark_published is not None:
            mark_published(envelope)

    # ---- 连接处理 ----

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        connection = _ClientConnection(writer=writer)
        self._connections.append(connection)
        try:
            first_line = await reader.readline()
            if not first_line:
                return
            first = _parse_line(first_line)
            if (
                first is None
                or first.get("type") != MSG_AUTH
                or first.get("token") != self._token
            ):
                await self._send_to(connection, {"type": MSG_AUTH_FAILED})
                return
            connection.authenticated = True
            await self._send_to(connection, {"type": MSG_AUTH_OK})
            await self._send_to(
                connection,
                {"type": MSG_CONNECTION_STATE, "state": "connected"},
            )
            while not self._closed:
                line = await reader.readline()
                if not line:
                    break
                message = _parse_line(line)
                if message is None:
                    await self._send_to(
                        connection,
                        {"type": MSG_PROTOCOL_ERROR, "error": "invalid json line"},
                    )
                    break
                await self._handle_message(connection, message)
        except (ConnectionError, OSError):
            pass
        finally:
            self._connections.remove(connection)
            self._close_connection(connection)

    async def _handle_message(
        self, connection: _ClientConnection, message: dict[str, Any]
    ) -> None:
        message_type = message.get("type")
        if message_type == MSG_COMMAND:
            await self._handle_command(connection, message.get("command"))
        elif message_type == MSG_RESUME:
            await self._handle_resume(connection, message)
        else:
            await self._send_to(
                connection,
                {"type": MSG_PROTOCOL_ERROR, "error": f"unknown message type: {message_type}"},
            )

    async def _handle_command(
        self, connection: _ClientConnection, command: Any
    ) -> None:
        if not isinstance(command, dict):
            await self._send_to(
                connection,
                self._command_result(
                    command_id=None,
                    status=CommandStatus.INVALID_COMMAND,
                    errors=["command 必须是对象"],
                ),
            )
            return

        errors = validate_command(command)
        if errors:
            await self._send_to(
                connection,
                self._command_result(
                    command_id=command.get("command_id"),
                    status=CommandStatus.INVALID_COMMAND,
                    errors=errors,
                ),
            )
            return

        registry_result = await self._registry.register(command)
        if registry_result.status is not CommandStatus.NEW:
            await self._send_to(
                connection,
                self._command_result(
                    command_id=command.get("command_id"),
                    status=registry_result.status,
                    result=registry_result.existing_result,
                ),
            )
            return

        await self._send_to(
            connection,
            self._command_result(
                command_id=command.get("command_id"), status=CommandStatus.ACCEPTED
            ),
        )
        task = asyncio.create_task(self._drive_router(connection, command))
        self._router_tasks.add(task)
        task.add_done_callback(self._router_tasks.discard)

    async def _drive_router(
        self, connection: _ClientConnection, command: dict[str, Any]
    ) -> None:
        """把命令原样转交路由器；执行失败以 ROUTER_ERROR 回执，不拖垮服务。"""
        try:
            await self._router.handle_command(command)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._send_to(
                connection,
                self._command_result(
                    command_id=command.get("command_id"),
                    status=CommandStatus.ROUTER_ERROR,
                    errors=[f"{type(exc).__name__}: {exc}"],
                ),
            )

    async def _handle_resume(
        self, connection: _ClientConnection, message: dict[str, Any]
    ) -> None:
        scope_type = message.get("scope_type", "")
        scope_id = message.get("scope_id", "")
        try:
            after_seq = int(message.get("last_event_seq", 0))
        except (TypeError, ValueError):
            await self._send_to(
                connection,
                {"type": MSG_PROTOCOL_ERROR, "error": "last_event_seq must be an integer"},
            )
            return
        events = await self._event_store.events_after(scope_type, scope_id, after_seq)
        for envelope in events:
            await self._send_to(connection, {"type": MSG_EVENT, "event": envelope})
        await self._send_to(
            connection, {"type": MSG_RESUME_DONE, "replayed": len(events)}
        )

    # ---- 发送工具 ----

    @staticmethod
    def _command_result(
        *,
        command_id: str | None,
        status: CommandStatus,
        result: dict[str, Any] | None = None,
        errors: list[str] | None = None,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "type": MSG_COMMAND_RESULT,
            "command_id": command_id,
            "status": status.value,
        }
        if result is not None:
            message["result"] = result
        if errors:
            message["errors"] = errors
        return message

    async def _broadcast(self, message: dict[str, Any]) -> bool:
        async with self._broadcast_lock:
            succeeded = True
            for connection in list(self._connections):
                if connection.authenticated:
                    succeeded = await self._send_to(connection, message) and succeeded
            return succeeded

    async def _send_to(
        self, connection: _ClientConnection, message: dict[str, Any]
    ) -> bool:
        try:
            connection.writer.write(encode_message(message))
            await connection.writer.drain()
        except (ConnectionError, OSError):
            self._close_connection(connection)
            return False
        return True

    @staticmethod
    def _close_connection(connection: _ClientConnection) -> None:
        try:
            connection.writer.close()
        except (ConnectionError, OSError):
            pass


def _parse_line(line: bytes) -> dict[str, Any] | None:
    try:
        return decode_line(line)
    except (ValueError, UnicodeDecodeError):
        return None
