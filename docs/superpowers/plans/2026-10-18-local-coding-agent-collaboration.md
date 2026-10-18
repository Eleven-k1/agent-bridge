# Local Coding Agent Collaboration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run actual local coding-agent CLIs through an auditable collaboration workflow visible in the browser Web UI.

**Architecture:** Add provider-specific Codex and Claude process adapters behind a local Agent registry; a coordinator runs read-only analysis and synthesis, waits for explicit plan approval, assigns one writer, then runs a separate read-only reviewer. Persist workflow facts as TASK events and stream sanitized output as transient frames; the existing model gateway remains for its current model API path.

**Tech Stack:** Python 3.11+, asyncio subprocesses, SQLite formal events, React 19/TypeScript, Vitest, pytest.

## Global Constraints

- The supported product interface is the local browser Web UI.
- The initial providers are Codex CLI and Claude Code; uninstalled or unsupported providers must display their actual status.
- Never accept an executable, argv, environment override, or cwd from browser input.
- Start Codex analysis with its read-only sandbox. Start Claude analysis with a read-only tool allowlist, disabled MCP, and explicit user notice that this is not OS file isolation.
- Require a persisted user approval event before creating a writer process.
- Permit only one writer process per task run.
- Only Codex is a supported writer in v1. Create a server-owned Git worktree from the current HEAD after approval and run the writer only inside it; retain the worktree, branch, baseline, and diff for inspection.
- Reject workspaces that are not Git repositories. Never copy uncommitted main-worktree data, ignored files, or credentials into the task worktree.
- Warn before analysis that agents can read project files; users must exclude secrets and files they do not want sent to model providers.
- On Windows, resolve a known native runtime plus fixed CLI script; never invoke shell functions, aliases, or arbitrary `.cmd` files. Mark unresolved shims unavailable. Terminate child trees with a tested platform-specific mechanism.
- Do not read, transmit, or persist local CLI credentials.
- Persist bounded, redacted formal summaries; stream bounded, redacted output only as transient frames.
- Terminate the complete child process tree during cancellation, timeout, and shutdown.
- Do not create automatic Git commits, push remotes, or execute tasks in the main application workspace without an approved baseline.

---

### Task 1: Agent inventory and provider contracts

**Files:**
- Create: `backend/src/agent_bridge/agent_runtime/__init__.py`
- Create: `backend/src/agent_bridge/agent_runtime/registry.py`
- Create: `backend/src/agent_bridge/agent_runtime/types.py`
- Test: `backend/tests/agent_runtime/test_registry.py`
- Test: `backend/tests/agent_runtime/test_types.py`
- Modify: `backend/pyproject.toml` only if a subprocess/parser dependency proves necessary; the initial implementation must use the standard library.

**Interfaces:** `AgentProvider` has immutable `provider_id`, `display_name`, `supports_read_only`, `supports_write`, `build_argv(role, prompt, workspace)`, and `parse_event(line)`. `discover_agents(path=None)` returns deterministic `AgentAvailability` values containing provider id, installed flag, safe resolved launch target, supported roles, and a safe diagnostic. Browser payloads only reference provider IDs.

- [ ] Add failing tests for PATH discovery, missing binary, deterministic provider ordering, unknown provider rejection, and no arbitrary executable fields in the availability contract.
- [ ] Verify the focused pytest command fails because registry/types are absent.
- [ ] Implement provider metadata for Codex and Claude Code plus honest uninstalled/unadapted status descriptors for OpenCode, DeepSeek Harness, and ZCode. Windows discovery must ignore PowerShell functions and resolve only verified fixed Node CLI entrypoints.
- [ ] Run the focused tests and verify detection uses actual executable files rather than environment claims.
- [ ] Commit as `feat(agent-runtime): define local agent providers`.

### Task 2: Safe CLI process and streaming adapters

**Files:**
- Create: `backend/src/agent_bridge/agent_runtime/process.py`
- Create: `backend/src/agent_bridge/agent_runtime/codex.py`
- Create: `backend/src/agent_bridge/agent_runtime/claude.py`
- Test: `backend/tests/agent_runtime/test_process.py`
- Test: `backend/tests/agent_runtime/test_codex.py`
- Test: `backend/tests/agent_runtime/test_claude.py`

**Interfaces:** `AgentProcess.run(provider, role, prompt, workspace, on_output, timeout_s)` returns `AgentResult(exit_code, final_text, session_id, elapsed_ms)` and accepts an `asyncio.Event` for cancellation. `parse_event(line)` returns a normalized `AgentOutput(text, channel, is_final, session_id)` or `None`. Arguments are passed as an argv list; prompts containing spaces/newlines remain one stdin value or one argv element.

- [ ] Add failing fixture executable tests for Codex JSONL text deltas/final messages, Claude stream-json partial/final text, malformed lines, stderr reporting, nonzero exit, timeout, and cancel.
- [ ] Verify each test fails before implementation.
- [ ] Implement provider-specific argv construction, bounded line reads, redaction, and process-tree termination with graceful wait followed by forced cleanup. Codex is the only v1 writer; Claude uses explicit read-only tool allowlist. Windows launch uses only resolved native runtime and fixed script paths, and child trees are terminated and reaped.
- [ ] Run adapter tests and verify neither provider invokes an OS shell command string.
- [ ] Commit as `feat(agent-runtime): run codex and claude cli processes`.

### Task 3: Durable collaboration coordinator

**Files:**
- Create: `backend/src/agent_bridge/agent_runtime/coordinator.py`
- Modify: `backend/src/agent_bridge/app.py`
- Modify: `backend/src/agent_bridge/orchestrator/core.py`
- Test: `backend/tests/agent_runtime/test_coordinator.py`
- Test: `backend/tests/orchestrator/test_state_machine.py`

**Interfaces:** `AgentCollaborationCoordinator.start(command, emit_event, emit_frame)` validates agent IDs and run scope, confirms a valid Git repository and selected supported providers, starts concurrently selected read-only analyses, collects final outputs, runs selected coordinator synthesis, and emits `AGENT_COLLABORATION_*` formal facts plus transient `agent_frame` chunks. It provides `approve_plan(task_id, run_id, approval_digest)`, `cancel(task_id, run_id)`, `shutdown()`, and `restore(events)`. New agent events are explicitly known to M1 replay and do not mutate unrelated task lifecycle state.

- [ ] Add failing tests for concurrent read-only dispatch, visible provider/role identity, synthesis only after all required analysis results, formal event sequence, rejection of unknown/unavailable agent IDs, and restoration with Agent events between M1 lifecycle events.
- [ ] Verify the tests fail before implementation.
- [ ] Implement coordinator stage state, event identities/deduplication keys, bounded/redacted summaries, warning/consent, server-owned task worktree baseline metadata, and orderly failure/cancellation handling.
- [ ] Run coordinator and M1 restore tests.
- [ ] Commit as `feat(orchestrator): coordinate visible local agents`.

### Task 4: Approval, single-writer execution, review, and cancellation

**Files:**
- Modify: `backend/src/agent_bridge/agent_runtime/coordinator.py`
- Modify: `backend/src/agent_bridge/agent_runtime/process.py`
- Test: `backend/tests/agent_runtime/test_coordinator.py`
- Test: `backend/tests/agent_runtime/test_process.py`

**Interfaces:** `approve_plan` must match the currently persisted plan digest and emit the approval fact before creating a dedicated Git worktree and launching the sole supported Codex `workspace-write` process. The coordinator holds one in-memory writer lease per active run, rejects duplicate/stale approval, starts an independent read-only reviewer after writer exit code 0, and reaches a terminal event only after review or explicit failure/cancellation. Preserve the branch/worktree and report Git diff; never auto-merge or commit.

- [ ] Add failing tests proving no writer starts before approval, approval digest must match, duplicate writers are rejected, reviewer provider differs from writer, failed writer skips review, and cancel kills all children.
- [ ] Verify the tests fail before implementation.
- [ ] Implement the approval gate, Git worktree creation from the recorded HEAD, Codex-only workspace-write policy, lifecycle transitions, diff capture, and subprocess cleanup.
- [ ] Run the focused suite and inspect all child processes are reaped.
- [ ] Commit as `feat(orchestrator): gate writes and run independent reviews`.

### Task 5: Backend commands, event recovery, and Web bridge

**Files:**
- Modify: `backend/src/agent_bridge/app.py`
- Modify: `backend/src/agent_bridge/orchestrator/core.py`
- Modify: `web-ui/server/web_server.py`
- Test: `backend/tests/integration/test_agent_collaboration.py`
- Test: `web-ui/tests/test_web_server.py`

**Interfaces:** Add fixed command types `LIST_LOCAL_AGENTS`, `START_AGENT_COLLABORATION`, `APPROVE_AGENT_PLAN`, `CANCEL_AGENT_COLLABORATION`. The Web bridge constructs TASK envelopes with server-owned task/run IDs; validates prompt length, unique supported provider IDs, coordinator and writer/reviewer selection, and approval digest. It never forwards caller-supplied argv, cwd, env, executable paths, or permissions.

- [ ] Add failing integration tests for inventory event, start/approve/cancel commands, command idempotency, persisted event replay after app restart, and rejection of arbitrary command data.
- [ ] Verify tests fail before route implementation.
- [ ] Route commands to the coordinator; restore Agent event types without blocking M1 state restoration; close coordinator before closing SQLite.
- [ ] Extend the Web bridge command allowlist and payload validation without changing M6 generic transport behavior.
- [ ] Run backend integration and Web server protocol tests.
- [ ] Commit as `feat(backend): expose local agent collaboration workflow`.

### Task 6: Browser collaboration workspace

**Files:**
- Modify: `web-ui/client/src/App.tsx`
- Modify: `web-ui/client/src/style.css`
- Test: `web-ui/client/src/app.test.tsx`

**Interfaces:** Display actual provider availability, selectable analysis participants and coordinator, a live per-agent output/status timeline, synthesized plan, approval/deny controls, single writer identity, independent reviewer result, and cancel action. Track TASK event cursors and replay persisted collaboration facts; do not restore transient output after reconnect. Never present provider events as normal model response content.

- [ ] Add failing component tests for unavailable-provider status, multi-agent run command, parallel outputs tied to provider IDs, approval gating, writer/reviewer handoff, cancellation, and reconnect recovery.
- [ ] Verify component tests fail before UI implementation.
- [ ] Implement the collaboration controls and timeline using existing icon and WebSocket patterns, preserving the current model sandbox as a separate feature.
- [ ] Run component tests, typecheck, responsive CSS checks, and production build.
- [ ] Commit as `feat(web-ui): show real local agent collaboration`.

### Task 7: Documentation and final verification

**Files:**
- Modify: `README.md`
- Modify: `docs/current-status.md`
- Modify: `docs/modules/zcode-m2-m6-m7.md`
- Modify: `docs/superpowers/specs/2026-08-30-multi-agent-bridge-design.md`
- Modify: `docs/superpowers/specs/2026-08-30-multi-agent-task-allocation.md`
- Modify: `docs/superpowers/plans/2026-10-18-web-ui-workbench.md`

- [ ] Replace current-run instructions that imply a single local model is the product workflow; mark the August API-model-only plan/spec as historical and direct readers to the current local CLI Agent design.
- [ ] Document supported real adapters, exact limitations, CLI installation/login prerequisite, Web UI workflow, permission behavior, cancellation, event recovery, and test commands.
- [ ] Run Python suite using a repository-local pytest basetemp, Web UI tests, typecheck, and production build. Verify on a clean install that the Web lockfile contains no links into `desktop/node_modules`.
- [ ] Inspect `git diff --check`, dependency changes, subprocess cleanup, `git status`, and temporary artifacts.
- [ ] Commit documentation after all checks pass.
