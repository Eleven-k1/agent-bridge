# Architecture

## Scope

Agent Bridge is a local single-user system. The browser is the presentation and approval surface; Python owns business routing, persistence, process execution, and Git worktrees. No remote agent service or browser-side process execution is part of the design.

## Components

### Browser Web UI

`web-ui/client/src/App.tsx` separates transient frames from formal events and rebuilds the task report from persisted `TASK` facts. `web-ui/client/src/app.test.tsx` verifies the collaboration workflow and reconnect behavior.

### Web server and bridge session

`web-ui/server/web_server.py` serves the built client and upgrades `/ws` connections. It validates the limited browser protocol, checks the local origin, and creates a `BridgeSession`. `web-ui/server/bridge_session.py` authenticates to the loopback NDJSON bridge. These layers do not execute agent business logic.

### Application router

`backend/src/agent_bridge/app.py` connects transport to the event store, model gateway, security services, and `AgentCollaborationCoordinator`. The router rejects malformed or unauthorized commands before business execution. The older Fake Provider path remains available for deterministic protocol tests.

### Agent runtime

`backend/src/agent_bridge/agent_runtime/` contains discovery, provider adapters, process supervision, and orchestration:

- `registry.py` resolves safe local launch targets and reports availability.
- `codex.py` parses Codex JSONL and selects read-only or workspace-write modes by role.
- `claude.py` parses Claude stream JSON and restricts the first integration to read-only tools.
- `process.py` starts argv-based processes, bounds output, applies timeouts, and terminates process trees.
- `coordinator.py` runs analysis in parallel, creates the worktree after approval, invokes the writer, computes a redacted diff, and invokes the reviewer.

### Persistence and security

SQLite stores command idempotency records, formal events, approvals, leases, and recovery data. Security modules enforce scope, path, approval, write lease, and redaction rules. Formal event sequence fields are independent for `TASK`, `WORKSPACE`, and `LOCAL_CONFIG`.

## Collaboration state machine

```text
START_AGENT_COLLABORATION
          |
          v
parallel read-only analysis
          |
          v
AGENT_PLAN_READY ---- reject/cancel ----> terminal
          |
       approve
          v
create worktree + Codex writer
          |
          v
redacted Git diff + independent review
          |
          v
AGENT_COLLABORATION_COMPLETED
```

Required analysis failure, coordinator failure, writer failure, reviewer failure, timeout, or service restart produces a formal failed or cancelled terminal event with available diagnostics. Cancellation is propagated through a shared event and managed process termination.

## Event model

Each formal event contains protocol version, scope, identity, causal identity, actor, payload, digest metadata, timestamp, integrity reference, and a scope-specific sequence. Agent events include `task_id` and `run_id`.

| Event | Report section |
| --- | --- |
| `AGENT_ANALYSIS_COMPLETED` | Agent analysis conclusions |
| `AGENT_PLAN_READY` | Coordinated plan |
| `AGENT_PLAN_APPROVED` | Approval state |
| `AGENT_WRITER_COMPLETED` | Writer exit code, branch, worktree, diff |
| `AGENT_REVIEW_COMPLETED` | Independent review |
| `AGENT_PARTICIPANT_STATUS` | Provider failure diagnostics |
| `AGENT_COLLABORATION_*` | Overall terminal status and final diagnostics |

On reconnect the browser tracks a cursor for each subscribed scope. Events received ahead of the next expected sequence are buffered and drained in order. This avoids rendering a terminal report before its analysis and execution facts arrive.

## Git isolation

At collaboration start, the coordinator records `HEAD` and whether the main workspace is dirty. After approval it runs `git worktree add -b ... <path> <recorded-baseline-commit>`, so commits made while a user reviews the plan cannot silently change the writer's base. Agent changes stay in that worktree. The coordinator verifies the worktree `HEAD`, computes a diff against the recorded baseline, redacts secrets, limits persisted size, and leaves cleanup to the user.

## Trust boundaries

- Browser to Web server: local HTTP/WebSocket, limited command schema, local origin check.
- Web server to bridge: loopback NDJSON with a per-process token.
- Backend to provider CLI: argv only, resolved executable or verified package entrypoint.
- Provider to project: constrained by provider role flags and selected workspace; this is application-level policy, not an OS sandbox.
- Formal event to UI: persisted and redacted payloads only.

New providers require a dedicated adapter, safe discovery, role capability declaration, output parser, cancellation behavior, and real CLI contract tests. Adding a provider to the UI inventory alone is intentionally insufficient.
