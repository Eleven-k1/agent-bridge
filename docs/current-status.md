# Current Status

Date: 2026-10-18

## Product

The supported interface is the local browser Web UI at `http://127.0.0.1:5173/`. The desktop directory contains an early prototype and is not part of the supported workflow.

The Web UI now exposes a real local CLI collaboration workflow alongside the existing model sandbox:

- Discover local CLI agents and display their installation and adapter status.
- Select 2-4 agents for parallel analysis, designate a coordinator, select a Codex writer, and choose a different independent reviewer.
- Acknowledge workspace access before analysis begins.
- Review and approve or reject the coordinated plan before the writer starts.
- Show per-agent temporary output, persisted collaboration events, cancellation state, final Git diff, and independent review outcome.
- Resume persisted task events after reconnect; temporary output is cleared on disconnect.

The initial adapters target Codex CLI and Claude Code CLI. The writer is restricted to Codex. The browser communicates through the loopback Web service and does not launch local processes itself.

## Safety and Runtime

Analysis uses read-only CLI modes. A single writer applies the approved plan, and a different CLI agent reviews the resulting diff. Workspace access and plan approval are explicit UI steps. Process output is bounded, credentials are redacted from persisted diffs, and cancellation terminates managed child processes.

Formal events are persisted in SQLite and are the source of truth for resumable state. Agent streaming output is temporary presentation data. The previous Fake Provider/model sandbox remains available as a separate workflow.

## Verification

Backend full suite: 268 passed using a repository-local pytest base directory. Web UI: 8 client tests passed, TypeScript typecheck passed, Vite production build passed, and `GET /health` returned `{"ok":true,"service":"web-ui"}`.

## Acceptance Limits

- Real Codex and Claude CLI end-to-end collaboration has not yet been accepted on this machine. Adapter discovery and process tests do not prove each installed CLI's version-specific flags and behavior.
- This phase supports Codex CLI and Claude Code CLI; DeepSeek Harness, ZCode, and OpenCode need dedicated adapters before they can participate as real agents.
- The writer currently uses Codex, while analysis and review can select supported providers.
- Worktree-level verification is complete only after the backend full suite, diff checks, and an interactive Web UI smoke test pass.

Older desktop UI plans and the August 2026 multi-agent bridge/task-allocation proposals under `docs/superpowers/` are historical references. Use this status document and the current collaboration design dated 2026-10-18 as the execution source of truth.
