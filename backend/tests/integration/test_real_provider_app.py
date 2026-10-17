"""Real provider commands through the authenticated application boundary."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from agent_bridge.app import build_integrated_app
from agent_bridge.contracts.envelopes import canonical_digest
from agent_bridge.model_gateway.openai_compatible import OpenAICompatibleProvider

from test_integrated_app import LineClient, task_command


def local_command(command_id: str) -> dict:
    payload: dict = {}
    return {
        "protocol_version": "0.5.1",
        "scope_type": "LOCAL_CONFIG",
        "scope_id": "local-config",
        "command_id": command_id,
        "command_idempotency_key": f"idem:{command_id}",
        "command_request_digest": canonical_digest(payload),
        "issuer": "local-user",
        "command_type": "LIST_LOCAL_MODELS",
        "payload": payload,
        "issued_at": "2026-09-26T00:00:00+00:00",
    }


def test_real_run_streams_and_persists_without_credentials(tmp_path) -> None:
    requests: list[httpx.Request] = []
    providers: list[OpenAICompatibleProvider] = []

    def relay(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=(
                'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":" world"},"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    async def body():
        def provider_factory():
            provider = OpenAICompatibleProvider(transport=httpx.MockTransport(relay))
            providers.append(provider)
            return provider

        app = build_integrated_app(
            tmp_path / "audit.sqlite3",
            workspace_root=tmp_path,
            real_provider_factory=provider_factory,
        )
        await app.start()
        try:
            client = LineClient("127.0.0.1", app.port, app.token)
            await client.connect()
            await client.send({"type": "command", "command": task_command(
                "REAL_MODEL_RUN", "real-1",
                {"model": "local-model", "messages": [{"role": "user", "content": "Hi"}], "system": "Be brief"},
            )})
            _, messages = await client.recv_until(
                lambda item: item.get("type") == "model_frame"
                and item["frame"]["frame_type"] == "MODEL_STREAM_END_FRAME"
            )
            await client.send({
                "type": "resume", "scope_type": "TASK", "scope_id": "task-1", "last_event_seq": 0
            })
            _, replay = await client.recv_until(lambda item: item.get("type") == "resume_done")
            await client.close()
            return messages, replay
        finally:
            await app.aclose()

    messages, replay = asyncio.run(body())
    assert [item["content_delta"] for item in messages if item.get("type") == "model_frame"
            and item["frame"]["frame_type"] == "MODEL_DELTA_FRAME"] == ["hello", " world"]
    events = [item["event"] for item in messages if item.get("type") == "event"]
    assert [item["event_type"] for item in events] == [
        "MODEL_OPERATION_PLANNED", "MODEL_OPERATION_STARTED",
        "MODEL_CONTENT_CHUNK_RECORDED", "MODEL_CONTENT_CHUNK_RECORDED",
        "MODEL_OPERATION_COMPLETED",
    ]
    assert [item["event"] for item in replay if item.get("type") == "event"] == events
    assert providers[0]._client.is_closed
    assert len(requests) == 1
    assert requests[0].url == "http://127.0.0.1:8080/v1/chat/completions"
    assert "authorization" not in requests[0].headers
    assert json.loads(requests[0].content) == {
        "model": "local-model", "messages": [
            {"role": "system", "content": "Be brief"}, {"role": "user", "content": "Hi"}
        ], "stream": True,
    }


@pytest.mark.parametrize(
    ("status_code", "expected_models", "expected_status", "expected_category"),
    [
        (200, ["model-a", "model-b"], "available", None),
        (404, [], "unavailable", "PROTOCOL"),
        (503, [], "unavailable", "SERVER"),
    ],
)
def test_model_discovery_publishes_local_config_event(
    tmp_path, status_code, expected_models, expected_status, expected_category
) -> None:
    def relay(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://127.0.0.1:8080/v1/models"
        assert "authorization" not in request.headers
        if status_code == 200:
            return httpx.Response(200, json={"data": [{"id": model} for model in expected_models]})
        return httpx.Response(status_code, json={"error": {"message": "sk-sensitive-token"}})

    async def body():
        app = build_integrated_app(
            tmp_path / "audit.sqlite3", workspace_root=tmp_path,
            real_provider_factory=lambda: OpenAICompatibleProvider(transport=httpx.MockTransport(relay)),
        )
        await app.start()
        try:
            client = LineClient("127.0.0.1", app.port, app.token)
            await client.connect()
            await client.send({"type": "command", "command": local_command("list-1")})
            event_message, _ = await client.recv_until(
                lambda item: item.get("type") == "event"
                and item["event"]["event_type"] == "LOCAL_MODELS_LISTED"
            )
            await client.close()
            return event_message["event"]
        finally:
            await app.aclose()

    event = asyncio.run(body())
    assert event["scope_type"] == "LOCAL_CONFIG"
    assert event["local_config_event_sequence"] == 1
    assert event["payload"] == {
        "models": expected_models, "status": expected_status, "category": expected_category
    }
    assert "sk-sensitive-token" not in json.dumps(event)


def test_failed_discovery_does_not_block_manual_model_run(tmp_path) -> None:
    def relay(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(404)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "manual works"}, "finish_reason": "stop"}]
        })

    async def body():
        app = build_integrated_app(
            tmp_path / "audit.sqlite3", workspace_root=tmp_path,
            real_provider_factory=lambda: OpenAICompatibleProvider(transport=httpx.MockTransport(relay)),
        )
        await app.start()
        try:
            client = LineClient("127.0.0.1", app.port, app.token)
            await client.connect()
            await client.send({"type": "command", "command": local_command("list-1")})
            await client.recv_until(lambda item: item.get("type") == "event"
                                    and item["event"]["event_type"] == "LOCAL_MODELS_LISTED")
            await client.send({"type": "command", "command": task_command(
                "REAL_MODEL_RUN", "real-1",
                {"model": "manual-model", "messages": [{"role": "user", "content": "Hi"}], "stream": False},
            )})
            _, messages = await client.recv_until(
                lambda item: item.get("type") == "model_frame"
                and item["frame"]["frame_type"] == "MODEL_STREAM_END_FRAME"
            )
            await client.close()
            return messages
        finally:
            await app.aclose()

    messages = asyncio.run(body())
    assert [item["content_delta"] for item in messages if item.get("type") == "model_frame"
            and item["frame"]["frame_type"] == "MODEL_DELTA_FRAME"] == ["manual works"]


def test_real_run_rejects_blank_model(tmp_path) -> None:
    async def body():
        app = build_integrated_app(tmp_path / "audit.sqlite3", workspace_root=tmp_path)
        await app.start()
        try:
            client = LineClient("127.0.0.1", app.port, app.token)
            await client.connect()
            await client.send({"type": "command", "command": task_command(
                "REAL_MODEL_RUN", "real-1", {"model": " ", "messages": []}
            )})
            result, _ = await client.recv_until(lambda item: item.get("type") == "command_result"
                                                and item.get("status") == "ROUTER_ERROR")
            await client.close()
            return result
        finally:
            await app.aclose()

    result = asyncio.run(body())
    assert "non-empty model" in result["errors"][0]
