from __future__ import annotations

import json

import httpx
import pytest

from conftest import run
from agent_bridge.model_gateway import (
    ErrorCategory,
    ModelRequest,
    OpenAICompatibleProvider,
    ProviderEventKind,
    TimeoutPolicy,
)


def request(**overrides):
    data = dict(
        operation_id="op-1", model="local-model", messages=[{"role": "user", "content": "hi"}],
        system=None, timeout=TimeoutPolicy(1, 1, 1), task_id="t", run_id="r", agent_id="a",
    )
    data.update(overrides)
    return ModelRequest(**data)


class RouteTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler): self.handler = handler
    async def handle_async_request(self, req): return await self.handler(req)


def response(req, status=200, body=None, headers=None):
    return httpx.Response(status, headers=headers or {"content-type": "application/json"}, json=body, request=req)


def test_models_uses_v1_path_without_authorization():
    async def body():
        seen = {}
        async def handler(req):
            seen["path"] = req.url.path; seen["auth"] = req.headers.get("authorization")
            return response(req, body={"data": [{"id": "a"}, {"id": "b"}]})
        p = OpenAICompatibleProvider("http://local.test", RouteTransport(handler))
        models = await p.list_models(); p.close()
        return models, seen
    models, seen = run(body())
    assert models == ["a", "b"]
    assert seen == {"path": "/v1/models", "auth": None}


def test_stream_parses_sse_and_done():
    async def body():
        async def handler(req):
            payload = b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n' \
                b'data: {"choices":[{"delta":{"content":"llo"}}],"usage":{"total_tokens":2}}\n\n' \
                b'data: [DONE]\n\n'
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=payload, request=req)
        p = OpenAICompatibleProvider(transport=RouteTransport(handler))
        return [e async for e in p.stream(request())]
    events = run(body())
    assert [e.kind for e in events] == [ProviderEventKind.STARTED, ProviderEventKind.DELTA, ProviderEventKind.DELTA, ProviderEventKind.COMPLETED]
    assert [e.text for e in events if e.kind is ProviderEventKind.DELTA] == ["he", "llo"]
    assert events[-1].usage == {"total_tokens": 2}


def test_stream_parses_non_stream_json():
    async def body():
        async def handler(req):
            return response(req, body={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"x": 1}})
        p = OpenAICompatibleProvider(transport=RouteTransport(handler))
        return [e async for e in p.stream(request())]
    events = run(body())
    assert [e.kind for e in events] == [ProviderEventKind.STARTED, ProviderEventKind.DELTA, ProviderEventKind.COMPLETED]
    assert events[1].text == "ok" and events[-1].usage == {"x": 1}


@pytest.mark.parametrize("status,category", [(401, ErrorCategory.AUTH), (429, ErrorCategory.RATE_LIMIT), (500, ErrorCategory.SERVER)])
def test_http_status_maps_error(status, category):
    async def body():
        async def handler(req): return response(req, status=status, body={"error": {"message": "bad"}})
        p = OpenAICompatibleProvider(transport=RouteTransport(handler))
        with pytest.raises(Exception) as exc: await p.list_models()
        return exc.value
    err = run(body())
    assert err.category is category


def test_malformed_sse_emits_protocol_failed_event():
    async def body():
        async def handler(req):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b"data: {bad}\n\ndata: [DONE]\n\n", request=req)
        p = OpenAICompatibleProvider(transport=RouteTransport(handler))
        return [e async for e in p.stream(request())]
    events = run(body())
    assert events[-1].kind is ProviderEventKind.FAILED
    assert events[-1].error.category is ErrorCategory.PROTOCOL


def test_cancel_closes_response_and_ignores_later_chunks():
    async def body():
        async def handler(req):
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n', request=req)
        p = OpenAICompatibleProvider(transport=RouteTransport(handler))
        events = [e async for e in p.stream(request())]
        p.request_cancel("op-1")
        return events
    assert run(body())[1].text == "a"
