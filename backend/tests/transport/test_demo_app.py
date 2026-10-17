"""Fake 闭环端到端集成测试：M2 网关 -> M6 服务 -> 测试客户端。

验证 M0 v0.5.1 §6/§13 的完整链路：
- FAKE_MODEL_RUN 命令驱动 FakeProvider，帧与正式事件同时到达客户端；
- 事件先持久化后发布（信封完整、序号单调）；
- 帧内联文本与正式内容块事件记录一致；
- 取消链路（CANCEL_REQUESTED -> CANCELLED）；
- 断线重连后依据正式事件恢复，帧不补发。
"""

from __future__ import annotations

import asyncio

from conftest import LineClient, make_command, run

from agent_bridge.transport.demo import DemoApp, build_demo_app


def run_command(command_type: str, payload: dict, **kwargs) -> dict:
    return make_command(
        command_type=command_type, scope_type="TASK", payload=payload, **kwargs
    )


class TestFakeLoopEndToEnd:
    def test_full_run_streams_frames_and_formal_events(self) -> None:
        async def body():
            app = build_demo_app()
            await app.start()
            try:
                client = LineClient("127.0.0.1", app.port, app.token)
                await client.connect()
                await client.send(
                    {
                        "type": "command",
                        "command": run_command(
                            "FAKE_MODEL_RUN",
                            {"text_chunks": ["你好", "，", "世界"], "chunk_delay_s": 0.01},
                        ),
                    }
                )
                # STREAM_END 帧是本次操作的最后一条消息（在 COMPLETED 事件之后）
                _, seen = await client.recv_until(
                    lambda m: m.get("type") == "model_frame"
                    and m["frame"]["frame_type"] == "MODEL_STREAM_END_FRAME"
                )
                await client.close()
                return seen
            finally:
                await app.aclose()

        seen = run(body())

        receipts = [m for m in seen if m["type"] == "command_result"]
        assert receipts[0]["status"] == "ACCEPTED"

        frames = [m["frame"] for m in seen if m["type"] == "model_frame"]
        frame_types = [f["frame_type"] for f in frames]
        assert frame_types[0] == "MODEL_STREAM_STARTED_FRAME"
        assert frame_types[-1] == "MODEL_STREAM_END_FRAME"
        deltas = [f for f in frames if f["frame_type"] == "MODEL_DELTA_FRAME"]
        assert [d["content_delta"] for d in deltas] == ["你好", "，", "世界"]
        assert [d["model_fragment_sequence"] for d in deltas] == [1, 2, 3]
        # 帧不是正式事件：没有序号与 event_id
        assert all("task_event_sequence" not in f for f in frames)
        assert all("event_id" not in f for f in frames)

        events = [m["event"] for m in seen if m["type"] == "event"]
        event_types = [e["event_type"] for e in events]
        assert event_types[0] == "MODEL_OPERATION_PLANNED"
        assert event_types[-1] == "MODEL_OPERATION_COMPLETED"
        assert event_types.count("MODEL_CONTENT_CHUNK_RECORDED") == 3
        # TASK 序号流单调递增、无空洞
        sequences = [e["task_event_sequence"] for e in events]
        assert sequences == list(range(1, len(events) + 1))
        # 信封完整（先持久化后发布）
        assert all(e.get("event_id") for e in events)
        assert all(e.get("candidate_event_digest") for e in events)
        # 正式内容块与帧内联文本一致
        chunks = [e for e in events if e["event_type"] == "MODEL_CONTENT_CHUNK_RECORDED"]
        assert [c["payload"]["text"] for c in chunks] == ["你好", "，", "世界"]

    def test_cancel_command_produces_cancelled_terminal_event(self) -> None:
        async def body():
            app = build_demo_app()
            await app.start()
            try:
                client = LineClient("127.0.0.1", app.port, app.token)
                await client.connect()
                await client.send(
                    {
                        "type": "command",
                        "command": run_command(
                            "FAKE_MODEL_RUN",
                            {"text_chunks": ["a", "b", "c", "d"], "chunk_delay_s": 0.05},
                            idempotency_key="run-1",
                        ),
                    }
                )
                await client.recv_until(
                    lambda m: m.get("type") == "event"
                    and m["event"]["event_type"] == "MODEL_CONTENT_CHUNK_RECORDED"
                )
                await client.send(
                    {
                        "type": "command",
                        "command": run_command(
                            "FAKE_MODEL_CANCEL", {"operation_id": app.last_operation_id}
                        ),
                    }
                )
                _, seen = await client.recv_until(
                    lambda m: m.get("type") == "model_frame"
                    and m["frame"]["frame_type"] == "MODEL_STREAM_END_FRAME"
                )
                await client.close()
                return seen
            finally:
                await app.aclose()

        seen = run(body())

        event_types = [m["event"]["event_type"] for m in seen if m["type"] == "event"]
        assert "MODEL_OPERATION_CANCEL_REQUESTED" in event_types
        assert event_types[-1] == "MODEL_OPERATION_CANCELLED"
        assert "MODEL_OPERATION_COMPLETED" not in event_types
        # 取消终态之后不再有任何帧
        frame_types = [m["frame"]["frame_type"] for m in seen if m["type"] == "model_frame"]
        assert "MODEL_STREAM_END_FRAME" in frame_types
        end_index = frame_types.index("MODEL_STREAM_END_FRAME")
        assert frame_types[end_index + 1 :] == []

    def test_reconnect_resumes_from_formal_events_only(self) -> None:
        async def body():
            app = build_demo_app()
            await app.start()
            try:
                client = LineClient("127.0.0.1", app.port, app.token)
                await client.connect()
                await client.send(
                    {
                        "type": "command",
                        "command": run_command(
                            "FAKE_MODEL_RUN",
                            {"text_chunks": ["x", "y"], "chunk_delay_s": 0.01},
                            idempotency_key="run-once",
                        ),
                    }
                )
                completed, first_seen = await client.recv_until(
                    lambda m: m.get("type") == "event"
                    and m["event"]["event_type"] == "MODEL_OPERATION_COMPLETED"
                )
                first_event_count = sum(1 for m in first_seen if m["type"] == "event")
                await client.close()

                # 断线重连：从 0 号游标恢复
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
                _, resumed = await client2.recv_until(lambda m: m["type"] == "resume_done")
                await client2.close()
                return first_event_count, resumed
            finally:
                await app.aclose()

        first_count, resumed = run(body())

        resumed_events = [m["event"] for m in resumed if m["type"] == "event"]
        assert len(resumed_events) == first_count
        sequences = [e["task_event_sequence"] for e in resumed_events]
        assert sequences == list(range(1, first_count + 1))
        # 断线重连只补发正式事件：帧不补发
        assert not any(m["type"] == "model_frame" for m in resumed)
        # 最终消息可从正式事件恢复（不依赖帧）
        texts = [
            e["payload"]["text"]
            for e in resumed_events
            if e["event_type"] == "MODEL_CONTENT_CHUNK_RECORDED"
        ]
        assert texts == ["x", "y"]

    def test_resume_from_partial_cursor_replays_tail_only(self) -> None:
        async def body():
            app = build_demo_app()
            await app.start()
            try:
                client = LineClient("127.0.0.1", app.port, app.token)
                await client.connect()
                await client.send(
                    {
                        "type": "command",
                        "command": run_command(
                            "FAKE_MODEL_RUN",
                            {"text_chunks": ["x"], "chunk_delay_s": 0.01},
                            idempotency_key="run-once",
                        ),
                    }
                )
                await client.recv_until(
                    lambda m: m.get("type") == "event"
                    and m["event"]["event_type"] == "MODEL_OPERATION_COMPLETED"
                )
                await client.close()

                client2 = LineClient("127.0.0.1", app.port, app.token)
                await client2.connect()
                await client2.send(
                    {
                        "type": "resume",
                        "scope_type": "TASK",
                        "scope_id": "task-1",
                        "last_event_seq": 2,
                    }
                )
                _, resumed = await client2.recv_until(lambda m: m["type"] == "resume_done")
                await client2.close()
                return resumed
            finally:
                await app.aclose()

        resumed = run(body())

        resumed_events = [m["event"] for m in resumed if m["type"] == "event"]
        sequences = [e["task_event_sequence"] for e in resumed_events]
        assert all(seq > 2 for seq in sequences)

    def test_duplicate_command_key_replays_without_new_events(self) -> None:
        async def body():
            app = build_demo_app()
            await app.start()
            try:
                client = LineClient("127.0.0.1", app.port, app.token)
                await client.connect()
                command = run_command(
                    "FAKE_MODEL_RUN",
                    {"text_chunks": ["z"], "chunk_delay_s": 0.01},
                    idempotency_key="idem-1",
                )
                await client.send({"type": "command", "command": command})
                await client.recv_until(
                    lambda m: m.get("type") == "model_frame"
                    and m["frame"]["frame_type"] == "MODEL_STREAM_END_FRAME"
                )
                # 同幂等键重发：只应收到 REPLAY 回执，不产生新事件
                await client.send({"type": "command", "command": command})
                reply = await client.recv()
                await asyncio.sleep(0.05)
                extra = []
                try:
                    while True:
                        extra.append(await client.recv(timeout=0.2))
                except (TimeoutError, asyncio.TimeoutError):
                    pass
                await client.close()
                return reply, extra
            finally:
                await app.aclose()

        reply, extra = run(body())

        assert reply["type"] == "command_result"
        assert reply["status"] == "REPLAY_EXISTING_RESULT"
        # 重放不会触发新的模型运行或事件
        assert not any(m.get("type") == "event" for m in extra)
        assert not any(m.get("type") == "model_frame" for m in extra)


class TestDemoAppLifecycle:
    def test_demo_app_exposes_port_and_token(self) -> None:
        async def body():
            app: DemoApp = build_demo_app()
            await app.start()
            try:
                return app.port > 0, len(app.token) >= 32, app.last_operation_id
            finally:
                await app.aclose()

        port_ok, token_ok, last_op = run(body())
        assert port_ok
        assert token_ok
        assert last_op is None
