"""M2 网关核心行为测试：帧生成、候选事件、流式顺序。

对应 M0 v0.5.1 §6：
- 临时帧携带 operation_id 和 model_fragment_sequence；
- 临时帧没有 task_event_sequence / event_id，不是正式事件；
- 模型生命周期与最终结果必须进入正式候选事件；
- 已收到内容可作为内容块被正式事件引用。
"""

from __future__ import annotations

import pytest

from conftest import run

from agent_bridge.model_gateway.fake import FakeProvider, FakeScenario
from agent_bridge.model_gateway.gateway import GatewayCandidate, GatewayFrame, ModelGateway
from agent_bridge.model_gateway.types import ModelRequest, TimeoutPolicy


def make_request(operation_id: str = "op-1", **overrides) -> ModelRequest:
    data = {
        "operation_id": operation_id,
        "model": "fake-model",
        "messages": [{"role": "user", "content": "你好"}],
        "system": None,
        "timeout": TimeoutPolicy(
            connect_timeout_s=1.0,
            first_token_timeout_s=1.0,
            stream_idle_timeout_s=1.0,
        ),
        "task_id": "task-1",
        "run_id": "run-1",
        "agent_id": "agent-executor",
        "max_retries": 0,
    }
    data.update(overrides)
    return ModelRequest(**data)


async def collect(gateway: ModelGateway, request: ModelRequest):
    frames: list[GatewayFrame] = []
    candidates: list[GatewayCandidate] = []
    async for item in gateway.execute(request):
        if isinstance(item, GatewayFrame):
            frames.append(item)
        else:
            candidates.append(item)
    return frames, candidates


class TestNormalStream:
    def test_full_stream_emits_frames_and_formal_candidates(self) -> None:
        async def body():
            provider = FakeProvider(
                FakeScenario(text_chunks=["你", "好", "，", "世", "界"])
            )
            gateway = ModelGateway(provider)
            frames, candidates = await collect(gateway, make_request())
            return frames, candidates

        frames, candidates = run(body())

        frame_types = [f.frame["frame_type"] for f in frames]
        assert frame_types[0] == "MODEL_STREAM_STARTED_FRAME"
        assert frame_types[-1] == "MODEL_STREAM_END_FRAME"
        assert frame_types[1:-1] == ["MODEL_DELTA_FRAME"] * 5

        event_types = [c.event["event_type"] for c in candidates]
        assert event_types[0] == "MODEL_OPERATION_PLANNED"
        assert "MODEL_OPERATION_STARTED" in event_types
        assert event_types.count("MODEL_CONTENT_CHUNK_RECORDED") == 5
        assert event_types[-1] == "MODEL_OPERATION_COMPLETED"

    def test_delta_frames_carry_operation_id_and_monotonic_fragment_sequence(self) -> None:
        async def body():
            gateway = ModelGateway(FakeProvider(FakeScenario(text_chunks=["a", "b", "c"])))
            frames, _ = await collect(gateway, make_request())
            return [f.frame for f in frames if f.frame["frame_type"] == "MODEL_DELTA_FRAME"]

        deltas = run(body())

        assert all(d["operation_id"] == "op-1" for d in deltas)
        assert [d["model_fragment_sequence"] for d in deltas] == [1, 2, 3]

    def test_frames_are_transient_not_formal_events(self) -> None:
        """M0 §10：MODEL_DELTA_FRAME 不拥有 task_event_sequence。"""

        async def body():
            gateway = ModelGateway(FakeProvider(FakeScenario(text_chunks=["x"])))
            frames, _ = await collect(gateway, make_request())
            return [f.frame for f in frames]

        for frame in run(body()):
            assert "task_event_sequence" not in frame
            assert "event_id" not in frame
            assert frame["scope_type"] == "TASK"
            assert frame["task_id"] == "task-1"
            assert frame["run_id"] == "run-1"

    def test_delta_frame_carries_inline_text_and_reference(self) -> None:
        """帧的 content_delta_ref 是可引用 id；content_delta 是 UI 即时显示用的内联文本。"""

        async def body():
            gateway = ModelGateway(FakeProvider(FakeScenario(text_chunks=["你", "好"])))
            frames, _ = await collect(gateway, make_request())
            return [f.frame for f in frames if f.frame["frame_type"] == "MODEL_DELTA_FRAME"]

        deltas = run(body())

        assert [d["content_delta"] for d in deltas] == ["你", "好"]
        assert deltas[0]["content_delta_ref"] == "content:op-1:1"
        assert deltas[1]["content_delta_ref"] == "content:op-1:2"

    def test_content_chunk_event_references_received_text(self) -> None:
        async def body():
            gateway = ModelGateway(FakeProvider(FakeScenario(text_chunks=["你", "好"])))
            _, candidates = await collect(gateway, make_request())
            return [c.event for c in candidates if c.event["event_type"] == "MODEL_CONTENT_CHUNK_RECORDED"]

        chunks = run(body())

        assert [c["payload"]["content_delta_ref"] for c in chunks] == [
            "content:op-1:1",
            "content:op-1:2",
        ]
        assert [c["payload"]["text"] for c in chunks] == ["你", "好"]

    def test_completed_event_carries_full_text_reference_and_usage(self) -> None:
        async def body():
            gateway = ModelGateway(FakeProvider(FakeScenario(text_chunks=["ab", "cd"])))
            _, candidates = await collect(gateway, make_request())
            return [c.event for c in candidates if c.event["event_type"] == "MODEL_OPERATION_COMPLETED"]

        completed = run(body())

        assert len(completed) == 1
        payload = completed[0]["payload"]
        assert payload["content_refs"] == ["content:op-1:1", "content:op-1:2"]
        assert payload["finish_reason"] == "stop"
        assert payload["usage"]["chunk_count"] == 2

    def test_keepalive_scenario_emits_keepalive_frame(self) -> None:
        async def body():
            provider = FakeProvider(
                FakeScenario(text_chunks=["a"], keepalives_between_chunks=True)
            )
            gateway = ModelGateway(provider)
            frames, _ = await collect(gateway, make_request())
            return [f.frame["frame_type"] for f in frames]

        types = run(body())

        assert "MODEL_STREAM_KEEPALIVE_FRAME" in types
        keepalive_pos = types.index("MODEL_STREAM_KEEPALIVE_FRAME")
        assert 0 < keepalive_pos < len(types) - 1

    def test_candidate_events_have_stable_dedup_keys(self) -> None:
        """同一 operation 的不同事实事件必须各有不同且稳定的去重键。"""

        async def body():
            gateway = ModelGateway(FakeProvider(FakeScenario(text_chunks=["a"])))
            _, candidates = await collect(gateway, make_request())
            return [c.event["event_deduplication_key"] for c in candidates]

        keys = run(body())

        assert len(keys) == len(set(keys))
        assert all(k.startswith("op-1:") for k in keys)


class TestScopeValidation:
    def test_task_scope_frame_rejects_missing_task_context(self) -> None:
        """帧的 TASK 作用域要求 task_id/run_id；模型调用缺任务上下文必须被拒绝。"""

        async def body():
            request = make_request(task_id=None)
            gateway = ModelGateway(FakeProvider(FakeScenario(text_chunks=["a"])))
            return await collect(gateway, request)

        with pytest.raises(ValueError, match="task_id"):
            run(body())

    def test_operation_without_task_scope_is_rejected(self) -> None:
        async def body():
            request = make_request(scope_type="LOCAL_CONFIG")
            gateway = ModelGateway(FakeProvider(FakeScenario(text_chunks=["a"])))
            return await collect(gateway, request)

        with pytest.raises(ValueError, match="scope_type"):
            run(body())
