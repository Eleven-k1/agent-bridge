"""M6 LoopbackServer 边界测试。

对应 M0 v0.5.1 §5/§7/§10：
- 服务只监听 127.0.0.1；
- 鉴权失败即断开；
- M6 只做鉴权、传输校验、基础格式校验和转发，不解释业务命令；
- 未经基础校验的命令不交给路由器；
- LOCAL_CONFIG 命令不要求 workspace_identity；
- M6 只能发布已持久化确认的正式事件；
- 临时帧转发但不作为正式事件；
- 断线重连只补发正式事件。
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import LineClient, make_command, run

from agent_bridge.transport.memory import (
    InMemoryCommandRegistry,
    InMemoryEventStore,
)
from agent_bridge.transport.server import LoopbackServer
from agent_bridge.transport.server import _ClientConnection


class RecordingRouter:
    """记录收到的命令、不做任何业务解释的测试路由器。"""

    def __init__(self) -> None:
        self.commands: list[dict] = []
        self.shutdown_called = False

    async def handle_command(self, command: dict) -> None:
        self.commands.append(command)

    async def shutdown(self) -> None:
        self.shutdown_called = True


async def start_server(router: RecordingRouter) -> tuple[LoopbackServer, str]:
    registry = InMemoryCommandRegistry()
    store = InMemoryEventStore()
    server = LoopbackServer(router=router, registry=registry, event_store=store)
    await server.start()
    return server, server.token


class TestLoopbackBoundary:
    def test_server_binds_only_loopback(self) -> None:
        async def body():
            server, _ = await start_server(RecordingRouter())
            try:
                sock = server.sockets[0]
                return sock.getsockname()[0]
            finally:
                await server.aclose()

        assert run(body()) == "127.0.0.1"

    def test_auth_with_wrong_token_is_rejected_and_disconnected(self) -> None:
        async def body():
            server, token = await start_server(RecordingRouter())
            try:
                client = LineClient("127.0.0.1", server.port, "wrong-token")
                await client.connect(authenticate=False)
                await client.send({"type": "auth", "token": "wrong-token"})
                reply = await client.recv()
                # 连接随后被服务端关闭
                closed = False
                try:
                    await client.recv(timeout=1.0)
                except (ConnectionError, TimeoutError):
                    closed = True
                return reply, closed
            finally:
                await server.aclose()

        reply, closed = run(body())
        assert reply["type"] == "auth_failed"
        assert closed

    def test_command_before_auth_is_rejected(self) -> None:
        async def body():
            server, _ = await start_server(RecordingRouter())
            try:
                client = LineClient("127.0.0.1", server.port, server.token)
                await client.connect(authenticate=False)
                await client.send({"type": "command", "command": make_command()})
                reply = await client.recv()
                return reply
            finally:
                await server.aclose()

        reply = run(body())
        assert reply["type"] == "auth_failed"

    def test_valid_token_receives_auth_ok_and_connection_state(self) -> None:
        async def body():
            server, _ = await start_server(RecordingRouter())
            try:
                client = LineClient("127.0.0.1", server.port, server.token)
                messages = await client.connect()
                await client.close()
                return messages
            finally:
                await server.aclose()

        messages = run(body())
        assert messages[0]["type"] == "auth_ok"
        assert any(m["type"] == "connection_state" for m in messages)


class TestCommandValidationAndForwarding:
    def test_command_with_missing_required_field_is_rejected_without_routing(self) -> None:
        async def body():
            router = RecordingRouter()
            server, _ = await start_server(router)
            try:
                client = LineClient("127.0.0.1", server.port, server.token)
                await client.connect()
                bad = make_command()
                bad.pop("command_request_digest")
                await client.send({"type": "command", "command": bad})
                reply = await client.recv()
                await client.close()
                return reply, router.commands
            finally:
                await server.aclose()

        reply, routed = run(body())
        assert reply["type"] == "command_result"
        assert reply["status"] == "INVALID_COMMAND"
        assert "command_request_digest" in reply["errors"][0]
        assert routed == []

    def test_task_command_missing_run_id_is_rejected(self) -> None:
        async def body():
            router = RecordingRouter()
            server, _ = await start_server(router)
            try:
                client = LineClient("127.0.0.1", server.port, server.token)
                await client.connect()
                bad = make_command(scope_type="TASK")
                bad.pop("run_id")
                await client.send({"type": "command", "command": bad})
                reply = await client.recv()
                await client.close()
                return reply["status"], reply["errors"]
            finally:
                await server.aclose()

        status, errors = run(body())
        assert status == "INVALID_COMMAND"
        assert any("run_id" in e for e in errors)

    def test_local_config_command_without_workspace_identity_is_valid(self) -> None:
        async def body():
            router = RecordingRouter()
            server, _ = await start_server(router)
            try:
                client = LineClient("127.0.0.1", server.port, server.token)
                await client.connect()
                await client.send(
                    {"type": "command", "command": make_command(scope_type="LOCAL_CONFIG")}
                )
                reply = await client.recv()
                await client.close()
                return reply, router.commands
            finally:
                await server.aclose()

        reply, routed = run(body())
        assert reply["status"] == "ACCEPTED"
        assert routed[0]["scope_type"] == "LOCAL_CONFIG"

    def test_workspace_command_requires_workspace_identity(self) -> None:
        async def body():
            router = RecordingRouter()
            server, _ = await start_server(router)
            try:
                client = LineClient("127.0.0.1", server.port, server.token)
                await client.connect()
                bad = make_command(scope_type="WORKSPACE")
                bad.pop("workspace_identity")
                await client.send({"type": "command", "command": bad})
                reply = await client.recv()
                await client.close()
                return reply["status"]
            finally:
                await server.aclose()

        assert run(body()) == "INVALID_COMMAND"

    def test_valid_command_is_forwarded_verbatim_to_router(self) -> None:
        """M6 不解释业务命令：路由器收到与线上一致的原始命令。"""

        async def body():
            router = RecordingRouter()
            server, _ = await start_server(router)
            try:
                client = LineClient("127.0.0.1", server.port, server.token)
                await client.connect()
                command = make_command(scope_type="TASK", command_type="FAKE_MODEL_RUN")
                await client.send({"type": "command", "command": command})
                reply = await client.recv()
                await client.close()
                return reply, router.commands[0]
            finally:
                await server.aclose()

        reply, forwarded = run(body())
        assert reply["type"] == "command_result"
        assert reply["command_id"] == "cmd-1"
        assert forwarded["command_type"] == "FAKE_MODEL_RUN"


class TestCommandIdempotency:
    def test_same_key_same_digest_replays(self) -> None:
        async def body():
            server, _ = await start_server(RecordingRouter())
            try:
                client = LineClient("127.0.0.1", server.port, server.token)
                await client.connect()
                await client.send(
                    {"type": "command", "command": make_command(idempotency_key="k1", digest="d1")}
                )
                first = await client.recv()
                await client.send(
                    {"type": "command", "command": make_command(idempotency_key="k1", digest="d1")}
                )
                second = await client.recv()
                await client.close()
                return first["status"], second["status"]
            finally:
                await server.aclose()

        first_status, second_status = run(body())
        assert first_status == "ACCEPTED"
        assert second_status == "REPLAY_EXISTING_RESULT"

    def test_same_key_different_digest_conflicts(self) -> None:
        async def body():
            server, _ = await start_server(RecordingRouter())
            try:
                client = LineClient("127.0.0.1", server.port, server.token)
                await client.connect()
                await client.send(
                    {"type": "command", "command": make_command(idempotency_key="k1", digest="d1")}
                )
                await client.recv()
                await client.send(
                    {"type": "command", "command": make_command(idempotency_key="k1", digest="d2")}
                )
                reply = await client.recv()
                await client.close()
                return reply["status"]
            finally:
                await server.aclose()

        assert run(body()) == "IDEMPOTENCY_CONFLICT"


class TestBroadcastAndShutdown:
    def test_frames_and_persisted_events_are_broadcast_to_all_clients(self) -> None:
        async def body():
            server, _ = await start_server(RecordingRouter())
            try:
                client_a = LineClient("127.0.0.1", server.port, server.token)
                client_b = LineClient("127.0.0.1", server.port, server.token)
                await client_a.connect()
                await client_b.connect()

                candidate = {
                    "protocol_version": "0.5.1",
                    "scope_type": "TASK",
                    "scope_id": "task-1",
                    "task_id": "task-1",
                    "run_id": "run-1",
                    "operation_id": "op-1",
                    "event_deduplication_key": "op-1:X",
                    "causation_id": "op-1",
                    "actor": "router",
                    "event_type": "MODEL_OPERATION_STARTED",
                    "payload": {},
                    "occurred_at": "2026-08-30T00:00:00+00:00",
                }
                envelope = await server.publish_event(candidate)
                await server.broadcast_frame({"frame_type": "MODEL_DELTA_FRAME", "content_delta": "hi"})

                event_a, _ = await client_a.recv_until(lambda m: m["type"] == "event")
                frame_a, _ = await client_a.recv_until(lambda m: m["type"] == "model_frame")
                event_b, _ = await client_b.recv_until(lambda m: m["type"] == "event")
                frame_b, _ = await client_b.recv_until(lambda m: m["type"] == "model_frame")
                await client_a.close()
                await client_b.close()
                return event_a, frame_a, event_b, frame_b, envelope
            finally:
                await server.aclose()

        event_a, frame_a, event_b, frame_b, envelope = run(body())
        for event in (event_a, event_b):
            assert event["event"]["event_id"] == envelope["event_id"]
            assert event["event"]["task_event_sequence"] == 1
        assert frame_a["frame"]["content_delta"] == "hi"
        assert frame_b["frame"]["content_delta"] == "hi"

    def test_shutdown_closes_port_and_calls_router_shutdown(self) -> None:
        async def body():
            router = RecordingRouter()
            server, _ = await start_server(router)
            port = server.port
            await server.aclose()
            # 端口已释放：再次连接应失败
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                still_open = True
            except (ConnectionError, OSError):
                still_open = False
            return still_open, router.shutdown_called

        still_open, shutdown_called = run(body())
        assert not still_open
        assert shutdown_called

    def test_resume_replays_only_formal_events_after_cursor(self) -> None:
        async def body():
            server, _ = await start_server(RecordingRouter())
            try:
                for i in range(1, 4):
                    candidate = {
                        "protocol_version": "0.5.1",
                        "scope_type": "TASK",
                        "scope_id": "task-1",
                        "task_id": "task-1",
                        "run_id": "run-1",
                        "operation_id": f"op-{i}",
                        "event_deduplication_key": f"op-{i}:DONE",
                        "causation_id": f"op-{i}",
                        "actor": "router",
                        "event_type": "MODEL_OPERATION_COMPLETED",
                        "payload": {"i": i},
                        "occurred_at": "2026-08-30T00:00:00+00:00",
                    }
                    await server.publish_event(candidate)
                await server.broadcast_frame({"frame_type": "MODEL_DELTA_FRAME"})

                client = LineClient("127.0.0.1", server.port, server.token)
                await client.connect()
                await client.send(
                    {"type": "resume", "scope_type": "TASK", "scope_id": "task-1", "last_event_seq": 1}
                )
                done, seen = await client.recv_until(lambda m: m["type"] == "resume_done")
                await client.close()
                return done, seen
            finally:
                await server.aclose()

        done, seen = run(body())
        events = [m["event"] for m in seen if m["type"] == "event"]
        assert [e["task_event_sequence"] for e in events] == [2, 3]
        # 断线重连只保证补发正式事件：帧不补发
        assert not any(m["type"] == "model_frame" for m in seen)
        assert done["replayed"] == 2


class TestEventPublishingGate:
    def test_published_event_is_a_complete_envelope(self) -> None:
        """M6 只能发布持久化确认过的事件：publish_event 返回并广播完整信封。"""

        async def body():
            server, _ = await start_server(RecordingRouter())
            try:
                client = LineClient("127.0.0.1", server.port, server.token)
                await client.connect()
                candidate = {
                    "protocol_version": "0.5.1",
                    "scope_type": "TASK",
                    "scope_id": "task-1",
                    "task_id": "task-1",
                    "run_id": "run-1",
                    "operation_id": "op-9",
                    "event_deduplication_key": "op-9:DONE",
                    "causation_id": "op-9",
                    "actor": "router",
                    "event_type": "MODEL_OPERATION_COMPLETED",
                    "payload": {"ok": True},
                    "occurred_at": "2026-08-30T00:00:00+00:00",
                }
                envelope = await server.publish_event(candidate)
                message, _ = await client.recv_until(lambda m: m["type"] == "event")
                await client.close()
                return envelope, message["event"]
            finally:
                await server.aclose()

        envelope, broadcast = run(body())
        for field in (
            "event_id",
            "task_event_sequence",
            "candidate_event_digest",
            "digest_algorithm_version",
            "integrity_ref",
        ):
            assert envelope[field], f"envelope missing {field}"
        assert broadcast == envelope

    def test_publish_duplicate_dedup_key_returns_existing_event(self) -> None:
        async def body():
            server, _ = await start_server(RecordingRouter())
            try:
                candidate = {
                    "protocol_version": "0.5.1",
                    "scope_type": "TASK",
                    "scope_id": "task-1",
                    "task_id": "task-1",
                    "run_id": "run-1",
                    "operation_id": "op-1",
                    "event_deduplication_key": "op-1:DONE",
                    "causation_id": "op-1",
                    "actor": "router",
                    "event_type": "MODEL_OPERATION_COMPLETED",
                    "payload": {"ok": True},
                    "occurred_at": "2026-08-30T00:00:00+00:00",
                }
                first = await server.publish_event(candidate)
                second = await server.publish_event(candidate)
                return first, second
            finally:
                await server.aclose()

        first, second = run(body())
        assert second["event_id"] == first["event_id"]
        assert second["task_event_sequence"] == first["task_event_sequence"]

    def test_failed_broadcast_keeps_event_pending_for_retry(self) -> None:
        class FailingWriter:
            def write(self, data: bytes) -> None:
                pass

            async def drain(self) -> None:
                raise BrokenPipeError("client disconnected")

            def close(self) -> None:
                pass

        async def body():
            store = InMemoryEventStore()
            published = []
            store.mark_published = lambda envelope: published.append(envelope["event_id"])
            server = LoopbackServer(
                router=RecordingRouter(),
                registry=InMemoryCommandRegistry(),
                event_store=store,
            )
            server._connections.append(_ClientConnection(FailingWriter(), authenticated=True))
            candidate = {
                "protocol_version": "0.5.1",
                "scope_type": "TASK",
                "scope_id": "task-1",
                "task_id": "task-1",
                "run_id": "run-1",
                "operation_id": "op-failed-broadcast",
                "event_deduplication_key": "op-failed-broadcast:DONE",
                "causation_id": "op-failed-broadcast",
                "actor": "router",
                "event_type": "MODEL_OPERATION_COMPLETED",
                "payload": {"ok": True},
                "occurred_at": "2026-08-30T00:00:00+00:00",
            }

            envelope = await server.publish_event(candidate)
            assert published == []

            server._connections.clear()
            await server.publish_event(candidate)
            await server.aclose()
            return envelope, published

        envelope, published = run(body())
        assert published == [envelope["event_id"]]


class TestScopeSequenceSeparation:
    def test_local_config_events_use_own_sequence_stream(self) -> None:
        async def body():
            server, _ = await start_server(RecordingRouter())
            try:
                def config_candidate(key: str) -> dict:
                    return {
                        "protocol_version": "0.5.1",
                        "scope_type": "LOCAL_CONFIG",
                        "scope_id": "config-scope",
                        "operation_id": key,
                        "event_deduplication_key": f"{key}:CHANGED",
                        "causation_id": key,
                        "actor": "router",
                        "event_type": "LOCAL_SETTING_CHANGED",
                        "payload": {},
                        "occurred_at": "2026-08-30T00:00:00+00:00",
                    }

                task_candidate = {
                    "protocol_version": "0.5.1",
                    "scope_type": "TASK",
                    "scope_id": "task-1",
                    "task_id": "task-1",
                    "run_id": "run-1",
                    "operation_id": "op-t",
                    "event_deduplication_key": "op-t:DONE",
                    "causation_id": "op-t",
                    "actor": "router",
                    "event_type": "MODEL_OPERATION_COMPLETED",
                    "payload": {},
                    "occurred_at": "2026-08-30T00:00:00+00:00",
                }
                c1 = await server.publish_event(config_candidate("cfg-1"))
                t1 = await server.publish_event(task_candidate)
                c2 = await server.publish_event(config_candidate("cfg-2"))
                return c1, t1, c2
            finally:
                await server.aclose()

        c1, t1, c2 = run(body())
        assert c1["local_config_event_sequence"] == 1
        assert t1["task_event_sequence"] == 1  # TASK 序号流不受 LOCAL_CONFIG 影响
        assert c2["local_config_event_sequence"] == 2


@pytest.mark.parametrize(
    "missing",
    ["protocol_version", "scope_type", "scope_id", "command_id", "issuer", "command_type", "payload"],
)
def test_each_required_command_field_is_enforced(missing: str) -> None:
    async def body():
        router = RecordingRouter()
        server, _ = await start_server(router)
        try:
            client = LineClient("127.0.0.1", server.port, server.token)
            await client.connect()
            bad = make_command()
            bad.pop(missing)
            await client.send({"type": "command", "command": bad})
            reply = await client.recv()
            await client.close()
            return reply
        finally:
            await server.aclose()

    reply = run(body())
    assert reply["status"] == "INVALID_COMMAND"
    assert any(missing in e for e in reply["errors"])
