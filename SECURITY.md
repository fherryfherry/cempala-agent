# Security Policy

CEMPALA runs external coding CLIs in full-auto mode with the privileges of the user
running the backend. This project takes security seriously — but its threat model is
deliberately narrow, so please read this before reporting.

## Supported versions

| Version | Supported          |
| ------- | ------------------ |
| `main`  | :white_check_mark: |
| < 0.1   | :x:                |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.** Report privately:

- **Email:** ferdevelop15@gmail.com
- **GitHub private advisory:** https://github.com/fherryfherry/cempala-agent/security/advisories/new

Please include:

- Which part is affected (backend / frontend / docs)
- Version or commit hash
- A minimal reproduction, if possible
- Impact assessment — what an attacker could do

You should receive an acknowledgement within 48 hours and a status update within 5
business days. Fixes land on `main` before any public disclosure.

## Security by design — the known, accepted risks

The portal is a local tool for running AI agents autonomously. By design:

- **Login is required (ADR-016), with per-workspace roles (viewer/editor/admin).** Superseded
  ADR-005's no-auth posture — but login only controls *who reaches* the portal, not what an
  authenticated editor's agents can do once inside a workspace (see the next point).
- **Full-auto agent CLIs are not sandboxed.** Every supported CLI runs in full-auto mode
  (`opencode --auto`, `claude --permission-mode ...`, `codex --dangerously-bypass-approvals-and-sandbox`,
  `agy --dangerously-skip-permissions`), and the working-directory flag is **not** a sandbox.
- **Agent runs have full user privileges.** An agent can run arbitrary commands with the
  privileges of the user running the backend. `repo_path` validation is a convenience
  check, not a security boundary. This is unchanged by ADR-016 — only bind beyond
  `127.0.0.1` deliberately, and only create accounts for people you actually trust with
  that access.

These are conscious architectural decisions, documented in detail in
[`docs/06-adr.md`](docs/06-adr.md) (ADR-016, ADR-010) and the
[README security warning](README.md#-security-warning--read-before-running).

Reports about these by-design properties (e.g. "agents can run arbitrary commands") are
**not** considered vulnerabilities; they are documented behavior. Reports about the
remaining guardrails failing — run timeout, cost limits, kill switch, loop detector —
**are** valuable and will be treated as real security issues.

### Guardrails

The portal's only brakes are guardrails. Every guardrail trip must leave a system comment
naming which guardrail fired — a silent failure path is itself a bug. If you find a way
for a guardrail to fail without a comment, that is a reportable vulnerability.

## Safe testing practice

When testing or dogfooding, never point a workspace at a directory containing production
secrets, and never run destructive commands against `backend/map.db` or `storage/` —
check for a live backend first (`lsof -i :8000`), and use a throwaway DB for manual
verification (see [CONTRIBUTING.md](CONTRIBUTING.md#security--please-read)).
