# Contributing to Agent Bridge

## Before changing code

Read the current execution source of truth:

- [Project README](README.md)
- [Architecture](docs/architecture.md)
- [Operations guide](docs/operations.md)
- [Current status](docs/current-status.md)

The browser Web UI is the supported product surface. Changes to the old `desktop/` prototype do not change the supported workflow unless the task explicitly targets that prototype.

## Development workflow

1. Create a focused Git branch or worktree from the current `master`.
2. Keep the main workspace and Agent worktrees separate.
3. For behavior changes, write a regression test first and observe it fail before implementing the smallest fix.
4. Update README or module documentation whenever behavior, interfaces, data, or user interaction changes.
5. Remove temporary scripts, debug output, mock data, and test artifacts before committing.
6. Use a clear commit message that describes the user-visible or architectural change.

## Required checks

Run from the repository root:

```powershell
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=backend\.pytest-run-contributing backend\tests
backend\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider --basetemp=backend\.pytest-run-contributing-web web-ui\tests
cd web-ui\client
npm.cmd ci --no-audit --no-fund
npm.cmd test
npm.cmd run typecheck
npm.cmd run build
cd ..\..
git diff --check
git status --short --branch
```

Use separate pytest base directories for concurrent Windows test runs. Remove those directories after verification. Do not commit `web-ui/dist/`, SQLite runtime data, PID files, launcher logs, or temporary worktrees.

## Pull requests

Explain the problem, the resulting behavior, the files or boundaries changed, and the verification output. State any external dependency or provider limitation explicitly. Do not claim a real CLI integration is accepted based only on adapter unit tests or fake-provider tests.
