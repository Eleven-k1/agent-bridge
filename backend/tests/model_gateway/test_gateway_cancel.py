"""M2 取消语义测试。

对应 M0 v0.5.1 §6 与职责书规则：
- cancel 幂等；
- 取消后不得再产生有效业务 delta；
- 取消确认后到达的 delta 必须丢弃或仅作诊断，不得进入有效业务内容；
- 已收到内容必须保留（不伪造结论，也不丢内容）。
"""

from __future__ import annotations

import asyncio

from conftest import run

from agent_bridge.model_gateway.fake import FakeProvider, FakeScenario
from agent_bridge.model_gateway.gateway import GatewayFrame, ModelGateway
from agent_bridge.model_gateway.types import ModelRequest, TimeoutPolicy


def make_request(operation_id: str = "op-1") -> ModelRequest:
    return ModelRequest(
        operation_id=operation_id,
        model="fake-model",
        messages=[{"role": "user", "content": "hi"}],
        system=None,
        timeout=TimeoutPolicy(
            connect_timeout_s=1.0, first_token_timeout_s=1.0, stream_idle_timeout_s=1.0
        ),
        task_id="task-1",
        run_id="run-1",
        agent_id="agent-executor",
        max_retries=0,
    )


class TestCancel:
    def test_cancel_stops_stream_and_emits_cancelled_candidate(self) -> None:
        """取消后：收到 CANCEL_REQUESTED 与 CANCELLED 候选事件，流终止，内容保留。"""

        async def body():
            provider = FakeProvider(
                FakeScenario(text_chunks=["a", "b", "c", "d"], chunk_delay_s=0.05)
            )
            gateway = ModelGateway(provider)
            request = make_request()

            frames = []
            candidates = []
            task = asyncio.create_task(_drain(gateway, request, frames, candidates))
            await asyncio.sleep(0.12)  # 大约收到 2 个 chunk 后取消
            await gateway.cancel("op-1")
            await task
            return frames, candidates

        frames, candidates = run(body())

        event_types = [c.event["event_type"] for c in candidates]
        assert "MODEL_OPERATION_CANCEL_REQUESTED" in event_types
        assert event_types[-1] == "MODEL_OPERATION_CANCELLED"
        # 已收到的内容事件必须保留
        recorded = [c for c in candidates if c.event["event_type"] == "MODEL_CONTENT_CHUNK_RECORDED"]
        assert 1 <= len(recorded) < 4

        # 取消事件之后不得再出现任何帧
        frame_types = [f.frame["frame_type"] for f in frames]
        assert "MODEL_STREAM_END_FRAME" in frame_types
        deltas_after_cancel = _deltas_after_end(frames)
        assert deltas_after_cancel == []

    def test_deltas_after_cancel_confirmation_are_dropped(self) -> None:
        """provider 在取消确认后仍吐 delta（ignore_cancel 场景）：必须被网关丢弃。"""

        async def body():
            provider = FakeProvider(
                FakeScenario(
                    text_chunks=["a", "b", "c"],
                    chunk_delay_s=0.05,
                    ignore_cancel=True,
                )
            )
            gateway = ModelGateway(provider)
            request = make_request()

            frames = []
            candidates = []
            task = asyncio.create_task(_drain(gateway, request, frames, candidates))
            await asyncio.sleep(0.08)
            await gateway.cancel("op-1")
            await asyncio.wait_for(task, timeout=2.0)
            return frames, candidates

        frames, candidates = run(body())

        # 终态是 CANCELLED 而不是 COMPLETED
        event_types = [c.event["event_type"] for c in candidates]
        assert event_types[-1] == "MODEL_OPERATION_CANCELLED"
        assert "MODEL_OPERATION_COMPLETED" not in event_types

        # 取消确认后到达的 delta 不得进入有效业务内容：
        # 已记录的 chunk 事件数量必须少于全部 chunk 数
        recorded = [
            c for c in candidates if c.event["event_type"] == "MODEL_CONTENT_CHUNK_RECORDED"
        ]
        assert len(recorded) < 3

        # 终帧之后没有任何新帧
        assert _deltas_after_end(frames) == []

    def test_cancel_is_idempotent(self) -> None:
        async def body():
            provider = FakeProvider(FakeScenario(text_chunks=["a"], chunk_delay_s=0.05))
            gateway = ModelGateway(provider)
            request = make_request()

            frames, candidates = [], []
            task = asyncio.create_task(_drain(gateway, request, frames, candidates))
            await asyncio.sleep(0.02)
            await gateway.cancel("op-1")
            await gateway.cancel("op-1")  # 重复取消不报错
            await asyncio.wait_for(task, timeout=2.0)
            event_types = [c.event["event_type"] for c in candidates]
            return event_types

        event_types = run(body())

        assert event_types.count("MODEL_OPERATION_CANCEL_REQUESTED") == 1
        assert event_types.count("MODEL_OPERATION_CANCELLED") == 1

    def test_cancel_unknown_operation_is_noop(self) -> None:
        async def body():
            gateway = ModelGateway(FakeProvider(FakeScenario(text_chunks=["a"])))
            await gateway.cancel("does-not-exist")
            return True

        assert run(body())


async def _drain(gateway, request, frames, candidates) -> None:
    from agent_bridge.model_gateway.gateway import GatewayCandidate

    async for item in gateway.execute(request):
        if isinstance(item, GatewayFrame):
            frames.append(item)
        elif isinstance(item, GatewayCandidate):
            candidates.append(item)


def _deltas_after_end(frames: list) -> list:
    """返回 STREAM_END 帧之后出现的所有帧（应为空）。"""
    types = [f.frame["frame_type"] for f in frames]
    if "MODEL_STREAM_END_FRAME" not in types:
        return ["NO_END_FRAME"]
    end_index = types.index("MODEL_STREAM_END_FRAME")
    return types[end_index + 1 :]
