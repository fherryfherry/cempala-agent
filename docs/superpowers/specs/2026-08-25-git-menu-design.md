# Design: Menu Git — branch tree & commit history

Date: 2026-08-25
Status: Approved (brainstorming)

## Problem

The portal's agents work inside each workspace's `repo_path`, creating branches and
commits, but there is no way to inspect that activity from the portal. This spec adds a
view-only "Git" menu per workspace showing the repo's branch graph, commit history, and
per-commit diff.

## Scope

In scope:

- Branch graph (branching/merge lanes, classic gitk/GitHub-network style), SVG-rendered.
- Commit list (per branch, filterable), with branch/tag badges on commits.
- Commit detail: metadata, changed files with +/- statistics, unified diff per file.
- Refresh: manual button + `refetchInterval` (30 s).
- Load more pagination (limit 100 per page).

Out of scope (explicitly rejected / post-MVP):

- Any git write action from the UI (commit, branch, checkout, push, reset). This is a
  hard boundary: the backend allowlist contains read-only subcommands only.
- Multiple repos per workspace (uses `workspace.repo_path`).
- Standalone tag list, blame view, staging UI, search across commits.
- Live update via SSE on `run_ended` (not requested; periodic refetch suffices).

## Architecture

```
frontend/app/w/[key]/git/page.tsx   →  lib/api.ts git functions  →  GET /api/workspaces/{id}/git/*
backend/app/api/git.py              →  backend/app/core/git.py (run_git, lane layout)  →  git CLI subprocess
```

The backend reads the repo via the `git` CLI as a subprocess — the same pattern the portal
already uses for the opencode adapter. No new dependencies (no pygit2/dulwich), no graph
library on the frontend (SVG rendered by hand).

## Backend

### `backend/app/core/git.py` (new module)

`run_git(repo_path, *args)`:

- `subprocess.run(["git", *args], cwd=repo_path, ...)`, no shell, timeout (e.g. 15 s).
- Env: `GIT_TERMINAL_PROMPT=0` so git never blocks waiting for credentials.
- Allowlist of subcommands (`log`, `show`, `branch`, `rev-parse`, `diff`): any other
  first arg raises an error. This is a defense-in-depth guard, not a security boundary.
- Return stdout/stderr; raise `GitError` on non-zero exit or git "not a repository"
  detection.

Public functions:

- `list_branches(repo_path) -> list[Branch]` — name, is_current, latest commit sha +
  subject. From `git for-each-ref`/`git branch --format`.
- `get_graph(repo_path, limit=100) -> GraphData` — collect commits from all branches
  (`git log --all --parents`) newest-first, then compute lane layout in Python using the
  classic gitk first-parent-following algorithm: each commit gets a lane index and the
  lane count; rows with fake merges removed. Output: ordered commits, each with
  `sha, subject, author_name, author_date (ISO), lane, total_lanes, parents, branch
  tips` (which branch heads point at it).
- `list_commits(repo_path, ref, limit, offset) -> page of commits` — from
  `git log --format=...` with `--skip`/`--max-count`.
- `get_commit(repo_path, sha) -> CommitDetail` — metadata, per-file stats
  (`git show --numstat --format=...`), and unified diff (`git show --patch`, truncated to
  a sane cap, e.g. 2 MB) parsed into per-file sections.

Errors surface as `AppError(404, "not a git repository")` or `400` with a helpful message
— same shape as the rest of the API.

### `backend/app/api/git.py` (new router)

Registered in `main.py` with prefix `/api`, nested under `/workspaces/{workspace_id}/git`.

Endpoints (all GET, all read-only):

- `GET /workspaces/{workspace_id}/git/branches` → `list[BranchOut]`
- `GET /workspaces/{workspace_id}/git/graph?limit=100` → `GraphOut`
- `GET /workspaces/{workspace_id}/git/commits?ref=<branch|sha>&limit=100&offset=0` →
  `list[CommitOut]` (offset used for "load more")
- `GET /workspaces/{workspace_id}/git/commits/{sha}` → `CommitDetailOut`

Every endpoint resolves the workspace, reads `workspace.repo_path`, and validates it
exists/contains a repo. Pydantic response models in `backend/app/schemas/git.py`.

## Frontend

- Nav item "Git" in `frontend/components/header.tsx` → `/w/[key]/git` (same pattern as
  other links).
- New page `frontend/app/w/[key]/git/page.tsx`.
- API client functions in `frontend/lib/api.ts`: `listGitBranches`, `getGitGraph`,
  `listGitCommits`, `getGitCommit` + types.
- Two-panel layout (grid, responsive stack):
  - **Left:** branch list (click to filter commits) + graph (SVG lanes + commit nodes,
    click a commit to select it) + commit list with branch/tag badges.
  - **Right:** commit detail — metadata, file list with `+N/-M` stats, unified diff
    rendered as monospace blocks (no external diff viewer).
- React Query: `useQuery` with `refetchInterval: 30000` for graph/branches/commits, plus
  a manual refresh button. Commit detail fetched on selection.
- "Load more" on the commit list appends the next 100.
- If backend returns "not a git repository" (or repo missing), show a clear empty state
  ("This workspace's repo_path is not a git repository") instead of an error page.

## Data flow & error handling

- Frontend → `GET /api/.../git/graph` → backend `run_git` → parsed lane graph → JSON.
- Any git failure (missing repo, corrupt repo, subprocess timeout) maps to a structured
  `AppError`; frontend shows a friendly message + the underlying git error string.
- No SSE/event changes; git data is refetched, never streamed.

## Testing

New pytest suite `backend/tests/test_git.py` using a real throwaway git repo created in a
tmp dir (the existing conftest already spins up temp repos; git must be available in the
test env — it is, since the app shells out to it). Cover:

- lane assignment on merge/two-branch topologies (single commit criss-cross, merge with
  two parents, branch tip at different commits),
- `run_git` allowlist rejection of write subcommands,
- API endpoints return expected shape; missing/invalid repo → 404/400,
- commit diff payload cap/truncation.

## Conventions

- Docs, identifiers, code, commits in English.
- Reference the MAP ticket number (add one to `docs/04-tasks.md` and use it in the
  commit, per repo convention).
- Update roadmap/README if the feature is worth listing there.
