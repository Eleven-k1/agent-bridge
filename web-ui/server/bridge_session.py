from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from agent_bridge.transport.protocol import MSG_AUTH_OK, encode_message

MessageHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class BridgeSession:
    """One browser session connected to the local agent_bridge NDJSON endpoint."""

    def __init__(self, host: str, port: int, token: str, on_message: MessageHandler | None = None) -> None:
        self.host, self.port, self.token = host, port, token
        self.on_message = on_message
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self.closed = False

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        self.writer.write(encode_message({"type": "auth", "token": self.token}))
        await self.writer.drain()
        line = await asyncio.wait_for(self.reader.readline(), 5)
        if not line:
            raise ConnectionError("agent bridge closed during authentication")
        reply = json.loads(line)
        if reply.get("type") != MSG_AUTH_OK:
            raise PermissionError("agent bridge authentication failed")
        self._reader_task = asyncio.create_task(self._read_loop())

    async def send(self, message: dict[str, Any]) -> None:
        if self.closed or self.writer is None:
            raise ConnectionError("bridge session is closed")
        async with self._write_lock:
            self.writer.write(encode_message(message))
            await self.writer.drain()

    async def _read_loop(self) -> None:
        assert self.reader is not None
        try:
            while not self.closed:
                line = await self.reader.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if self.on_message is not None:
                    result = self.on_message(message)
                    if asyncio.iscoroutine(result):
                        await result
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            self.closed = True

    async def aclose(self) -> None:
        self.closed = True
        if self._reader_task is not None and self._reader_task is not asyncio.current_task():
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()
        self.writer = None
        self.reader = None
