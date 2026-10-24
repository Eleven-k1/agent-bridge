# Third-Party Notices

Agent Bridge uses third-party packages. Their licenses remain with their respective copyright holders. The package manifests and lockfiles are the authoritative dependency records; this file summarizes direct dependencies used by the project.

## Python

| Package | Declared range | License | Use |
| --- | --- | --- | --- |
| [httpx](https://pypi.org/project/httpx/) | `>=0.27,<1` | BSD-3-Clause | Backend HTTP client |
| [pytest](https://pypi.org/project/pytest/) | `>=9,<10` | MIT | Test-only optional dependency |

The Python dependency metadata is declared in `backend/pyproject.toml`. Transitive Python dependencies are installed by the package manager and retain their upstream licenses.

## Web UI

| Package | Resolved version | License | Use |
| --- | ---: | --- | --- |
| [React](https://www.npmjs.com/package/react) | 19.3.1 | MIT | UI runtime |
| [React DOM](https://www.npmjs.com/package/react-dom) | 19.3.1 | MIT | Browser renderer |
| [lucide-react](https://www.npmjs.com/package/lucide-react) | 1.59.0 | ISC | UI icons |
| [Vite](https://www.npmjs.com/package/vite) | 6.4.4 | MIT | Build tool and dev server |
| [TypeScript](https://www.npmjs.com/package/typescript) | 5.9.3 | Apache-2.0 | Type checking |
| [Vitest](https://www.npmjs.com/package/vitest) | 3.2.7 | MIT | Test runner |
| [jsdom](https://www.npmjs.com/package/jsdom) | 25.0.1 | MIT | Browser test environment |
| [@testing-library/react](https://www.npmjs.com/package/@testing-library/react) | 16.3.3 | MIT | UI tests |
| [@vitejs/plugin-react](https://www.npmjs.com/package/@vitejs/plugin-react) | 4.7.0 | MIT | Vite React integration |

The complete resolved dependency graph, integrity hashes, and package metadata are recorded in `web-ui/client/package-lock.json`. Run `npm.cmd ci` to install exactly that graph.

## Provider CLIs

Codex CLI, Claude Code, and any other local provider executable are external software. They are not bundled with this repository and are governed by their own terms and licenses.
