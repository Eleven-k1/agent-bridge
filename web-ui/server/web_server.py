from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .bridge_session import BridgeSession
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src"))
from agent_bridge.contracts.envelopes import canonical_digest

ALLOWED_COMMANDS = frozenset({"LIST_LOCAL_MODELS", "REAL_MODEL_RUN", "FAKE_MODEL_RUN", "FAKE_TOOL_RUN"})


class WebApp:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5173,
        *,
        bridge_host: str = "127.0.0.1",
        bridge_port: int | None = None,
        bridge_token: str | None = None,
        static_dir: str | Path | None = None,
        bridge_session_factory: Callable[..., BridgeSession] = BridgeSession,
    ) -> None:
        self.host, self.port = host, port
        self.bridge_host, self.bridge_port, self.bridge_token = bridge_host, bridge_port, bridge_token
        self.static_dir = Path(static_dir) if static_dir else None
        self.bridge_session_factory = bridge_session_factory
        self._server: asyncio.AbstractServer | None = None
        self._sessions: set[BridgeSession] = set()
        self._http_tasks: set[asyncio.Task[Any]] = set()

    @property
    def bound_port(self) -> int:
        if self._server is None or not self._server.sockets:
            raise RuntimeError("web app is not started")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        if self._server is None:
            self._server = await asyncio.start_server(self._handle_client, self.host, self.port)

    async def run(self) -> None:
        await self.start()
        await self._server.serve_forever()  # type: ignore[union-attr]

    async def aclose(self) -> None:
        for session in list(self._sessions):
            await session.aclose()
        self._sessions.clear()
        for task in list(self._http_tasks):
            task.cancel()
        if self._http_tasks:
            await asyncio.gather(*self._http_tasks, return_exceptions=True)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task:
            self._http_tasks.add(task)
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            request_line, headers = self._parse_headers(head)
            method, target, _ = request_line.split(" ", 2)
            if method != "GET":
                await self._http_response(writer, 405, b"method not allowed", "text/plain")
            elif target == "/health":
                await self._http_response(writer, 200, json.dumps({"ok": True, "service": "web-ui"}).encode(), "application/json")
            elif target == "/ws" and headers.get("upgrade", "").lower() == "websocket":
                await self._handle_websocket(reader, writer, headers)
            elif target == "/" or target.startswith("/assets/"):
                await self._serve_static(writer, target)
            else:
                await self._http_response(writer, 404, b"not found", "text/plain")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError, asyncio.TimeoutError):
            try:
                await self._http_response(writer, 400, b"bad request", "text/plain")
            except Exception:
                pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            if task:
                self._http_tasks.discard(task)

    @staticmethod
    def _parse_headers(raw: bytes) -> tuple[str, dict[str, str]]:
        lines = raw.decode("iso-8859-1").split("\r\n")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                break
            if ":" in line:
                key, value = line.split(":", 1)
                headers[key.strip().lower()] = value.strip()
        return lines[0], headers

    async def _serve_static(self, writer: asyncio.StreamWriter, target: str) -> None:
        path = self.static_dir / ("index.html" if target == "/" else target.removeprefix("/")) if self.static_dir else None
        if path is None or not path.is_file() or (self.static_dir and self.static_dir not in path.resolve().parents and path.resolve() != self.static_dir.resolve()):
            body, content_type = b"<!doctype html><title>Agent Bridge</title>", "text/html; charset=utf-8"
        else:
            body = path.read_bytes()
            content_type = "text/javascript" if path.suffix == ".js" else "text/css" if path.suffix == ".css" else "text/html; charset=utf-8"
        await self._http_response(writer, 200, body, content_type)

    @staticmethod
    async def _http_response(writer: asyncio.StreamWriter, status: int, body: bytes, content_type: str) -> None:
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}.get(status, "Error")
        writer.write(f"HTTP/1.1 {status} {reason}\r\nContent-Type: {content_type}\r\nCache-Control: no-store\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
        await writer.drain()

    async def _handle_websocket(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, headers: dict[str, str]) -> None:
        key = headers.get("sec-websocket-key")
        if not key or self.bridge_port is None or not self.bridge_token:
            await self._http_response(writer, 400, b"websocket unavailable", "text/plain")
            return
        origin = headers.get("origin")
        if origin and origin not in {f"http://{self.host}:{self.port}", f"http://127.0.0.1:{self.port}"}:
            await self._http_response(writer, 403, b"origin forbidden", "text/plain")
            return
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        writer.write(f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode())
        await writer.drain()
        async def on_bridge(message: dict[str, Any]) -> None:
            await self._ws_send(writer, message)
        session = self.bridge_session_factory(self.bridge_host, self.bridge_port, self.bridge_token, on_bridge)
        self._sessions.add(session)
        try:
            await session.connect()
            while True:
                opcode, payload = await self._ws_read(reader)
                if opcode == 8:
                    break
                if opcode == 9:
                    await self._ws_send(writer, payload, opcode=10)
                    continue
                if opcode != 1:
                    continue
                try:
                    incoming = json.loads(payload)
                except (ValueError, json.JSONDecodeError):
                    await self._ws_send(writer, {"type": "input_error", "error": "invalid json"})
                    continue
                if isinstance(incoming, dict) and incoming.get("type") == "resume":
                    resume = self._build_resume(incoming)
                    if resume is None:
                        await self._ws_send(writer, {"type": "input_error", "error": "invalid resume"})
                        continue
                    await session.send(resume)
                    continue
                command = self._build_command(incoming)
                if command is None:
                    await self._ws_send(writer, {"type": "input_error", "error": "invalid command"})
                    continue
                await session.send({"type": "command", "command": command})
        except (ConnectionError, OSError, asyncio.IncompleteReadError, PermissionError):
            pass
        finally:
            self._sessions.discard(session)
            await session.aclose()

    @staticmethod
    def _build_resume(incoming: Any) -> dict[str, Any] | None:
        if not isinstance(incoming, dict) or incoming.get("type") != "resume":
            return None
        scope_type, scope_id, cursor = incoming.get("scope_type"), incoming.get("scope_id"), incoming.get("last_event_seq")
        if scope_type not in {"TASK", "LOCAL_CONFIG"} or not isinstance(scope_id, str) or not scope_id.strip():
            return None
        if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
            return None
        return {"type": "resume", "scope_type": scope_type, "scope_id": scope_id.strip(), "last_event_seq": cursor}

    @staticmethod
    def _build_command(incoming: Any) -> dict[str, Any] | None:
        if not isinstance(incoming, dict) or incoming.get("type") != "command":
            return None
        kind, raw = incoming.get("command_type"), incoming.get("payload")
        if kind not in ALLOWED_COMMANDS or not isinstance(raw, dict):
            return None
        payload: dict[str, Any]
        scope = "LOCAL_CONFIG" if kind == "LIST_LOCAL_MODELS" else "TASK"
        if kind == "LIST_LOCAL_MODELS":
            payload = {}
        elif kind == "REAL_MODEL_RUN":
            model = raw.get("model")
            messages = raw.get("messages")
            if not isinstance(model, str) or not model.strip() or not isinstance(messages, list):
                return None
            safe_messages = [{"role": m["role"], "content": m["content"]} for m in messages if isinstance(m, dict) and isinstance(m.get("role"), str) and isinstance(m.get("content"), str)]
            payload = {"model": model.strip(), "messages": safe_messages, "stream": bool(raw.get("stream", True))}
        elif kind == "FAKE_MODEL_RUN":
            chunks = raw.get("text_chunks", [])
            if not isinstance(chunks, list) or not all(isinstance(c, str) for c in chunks):
                return None
            try:
                delay = float(raw.get("chunk_delay_s", 0.05))
            except (TypeError, ValueError):
                return None
            payload = {"text_chunks": chunks[:100], "chunk_delay_s": min(max(delay, 0), 5)}
        else:
            payload = {"target_path": "fake-output.txt", "content": str(raw.get("content", "fake content"))[:10000]}
        cid = uuid.uuid4().hex
        command = {"protocol_version": "0.5.1", "scope_type": scope, "scope_id": "local-config" if scope == "LOCAL_CONFIG" else "task-1", "command_id": f"cmd-{cid}", "command_idempotency_key": f"web-{cid}", "issuer": "local-web", "command_type": kind, "payload": payload, "issued_at": datetime.now(timezone.utc).isoformat()}
        if scope == "TASK":
            command.update({"task_id": "task-1", "run_id": f"run-{cid}"})
        command["command_request_digest"] = canonical_digest(command)
        return command

    @staticmethod
    async def _ws_send(writer: asyncio.StreamWriter, value: Any, opcode: int = 1) -> None:
        data = value if isinstance(value, (bytes, bytearray)) else json.dumps(value, ensure_ascii=False).encode()
        first = 0x80 | (opcode & 0x0F)
        n = len(data)
        header = bytes([first, n]) if n < 126 else bytes([first, 126]) + n.to_bytes(2, "big") if n < 65536 else bytes([first, 127]) + n.to_bytes(8, "big")
        writer.write(header + data)
        await writer.drain()

    @staticmethod
    async def _ws_read(reader: asyncio.StreamReader) -> tuple[int, Any]:
        head = await reader.readexactly(2)
        opcode, length = head[0] & 15, head[1] & 127
        if length == 126:
            length = int.from_bytes(await reader.readexactly(2), "big")
        elif length == 127:
            length = int.from_bytes(await reader.readexactly(8), "big")
        if length > 1_000_000:
            raise ValueError("websocket frame too large")
        if not (head[1] & 128):
            raise ValueError("client websocket frames must be masked")
        mask = await reader.readexactly(4)
        data = await reader.readexactly(length)
        if mask:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        if opcode == 1:
            return opcode, data.decode("utf-8")
        return opcode, data
