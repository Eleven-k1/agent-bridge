# 固定本地 OpenAI 中转连接实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 Fake Provider 闭环中接入固定的 `http://127.0.0.1:8080` OpenAI 兼容中转，自动发现模型并支持手动模型回退。

**Architecture:** 新增一个只负责 HTTP/SSE 解析的 `OpenAICompatibleProvider`，实现现有 `ModelProvider` 接口并把所有结果转换成 `ProviderEvent`。应用层增加真实模型命令和模型发现命令，但继续由 `ModelGateway` 统一处理取消、超时、重试、临时帧和持久化候选事件；桌面端通过现有 IPC 只接收脱敏状态、模型列表、帧和正式事件。

**Tech Stack:** Python 3.11+, `httpx` async HTTP, SQLite/M5 现有事件链路, Electron 44, React 19, TypeScript, pytest, Vitest.

## Global Constraints

- Provider Base URL 固定为 `http://127.0.0.1:8080`。
- 请求路径固定为 `/v1/models` 和 `/v1/chat/completions`。
- 不发送 `Authorization`，不增加 API Key 或凭据存储。
- Provider 不写数据库、不访问项目文件、不执行工具。
- 模型发现失败时必须允许手动输入模型名。
- 必须保留 Fake Provider、Fake Tool、SQLite、断线恢复和协议回归测试。
- 错误进入 UI、事件和日志前必须脱敏。
- 完成前同步 README、`docs/current-status.md` 和 Provider 模块文档，并清理临时产物。
- 当前目录不是 Git 仓库，不执行或声称执行 commit。

---

### Task 1: HTTP Provider 类型与测试传输层

**Files:**
- Create: `backend/src/agent_bridge/model_gateway/openai_compatible.py`
- Modify: `backend/src/agent_bridge/model_gateway/__init__.py`
- Modify: `backend/pyproject.toml`
- Create: `backend/tests/model_gateway/test_openai_compatible.py`

**Interfaces:**
- `OpenAICompatibleProvider(base_url: str = "http://127.0.0.1:8080", transport: AsyncTransport | None = None)`
- `async def list_models() -> list[str]`
- `async def stream(request: ModelRequest) -> AsyncIterator[ProviderEvent]`
- `request_cancel(operation_id: str) -> None`
- `close() -> None`

- [ ] Write failing tests for base URL/path construction, no `Authorization` header, model list parsing, SSE parsing, non-stream response conversion, empty deltas, `[DONE]`, and malformed JSON.
- [ ] Run `backend\.venv\Scripts\python.exe -m pytest tests\model_gateway\test_openai_compatible.py -q -p no:cacheprovider`; confirm import/implementation failures.
- [ ] Add `httpx` runtime dependency with a compatible bounded version and an injectable `httpx.AsyncBaseTransport` test seam.
- [ ] Implement fixed URL normalization so only the configured local relay is accepted; reject non-local or non-HTTP URLs.
- [ ] Implement `GET /v1/models` parsing from `data[].id`; return an empty list for a valid response without models and raise sanitized `ProviderError` for transport/status/shape failures.
- [ ] Implement `POST /v1/chat/completions` request mapping from `ModelRequest`; omit Authorization; support streaming SSE and non-stream JSON.
- [ ] Map HTTP status and transport exceptions to existing `ErrorCategory`, preserve timeout phase metadata, and never include response bodies containing credentials.
- [ ] Track active `httpx` responses by operation ID so `request_cancel()` closes the active response and causes `CANCELLED` behavior.
- [ ] Run the focused provider tests; expected result is all provider tests passing.

### Task 2: Gateway integration and real model command

**Files:**
- Modify: `backend/src/agent_bridge/app.py`
- Modify: `backend/src/agent_bridge/model_gateway/gateway.py` only if provider cancellation requires a narrow adapter hook
- Create: `backend/tests/integration/test_real_provider_app.py`

**Interfaces:**
- Command type: `REAL_MODEL_RUN` with payload `{model: string, messages: list[dict], system?: string, stream?: boolean}`.
- Command type: `LIST_LOCAL_MODELS` with no task scope requirement and a `LOCAL_CONFIG` response event.
- App owns provider lifetime and closes it during `IntegratedApp.aclose()`.

- [ ] Write failing integration tests using a fake `httpx` transport that assert `REAL_MODEL_RUN` flows through `ModelGateway`, emits temporary frames, persists formal events, and sends no Authorization header.
- [ ] Add a model discovery test for success, 404 fallback, and sanitized failure publication.
- [ ] Run the focused integration tests and confirm they fail before routing exists.
- [ ] Add a provider factory for the single fixed relay and keep the existing Fake Provider path unchanged.
- [ ] Route `LIST_LOCAL_MODELS` to `OpenAICompatibleProvider.list_models()` and publish a secret-free `LOCAL_CONFIG` event containing model IDs and status category.
- [ ] Route `REAL_MODEL_RUN` to `ModelGateway(OpenAICompatibleProvider(...))`, require `task_id`, `run_id`, and a non-empty model, and use the existing `_drive_model` publication path.
- [ ] Ensure provider instances and active HTTP resources are closed on normal shutdown and task cancellation.
- [ ] Run focused integration tests, then the complete backend suite.

### Task 3: Desktop bridge commands and renderer state

**Files:**
- Modify: `desktop/src/main/index.ts`
- Modify: `desktop/src/preload/index.ts`
- Modify: `desktop/src/renderer/bridge-api.ts`
- Modify: `desktop/src/renderer/task-store.ts`
- Modify: `desktop/src/renderer/App.tsx`
- Create or modify: `desktop/tests/real-provider.test.tsx`

**Interfaces:**
- IPC methods: `listLocalModels()`, `runRealTask(model: string, messages: WireMessage[], stream: boolean)`.
- Renderer state adds `availableModels`, `modelDiscoveryStatus`, `manualModel`, and sanitized `lastProviderError`.

- [ ] Write failing Vitest tests for model discovery success/failure, manual model fallback, real task submission, and display of streamed/formal output.
- [ ] Run `npm test -- real-provider.test.tsx` and confirm expected failures.
- [ ] Add main-process IPC handlers that submit `LIST_LOCAL_MODELS` and `REAL_MODEL_RUN` through the existing `BridgeConnection`; do not add secret fields.
- [ ] Add preload-safe bridge methods and renderer store message handling for `LOCAL_CONFIG` model results and provider error categories.
- [ ] Extend the existing minimal UI with a fixed relay label, refresh models button, model select/input fallback, and real task button while retaining Fake controls.
- [ ] Disable real task submission when no model is selected and show a clear local relay unavailable state.
- [ ] Run focused desktop tests, full desktop tests, and TypeScript typecheck.

### Task 4: End-to-end protocol and regression verification

**Files:**
- Modify: `backend/tests/integration/test_integrated_app.py` only for shared helpers if needed
- Create: `backend/tests/model_gateway/test_openai_compatible_errors.py` if error cases are clearer separated
- Modify: `desktop/tests/bridge-connection.test.ts` only if new message types need cursor coverage

- [ ] Add regression cases for 401, 429, 4xx, 5xx, network failure, first-token timeout, stream-idle timeout, cancellation, retry before content, and no retry after content.
- [ ] Add an assertion that SQLite event payloads, renderer state, and sanitized errors contain no `Authorization`, API key, or raw response body.
- [ ] Run backend tests:
  `cd backend; .\.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider`
- [ ] Run desktop tests and typecheck:
  `cd desktop; npm test; npm run typecheck`
- [ ] Build desktop:
  `cd desktop; npm run build`
- [ ] If any failure appears, isolate it with the relevant focused test before making a fix; do not broaden changes without a failing test.

### Task 5: Documentation and residue cleanup

**Files:**
- Modify: `README.md`
- Modify: `docs/current-status.md`
- Modify: `docs/modules/gpt-m0-m1.md` or the current Provider module document identified during implementation
- Modify: `docs/superpowers/specs/2026-09-26-local-openai-relay-design.md` only if implementation decisions materially change

- [ ] Update startup instructions with the fixed local relay prerequisite and the model discovery/manual fallback behavior.
- [ ] Record the exact automated test commands and results without claiming real-provider manual acceptance unless the relay is running.
- [ ] Document that no API Key is sent or stored and that the feature is limited to the local relay.
- [ ] Search for temporary debug prints, one-off scripts, generated SQLite files, `.pytest_cache`, and test output files; remove only temporary artifacts.
- [ ] Search the changed files for `Authorization`, `api_key`, `sk-`, and raw response logging; confirm no credential path was introduced.
- [ ] Re-run the focused provider tests and the complete regression commands after cleanup.

## Plan Self-Review

- Spec coverage: fixed endpoint and no authentication are covered by Tasks 1-3; model discovery and manual fallback by Tasks 1-3; streaming/non-streaming, cancellation, timeout, retry and error classification by Tasks 1, 2 and 4; existing pipeline and regression by Tasks 2 and 4; documentation and residue cleanup by Task 5.
- Placeholder scan: no `TBD`, `TODO`, or unspecified implementation step is used; each task names files, interfaces, tests, commands, and expected behavior.
- Type consistency: `OpenAICompatibleProvider.list_models`, `OpenAICompatibleProvider.stream`, `LIST_LOCAL_MODELS`, and `REAL_MODEL_RUN` use the same names and payload shapes across backend, bridge, renderer, and tests.
