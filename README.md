# Agent Bridge

Agent Bridge is a Windows-local multi-agent coding workspace. The supported user interface is the browser Web UI; the early desktop prototype is not an application entry point.

## Collaboration workflow

1. Open the Web UI at `http://127.0.0.1:5173/` and choose installed local CLI agents for parallel analysis, coordination, and independent review.
2. The selected agents inspect the acknowledged project workspace and return proposals. Their intermediate output is shown separately from persisted task events.
3. Review and approve the coordinated plan in the UI. Only the designated Codex writer then applies changes.
4. A different selected CLI agent reviews the resulting Git diff. The Web UI then shows a durable task report with each analysis summary, the coordinated plan, writer branch and diff, review outcome, and any failure details; persisted TASK events restore it after a page reload.

The initial runtime adapters support Codex CLI and Claude Code CLI. Agent discovery reports installed tools and whether each is currently supported. This workflow is for local project work and requires explicit acknowledgement of workspace access and approval of the write plan.

On the current machine, a read-only Codex CLI smoke call passed. Claude Code was discovered and initialized in read-only mode, but its API returned HTTP 503 during the smoke call; see [current status](docs/current-status.md) before treating the full multi-agent workflow as accepted.

## Start the Web UI

Build the client after a fresh checkout or client source changes:

```powershell
cd web-ui/client
npm.cmd install
npm.cmd run build
cd ../..
wscript.exe .\web-ui\start-web.vbs
```

Then open `http://127.0.0.1:5173/`. Stop the service with `wscript.exe .\web-ui\stop-web.vbs`. The service uses the project Python environment and serves the built files from `web-ui/dist/`.

The backend can also be started directly with `backend\.venv\Scripts\python.exe -m agent_bridge.app` from the repository root. Persistent events are stored in SQLite; set `AGENT_BRIDGE_DB_PATH` to override the default database path.

## Validation

```powershell
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=backend\.pytest-run-tmp
cd web-ui\client
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
```

Current verification results and known acceptance limits are tracked in [docs/current-status.md](docs/current-status.md).
