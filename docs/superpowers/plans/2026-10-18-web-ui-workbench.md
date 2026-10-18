# Web UI Workbench Implementation Plan

> **For agentic workers:** Execute this plan inline in the current session. Keep every production change within `web-ui/`; do not edit `desktop/`.

**Goal:** Make the browser Web UI the supported workbench with task controls, a run inspector, event projection, and reconnect recovery.

**Architecture:** Keep the React client responsible for the responsive three-column workbench and transient UI state. Keep WebSocket command normalization, validation, and event replay forwarding inside the existing Python Web UI adapter; continue to use the existing compact command message and M6 resume protocol.

**Tech Stack:** React 19, TypeScript, Vite, Vitest, Testing Library, Python asyncio WebSocket adapter, lucide-react.

**Status:** Completed 2026-10-18.

## Global Constraints

- Browser UI uses only `http://127.0.0.1:5173/` as the product entry point.
- Commands sent by the browser remain `{ type: "command", command_type, payload }`; the adapter builds full backend envelopes.
- Terminal success, failure, and cancellation come only from formal task events.
- Stream frames are transient and must be cleared on disconnect; persisted events resume by scope cursor.
- Do not edit `desktop/` or widen the backend command allowlist.
- Keep the Web UI bound to loopback and do not expose credentials to the browser.

---

### Task 1: Lock down browser protocol behavior

**Files:**
- Create: `web-ui/client/vitest.config.ts`
- Create: `web-ui/client/src/app.test.tsx`
- Create: `web-ui/tests/test_web_server.py` cases for browser resume forwarding

**Interfaces:**
- Browser commands use the existing compact `command_type` and `payload` shape.
- Browser resume uses `{ type: "resume", scope_type, scope_id, last_event_seq }`.
- Downlink formal events use `event.event_type`, `event.payload`, and the sequence field matching `event.scope_type`.

- [x] Add client tests for custom real-task content, model discovery, terminal event projection, failed command display, and accepted-command pending state.
- [x] Add Python tests for valid task/local-config resume inputs and invalid scope/cursor inputs.
- [x] Run the focused client tests and the full Python suite.

### Task 2: Build the Web UI workbench

**Files:**
- Create: `web-ui/client/src/App.tsx`
- Modify: `web-ui/client/src/main.tsx`
- Modify: `web-ui/client/src/style.css`
- Modify: `web-ui/client/package.json`

**Interfaces:**
- `App` owns task prompt, selected/manual model, command-pending state, event cursors, stream text, recorded text, terminal state, and diagnostic text.
- `sendCommand(commandType, payload)` emits only the compact browser command shape.
- `sendResume()` emits task and local-config cursors after every successful WebSocket connection.

- [x] Render the central task composer and separate stream/formal-event result panes.
- [x] Render workspace/Fake controls on the left and model/connection/counters/last-event diagnostics on the right.
- [x] Add responsive layouts that stack controls at tablet and mobile widths.
- [x] Handle `connection_state`, `model_frame`, `event`, `command_result`, `protocol_error`, and `input_error` messages. Ignore `resume_done`, which does not change the UI projection.

### Task 3: Add Web UI resume forwarding

**Files:**
- Modify: `web-ui/server/web_server.py`
- Modify: `web-ui/tests/test_web_server.py`

**Interfaces:**
- `_build_resume(incoming)` accepts only supported `TASK` and `LOCAL_CONFIG` resume scopes, a non-empty scope ID, and a non-negative integer cursor.
- `_handle_websocket` forwards a validated resume to `BridgeSession.send` and rejects invalid input with the existing `input_error` response.

- [x] Add coverage for valid resume scopes, invalid scopes/cursors, and server forwarding.
- [x] Implement resume validation and forwarding without changing command validation or backend transport.
- [x] Run the full Python suite, including `web-ui/tests`.

### Task 4: Verify and update the supported entry point

**Files:**
- Modify: `README.md`
- Modify: `web-ui/README.md`
- Modify: `docs/current-status.md`

- [x] Document the browser Web UI as the supported user-facing workbench and keep desktop files out of the change.
- [x] Build the browser bundle into `web-ui/dist/` and verify the served HTML references the new asset hashes.
- [x] Run Web UI client tests, the full Python suite, `git diff --check`, and confirm no `desktop/` files changed.
- [x] Inspect the active `127.0.0.1:5173` page at desktop and mobile widths after building the new assets.
