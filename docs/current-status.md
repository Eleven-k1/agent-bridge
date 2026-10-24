# Current Status

Date: 2026-10-24

## Release Position

The browser collaboration workflow and formal project documentation are maintained on Git `master`. The main checkout is the supported launch location; other development worktrees may point to earlier commits and are not the supported launch location.

## Product

The supported interface is the local browser Web UI at `http://127.0.0.1:5173/`. The desktop directory contains an early prototype and is not part of the supported workflow.

The Web UI now exposes a real local CLI collaboration workflow alongside the existing model sandbox:

- Discover local CLI agents and display their installation and adapter status.
- Select 2-4 agents for parallel analysis, designate a coordinator, select a Codex writer, and choose a different independent reviewer.
- Acknowledge workspace access before analysis begins.
- Review and approve or reject the coordinated plan before the writer starts.
- Show per-agent temporary output, persisted collaboration events, cancellation state, final Git diff, and independent review outcome.
- After a run completes, fails, or is cancelled, show a task report with each analysis summary, the coordinated plan, execution branch and Git diff, review summary, and failure details. Replay of formal TASK events rebuilds the report after a page reload.
- Resume persisted task events after reconnect; temporary output is cleared on disconnect.

The initial adapters target Codex CLI and Claude Code CLI. The writer is restricted to Codex. The browser communicates through the loopback Web service and does not launch local processes itself.

## Safety and Runtime

Analysis uses read-only CLI modes. A single writer applies the approved plan, and a different CLI agent reviews the resulting diff. Workspace access and plan approval are explicit UI steps. Process output is bounded, credentials are redacted from persisted diffs, and cancellation terminates managed child processes.

Formal events are persisted in SQLite and are the source of truth for resumable state. Agent streaming output is temporary presentation data. The previous Fake Provider/model sandbox remains available as a separate workflow.

## Verification

This release adds a regression test for preserving the collaboration-start `HEAD` while a plan waits for approval. The final verification run was completed after the lockfile-based frontend install and documentation updates.

Release verification:

- [x] Backend full suite: 270 passed with an isolated pytest base directory.
- [x] Web server suite: 14 passed with a separate pytest base directory.
- [x] Focused Agent runtime and collaboration integration suite: 30 passed; these tests overlap the backend full suite.
- [x] Web UI tests: 12 passed after `npm.cmd ci`; TypeScript typecheck and Vite production build passed.
- [x] Main checkout serves the built Web UI on port 5173 and returns HTTP 200.
- [x] `git diff --check` passed; runtime databases, logs, build output, and pytest directories are ignored and untracked.

## Local CLI Smoke Check

On 2026-10-23, discovery resolved Codex CLI 0.163.0 through its official npm package and Node runtime on `PATH`, and Claude Code 2.1.287 through its official native npm package. A real Codex read-only invocation returned the expected response. Claude initialized in read-only mode with no MCP servers, then the Anthropic API returned repeated HTTP 503 `server_error` responses before a final answer; full multi-agent collaboration therefore remains unaccepted pending a successful Claude request.

The Windows Codex adapter runs the verified `@openai/codex` JavaScript entrypoint directly under the resolved native `node.exe`; it does not execute the npm `.cmd` or PowerShell shims. Claude runs in `--safe-mode` with only `Read`, `Glob`, and `Grep` tools and strict MCP configuration, preserving normal CLI authentication while disabling user/project customizations.

## Acceptance Limits

- A real Codex read-only smoke call passed. Claude started with the expected read-only capabilities, but upstream HTTP 503 responses prevented a final answer; parallel analysis and the full approve/write/review flow still need end-to-end acceptance.
- This phase supports Codex CLI and Claude Code CLI; DeepSeek Harness, ZCode, and OpenCode need dedicated adapters before they can participate as real agents.
- The writer currently uses Codex, while analysis and review can select supported providers.
- Full real-provider acceptance remains limited by the external Claude API response described above. Automated and local Web UI verification does not substitute for a successful Claude-backed end-to-end run.

Older desktop UI plans and the August 2026 multi-agent bridge/task-allocation proposals under `docs/superpowers/` are historical references. Use this status document and the current collaboration design dated 2026-10-18 as the execution source of truth.
