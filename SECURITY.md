# Security Policy

## Scope

Agent Bridge runs local CLI Agents against a user-selected project workspace. The browser service binds to the loopback interface and the backend owns process execution, persistence, Git worktrees, and approval gates.

## Reporting a vulnerability

Do not include API keys, CLI tokens, cookies, private project files, or full database files in an issue or pull request. No public security contact is configured for this repository yet. Until the project owner publishes one, do not disclose suspected vulnerabilities in public issues; send them privately to the project owner through the same private channel used to grant access to this repository and include:

- affected commit or version;
- operating system and runtime versions;
- reproducible steps using non-sensitive files;
- expected and observed behavior;
- whether credentials, workspace files, process boundaries, or persisted events are involved.

Allow time for the issue to be reproduced and fixed before public disclosure.

## Local safety expectations

- Keep credentials in the operating system credential store or the CLI's supported login flow; do not commit them.
- Do not put secrets in a workspace that an analysis Agent can read.
- Review the coordinated plan before approving a writer.
- Inspect the worktree branch and redacted diff before integrating changes.
- Treat provider read-only flags as application policy, not a replacement for OS-level isolation.
