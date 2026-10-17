"""OpenAI compatible provider for the local relay.

The provider intentionally has a small surface: it talks to the fixed local
OpenAI compatible HTTP API, does not send credentials, and translates either
SSE or JSON chat completion responses into the common ``ProviderEvent`` types.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from agent_bridge.model_gateway.provider import ModelProvider
from agent_bridge.model_gateway.types import (
    DEFAULT_RETRYABLE_CATEGORIES,
    ErrorCategory,
    ModelRequest,
    ProviderError,
    ProviderEvent,
    ProviderEventKind,
    TimeoutPhase,
)


DEFAULT_BASE_URL = "http://127.0.0.1:8080"


class OpenAICompatibleProvider(ModelProvider):
    """Provider for a local OpenAI-compatible relay.

    ``transport`` is injectable so tests and embedders can provide an
    ``httpx.AsyncBaseTransport`` without opening a real socket.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        base_url = base_url.rstrip("/")
        if not base_url:
            raise ValueError("base_url cannot be empty")
        self.base_url = base_url
        self._client = httpx.AsyncClient(base_url=base_url, transport=transport)
        self._cancel_flags: dict[str, bool] = {}
        self._active_responses: dict[str, httpx.Response] = {}
        self._closed = False

    async def list_models(self) -> list[str]:
        """Return model ids advertised by ``GET /v1/models``."""

        self._ensure_open()
        try:
            response = await self._client.get("/v1/models", timeout=10.0)
        except Exception as exc:
            raise self._network_error(exc, phase=TimeoutPhase.CONNECT) from exc
        try:
            self._raise_for_status(response)
            try:
                payload = response.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise self._protocol_error("模型列表响应不是有效 JSON") from exc
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, list):
                raise self._protocol_error("模型列表响应缺少 data 数组")
            models: list[str] = []
            for item in data:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise self._protocol_error("模型列表包含无效模型条目")
                models.append(item["id"])
            return models
        finally:
            await response.aclose()

    def stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        return self._stream(request)

    async def _stream(self, request: ModelRequest) -> AsyncIterator[ProviderEvent]:
        self._ensure_open()
        operation_id = request.operation_id
        self._cancel_flags.setdefault(operation_id, False)
        if self._cancel_flags[operation_id]:
            yield ProviderEvent(kind=ProviderEventKind.CANCELLED)
            self._cancel_flags.pop(operation_id, None)
            return

        messages = [dict(message) for message in request.messages]
        if request.system:
            messages.insert(0, {"role": "system", "content": request.system})
        payload = {"model": request.model, "messages": messages, "stream": request.stream}
        timeout = httpx.Timeout(
            connect=request.timeout.connect_timeout_s,
            read=None,
            write=request.timeout.connect_timeout_s,
            pool=request.timeout.connect_timeout_s,
        )

        try:
            async with self._client.stream(
                "POST",
                "/v1/chat/completions",
                json=payload,
                timeout=timeout,
            ) as response:
                self._active_responses[operation_id] = response
                try:
                    self._raise_for_status(response)
                    yield ProviderEvent(kind=ProviderEventKind.STARTED)
                    content_type = response.headers.get("content-type", "").lower()
                    if "event-stream" in content_type:
                        async for event in self._iter_sse(response, request):
                            yield event
                    else:
                        # A relay may ignore ``stream`` and return one JSON
                        # completion. Read and parse it through the same event
                        # contract.
                        try:
                            raw = await response.aread()
                            payload_json = json.loads(raw)
                        except (httpx.HTTPError, ValueError, json.JSONDecodeError):
                            yield ProviderEvent(
                                kind=ProviderEventKind.FAILED,
                                error=self._protocol_error("模型响应不是有效 JSON"),
                            )
                            return
                        for event in self._events_from_json(payload_json):
                            yield event
                except httpx.TimeoutException as exc:
                    if self._cancel_flags.get(operation_id):
                        yield ProviderEvent(kind=ProviderEventKind.CANCELLED)
                    else:
                        yield ProviderEvent(
                            kind=ProviderEventKind.FAILED,
                            error=ProviderError(
                                category=ErrorCategory.TIMEOUT,
                                retryable=True,
                                message="模型响应超时",
                                timeout_phase=TimeoutPhase.STREAM_IDLE,
                            ),
                        )
                except httpx.HTTPError as exc:
                    if self._cancel_flags.get(operation_id):
                        yield ProviderEvent(kind=ProviderEventKind.CANCELLED)
                    else:
                        yield ProviderEvent(
                            kind=ProviderEventKind.FAILED,
                            error=self._network_error(exc),
                        )
                finally:
                    self._active_responses.pop(operation_id, None)
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            if self._cancel_flags.get(operation_id):
                yield ProviderEvent(kind=ProviderEventKind.CANCELLED)
            else:
                raise self._network_error(exc, phase=TimeoutPhase.CONNECT) from exc
        except httpx.HTTPError as exc:
            if self._cancel_flags.get(operation_id):
                yield ProviderEvent(kind=ProviderEventKind.CANCELLED)
            else:
                raise self._network_error(exc) from exc
        finally:
            self._cancel_flags.pop(operation_id, None)

    async def _iter_sse(
        self, response: httpx.Response, request: ModelRequest
    ) -> AsyncIterator[ProviderEvent]:
        data_lines: list[str] = []
        usage: dict[str, Any] | None = None
        finish_reason: str | None = None
        saw_content = False
        iterator = response.aiter_lines().__aiter__()
        while True:
            if self._cancel_flags.get(request.operation_id):
                yield ProviderEvent(kind=ProviderEventKind.CANCELLED)
                return
            try:
                line = await asyncio.wait_for(
                    iterator.__anext__(),
                    timeout=(
                        request.timeout.stream_idle_timeout_s
                        if saw_content
                        else request.timeout.first_token_timeout_s
                    ),
                )
            except StopAsyncIteration:
                if data_lines:
                    event = self._parse_sse_event("\n".join(data_lines), usage, finish_reason)
                    if event is not None:
                        yield event
                        if event.kind is ProviderEventKind.FAILED:
                            return
                # Some relays close the stream without a [DONE] marker.
                yield ProviderEvent(
                    kind=ProviderEventKind.COMPLETED,
                    finish_reason=finish_reason or "stop",
                    usage=usage,
                )
                return
            except asyncio.TimeoutError:
                yield ProviderEvent(
                    kind=ProviderEventKind.FAILED,
                    error=ProviderError(
                        category=ErrorCategory.TIMEOUT,
                        retryable=True,
                        message="模型流式响应空闲超时",
                        timeout_phase=(
                            TimeoutPhase.STREAM_IDLE
                            if saw_content
                            else TimeoutPhase.FIRST_TOKEN
                        ),
                    ),
                )
                return
            except UnicodeDecodeError:
                yield ProviderEvent(
                    kind=ProviderEventKind.FAILED,
                    error=self._protocol_error("模型 SSE 数据不是有效文本"),
                )
                return

            if line == "":
                if not data_lines:
                    continue
                event, usage, finish_reason, done = self._parse_sse_payload(
                    "\n".join(data_lines), usage, finish_reason
                )
                data_lines.clear()
                if event is not None:
                    yield event
                    if event.kind is ProviderEventKind.FAILED:
                        return
                    if event.kind is ProviderEventKind.DELTA:
                        saw_content = True
                if done:
                    yield ProviderEvent(
                        kind=ProviderEventKind.COMPLETED,
                        finish_reason=finish_reason or "stop",
                        usage=usage,
                    )
                    return
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

    def _parse_sse_payload(
        self,
        raw: str,
        usage: dict[str, Any] | None,
        finish_reason: str | None,
    ) -> tuple[ProviderEvent | None, dict[str, Any] | None, str | None, bool]:
        if raw.strip() == "[DONE]":
            return None, usage, finish_reason, True
        try:
            payload = json.loads(raw)
        except (ValueError, json.JSONDecodeError):
            return (
                ProviderEvent(
                    kind=ProviderEventKind.FAILED,
                    error=self._protocol_error("模型 SSE 数据不是有效 JSON"),
                ),
                usage,
                finish_reason,
                True,
            )
        if not isinstance(payload, dict):
            return (
                ProviderEvent(
                    kind=ProviderEventKind.FAILED,
                    error=self._protocol_error("模型 SSE 数据格式无效"),
                ),
                usage,
                finish_reason,
                True,
            )
        if isinstance(payload.get("usage"), dict):
            usage = payload["usage"]
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None, usage, finish_reason, False
        choice = choices[0]
        if not isinstance(choice, dict):
            return (
                ProviderEvent(
                    kind=ProviderEventKind.FAILED,
                    error=self._protocol_error("模型 SSE choices 格式无效"),
                ),
                usage,
                finish_reason,
                True,
            )
        if isinstance(choice.get("finish_reason"), str):
            finish_reason = choice["finish_reason"]
        delta = choice.get("delta")
        text = delta.get("content") if isinstance(delta, dict) else None
        if isinstance(text, str) and text:
            return ProviderEvent(kind=ProviderEventKind.DELTA, text=text), usage, finish_reason, False
        return None, usage, finish_reason, False

    def _parse_sse_event(
        self,
        raw: str,
        usage: dict[str, Any] | None,
        finish_reason: str | None,
    ) -> ProviderEvent | None:
        event, _, _, _ = self._parse_sse_payload(raw, usage, finish_reason)
        return event

    def _events_from_json(self, payload: Any) -> list[ProviderEvent]:
        if not isinstance(payload, dict):
            return [ProviderEvent(kind=ProviderEventKind.FAILED, error=self._protocol_error("模型响应格式无效"))]
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return [ProviderEvent(kind=ProviderEventKind.FAILED, error=self._protocol_error("模型响应缺少 choices"))]
        choice = choices[0]
        if not isinstance(choice, dict):
            return [ProviderEvent(kind=ProviderEventKind.FAILED, error=self._protocol_error("模型响应 choices 格式无效"))]
        events: list[ProviderEvent] = []
        message = choice.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if isinstance(text, str) and text:
            events.append(ProviderEvent(kind=ProviderEventKind.DELTA, text=text))
        events.append(
            ProviderEvent(
                kind=ProviderEventKind.COMPLETED,
                finish_reason=choice.get("finish_reason") if isinstance(choice.get("finish_reason"), str) else "stop",
                usage=payload.get("usage") if isinstance(payload.get("usage"), dict) else None,
            )
        )
        return events

    def request_cancel(self, operation_id: str) -> None:
        self._cancel_flags[operation_id] = True
        response = self._active_responses.get(operation_id)
        if response is not None:
            try:
                asyncio.get_running_loop().create_task(response.aclose())
            except RuntimeError:
                pass

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._client.aclose()

    def close(self) -> None:
        if not self._closed:
            asyncio.get_running_loop().create_task(self.aclose())

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("provider is closed")

    @staticmethod
    def _protocol_error(message: str) -> ProviderError:
        return ProviderError(
            category=ErrorCategory.PROTOCOL,
            retryable=False,
            message=message,
        )

    @staticmethod
    def _network_error(
        exc: Exception, *, phase: TimeoutPhase | None = None
    ) -> ProviderError:
        if isinstance(exc, httpx.TimeoutException):
            return ProviderError(
                category=ErrorCategory.TIMEOUT,
                retryable=True,
                message="连接本地模型中转超时",
                timeout_phase=phase or TimeoutPhase.CONNECT,
            )
        return ProviderError(
            category=ErrorCategory.NETWORK,
            retryable=ErrorCategory.NETWORK in DEFAULT_RETRYABLE_CATEGORIES,
            message="无法连接本地模型中转",
            provider_message=str(exc) or None,
        )

    @classmethod
    def _raise_for_status(cls, response: httpx.Response) -> None:
        if response.is_success:
            return
        status = response.status_code
        category = (
            ErrorCategory.AUTH
            if status in (401, 403)
            else ErrorCategory.RATE_LIMIT
            if status == 429
            else ErrorCategory.TIMEOUT
            if status == 408
            else ErrorCategory.SERVER
            if status >= 500
            else ErrorCategory.PROTOCOL
        )
        retryable = category in DEFAULT_RETRYABLE_CATEGORIES
        provider_message: str | None = None
        try:
            body = response.json()
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                message = body["error"].get("message")
                if isinstance(message, str):
                    provider_message = message
        except (ValueError, json.JSONDecodeError):
            pass
        raise ProviderError(
            category=category,
            retryable=retryable,
            message=f"模型中转返回 HTTP {status}",
            provider_message=provider_message,
        )
