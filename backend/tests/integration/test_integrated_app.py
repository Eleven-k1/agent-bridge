"""三方模块的第一条真实集成链路。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from agent_bridge.contracts.envelopes import canonical_digest
from agent_bridge.app import build_integrated_app


def task_command(command_type: str, command_id: str, payload: dict | None = None) -> dict:
    body = payload or {}
    return {
        "protocol_version": "0.5.1",
        "scope_type": "TASK",
        "scope_id": "task-1",
        "command_id": command_id,
        "command_idempotency_key": f"idem:{command_id}",
        "command_request_digest": canonical_digest(body),
        "issuer": "local-user",
        "command_type": command_type,
        "payload": body,
        "issued_at": "2026-08-30T00:00:00+00:00",
        "task_id": "task-1",
        "run_id": "run-1",
    }


class LineClient:
    def __init__(self, host: str, port: int, token: str) -> None:
        self.host, self.port, self.token = host, port, token
        self.reader = None
        self.writer = None

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        await self.send({"type": "auth", "token": self.token})
        assert (await self.recv())["type"] == "auth_ok"
        assert (await self.recv())["type"] == "connection_state"

    async def send(self, message: dict) -> None:
        assert self.writer is not None
        self.writer.write((json.dumps(message) + "\n").encode())
        await self.writer.drain()

    async def recv(self) -> dict:
        assert self.reader is not None
        line = await asyncio.wait_for(self.reader.readline(), timeout=3)
        if not line:
            raise ConnectionError("server closed")
        return json.loads(line)

    async def recv_until(self, predicate):
        seen = []
        while True:
            message = await self.recv()
            seen.append(message)
            if predicate(message):
                return message, seen

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()


def run(coro):
    return asyncio.run(coro)


def test_m1_m2_m5_m6_chain_persists_and_recovers_formal_events(tmp_path) -> None:
    async def body():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        app = build_integrated_app(
            tmp_path / "audit.sqlite3", workspace_root=workspace
        )
        await app.start()
        try:
            client = LineClient("127.0.0.1", app.port, app.token)
            await client.connect()

            await client.send(
                {"type": "command", "command": task_command("CREATE_TASK", "create-1")}
            )
            _, created_messages = await client.recv_until(
                lambda message: message.get("type") == "event"
                and message["event"]["event_type"] == "TASK_CREATED"
            )

            await client.send(
                {"type": "command", "command": task_command("START_TASK", "start-1")}
            )
            _, started_messages = await client.recv_until(
                lambda message: message.get("type") == "event"
                and message["event"]["event_type"] == "TASK_STARTED"
            )

            await client.send(
                {
                    "type": "command",
                    "command": task_command(
                        "FAKE_MODEL_RUN",
                        "model-1",
                        {"text_chunks": ["集成", "成功"], "chunk_delay_s": 0.001},
                    ),
                }
            )
            _, model_messages = await client.recv_until(
                lambda message: message.get("type") == "model_frame"
                and message["frame"]["frame_type"] == "MODEL_STREAM_END_FRAME"
            )

            model_frames = [
                message for message in model_messages if message.get("type") == "model_frame"
            ]
            delta_frames = [
                message for message in model_frames
                if message["frame"]["frame_type"] == "MODEL_DELTA_FRAME"
            ]
            expected_frame_fields = {
                "scope_type",
                "task_id",
                "run_id",
                "operation_id",
                "model_fragment_sequence",
                "frame_type",
                "content_delta_ref",
                "received_at",
            }
            assert all(set(message["frame"]) == expected_frame_fields for message in model_frames)
            assert all(isinstance(message["frame"]["content_delta_ref"], str) for message in model_frames)
            assert delta_frames
            assert all("content_delta" not in message["frame"] for message in delta_frames)
            assert all("protocol_version" not in message["frame"] for message in delta_frames)
            assert all("scope_id" not in message["frame"] for message in delta_frames)
            assert [message["content_delta"] for message in delta_frames] == ["集成", "成功"]

            fake_target = tmp_path / "workspace" / "fake-output.txt"
            await client.send(
                {
                    "type": "command",
                    "command": task_command(
                        "FAKE_TOOL_RUN",
                        "tool-1",
                        {
                            "target_path": "fake-output.txt",
                            "content": "fake content",
                        },
                    ),
                }
            )
            _, tool_messages = await client.recv_until(
                lambda message: message.get("type") == "event"
                and message["event"]["event_type"] == "TOOL_EXECUTION_COMPLETED"
            )
            tool_events = [
                message["event"]
                for message in tool_messages
                if message.get("type") == "event"
                and message["event"]["event_type"].startswith("TOOL_EXECUTION_")
            ]
            assert [event["event_type"] for event in tool_events] == [
                "TOOL_EXECUTION_PLANNED",
                "TOOL_EXECUTION_COMPLETED",
            ]
            assert not fake_target.exists()
            assert app._event_store_raw.pending_scopes() == []

            await client.close()

            client2 = LineClient("127.0.0.1", app.port, app.token)
            await client2.connect()
            await client2.send(
                {
                    "type": "resume",
                    "scope_type": "TASK",
                    "scope_id": "task-1",
                    "last_event_seq": 0,
                }
            )
            _, resumed_messages = await client2.recv_until(
                lambda message: message.get("type") == "resume_done"
            )
            await client2.close()
            return (
                created_messages + started_messages + model_messages + tool_messages,
                resumed_messages,
            )
        finally:
            await app.aclose()

    first_messages, resumed_messages = run(body())
    first_events = [message["event"] for message in first_messages if message.get("type") == "event"]
    resumed_events = [message["event"] for message in resumed_messages if message.get("type") == "event"]

    assert [event["event_type"] for event in first_events][:2] == ["TASK_CREATED", "TASK_STARTED"]
    assert any(event["event_type"] == "MODEL_OPERATION_COMPLETED" for event in first_events)
    assert [event["task_event_sequence"] for event in resumed_events] == list(
        range(1, len(resumed_events) + 1)
    )
    assert [event["event_type"] for event in resumed_events] == [
        event["event_type"] for event in first_events
    ]
    first_by_sequence = {
        event["task_event_sequence"]: event for event in first_events
    }
    for resumed in resumed_events:
        original = first_by_sequence[resumed["task_event_sequence"]]
        assert resumed == original


def test_integrated_publish_marks_events_and_replays_pending_events_on_start(tmp_path) -> None:
    async def body():
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        db_path = tmp_path / "audit.sqlite3"
        app = build_integrated_app(db_path, workspace_root=workspace)
        await app.start()
        try:
            pending_candidate = app._event_store._complete_candidate({
                "scope_type": "TASK",
                "scope_id": "task-1",
                "task_id": "task-1",
                "run_id": "run-1",
                "event_type": "TASK_CREATED",
                "event_deduplication_key": "pending:create",
                "payload": {"source": "test"},
            })
            app._event_store_raw.append(SimpleNamespace(**pending_candidate))
            assert app._event_store_raw.pending_publications(scope_type="TASK", scope_id="task-1") == [1]
        finally:
            await app.aclose()

        reopened = build_integrated_app(db_path, workspace_root=workspace)
        await reopened.start()
        try:
            assert reopened._event_store_raw.pending_publications(scope_type="TASK", scope_id="task-1") == []
            assert reopened.router._orchestrator.get_state("task-1", "run-1").lifecycle.value == "CREATED"
        finally:
            await reopened.aclose()

    run(body())
