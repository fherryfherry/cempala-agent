# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.5] - 2026-08-30

### Added

- Floating chat: a PM-avatar trigger button, bottom-right on every workspace page, opens a
  floating conversation panel styled after the onboarding wizard's chat bubbles — sharing the
  same conversation data as the full Chat page, so nothing is duplicated between them.
- Notification toasts now show one card per agent (avatar, activity text, relative time), which
  updates in place on that agent's next activity instead of stacking a new card per event; cards
  stay fully visible (no collapsing) and dismiss a bit later than before.
- Kanban board's Backlog column is now hidden while a specific sprint is selected (backlog
  tickets have no sprint, so they only ever showed empty there) — only visible under "All sprints".
- Ticket detail page shows a pulsing "running" badge while any of its runs are in flight.

### Fixed

- Auto-check's stale-ticket nudge now also covers tickets still stuck at `todo` (previously only
  in_progress/review/qa/security/blocked were revisited, so an assigned ticket whose first
  schedule silently failed could sit invisible to the nudge loop forever).

## [0.2.4] - 2026-08-29

### Fixed

- Agent prompts now state the team roster is exhaustive, so agents (esp. PM in chat) stop
  narrating handoffs to generic SDLC roles (QA, Security) that have no agent in the workspace
  and promising follow-through that could never fire.
- Removed a stale "typing" label left on rendered chat messages after they finish streaming.

## [0.2.3] - 2026-08-29

### Fixed

- `install.sh` no longer tries to apt-install a Python version that doesn't exist in Ubuntu
  22.04's default repos (`python3.12`); `uv` manages its own Python interpreter, so no system
  Python install is attempted at all now.
- `install.sh` no longer installs the distro-packaged `nodejs` (Ubuntu 22.04 ships v12, need
  v20+) — uses NodeSource's setup script on apt/dnf instead, and verifies the installed version
  is actually ≥20 before proceeding.
- Fixed a cosmetic double-slash (`.//home/...`) in the installer's final message when given an
  absolute destination path.

Verified end-to-end (fresh install, re-run/idempotency, and `uv`'s Python auto-download) in a
clean Ubuntu 22.04 Docker container.

## [0.2.2] - 2026-08-29

### Added

- `install.sh` now auto-installs missing prerequisites (git, Python 3.11+, Node.js, make, `uv`,
  `opencode`) via brew/apt/dnf instead of only checking for them.

### Changed

- README reordered: Features → Quick install → Setup from scratch → Running → Layout → Settings
  storage → Prerequisites → Security warning (+ Tailscale remote-access section) → Contributing.
- README intro trimmed to a single paragraph (moved the "no coding agent of its own" note and the
  *cempala* name origin story out).

## [0.2.1] - 2026-08-29

### Added

- `HOST_BACKEND` Makefile var (default `127.0.0.1`) to override the backend bind address when
  needed, instead of hardcoding it.
- `allowedDevOrigins` in `next.config.ts` for accessing the frontend dev server over Tailscale.

## [0.2.0] - 2026-08-29

### Added

- One-shot dev script `./run.sh` — setup (venv + node_modules) + migrate + backend/frontend together.
- Workspace terminate endpoint `POST /workspaces/{id}/terminate` + settings card, with 409-timeout
  behavior keeping the workspace paused.
- Global AI-orchestrator default model setting (`GET/PUT /api/settings/orchestrator-model`);
  `Agent.model` is now nullable so the global default applies to all run types.
- Git menu (MAP-053) — branch tree + commit history.
- Agent CLI adapters for `agy` and `codex` (full autonomous flow, not stubs).

### Changed

- README made tool-agnostic; documented all four agent CLIs (`opencode`, `claude`, `codex`,
  `agy`) with per-OS install links and prerequisites (Python/Node/uv/make).

### Removed

- Unused release ticket status.

## [0.1.0] - 2026-08-22

MVP: a Jira-like portal where AI agents work tickets autonomously inside a local repo.

### Added

- **Backend (FastAPI + SQLite/SQLAlchemy/Alembic)**
  - Bootstrap (MAP-002), DB schema + migrations (MAP-003), Makefile dev runner (MAP-005).
  - Workspace CRUD (MAP-006) with auto-create `repo_path`, agent CRUD (MAP-008), ticket CRUD +
    key numbering (MAP-009), ticket state machine + PATCH enforcement (MAP-012), comment API +
    @mention parsing (MAP-010), attachment upload/download/delete (MAP-011).
  - `GET /api/models` from `opencode models` (MAP-007).
  - Prompt builder + per-role defaults (MAP-019), ` ```map ` block parser (MAP-018), opencode
    subprocess adapter (MAP-020), stub adapters for claude/agy/codex (MAP-021).
  - Run API + orchestrator (MAP-023), SSE events stream (MAP-022), EventBus + event persistence
    (MAP-017), recover interrupted runs on startup (MAP-026).
  - Guardrails: schedule-time + runtime checks (MAP-027), loop detector (MAP-028), kill switch
    pause/resume (MAP-031).
  - Handoff engine (MAP-029), full autonomous flow — auto-schedule `tickets[]`, close epics,
    verify sessions (MAP-030).
- **Frontend (Next.js App Router)**
  - Bootstrap (MAP-004), workspace + agent setup UI (MAP-013), kanban board UI (MAP-014),
    ticket detail page UI (MAP-015), workspace settings UI (MAP-032).
  - Activity feed + run detail panel UI (MAP-025), live SSE events for comments/status changes,
    activity toast notifications, workspace SSE context (MAP-024).
  - Chat with PM page for conversational idea intake, ChatGPT-style composer with attach menu
    and speech-to-text, quick-send suggestion chips, typing indicator, attachment preview
    dialog, CEMPALA branding.
  - Workspace dashboard overview page + stats helpers with unit tests (MAP-034).
  - Dark mode toggle; auto-suggested agent names (Indonesian) based on role.
- **Docs** — PRD, technical spec, agent design, task list, roadmap, ADRs, dogfood report
  (MAP-001).
- **Testing** — 100-way ticket numbering + full state machine matrix (MAP-016).

### Fixed

- Track `storage/attachments/.gitkeep` (MAP-001).
- `comment.author_agent_id` should `SET NULL`, not `CASCADE` (MAP-003).
- Uniform error shape for pydantic validation errors (MAP-006).
- Nested ticket comments missing @mentions (MAP-015 follow-up).
- Deterministic-flake in `test_valid_map_block_transitions` (MAP-029 fallout); subprocess-kill
  tests use unique sleep durations / poll for spawn instead of fixed sleeps.
- `--font-sans` CSS variable was self-referencing, breaking font loading; widened model select
  dropdown.
- opencode adapter JSON schema didn't match real CLI output (found via MAP-033 dogfood).
- Test fixtures depended on an ambient, pre-migrated `backend/map.db`.

[Unreleased]: https://github.com/fherryfherry/cempala-agent/compare/v0.2.3...HEAD
[0.2.3]: https://github.com/fherryfherry/cempala-agent/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/fherryfherry/cempala-agent/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/fherryfherry/cempala-agent/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/fherryfherry/cempala-agent/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/fherryfherry/cempala-agent/releases/tag/v0.1.0
