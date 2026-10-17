"""M2 模型网关核心。

职责边界：
- 把 Provider 统一事件流转换为 M0 v0.5.1 §6 的临时模型流式帧（发给 M6/M7，仅 UI 即时显示）；
- 产出模型正式候选事件（MODEL_OPERATION_* / MODEL_CONTENT_CHUNK_RECORDED，交由持久化入口落盘）；
- 实施取消门：取消确认后到达的 delta 一律丢弃，不得进入有效业务内容；
- 实施三段超时（connect 语义由真实适配器处理，网关覆盖 first_token / stream_idle）；
- 错误归一化与脱敏：供应商原始消息经脱敏检查后才可保留；
- 首字节前的传输级失败做有限重试；已收到内容后失败只保留内容并产生 FAILED 终态。

网关不持久化任何事件、不访问文件、不持有凭据。
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent_bridge.model_gateway.provider import ModelProvider
from agent_bridge.model_gateway.types import (
    ErrorCategory,
    FrameType,
    ModelRequest,
    ProviderError,
    ProviderEventKind,
    TimeoutPhase,
)

PROTOCOL_VERSION = "0.5.1"

#: 供应商原始消息中命中任一模式即整体丢弃，宁缺毋滥。
_CREDENTIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)api[_-]?key"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{16,}"),
    re.compile(r"(?i)(password|passwd|secret|token)\s*[:=]"),
)


def sanitize_provider_message(text: str | None) -> str | None:
    """供应商原始消息脱敏：命中疑似凭据模式时返回 None。"""
    if text is None:
        return None
    for pattern in _CREDENTIAL_PATTERNS:
        if pattern.search(text):
            return None
    return text


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class GatewayFrame:
    """临时模型流式帧（M0 §6）：不是正式事件，无 task_event_sequence、无 event_id。

    frame 字典字段与 M0 严格对齐，另含 content_delta 扩展字段（UI 即时显示用），
    该扩展已记录在 zcode-to-ai-a 移交单中，待契约侧确认。
    """

    frame: dict[str, Any]


@dataclass
class GatewayCandidate:
    """模型正式候选事件：等待持久化入口确认后才会成为正式事件。"""

    event: dict[str, Any]


@dataclass
class _OperationState:
    """单个模型操作的网关内状态。"""

    operation_id: str
    cancel_requested: bool = False
    cancel_confirmed: bool = False
    received_content: bool = False
    content_refs: list[str] = field(default_factory=list)
    post_cancel_deltas_dropped: int = 0
    attempt: int = 0


class ModelGateway:
    """把统一 Provider 流转换为临时帧 + 正式候选事件流。"""

    def __init__(self, provider: ModelProvider) -> None:
        self._provider = provider
        self._active: dict[str, _OperationState] = {}

    async def cancel(self, operation_id: str) -> None:
        """请求取消指定操作。幂等；未知操作为空操作。"""
        state = self._active.get(operation_id)
        if state is None or state.cancel_requested:
            return
        state.cancel_requested = True
        self._provider.request_cancel(operation_id)

    async def execute(self, request: ModelRequest):
        """执行一次模型操作，产出 GatewayFrame 与 GatewayCandidate 的混合流。"""
        self._validate_scope(request)
        state = _OperationState(operation_id=request.operation_id)
        self._active[request.operation_id] = state
        try:
            yield GatewayCandidate(
                self._candidate(
                    request,
                    "MODEL_OPERATION_PLANNED",
                    {"model": request.model, "messages": len(request.messages)},
                )
            )
            attempt = 0
            while True:
                attempt += 1
                state.attempt = attempt
                try:
                    async for item in self._run_attempt(request, state, attempt):
                        yield item
                    return
                except ProviderError as err:
                    if (
                        err.retryable
                        and not state.received_content
                        and attempt <= request.max_retries
                    ):
                        continue
                    yield GatewayCandidate(
                        self._candidate(
                            request,
                            "MODEL_OPERATION_FAILED",
                            {
                                "error": self._normalized_error_payload(err),
                                "attempts": attempt,
                            },
                        )
                    )
                    yield GatewayFrame(self._frame(request, FrameType.MODEL_STREAM_END_FRAME, 0))
                    return
                except asyncio.CancelledError:
                    raise
                except Exception:
                    err = ProviderError(
                        category=ErrorCategory.UNKNOWN,
                        retryable=False,
                        message="模型网关发生未分类内部错误",
                    )
                    yield GatewayCandidate(
                        self._candidate(
                            request,
                            "MODEL_OPERATION_FAILED",
                            {
                                "error": self._normalized_error_payload(err),
                                "attempts": attempt,
                            },
                        )
                    )
                    yield GatewayFrame(self._frame(request, FrameType.MODEL_STREAM_END_FRAME, 0))
                    return
        finally:
            self._active.pop(request.operation_id, None)

    async def _run_attempt(self, request: ModelRequest, state: _OperationState, attempt: int):
        provider_iter = self._provider.stream(request).__aiter__()
        fragment = 0
        while True:
            timeout = (
                request.timeout.first_token_timeout_s
                if not state.received_content
                else request.timeout.stream_idle_timeout_s
            )
            try:
                event = await asyncio.wait_for(provider_iter.__anext__(), timeout=timeout)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                phase = (
                    TimeoutPhase.FIRST_TOKEN
                    if not state.received_content
                    else TimeoutPhase.STREAM_IDLE
                )
                raise ProviderError(
                    category=ErrorCategory.TIMEOUT,
                    retryable=True,
                    message=f"供应商响应超时（{phase.value} 阶段）",
                    timeout_phase=phase,
                ) from None

            # 取消门入口：取消请求后到达的第一个事件触发取消三件套
            if state.cancel_requested and not state.cancel_confirmed:
                yield GatewayCandidate(
                    self._candidate(request, "MODEL_OPERATION_CANCEL_REQUESTED", {})
                )
                yield GatewayCandidate(
                    self._candidate(
                        request,
                        "MODEL_OPERATION_CANCELLED",
                        {
                            "received_content_refs": list(state.content_refs),
                            "post_cancel_deltas_dropped": state.post_cancel_deltas_dropped,
                        },
                    )
                )
                yield GatewayFrame(
                    self._frame(request, FrameType.MODEL_STREAM_END_FRAME, fragment)
                )
                state.cancel_confirmed = True

            if state.cancel_confirmed:
                # 取消确认后的一切事件（含 delta）只作诊断计数，不进入有效业务内容
                if event.kind is ProviderEventKind.DELTA:
                    state.post_cancel_deltas_dropped += 1
                continue

            if event.kind is ProviderEventKind.CANCELLED:
                if not state.cancel_requested:
                    # Provider 自发取消（无用户请求）：仍按取消终态收尾
                    yield GatewayCandidate(
                        self._candidate(
                            request,
                            "MODEL_OPERATION_CANCELLED",
                            {"received_content_refs": list(state.content_refs)},
                        )
                    )
                    yield GatewayFrame(
                        self._frame(request, FrameType.MODEL_STREAM_END_FRAME, fragment)
                    )
                return

            if event.kind is ProviderEventKind.STARTED:
                yield GatewayFrame(
                    self._frame(request, FrameType.MODEL_STREAM_STARTED_FRAME, fragment)
                )
                yield GatewayCandidate(
                    self._candidate(request, "MODEL_OPERATION_STARTED", {"model": request.model})
                )
            elif event.kind is ProviderEventKind.DELTA:
                fragment += 1
                ref = f"content:{request.operation_id}:{fragment}"
                yield GatewayFrame(
                    self._frame(
                        request,
                        FrameType.MODEL_DELTA_FRAME,
                        fragment,
                        content_delta=event.text,
                        content_delta_ref=ref,
                    )
                )
                yield GatewayCandidate(
                    self._candidate(
                        request,
                        "MODEL_CONTENT_CHUNK_RECORDED",
                        {
                            "content_delta_ref": ref,
                            "text": event.text,
                            "model_fragment_sequence": fragment,
                        },
                        dedup_suffix=str(fragment),
                    )
                )
                state.received_content = True
                state.content_refs.append(ref)
            elif event.kind is ProviderEventKind.KEEPALIVE:
                yield GatewayFrame(
                    self._frame(
                        request, FrameType.MODEL_STREAM_KEEPALIVE_FRAME, fragment
                    )
                )
            elif event.kind is ProviderEventKind.COMPLETED:
                usage = dict(event.usage or {})
                yield GatewayCandidate(
                    self._candidate(
                        request,
                        "MODEL_OPERATION_COMPLETED",
                        {
                            "content_refs": list(state.content_refs),
                            "finish_reason": event.finish_reason,
                            "usage": usage,
                            "attempts": attempt,
                        },
                    )
                )
                yield GatewayFrame(
                    self._frame(request, FrameType.MODEL_STREAM_END_FRAME, fragment)
                )
                return
            elif event.kind is ProviderEventKind.FAILED:
                err = event.error or ProviderError(
                    category=ErrorCategory.UNKNOWN,
                    retryable=False,
                    message="供应商报告失败但未给出错误",
                )
                yield GatewayCandidate(
                    self._candidate(
                        request,
                        "MODEL_OPERATION_FAILED",
                        {
                            "error": self._normalized_error_payload(err),
                            "attempts": attempt,
                        },
                    )
                )
                yield GatewayFrame(
                    self._frame(request, FrameType.MODEL_STREAM_END_FRAME, fragment)
                )
                return

    # ---- 内部构造工具 ----

    def _validate_scope(self, request: ModelRequest) -> None:
        if request.scope_type != "TASK":
            raise ValueError(
                f"模型操作仅支持 TASK 作用域（收到 scope_type={request.scope_type}）"
            )
        if not request.task_id or not request.run_id:
            raise ValueError("TASK 作用域的模型请求必须携带 task_id 与 run_id")

    def _frame(
        self,
        request: ModelRequest,
        frame_type: FrameType,
        sequence: int,
        *,
        content_delta: str | None = None,
        content_delta_ref: str | None = None,
    ) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "scope_type": "TASK",
            "scope_id": request.task_id,
            "task_id": request.task_id,
            "run_id": request.run_id,
            "operation_id": request.operation_id,
            "model_fragment_sequence": sequence,
            "frame_type": frame_type.value,
            "content_delta_ref": content_delta_ref,
            "content_delta": content_delta,
            "received_at": _utc_now_iso(),
        }

    def _candidate(
        self,
        request: ModelRequest,
        event_type: str,
        payload: dict[str, Any],
        *,
        dedup_suffix: str | None = None,
    ) -> dict[str, Any]:
        dedup_key = f"{request.operation_id}:{event_type}"
        if dedup_suffix is not None:
            dedup_key = f"{dedup_key}:{dedup_suffix}"
        return {
            "protocol_version": PROTOCOL_VERSION,
            "scope_type": "TASK",
            "scope_id": request.task_id,
            "task_id": request.task_id,
            "run_id": request.run_id,
            "operation_id": request.operation_id,
            "event_deduplication_key": dedup_key,
            "causation_id": request.operation_id,
            "actor": request.agent_id,
            "event_type": event_type,
            "payload": payload,
            "occurred_at": _utc_now_iso(),
        }

    def _normalized_error_payload(self, err: ProviderError) -> dict[str, Any]:
        return {
            "category": err.category.value,
            "retryable": err.retryable,
            "message": sanitize_provider_message(err.message) or "供应商错误（详情已脱敏）",
            "provider_message": sanitize_provider_message(err.provider_message),
            "timeout_phase": err.timeout_phase.value if err.timeout_phase else None,
        }
