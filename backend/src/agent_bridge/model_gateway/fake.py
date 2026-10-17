"""Fake Provider：确定性、可编程的假模型供应商。

不连接任何真实服务、不读取文件、不持有凭据。
场景覆盖：正常流式、keepalive、首字节前失败（可重试）、流中途失败、
挂起（触发超时）、未分类异常、忽略取消（模拟取消后仍在到达的缓冲 delta）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from agent_bridge.model_gateway.provider import ModelProvider
from agent_bridge.model_gateway.types import (
    DEFAULT_RETRYABLE_CATEGORIES,
    ErrorCategory,
    ModelRequest,
    ProviderError,
    ProviderEvent,
    ProviderEventKind,
)


class FakeScenario:
    """一次可编程的假模型行为脚本。"""

    def __init__(
        self,
        *,
        text_chunks: list[str] | None = None,
        chunk_delay_s: float = 0.0,
        keepalives_between_chunks: bool = False,
        hang_before_content_s: float = 0.0,
        hang_at_chunk: int | None = None,
        hang_duration_s: float = 10.0,
        fail_after_chunks: int | None = None,
        fail_category: ErrorCategory = ErrorCategory.PROTOCOL,
        fail_provider_message: str | None = None,
        retryable_failures_before_content: int = 0,
        raise_unclassified: bool = False,
        ignore_cancel: bool = False,
    ) -> None:
        if text_chunks is None:
            text_chunks = ["你好，世界。"]
        self.text_chunks = list(text_chunks)
        self.chunk_delay_s = chunk_delay_s
        self.keepalives_between_chunks = keepalives_between_chunks
        self.hang_before_content_s = hang_before_content_s
        self.hang_at_chunk = hang_at_chunk
        self.hang_duration_s = hang_duration_s
        self.fail_after_chunks = fail_after_chunks
        self.fail_category = fail_category
        self.fail_provider_message = fail_provider_message
        self.retryable_failures_before_content = retryable_failures_before_content
        self.raise_unclassified = raise_unclassified
        self.ignore_cancel = ignore_cancel


class FakeProvider(ModelProvider):
    """按场景脚本产出事件流的假供应商。

    每个操作维护独立的尝试计数与取消标志，保证多操作互不干扰。
    """

    def __init__(self, scenario: FakeScenario) -> None:
        self.scenario = scenario
        self._attempts: dict[str, int] = {}
        self._cancel_flags: dict[str, bool] = {}
        # 诊断计数：取消确认后被网关丢弃的 delta 数（仅测试与诊断用）
        self.post_cancel_deltas_emitted = 0

    def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        return self._run(request)

    def request_cancel(self, operation_id: str) -> None:
        self._cancel_flags[operation_id] = True

    async def _run(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        operation_id = request.operation_id
        attempt = self._attempts.get(operation_id, 0)
        self._attempts[operation_id] = attempt + 1
        cancelled = self._cancel_flags.get(operation_id, False)

        if self.scenario.raise_unclassified:
            raise RuntimeError("fake provider internal glitch")

        if attempt < self.scenario.retryable_failures_before_content:
            raise ProviderError(
                category=self.scenario.fail_category,
                retryable=self.scenario.fail_category in DEFAULT_RETRYABLE_CATEGORIES,
                message=f"供应商传输失败（第 {attempt + 1} 次尝试前）",
                provider_message=self.scenario.fail_provider_message,
            )

        if self.scenario.hang_before_content_s > 0:
            await asyncio.sleep(self.scenario.hang_before_content_s)

        yield ProviderEvent(kind=ProviderEventKind.STARTED)

        emitted = 0
        for chunk in self.scenario.text_chunks:
            if self.scenario.keepalives_between_chunks:
                yield ProviderEvent(kind=ProviderEventKind.KEEPALIVE)

            if self.scenario.hang_at_chunk is not None and emitted == self.scenario.hang_at_chunk:
                await asyncio.sleep(self.scenario.hang_duration_s)

            await asyncio.sleep(self.scenario.chunk_delay_s)

            if (
                self.scenario.fail_after_chunks is not None
                and emitted == self.scenario.fail_after_chunks
            ):
                yield ProviderEvent(
                    kind=ProviderEventKind.FAILED,
                    error=ProviderError(
                        category=self.scenario.fail_category,
                        retryable=self.scenario.fail_category in DEFAULT_RETRYABLE_CATEGORIES,
                        message="供应商流中途失败",
                        provider_message=self.scenario.fail_provider_message,
                    ),
                )
                return

            cancelled = self._cancel_flags.get(operation_id, False)
            if cancelled and not self.scenario.ignore_cancel:
                yield ProviderEvent(kind=ProviderEventKind.CANCELLED)
                return

            if cancelled and self.scenario.ignore_cancel:
                # 模拟取消确认后仍在到达的缓冲 delta；网关必须丢弃
                self.post_cancel_deltas_emitted += 1

            yield ProviderEvent(kind=ProviderEventKind.DELTA, text=chunk)
            emitted += 1

        yield ProviderEvent(
            kind=ProviderEventKind.COMPLETED,
            finish_reason="stop",
            usage={"chunk_count": emitted, "text_length": len("".join(self.scenario.text_chunks))},
        )
