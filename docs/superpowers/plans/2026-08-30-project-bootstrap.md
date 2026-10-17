# Multi-Agent Bridge Bootstrap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first runnable vertical slice that proves the frozen M0 contract can travel from a Python service to an Electron/React desktop UI using fake model and tool dependencies.

**Architecture:** Keep the Python service and desktop process separate and bind the service only to `127.0.0.1`. Define M0 once in Python and expose versioned JSON schemas for the TypeScript client; all first-slice behavior uses Fake Provider and Fake Tool so no real credentials or project writes are needed.

**Tech Stack:** Python, pytest, Electron, React, TypeScript, Vitest; exact libraries and supported versions are selected after the required open-source comparison.

## Global Constraints

- Windows, single user, local-only; no public server, team account, cloud sync, or remote execution.
- The authoritative contract is `M0-公共协议与数据契约-v0.5.1-最终版.txt`.
- The Python service binds only to `127.0.0.1` and desktop communication uses an ephemeral session credential.
- API keys must use Windows credential storage and must never appear in SQLite, logs, project data, errors, or exports.
- Research reusable GitHub projects first and record license, maintenance, security, and Windows fit before selecting dependencies.
- Use test-driven development and update current documentation whenever interfaces, behavior, data, interaction, or module ownership changes.

---

### Task 1: Establish the repository and dependency decision record

**Files:**
- Create: `.gitignore`
- Create: `docs/research/open-source-evaluation.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: the three authoritative documents listed in `README.md`
- Produces: a clean Git baseline and a recorded dependency choice for Tasks 2-4

- [ ] **Step 1: Initialize Git and inspect the baseline**

Run: `git init`

Run: `git status --short`

Expected: only the current project documents are listed as untracked; no generated environment, dependency, build, or credential files exist.

- [ ] **Step 2: Research reusable implementations**

Compare at least two maintained candidates for each material choice: Python local API/streaming, OpenAI-compatible and Anthropic adapters, Electron/Python process lifecycle, Windows credential storage, and schema generation/validation. Record repository URL, license, last meaningful maintenance evidence, relevant security notes, Windows compatibility, reuse scope, and rejection reason in `docs/research/open-source-evaluation.md`.

Expected: every selected dependency has an explicit reason and license; copied source is not used without license compatibility.

- [ ] **Step 3: Create the ignore policy**

Create `.gitignore` with the selected tools' standard generated paths plus these mandatory entries:

```gitignore
.env
.env.*
!.env.example
*.db
*.sqlite
*.sqlite3
*.log
__pycache__/
.pytest_cache/
.venv/
node_modules/
dist/
build/
coverage/
```

- [ ] **Step 4: Verify no secret or generated artifact is tracked**

Run: `git status --short`

Expected: no `.env`, database, log, virtual environment, dependency, build, or coverage path is listed.

- [ ] **Step 5: Commit the project baseline**

```powershell
git add README.md M0-公共协议与数据契约-v0.5.1-最终版.txt docs .gitignore
git commit -m "docs: establish multi-agent bridge baseline"
```

Expected: the commit succeeds and `git status --short` is empty.

### Task 2: Implement and test the M0 contract package

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/src/agent_bridge/contracts/envelopes.py`
- Create: `backend/src/agent_bridge/contracts/lifecycle.py`
- Create: `backend/src/agent_bridge/contracts/schema_export.py`
- Create: `backend/tests/contracts/test_envelopes.py`
- Create: `backend/tests/contracts/test_lifecycle.py`
- Create: `backend/tests/contracts/test_schema_export.py`
- Create: `contracts/generated/`

**Interfaces:**
- Consumes: M0 scopes, identifiers, idempotency rules, lifecycle states, terminal invariants, and model fragment rules
- Produces: validated Python contract types and versioned JSON schemas consumed by the desktop client

- [ ] **Step 1: Write failing contract tests**

Cover all three scopes, required task/workspace identifiers, stable idempotency conflicts, valid lifecycle transitions, terminal invariants, and the fact that model fragments carry `operation_id` plus `model_fragment_sequence` but no task-global event sequence.

- [ ] **Step 2: Run the tests and confirm the expected failure**

Run: `python -m pytest backend/tests/contracts -v`

Expected: FAIL because the contract package does not exist yet.

- [ ] **Step 3: Implement only the tested M0 types and validators**

Use the selected validation library. Keep transport, database, provider, UI, and tool behavior out of this package. Export deterministic versioned JSON schemas into `contracts/generated/`.

- [ ] **Step 4: Run contract tests**

Run: `python -m pytest backend/tests/contracts -v`

Expected: PASS, including deterministic schema export verified by a second generation producing no diff.

- [ ] **Step 5: Commit M0 implementation**

```powershell
git add backend contracts
git commit -m "feat: implement M0 contract package"
```

### Task 3: Build the fake local-service vertical slice

**Files:**
- Create: `backend/src/agent_bridge/app.py`
- Create: `backend/src/agent_bridge/fakes/provider.py`
- Create: `backend/src/agent_bridge/fakes/tool.py`
- Create: `backend/src/agent_bridge/fakes/approval.py`
- Create: `backend/tests/integration/test_fake_task_stream.py`

**Interfaces:**
- Consumes: M0 command/event types and the selected local communication library
- Produces: a loopback-only test service that accepts one task command, emits fake model fragments, persists/returns a formal terminal event, and supports clean shutdown

- [ ] **Step 1: Write a failing integration test**

The test starts the service on a free loopback port with a temporary session credential, rejects a missing credential, accepts one valid task command, observes ordered fake fragments, requires a Fake Approval decision before the Fake Tool action, receives the persisted terminal event, reconnects, and retrieves the same formal result without replaying the fake operation.

- [ ] **Step 2: Run the test and confirm the expected failure**

Run: `python -m pytest backend/tests/integration/test_fake_task_stream.py -v`

Expected: FAIL because the service and fakes do not exist yet.

- [ ] **Step 3: Implement the minimum service and fakes**

Bind explicitly to `127.0.0.1`; keep fake behavior deterministic; never call a real provider, shell, Git, or project filesystem; keep model fragments transient and publish the final formal result only through the M0 event path.

- [ ] **Step 4: Run backend verification**

Run: `python -m pytest backend/tests -v`

Expected: PASS with no process left listening after the test exits.

- [ ] **Step 5: Commit the backend slice**

```powershell
git add backend
git commit -m "feat: add fake local task stream"
```

### Task 4: Build the minimal desktop consumer

**Files:**
- Create: `desktop/package.json`
- Create: `desktop/src/main/service-process.ts`
- Create: `desktop/src/main/index.ts`
- Create: `desktop/src/renderer/App.tsx`
- Create: `desktop/src/renderer/task-store.ts`
- Create: `desktop/tests/task-flow.test.tsx`
- Create: `desktop/tests/service-process.test.ts`

**Interfaces:**
- Consumes: generated M0 schemas and the loopback fake service from Task 3
- Produces: a desktop window that starts/stops the Python child process, submits one fake task, shows streaming text, and rebuilds the final state from formal events after reconnect

- [ ] **Step 1: Write failing desktop tests**

Test child-process cleanup, ephemeral credential forwarding, one task submission, ordered fragment display, formal terminal-state projection, reconnect recovery, and the absence of direct provider, database, shell, and credential access from renderer code.

- [ ] **Step 2: Run the tests and confirm the expected failure**

Run: `npm --prefix desktop test`

Expected: FAIL because the desktop application does not exist yet.

- [ ] **Step 3: Implement the minimal UI and process owner**

Use generated schemas at the local-service boundary. The Electron main process owns the Python child and session credential; the renderer receives only narrow IPC operations and projected task data.

- [ ] **Step 4: Run automated verification**

Run: `npm --prefix desktop test`

Run: `python -m pytest backend/tests -v`

Expected: both suites PASS.

- [ ] **Step 5: Perform real Windows smoke verification**

Start the desktop application, submit the fake task, confirm visible streaming and final completion, close the window, and verify the Python child process and listener are gone. Record exact commands and evidence in `docs/status/first-vertical-slice.md`.

- [ ] **Step 6: Update documentation and remove temporary residue**

Update `README.md` with the real setup/test/run commands and current limitations. Search for debug prints, one-off scripts, temporary APIs, hard-coded fake paths outside the formal fake modules, disabled security checks, and unused validation code; remove confirmed temporary residue and rerun both suites.

- [ ] **Step 7: Commit the runnable slice**

```powershell
git add desktop backend contracts README.md docs/status
git commit -m "feat: deliver first runnable desktop slice"
```
