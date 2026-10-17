# 真实模型连接 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Windows 本机桌面工作台中配置多个命名连接，先通过 OpenAI 兼容中转站完成一次真实模型请求，并保持凭据、任务状态和工具权限边界不被绕过。

**Architecture:** Python 后端新增 `LocalConfigService`、SQLite 连接元数据仓库、Windows 凭据管理器适配和 `OpenAICompatibleProvider`。所有配置命令仍经 M6 的 `LOCAL_CONFIG` 登记，真实任务请求仍经 M1/M2/M5/M6，Electron renderer 只接收脱敏配置状态和正式事件。

**Tech Stack:** Python 3.11+、SQLite WAL、现有 M0 v0.5.1 契约、`httpx` 异步 HTTP、Windows Credential Manager/CredUI、Electron 44、React 19、TypeScript、Vitest、pytest。

## Global Constraints

- 平台固定为 Windows 本机，单用户模式，不增加登录、团队或远程服务。
- 第一阶段只实现 `openai_compatible` Provider；Claude Anthropic Messages 和 Z.AI 只预留注册边界。
- API Key 保存到 Windows 凭据管理器，不保存到明文文件、SQLite、事件、日志、URL、剪贴板或导出配置。
- API Key 不进入 React 状态；密钥录入由主进程触发系统安全输入通道，页面只显示凭据状态。
- Provider 不写数据库、不访问项目文件、不执行 Shell/Git；正式事件仍由 M5 先持久化再由 M6 发布。
- `LOCAL_CONFIG` 命令不要求 `task_id`、`run_id` 或 `workspace_identity`，不得获得项目副作用权限。
- 真实工具执行、审批 UI、Git、Shell、下载和安装依赖保持关闭。
- 流式和非流式响应都必须转换成现有 `ProviderEvent`；只在收到内容前做有限重试。
- 完成前必须同步 `README.md`、`docs/current-status.md` 和 Provider 模块说明，并清理测试数据库、缓存和临时脚本。
- 当前目录不是 Git 仓库；执行阶段不假称提交，使用每个任务的测试结果作为检查点。

---

### Task 1: 建立连接配置公共类型和依赖

**Files:**
- Create: `backend/src/agent_bridge/config/__init__.py`
- Create: `backend/src/agent_bridge/config/models.py`
- Modify: `backend/pyproject.toml`
- Test: `backend/tests/config/test_models.py`

**Interfaces:**
- Produces `ProviderProfile`, `ConnectionCapabilities`, `ConnectionTestStatus` and `normalize_base_url()` for Tasks 2-7.
- `ProviderProfile.public_dict()` is the only profile representation allowed to cross the M6/UI boundary.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import UTC, datetime

from agent_bridge.config.models import (
    ConnectionCapabilities,
    ConnectionTestStatus,
    ProviderProfile,
    normalize_base_url,
)


def test_profile_public_dict_has_no_secret_field():
    profile = ProviderProfile.new(
        display_name="ChatGPT 中转站",
        protocol="openai_compatible",
        base_url="https://relay.example/v1/",
        model_name="gpt-test",
    )
    public = profile.public_dict()
    assert public["base_url"] == "https://relay.example/v1"
    assert public["credential_status"] == "UNCONFIGURED"
    assert "api_key" not in public
    assert "secret" not in public


def test_normalize_base_url_rejects_non_http_and_duplicate_slash():
    assert normalize_base_url("https://relay.example/v1/") == "https://relay.example/v1"
    try:
        normalize_base_url("file:///tmp/provider")
    except ValueError as error:
        assert "http" in str(error)
    else:
        raise AssertionError("non-http URL was accepted")


def test_connection_capabilities_and_status_are_serializable():
    capabilities = ConnectionCapabilities(model_list=True, streaming=False)
    profile = ProviderProfile.new(
        display_name="relay",
        protocol="openai_compatible",
        base_url="http://127.0.0.1:8000",
        model_name=None,
    )
    updated = profile.with_test(
        status=ConnectionTestStatus.PASSED,
        capabilities=capabilities,
        error=None,
    )
    assert updated.last_test_status is ConnectionTestStatus.PASSED
    assert updated.public_dict()["capabilities"]["streaming"] is False
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests\config\test_models.py -q -p no:cacheprovider`

Expected: FAIL because `agent_bridge.config.models` does not exist.

- [ ] **Step 3: Add the minimal types**

Implement the following concrete shape in `models.py`:

```python
from dataclasses import dataclass, replace
from enum import Enum
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4


class ConnectionTestStatus(str, Enum):
    NEVER = "NEVER"
    PASSED = "PASSED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class ConnectionCapabilities:
    model_list: bool | None = None
    streaming: bool | None = None
    tested_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"model_list": self.model_list, "streaming": self.streaming, "tested_at": self.tested_at}


@dataclass(frozen=True)
class ProviderProfile:
    profile_id: str
    display_name: str
    protocol: str
    base_url: str
    model_name: str | None
    enabled: bool
    credential_ref: str
    credential_status: str
    capabilities: ConnectionCapabilities
    last_test_status: ConnectionTestStatus
    last_test_error: dict[str, str] | None
    created_at: str
    updated_at: str

    @classmethod
    def new(cls, *, display_name: str, protocol: str, base_url: str, model_name: str | None) -> "ProviderProfile":
        normalized = normalize_base_url(base_url)
        if protocol != "openai_compatible":
            raise ValueError("第一阶段只支持 openai_compatible")
        if not display_name.strip():
            raise ValueError("display_name 不能为空")
        profile_id = f"provider-{uuid4().hex}"
        now = datetime.now(UTC).isoformat()
        return cls(profile_id, display_name.strip(), protocol, normalized, model_name, True,
                   f"agent-bridge/provider/{profile_id}", "UNCONFIGURED",
                   ConnectionCapabilities(), ConnectionTestStatus.NEVER, None, now, now)

    def public_dict(self) -> dict[str, object]:
        return {"profile_id": self.profile_id, "display_name": self.display_name,
                "protocol": self.protocol, "base_url": self.base_url, "model_name": self.model_name,
                "enabled": self.enabled, "credential_ref": self.credential_ref,
                "credential_status": self.credential_status,
                "capabilities": self.capabilities.to_dict(),
                "last_test_status": self.last_test_status.value,
                "last_test_error": self.last_test_error,
                "created_at": self.created_at, "updated_at": self.updated_at}

    def with_test(self, *, status: ConnectionTestStatus, capabilities: ConnectionCapabilities,
                  error: dict[str, str] | None) -> "ProviderProfile":
        return replace(self, capabilities=capabilities, last_test_status=status,
                       last_test_error=error, updated_at=datetime.now(UTC).isoformat())


def normalize_base_url(value: str) -> str:
    parsed = urlsplit(value.strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Base URL 必须是 http 或 https 地址")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
```

Include `datetime` and `UTC` imports, and ensure `public_dict()` never accepts or creates an API key field.

- [ ] **Step 4: Run the focused test to verify it passes**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests\config\test_models.py -q -p no:cacheprovider`

Expected: `3 passed`.

- [ ] **Step 5: Record a test checkpoint**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests\config\test_models.py -q -p no:cacheprovider`; keep the passing output in the task notes. Do not run a Git command because the workspace has no repository.

### Task 2: Implement Windows Credential Manager boundary

**Files:**
- Create: `backend/src/agent_bridge/config/credentials.py`
- Create: `backend/tests/config/test_credentials.py`
- Modify: `backend/src/agent_bridge/config/__init__.py`

**Interfaces:**
- `CredentialStore.set_secret(credential_ref: str, secret: str) -> None`
- `CredentialStore.get_secret(credential_ref: str) -> str`
- `CredentialStore.delete_secret(credential_ref: str) -> None`
- `CredentialStore.prompt_and_set(credential_ref: str, display_name: str) -> None`
- `MemoryCredentialStore` is test-only and never selected by production assembly.

- [ ] **Step 1: Write failing tests**

```python
from agent_bridge.config.credentials import CredentialUnavailable, MemoryCredentialStore


def test_memory_store_round_trip_and_delete():
    store = MemoryCredentialStore()
    store.set_secret("agent-bridge/provider/p1", "secret-value")
    assert store.get_secret("agent-bridge/provider/p1") == "secret-value"
    store.delete_secret("agent-bridge/provider/p1")
    try:
        store.get_secret("agent-bridge/provider/p1")
    except CredentialUnavailable:
        pass
    else:
        raise AssertionError("deleted secret was returned")


def test_memory_store_rejects_empty_secret():
    store = MemoryCredentialStore()
    try:
        store.set_secret("ref", "")
    except ValueError as error:
        assert "empty" in str(error)
    else:
        raise AssertionError("empty credential was accepted")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests\config\test_credentials.py -q -p no:cacheprovider`

Expected: FAIL because the credential module is absent.

- [ ] **Step 3: Implement the boundary**

Define a `CredentialStore` protocol and `CredentialUnavailable(RuntimeError)`. `MemoryCredentialStore` must keep values only in a private dictionary for tests. `WindowsCredentialStore` must use `ctypes` bindings to `CredWriteW`, `CredReadW`, and `CredDeleteW` for generic credentials, with a credential target equal to `credential_ref`; on non-Windows or any API error it raises `CredentialUnavailable` and never writes a fallback file.

`prompt_and_set()` must open the Windows native secure credential prompt from the backend process, use the returned password as the API Key, reject cancellation and empty input, then call `set_secret()`. It must not return the secret to the caller. Tests patch the prompt function and assert only status/error is returned.

- [ ] **Step 4: Add credential redaction tests and run**

Add a test that calls `str(CredentialUnavailable("Windows credential unavailable"))` and verifies no supplied secret appears in the exception. Run:

`cd backend; .\.venv\Scripts\python.exe -m pytest tests\config\test_credentials.py tests\security\test_redaction.py -q -p no:cacheprovider`

Expected: all focused credential and redaction tests pass.

- [ ] **Step 5: Record checkpoint**

Run the same command again after removing any temporary credential files or diagnostic output. No production test may use a real API Key.

### Task 3: Add SQLite ProviderProfileStore

**Files:**
- Create: `backend/src/agent_bridge/config/profile_store.py`
- Create: `backend/tests/config/test_profile_store.py`

**Interfaces:**
- `ProviderProfileStore(path: Path | str)`
- `list_profiles() -> list[ProviderProfile]`
- `get(profile_id: str) -> ProviderProfile | None`
- `upsert(profile: ProviderProfile) -> ProviderProfile`
- `delete(profile_id: str) -> None`
- `set_active(profile_id: str | None) -> None`
- `get_active() -> str | None`
- `close() -> None`

- [ ] **Step 1: Write failing persistence tests**

```python
from agent_bridge.config.models import ProviderProfile
from agent_bridge.config.profile_store import ProviderProfileStore


def make_profile(name: str = "relay") -> ProviderProfile:
    return ProviderProfile.new(
        display_name=name,
        protocol="openai_compatible",
        base_url="https://relay.example/v1",
        model_name="model-a",
    )


def test_profile_round_trip_and_restart(tmp_path):
    path = tmp_path / "config.sqlite3"
    first = ProviderProfileStore(path)
    profile = make_profile()
    first.upsert(profile)
    first.set_active(profile.profile_id)
    first.close()

    second = ProviderProfileStore(path)
    loaded = second.get(profile.profile_id)
    assert loaded is not None
    assert loaded.public_dict() == profile.public_dict()
    assert second.get_active() == profile.profile_id
    second.close()


def test_profile_store_metadata_does_not_contain_api_key(tmp_path):
    path = tmp_path / "config.sqlite3"
    store = ProviderProfileStore(path)
    store.upsert(make_profile())
    store.close()
    assert "sk-test-secret" not in path.read_bytes().decode("utf-8", errors="ignore")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests\config\test_profile_store.py -q -p no:cacheprovider`

Expected: FAIL because the store is absent.

- [ ] **Step 3: Implement schema and mapping**

Use `open_database()` with a schema containing `provider_profile` and `provider_active` tables. Store capabilities and error metadata as canonical JSON; never create a secret or api-key column. Implement row-to-profile conversion with strict enum/status validation and delete active selection when its profile is deleted.

- [ ] **Step 4: Add update/delete/active tests and run**

Test that an upsert preserves `profile_id` and `created_at`, edits `display_name` and `model_name`, deleting the active profile returns `None`, and two profiles can coexist. Run:

`cd backend; .\.venv\Scripts\python.exe -m pytest tests\config\test_profile_store.py tests\storage -q -p no:cacheprovider`

Expected: new profile tests and existing storage tests pass.

- [ ] **Step 5: Record checkpoint and inspect database schema**

Run the focused command, then inspect the temporary SQLite schema with `sqlite3` or the existing Python test helper. Delete the temporary database after inspection.

### Task 4: Implement OpenAICompatibleProvider

**Files:**
- Create: `backend/src/agent_bridge/model_gateway/openai_compatible.py`
- Create: `backend/tests/model_gateway/test_openai_compatible.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/src/agent_bridge/model_gateway/__init__.py`

**Interfaces:**
- `OpenAICompatibleProvider(profile: ProviderProfile, credentials: CredentialStore, client_factory: Callable[..., httpx.AsyncClient] | None = None)`
- `stream(request: ModelRequest) -> AsyncIterator[ProviderEvent]`
- `request_cancel(operation_id: str) -> None`
- `close() -> None`
- `list_models() -> list[str]`
- `test_connection(model: str | None) -> ConnectionProbeResult`

- [ ] **Step 1: Write parser and error tests first**

```python
import asyncio
import httpx
import pytest
from agent_bridge.config.credentials import MemoryCredentialStore
from agent_bridge.config.models import ProviderProfile
from agent_bridge.model_gateway.openai_compatible import OpenAICompatibleProvider
from agent_bridge.model_gateway.types import ModelRequest, ProviderError, ProviderEventKind, TimeoutPolicy


def profile_and_store():
    profile = ProviderProfile.new(
        display_name="relay", protocol="openai_compatible",
        base_url="https://relay.example/v1", model_name="gpt-test",
    )
    credentials = MemoryCredentialStore()
    credentials.set_secret(profile.credential_ref, "sk-test-secret")
    return profile, credentials


def request():
    return ModelRequest(
        operation_id="op-1", model="gpt-test", messages=[{"role": "user", "content": "hi"}],
        system=None, timeout=TimeoutPolicy(2, 2, 2), task_id="task-1", run_id="run-1",
    )


def test_sse_stream_is_normalized():
    async def body():
        async def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == "Bearer sk-test-secret"
            return httpx.Response(200, text=(
                'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
                'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                'data: [DONE]\n\n'
            ), headers={"content-type": "text/event-stream"})
        profile, credentials = profile_and_store()
        provider = OpenAICompatibleProvider(
            profile, credentials,
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), **kwargs),
        )
        events = [event async for event in provider.stream(request())]
        assert [event.kind for event in events] == [
            ProviderEventKind.STARTED, ProviderEventKind.DELTA, ProviderEventKind.COMPLETED,
        ]
        assert events[1].text == "hello"
    asyncio.run(body())


def test_http_429_raises_retryable_rate_limit():
    async def body():
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": {"message": "slow down"}})
        profile, credentials = profile_and_store()
        provider = OpenAICompatibleProvider(
            profile, credentials,
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(handler), **kwargs),
        )
        with pytest.raises(ProviderError) as raised:
            _ = [event async for event in provider.stream(request())]
        assert raised.value.category.value == "RATE_LIMIT"
        assert raised.value.retryable is True
    asyncio.run(body())
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests\model_gateway\test_openai_compatible.py -q -p no:cacheprovider`

Expected: FAIL because the adapter and `httpx` dependency are absent.

- [ ] **Step 3: Add the adapter and dependency**

Add a pinned compatible `httpx` range to `backend/pyproject.toml`. Implement an async client with `httpx.Timeout(connect=..., read=..., write=..., pool=...)`. Read the credential only immediately before request construction. Emit `STARTED`, `DELTA`, `KEEPALIVE` when an SSE comment/empty heartbeat is received, and `COMPLETED`; request-level non-2xx responses raise `ProviderError` so M2 can apply its existing pre-content retry policy, while stream-level failures emit `FAILED` events. Parse only `choices[0].delta.content`, `finish_reason`, `usage`, and `[DONE]`. Keep an operation-to-task cancellation map and close the stream on `request_cancel()`.

`list_models()` calls `/models`, accepts an object whose `data` is a list of objects with string `id`, and raises a sanitized `ProviderError` for invalid shape. `test_connection()` calls `/models` when possible and then a one-token `stream=true` request for the selected model, returning `ConnectionProbeResult` with `authenticated`, `model_available`, `streaming`, `first_byte_ms`, `total_ms`, and sanitized error data.

- [ ] **Step 4: Add focused edge-case tests and run**

Cover non-stream JSON, 401/403/400/500 mappings, invalid SSE JSON, `[DONE]` without finish reason, cancellation, connect timeout, and a response containing `sk-test-secret` whose event/error output must not contain the secret. Run:

`cd backend; .\.venv\Scripts\python.exe -m pytest tests\model_gateway\test_openai_compatible.py tests\model_gateway\test_gateway_* -q -p no:cacheprovider`

Expected: all Provider and existing gateway tests pass.

- [ ] **Step 5: Record checkpoint**

Run the same command after removing all temporary mock output files. Do not call a real endpoint from automated tests.

### Task 5: Implement LocalConfigService and formal LOCAL_CONFIG events

**Files:**
- Create: `backend/src/agent_bridge/config/service.py`
- Create: `backend/tests/config/test_service.py`
- Modify: `backend/src/agent_bridge/app.py`
- Modify: `backend/src/agent_bridge/transport/interfaces.py` only if a typed config port is needed

**Interfaces:**
- `LocalConfigService(store: ProviderProfileStore, credentials: CredentialStore, provider_factory: Callable[[ProviderProfile], ModelProvider])`
- `execute(command: dict[str, Any]) -> list[dict[str, Any]]`
- Events use `scope_type="LOCAL_CONFIG"`, `scope_id="provider-profiles"`, `operation_id=command["command_id"]`, and deduplication key `"<command_id>:<event_type>"`.

- [ ] **Step 1: Write failing service tests**

```python
def command(command_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "command_id": f"cmd-{command_type.lower()}",
        "scope_type": "LOCAL_CONFIG",
        "scope_id": "provider-profiles",
        "command_type": command_type,
        "payload": payload,
    }


def make_service(tmp_path):
    events: list[dict[str, object]] = []
    credentials = RecordingCredentialStore()
    service = LocalConfigService(
        ProviderProfileStore(tmp_path / "config.sqlite3"),
        credentials,
        provider_factory=lambda _profile: FakeProvider(FakeScenario(text_chunks=["ok"])),
    )
    return service, events, credentials


def test_upsert_emits_public_profile_event(tmp_path):
    service, events, _ = make_service(tmp_path)
    result = asyncio.run(service.execute(command("UPSERT_PROVIDER_PROFILE", {
        "profile_id": "provider-p1", "display_name": "relay",
        "protocol": "openai_compatible", "base_url": "https://relay.example/v1",
        "model_name": "gpt-test", "enabled": True,
    })))
    assert result[0]["event_type"] == "PROVIDER_PROFILE_SAVED"
    assert "api_key" not in result[0]["payload"]
    assert result[0]["scope_type"] == "LOCAL_CONFIG"


def test_set_credential_contains_no_secret_in_event(tmp_path):
    service, _, credentials = make_service(tmp_path)
    result = asyncio.run(service.execute(command("SET_PROVIDER_CREDENTIAL", {
        "profile_id": "provider-p1", "display_name": "relay",
    })))
    assert "sk-test-secret" not in json.dumps(result, ensure_ascii=False)
    assert credentials.prompted_refs == ["agent-bridge/provider/provider-p1"]
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests\config\test_service.py -q -p no:cacheprovider`

Expected: FAIL because the service and event builders are absent.

- [ ] **Step 3: Implement command handlers and event builders**

Handle exactly these commands: `LIST_PROVIDER_PROFILES`, `UPSERT_PROVIDER_PROFILE`, `DELETE_PROVIDER_PROFILE`, `SET_PROVIDER_CREDENTIAL`, `CLEAR_PROVIDER_CREDENTIAL`, `LIST_PROVIDER_MODELS`, `TEST_PROVIDER_CONNECTION`, and `SET_ACTIVE_PROVIDER_PROFILE`. Every result is a candidate event; query results are `PROVIDER_PROFILES_SNAPSHOT` or `PROVIDER_MODELS_LISTED`, mutations are `PROVIDER_PROFILE_SAVED`, `PROVIDER_PROFILE_DELETED`, `PROVIDER_CREDENTIAL_STATUS_CHANGED`, `PROVIDER_CONNECTION_TESTED`, and `ACTIVE_PROVIDER_PROFILE_CHANGED`.

`SET_PROVIDER_CREDENTIAL` invokes `prompt_and_set()` using only `profile_id` and display name. It returns status metadata, never a secret. The service must reject unknown profile IDs, unsupported protocols, invalid URLs, and missing model names for model-specific operations with sanitized `LOCAL_CONFIG` failure events.

- [ ] **Step 4: Wire the service into IntegratedCommandRouter**

Instantiate `ProviderProfileStore` and production `WindowsCredentialStore` in `IntegratedApp`, inject `LocalConfigService`, and route the eight command types before Fake/TASK handlers. For every candidate returned by the service, call `server.publish_event()` so M5 persists before M6 publishes. Close the profile store and provider registry during `aclose()`.

- [ ] **Step 5: Run service, storage and integration tests**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests\config tests\storage tests\transport tests\integration -q -p no:cacheprovider`

Expected: all new configuration tests plus existing transport/storage/integration tests pass.

### Task 6: Add one real TASK model route

**Files:**
- Modify: `backend/src/agent_bridge/orchestrator/core.py`
- Modify: `backend/src/agent_bridge/app.py`
- Create: `backend/tests/integration/test_real_model_run.py`

**Interfaces:**
- Add the M1 command type `REAL_MODEL_RUN` as a task operation request that emits `MODEL_OPERATION_REQUESTED` without bypassing lifecycle invariants.
- `IntegratedCommandRouter._start_real_model_run(command)` resolves `profile_id` through `ProviderRegistry`, constructs `ModelRequest`, and drives the existing `ModelGateway`.

- [ ] **Step 1: Write failing integration test**

```python
from agent_bridge.tests.transport.conftest import LineClient, make_command


def make_task_command(*, command_type: str, payload: dict[str, object]) -> dict[str, object]:
    return make_command(
        command_type=command_type,
        scope_type="TASK",
        command_id=f"cmd-{command_type.lower()}",
        idempotency_key=f"idem-{command_type.lower()}",
        digest=f"digest-{command_type.lower()}",
        payload=payload,
    )


def test_real_model_run_uses_registered_provider_and_persists_events(tmp_path):
    async def body():
        app = build_integrated_app(tmp_path / "app.sqlite3", workspace_root=tmp_path / "workspace")
        app.register_test_provider("provider-p1", FakeProvider(FakeScenario(text_chunks=["真实路由"])))
        await app.start()
        try:
            client = LineClient("127.0.0.1", app.port, app.token)
            await client.connect()
            await client.send(make_task_command(
                command_type="REAL_MODEL_RUN",
                payload={"profile_id": "provider-p1", "prompt": "hello", "model": "gpt-test"},
            ))
            accepted = await client.recv_until(lambda message: message.get("type") == "command_result")
            event = await client.recv_until(lambda message: message.get("type") == "event" and
                                            message["event"].get("event_type") == "MODEL_OPERATION_COMPLETED")
            await client.close()
            return accepted, event
        finally:
            await app.aclose()
    accepted, event = asyncio.run(body())
    assert accepted["status"] == "ACCEPTED"
    assert event["event"]["event_type"] == "MODEL_OPERATION_COMPLETED"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests\integration\test_real_model_run.py -q -p no:cacheprovider`

Expected: FAIL because the real command and provider registry do not exist.

- [ ] **Step 3: Implement M1 request event and provider registry**

Extend the deterministic M1 transition table with `REAL_MODEL_RUN` only for an active TASK run; reject a created, paused, terminated or recovery-required run using the existing `TransitionError`. Add a registry method `register_test_provider()` used only by tests and a production `resolve(profile_id)` path that loads the profile and constructs `OpenAICompatibleProvider`.

- [ ] **Step 4: Implement the real model driver**

Reuse `_drive_model()` so frames go through `broadcast_frame()` and candidates go through `publish_event()`. Build `ModelRequest` with `operation_id`, task/run IDs, selected model, one user message from `payload["prompt"]`, and the existing three timeout values. Unknown profile, disabled profile, missing credential or missing prompt produces a sanitized router error and no provider call.

- [ ] **Step 5: Run the real-route and regression tests**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests\integration\test_real_model_run.py tests\integration\test_integrated_app.py tests\orchestrator tests\model_gateway -q -p no:cacheprovider`

Expected: real route passes with a fake registered adapter and all existing M1/M2 integration tests remain green.

### Task 7: Add desktop connection settings and real-run IPC

**Files:**
- Modify: `desktop/src/main/index.ts`
- Modify: `desktop/src/preload/index.ts`
- Modify: `desktop/src/renderer/bridge-api.ts`
- Create: `desktop/src/renderer/provider-store.ts`
- Create: `desktop/src/renderer/ConnectionSettings.tsx`
- Modify: `desktop/src/renderer/App.tsx`
- Modify: `desktop/src/renderer/index.html`
- Test: `desktop/tests/provider-store.test.ts`
- Modify: `desktop/tests/app.test.tsx`

**Interfaces:**
- Main IPC methods: `listProviderProfiles()`, `saveProviderProfile(input)`, `configureProviderCredential(profileId)`, `deleteProviderProfile(profileId)`, `listProviderModels(profileId)`, `testProviderConnection(profileId)`, `setActiveProviderProfile(profileId)`, `runRealTask(profileId, prompt)`.
- Renderer receives only `ProviderPublicProfile` and config event messages; no method returns a secret.
- `provider-store.ts` exports `createProviderStore()` with `profiles`, `activeProfileId`, `upsertFromEvent()`, `removeFromEvent()`, and `connectionState` projections.

- [ ] **Step 1: Write failing renderer projection tests**

```typescript
it("projects saved profiles without any credential value", () => {
  const store = createProviderStore();
  store.handleMessage({
    type: "event",
    event: {
      scope_type: "LOCAL_CONFIG",
      event_type: "PROVIDER_PROFILE_SAVED",
      payload: {
        profile: {
          profile_id: "provider-p1", display_name: "relay",
          protocol: "openai_compatible", base_url: "https://relay.example/v1",
          model_name: "gpt-test", credential_status: "CONFIGURED",
        },
      },
    },
  });
  expect(store.getState().profiles[0].display_name).toBe("relay");
  expect(JSON.stringify(store.getState())).not.toContain("sk-test-secret");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd desktop; npm test -- provider-store.test.ts`

Expected: FAIL because the provider store and settings UI are absent.

- [ ] **Step 3: Add narrow main/preload APIs**

Create a single main-process `makeLocalConfigCommand(commandType, payload)` helper that generates protocol `0.5.1`, scope `LOCAL_CONFIG`, scope ID `provider-profiles`, UUID command ID, stable SHA-256 request digest, issuer `local-user`, and issued timestamp. Each IPC handler submits one command through `BridgeConnection`; it never constructs an HTTP request and never accepts an API Key argument. `configureProviderCredential(profileId)` submits only the profile ID so the backend opens the secure prompt.

Expose the methods through `contextBridge` and update `BridgeApi` types. Keep `runFakeTask` and `runFakeTool` unchanged for regression coverage.

- [ ] **Step 4: Implement provider store and settings UI**

`provider-store.ts` must ignore TASK events, accept only the defined LOCAL_CONFIG event types, preserve profile metadata on reconnect, and expose status labels for `UNCONFIGURED`, `CONFIGURED`, `UNAVAILABLE`, `PASSED`, and `FAILED`. `ConnectionSettings.tsx` renders a compact list/form for name, Base URL, model name, enable toggle, model refresh, credential configure, test, delete, and active selection. It must not include an API Key input or display a secret. Add a prompt/result status area for the system credential dialog.

Add a minimal real-task prompt and “运行真实模型” button to `App.tsx`; it calls `runRealTask(activeProfileId, prompt)` and waits for formal events for final state. Existing Fake buttons remain available.

- [ ] **Step 5: Run desktop tests and typecheck**

Run: `cd desktop; npm test; npm run typecheck`

Expected: all existing desktop tests plus provider settings tests pass and TypeScript reports no errors.

### Task 8: End-to-end local configuration and provider probe tests

**Files:**
- Create: `backend/tests/integration/test_provider_config_flow.py`
- Modify: `desktop/tests/bridge-connection.test.ts`
- Modify: `desktop/tests/app.test.tsx`

**Interfaces:**
- The test server uses `httpx.MockTransport` or a local `asyncio.start_server`; no external network and no real secret.
- The observable contract is M6 `command_result` for registration plus persisted LOCAL_CONFIG events for business results.

- [ ] **Step 1: Write failing end-to-end tests**

Cover this sequence: upsert profile, configure credential through a fake credential store, list profiles after reconnect, list models, test a model, run a real model operation, disconnect/reconnect, and verify only formal events replay. Assert that database bytes, wire messages, and renderer state contain no fake secret.

- [ ] **Step 2: Run to verify failure**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests\integration\test_provider_config_flow.py -q -p no:cacheprovider`

Expected: FAIL until the full route is wired.

- [ ] **Step 3: Implement only missing integration glue**

Do not add business logic to M6. Fix only command construction, event projection, provider registry injection, or test fixtures needed to make the already-tested units compose through `IntegratedApp` and `BridgeConnection`.

- [ ] **Step 4: Run full backend and desktop suites**

Run:

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider
cd ..\desktop
npm test
npm run typecheck
npm run build
```

Expected: all backend tests pass, all desktop tests pass, TypeScript typecheck passes, and Electron main/preload/renderer build completes.

- [ ] **Step 5: Record the first real-provider manual test prerequisites**

Document that the manual test requires the user’s Base URL, API Key entered through the system prompt, and model name. Never put any of these values in the repository, test fixture, command line, or plan output.

### Task 9: Documentation synchronization, security scan, and completion verification

**Files:**
- Modify: `README.md`
- Modify: `docs/current-status.md`
- Modify: `docs/modules/zcode-m2-m6-m7.md`
- Modify: `docs/modules/gpt-m0-m1.md`
- Modify: `docs/modules/claude-m3-m4-m5.md`
- Create: `backend/tests/security/test_provider_secret_boundary.py`

**Interfaces:**
- Documentation must describe the actual current entry point and supported first Provider, not a historical plan.

- [ ] **Step 1: Write the secret-boundary regression test**

```python
def test_repository_and_event_payload_never_contain_test_secret(tmp_path):
    secret = "sk-test-secret-123456789"
    wire_messages, database_path = run_fake_config_and_model_flow(tmp_path, secret)
    assert secret not in json.dumps(wire_messages, ensure_ascii=False)
    assert secret not in database_path.read_bytes().decode("utf-8", errors="ignore")
```

The same test module must define `run_fake_config_and_model_flow(tmp_path: Path, secret: str) -> tuple[list[dict], Path]`: it constructs `IntegratedApp` with `MemoryCredentialStore`, runs the upsert and credential commands through a local `LineClient`, returns all received wire messages and the SQLite path, and never calls Windows or an external endpoint.

- [ ] **Step 2: Run the security test to verify the missing integration is visible**

Run: `cd backend; .\.venv\Scripts\python.exe -m pytest tests\security\test_provider_secret_boundary.py -q -p no:cacheprovider`

- [ ] **Step 3: Synchronize current documentation**

Update README quick start and “尚未实现” to state that OpenAI-compatible configuration is implemented only after the final verification, while Claude/Z.AI and real tools remain unavailable. Update current status with test counts, provider probe limitations, and the exact manual acceptance procedure. Update M2/M6/M7 module documentation for config IPC and the Provider; update M0/M1 and M3/M4/M5 docs only where LOCAL_CONFIG event ownership and credential storage boundaries changed.

- [ ] **Step 4: Run complete verification and clean temporary residue**

Run the backend suite, desktop suite, typecheck and build from Task 8. Then inspect `git status` only to confirm the directory is not a repository, search the repository for `sk-`, `api_key`, `Authorization: Bearer` and temporary debug prints, and remove only generated `__pycache__`, `.pytest_cache`, temporary SQLite files, mock output files and one-off scripts. Do not remove formal tests or documentation.

- [ ] **Step 5: Perform manual acceptance with the user’s relay**

Start the desktop app, create a named connection, configure the key in the Windows secure prompt, run the basic test, load or enter a model, run the one-token capability test, run one real prompt, disconnect/reconnect, and delete the connection. Record only pass/fail, latency and sanitized error categories in `docs/current-status.md`; never record the endpoint credential or response body.

## Plan Self-Review

- Spec coverage: Tasks 1-3 cover profile metadata and Windows credential storage; Task 4 covers OpenAI streaming/non-streaming, errors, cancellation, timeout and retry; Task 5 covers LOCAL_CONFIG commands and M5-before-M6 event publication; Task 6 covers a real TASK operation; Task 7 covers desktop settings and secret-free IPC; Task 8 covers end-to-end replay and regression; Task 9 covers documentation and residue cleanup.
- Placeholder scan: every implementation action names exact files, interfaces, commands, expected results, or concrete test code. Runtime dependency versions are selected during Task 4 only after the stated license/version check; no unresolved product decision is hidden there.
- Type consistency: `ProviderProfile`, `CredentialStore`, `ProviderProfileStore`, `LocalConfigService`, `OpenAICompatibleProvider`, and renderer store interfaces are named once and consumed with the same field names in later tasks.
- Scope: this plan deliberately excludes Claude/Z.AI adapters, real tools, installation packaging and complete multi-agent workflow, matching the confirmed design.
