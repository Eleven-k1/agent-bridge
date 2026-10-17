"""M2 超时与错误归一化测试。

对应职责书与既往已确认提案：
- 超时分三段：connect / first_token / stream_idle，错误可区分阶段；
- 错误归一化到统一类别（auth / rate_limit / timeout / network / protocol / server / cancelled / unknown）并标注可重试性；
- 仅首字节前的传输级失败做有限重试；已收到内容后失败必须保留内容并产生 FAILED 候选事件，不得自动重试；
- 错误消息不得包含凭据。
"""

from __future__ import annotations

import pytest
from conftest import run

from agent_bridge.model_gateway.fake import FakeProvider, FakeScenario
from agent_bridge.model_gateway.gateway import GatewayCandidate, GatewayFrame, ModelGateway
from agent_bridge.model_gateway.types import (
    ErrorCategory,
    ModelRequest,
    ProviderError,
    TimeoutPolicy,
)


def make_request(**overrides) -> ModelRequest:
    data = {
        "operation_id": "op-1",
        "model": "fake-model",
        "messages": [{"role": "user", "content": "hi"}],
        "system": None,
        "timeout": TimeoutPolicy(
            connect_timeout_s=1.0, first_token_timeout_s=1.0, stream_idle_timeout_s=1.0
        ),
        "task_id": "task-1",
        "run_id": "run-1",
        "agent_id": "agent-executor",
        "max_retries": 0,
    }
    data.update(overrides)
    return ModelRequest(**data)


async def collect(gateway, request):
    frames: list[GatewayFrame] = []
    candidates: list[GatewayCandidate] = []
    async for item in gateway.execute(request):
        if isinstance(item, GatewayFrame):
            frames.append(item)
        else:
            candidates.append(item)
    return frames, candidates


def last_event(candidates: list[GatewayCandidate]) -> GatewayCandidate:
    return candidates[-1]


class TestTimeoutPhases:
    def test_first_token_timeout_reports_phase(self) -> None:
        async def body():
            provider = FakeProvider(FakeScenario(text_chunks=["a"], hang_before_content_s=1.0))
            request = make_request(
                timeout=TimeoutPolicy(
                    connect_timeout_s=0.5,
                    first_token_timeout_s=0.1,
                    stream_idle_timeout_s=0.5,
                )
            )
            gateway = ModelGateway(provider)
            return await collect(gateway, request)

        _, candidates = run(body())

        failed = last_event(candidates)
        assert failed.event["event_type"] == "MODEL_OPERATION_FAILED"
        error = failed.event["payload"]["error"]
        assert error["category"] == "TIMEOUT"
        assert error["timeout_phase"] == "first_token"

    def test_stream_idle_timeout_reports_phase(self) -> None:
        async def body():
            provider = FakeProvider(
                FakeScenario(text_chunks=["a", "b"], hang_at_chunk=1, hang_duration_s=1.0)
            )
            request = make_request(
                timeout=TimeoutPolicy(
                    connect_timeout_s=0.5,
                    first_token_timeout_s=0.5,
                    stream_idle_timeout_s=0.1,
                )
            )
            gateway = ModelGateway(provider)
            return await collect(gateway, request)

        _, candidates = run(body())

        failed = last_event(candidates)
        assert failed.event["event_type"] == "MODEL_OPERATION_FAILED"
        error = failed.event["payload"]["error"]
        assert error["category"] == "TIMEOUT"
        assert error["timeout_phase"] == "stream_idle"
        # 挂起前已收到的内容必须保留
        recorded = [
            c.event["event_type"] for c in candidates
            if c.event["event_type"] == "MODEL_CONTENT_CHUNK_RECORDED"
        ]
        assert len(recorded) == 1


class TestErrorNormalization:
    @pytest.mark.parametrize(
        ("category", "retryable"),
        [
            (ErrorCategory.AUTH, False),
            (ErrorCategory.RATE_LIMIT, True),
            (ErrorCategory.PROTOCOL, False),
            (ErrorCategory.SERVER, True),
            (ErrorCategory.NETWORK, True),
        ],
    )
    def test_provider_failure_maps_to_normalized_category(self, category, retryable) -> None:
        async def body():
            provider = FakeProvider(
                FakeScenario(text_chunks=["a"], fail_after_chunks=0, fail_category=category)
            )
            gateway = ModelGateway(provider)
            return await collect(gateway, make_request())

        _, candidates = run(body())

        failed = last_event(candidates)
        assert failed.event["event_type"] == "MODEL_OPERATION_FAILED"
        error = failed.event["payload"]["error"]
        assert error["category"] == category.value
        assert error["retryable"] is retryable

    def test_failure_after_content_keeps_received_chunks(self) -> None:
        """流中途失败：已收内容保留 + FAILED 终态 + 不自动重试。"""

        async def body():
            provider = FakeProvider(
                FakeScenario(
                    text_chunks=["a", "b", "c"],
                    fail_after_chunks=2,
                    fail_category=ErrorCategory.NETWORK,
                )
            )
            gateway = ModelGateway(provider)
            return await collect(gateway, make_request(max_retries=3))

        _, candidates = run(body())

        recorded = [
            c for c in candidates if c.event["event_type"] == "MODEL_CONTENT_CHUNK_RECORDED"
        ]
        assert len(recorded) == 2
        failed = last_event(candidates)
        assert failed.event["event_type"] == "MODEL_OPERATION_FAILED"
        assert failed.event["payload"]["error"]["category"] == "NETWORK"

    def test_error_message_never_contains_credentials(self) -> None:
        async def body():
            provider = FakeProvider(
                FakeScenario(
                    text_chunks=["a"],
                    fail_after_chunks=0,
                    fail_category=ErrorCategory.AUTH,
                    fail_provider_message="invalid api key sk-secret-12345",
                )
            )
            gateway = ModelGateway(provider)
            return await collect(gateway, make_request())

        _, candidates = run(body())

        failed = last_event(candidates)
        payload_text = repr(failed.event["payload"])
        assert "sk-secret-12345" not in payload_text
        assert failed.event["payload"]["error"]["provider_message"] is None

    def test_unclassified_exception_becomes_unknown(self) -> None:
        async def body():
            provider = FakeProvider(
                FakeScenario(text_chunks=["a"], raise_unclassified=True)
            )
            gateway = ModelGateway(provider)
            return await collect(gateway, make_request())

        _, candidates = run(body())

        failed = last_event(candidates)
        assert failed.event["event_type"] == "MODEL_OPERATION_FAILED"
        assert failed.event["payload"]["error"]["category"] == "UNKNOWN"
        assert failed.event["payload"]["error"]["retryable"] is False


class TestRetryPolicy:
    def test_retryable_failure_before_first_token_is_retried(self) -> None:
        """首字节前的可重试失败：重试后成功，事件里保留重试事实。"""

        async def body():
            provider = FakeProvider(
                FakeScenario(
                    text_chunks=["ok"],
                    retryable_failures_before_content=2,
                    fail_category=ErrorCategory.NETWORK,
                )
            )
            gateway = ModelGateway(provider)
            return await collect(gateway, make_request(max_retries=3))

        _, candidates = run(body())

        event_types = [c.event["event_type"] for c in candidates]
        assert event_types[-1] == "MODEL_OPERATION_COMPLETED"
        # 重试事实进入正式事件（attempt 计数 > 1）
        completed = candidates[-1]
        assert completed.event["payload"]["attempts"] == 3

    def test_retry_exhaustion_produces_failure(self) -> None:
        async def body():
            provider = FakeProvider(
                FakeScenario(
                    text_chunks=["ok"],
                    retryable_failures_before_content=5,
                    fail_category=ErrorCategory.NETWORK,
                )
            )
            gateway = ModelGateway(provider)
            return await collect(gateway, make_request(max_retries=2))

        _, candidates = run(body())

        failed = last_event(candidates)
        assert failed.event["event_type"] == "MODEL_OPERATION_FAILED"
        assert failed.event["payload"]["error"]["category"] == "NETWORK"
        assert failed.event["payload"]["attempts"] == 3  # 首次 + 2 次重试

    def test_non_retryable_failure_is_not_retried(self) -> None:
        async def body():
            provider = FakeProvider(
                FakeScenario(
                    text_chunks=["ok"],
                    retryable_failures_before_content=3,
                    fail_category=ErrorCategory.AUTH,
                )
            )
            gateway = ModelGateway(provider)
            return await collect(gateway, make_request(max_retries=3))

        _, candidates = run(body())

        failed = last_event(candidates)
        assert failed.event["event_type"] == "MODEL_OPERATION_FAILED"
        assert failed.event["payload"]["attempts"] == 1


class TestProviderErrorContract:
    def test_provider_error_exposes_category_and_retryable(self) -> None:
        error = ProviderError(
            category=ErrorCategory.RATE_LIMIT, retryable=True, message="限流"
        )
        assert error.category is ErrorCategory.RATE_LIMIT
        assert error.retryable is True
        assert str(error) == "限流"
