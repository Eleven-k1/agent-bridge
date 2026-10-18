# Runtime and Web UI Modules

## Supported entry point

The supported product interface is the local browser Web UI. `desktop/` is an early prototype and is not used by the current workflow. The Web service serves the Vite build from `web-ui/dist/` and bridges browser commands to the Python backend over loopback.

## Local CLI collaboration

`backend/src/agent_bridge/agent_runtime/` contains local agent discovery, provider adapters, managed process execution, and the collaboration coordinator. The initial adapters are Codex CLI and Claude Code CLI. The coordinator runs parallel read-only analysis, publishes a consolidated plan, waits for UI approval, starts the designated Codex writer, then invokes a different reviewer against the resulting Git diff.

The Web UI exposes agent inventory, role selection, workspace access acknowledgement, plan approval or rejection, cancellation, temporary per-agent output, persisted events, diff inspection, and review results. Temporary output is not persisted as a formal event. Formal events are stored in SQLite and restored by the event cursor after reconnect.

## Existing model sandbox

The existing M2/M5/M6 model workflow remains available separately. `ModelGateway` supports `FakeProvider` and the fixed loopback OpenAI-compatible relay at `http://127.0.0.1:8080`. The browser does not receive relay credentials or connect to the provider directly. Formal model/task events are persisted before publication; temporary streaming frames are presentation-only.

## Start and verify

From the repository root, build the Web client and start the local service:

```powershell
cd web-ui/client
npm.cmd install
npm.cmd run build
cd ../..
wscript.exe .\web-ui\start-web.vbs
```

Open `http://127.0.0.1:5173/`. Stop with `wscript.exe .\web-ui\stop-web.vbs`.

Run the backend suite with a repository-local pytest temporary directory when the Windows system temp directory is inaccessible:

```powershell
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=backend\.pytest-run-tmp
```

Run client checks in `web-ui/client/`: `npm.cmd test`, `npm.cmd run typecheck`, and `npm.cmd run build`.

See [current-status.md](../current-status.md) for current acceptance limits. Real CLI end-to-end behavior still requires interactive acceptance against the locally installed tool versions.
