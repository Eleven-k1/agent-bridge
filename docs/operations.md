# Operations Guide

## Start, stop, and health

Start the supported browser service from the repository root:

```powershell
wscript.exe .\web-ui\start-web.vbs
```

Check the service:

```powershell
Invoke-WebRequest http://127.0.0.1:5173/health
```

Expected response is HTTP 200 with `{"ok":true,"service":"web-ui"}`. Stop the service with:

```powershell
wscript.exe .\web-ui\stop-web.vbs
```

The launcher keeps `web-ui\.web-ui.pid` and writes fatal startup diagnostics to `web-ui\launcher-error.log`.

## Rebuild after source changes

The server serves `web-ui/dist`, not TypeScript source files. After changing the client:

```powershell
cd web-ui\client
npm.cmd run build
cd ..\..
```

Then refresh the browser. A stale page usually means the build was not regenerated or a different worktree is serving port 5173.

## Confirm the active worktree

```powershell
Get-NetTCPConnection -LocalPort 5173 -State Listen
Get-CimInstance Win32_Process | Where-Object CommandLine -Match 'web-ui\\server\\launcher.py' | Select-Object ProcessId,CommandLine
git status --short --branch
git worktree list
```

The command line and `web-ui\.web-ui.pid` must point to the intended repository. The supported launcher is the one under the main checkout.

## CLI discovery diagnostics

The UI's “刷新库存” command maps to `LIST_LOCAL_AGENTS`. Discovery reports five known IDs:

- `codex`: resolves a native `codex.exe` or the verified `@openai/codex` JavaScript entrypoint with native Node.
- `claude-code`: resolves the official `claude.exe` package binary.
- `opencode`, `deepseek-harness`, `zcode`: inventory entries only until dedicated adapters exist.

If a supported Agent is unavailable, verify the official CLI is installed, its normal login works in a terminal, and the service was restarted after installation. Do not add arbitrary executable paths through the browser.

## Collaboration diagnostics

If the start button is disabled, check that the prompt is non-empty, at least two installed and adapted analysis agents are selected, the coordinator is selected, the writer is Codex, the reviewer differs from the writer, and workspace access is acknowledged.

If a plan never appears, inspect formal events for `AGENT_PARTICIPANT_STATUS`, `AGENT_ANALYSIS_COMPLETED`, `AGENT_COORDINATION_STARTED`, and `AGENT_COLLABORATION_FAILED`. Missing login, invalid JSONL, timeout, non-zero exit, or upstream API failure should produce a diagnostic rather than silently falling back to a model API.

If the report is incomplete after reconnect, refresh once and inspect the formal event count. The report uses formal TASK events only; live output is intentionally not restored. The client buffers out-of-order events and should not accept a later sequence as the new cursor until the gap is filled.

If port 5173 is already in use, use the project stop script first and inspect the command line before terminating another process:

```powershell
Get-NetTCPConnection -LocalPort 5173 -State Listen
Get-CimInstance Win32_Process -Filter "ProcessId = <PID>" | Format-List ProcessId,CommandLine
wscript.exe .\web-ui\stop-web.vbs
```

## Data and recovery

The default SQLite file is `web-ui\agent-bridge.sqlite3`. Back it up while the service is stopped if it must be preserved. Formal events are the recovery source of truth; deleting the database removes local task history and command idempotency records.

An interrupted run is recorded as a failed collaboration during service restoration. Worktrees created for a run are retained so the user can inspect or remove them after reviewing the branch and diff.

## Verification before release

Run from the repository root, using isolated pytest base directories:

```powershell
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=backend\.pytest-run-full
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=backend\.pytest-run-web web-ui\tests
cd web-ui\client
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
cd ..\..
git diff --check
git status --short --branch
```

Remove pytest temporary directories after the run. Do not commit `web-ui/dist/`, SQLite runtime data, PID files, launcher logs, or test temporary worktrees.

## Known acceptance limits

Automated coverage includes adapters, process management, coordinator transitions, persistence, transport, Web UI behavior, and failure paths. A real Codex read-only smoke call passed. Claude Code initialized with the intended read-only flags but a recent request received repeated upstream HTTP 503 responses, so successful full two-provider collaboration still requires a later external-service retry.
