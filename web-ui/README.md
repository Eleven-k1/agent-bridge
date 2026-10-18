# Local Web UI

The browser Web UI is the supported user interface. Build and start it from the repository root:

```powershell
cd web-ui/client
npm.cmd install
npm.cmd run build
cd ../..
wscript.exe .\web-ui\start-web.vbs
```

Open `http://127.0.0.1:5173/`. Stop the local service with `wscript.exe .\web-ui\stop-web.vbs`. The service reuses a healthy project instance and serves the Vite production output from `dist/`.

The workspace includes the local CLI collaboration workflow and the existing model sandbox. Collaboration supports Codex CLI and Claude Code CLI discovery, parallel read-only analysis, a coordinated plan requiring UI approval, one Codex writer, and an independent reviewer. The UI displays agent inventory, roles, temporary output, persisted events, cancellation, Git diff, and review outcome. The browser sends commands through the loopback Web service; local process management remains in the Python backend.

Client checks in `client/`: `npm.cmd test`, `npm.cmd run typecheck`, and `npm.cmd run build`. Backend tests from the repository root: `backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=backend\.pytest-run-tmp`.

Current limits and acceptance status are in [../docs/current-status.md](../docs/current-status.md). Real installed CLI end-to-end acceptance remains outstanding.
