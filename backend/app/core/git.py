"""Read-only git access for the Git menu.

All git operations go through run_git(), which shells out to the `git` binary with an
allowlist of read-only subcommands and enforces a timeout. No mutation — branch,
checkout, commit, push, reset are all blocked at the allowlist level (and are never
called by the API layer).
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Literal

GIT_TIMEOUT = 15.0
DIFF_CAP = 2_000_000
_READONLY = {"log", "show", "branch", "for-each-ref", "rev-parse", "diff", "diff-tree", "rev-list"}
_MUTATION = {"checkout", "checkout -b", "branch", "merge", "commit", "worktree"}


class GitError(Exception):
    def __init__(self, message: str, stderr: str = ""):
        super().__init__(message)
        self.stderr = stderr


def _run(repo_path: str, *args: str) -> subprocess.CompletedProcess:
    if not args or args[0] not in _READONLY:
        raise GitError(f"disallowed subcommand: {args[0] if args else '(empty)'}")
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError:
        raise GitError("git binary not found on the backend host")
    except subprocess.TimeoutExpired:
        raise GitError(f"git command timed out after {GIT_TIMEOUT}s")
    return proc


def _run_mutation(repo_path: str, *args: str) -> subprocess.CompletedProcess:
    """Run a git mutation command (checkout, checkout -b, branch, merge, commit).

    Uses the same allowlist approach as _run() but for mutation commands.
    """
    if not args:
        raise GitError("no git command given")
    cmd_str = args[0]
    if cmd_str not in _MUTATION:
        raise GitError(f"disallowed mutation subcommand: {cmd_str}")
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except FileNotFoundError:
        raise GitError("git binary not found on the backend host")
    except subprocess.TimeoutExpired:
        raise GitError(f"git command timed out after {GIT_TIMEOUT}s")
    return proc


def run_mutation(repo_path: str, *args: str) -> str:
    result = _run_mutation(repo_path, *args)
    if result.returncode != 0:
        raise GitError("git_mutation_failed", result.stderr.strip() or "git mutation failed")
    return result.stdout


def run_git(repo_path: str, *args: str) -> str:
    result = _run(repo_path, *args)
    if result.returncode != 0:
        stderr = result.stderr.strip()
        if "not a git repository" in stderr or "fatal: not a git repository" in stderr:
            raise GitError("not_a_repo", stderr)
        if "fatal: bad object" in stderr or "fatal: ambiguous argument" in stderr:
            raise GitError("object_not_found", stderr)
        raise GitError("git_failed", stderr or "git command failed")
    return result.stdout


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class Branch:
    name: str
    is_current: bool
    latest_sha: str
    latest_subject: str


@dataclass
class GraphCommit:
    sha: str
    parents: list[str]
    subject: str
    author_name: str
    author_date: str
    lane: int
    total_lanes: int
    decorations: list[str]


@dataclass
class CommitFile:
    path: str
    additions: int
    deletions: int
    status: Literal["A", "M", "D", "R", "C"] | None = None


@dataclass
class CommitDetail:
    sha: str
    subject: str
    author_name: str
    author_date: str
    body: str
    parents: list[str]
    is_merge: bool
    files: list[CommitFile]
    patch: str
    patch_truncated: bool


# ---------------------------------------------------------------------------
# Branch list
# ---------------------------------------------------------------------------

def list_branches(repo_path: str) -> list[Branch]:
    out = run_git(
        repo_path,
        "for-each-ref",
        "--format=%(HEAD)|%(refname:short)|%(objectname:short)|%(contents:subject)",
        "refs/heads",
    )
    branches = []
    for line in out.splitlines():
        if not line:
            continue
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        head_mark, name, sha, subject = parts
        branches.append(
            Branch(
                name=name,
                is_current=(head_mark == "*"),
                latest_sha=sha,
                latest_subject=subject,
            )
        )
    return branches


# ---------------------------------------------------------------------------
# Lane layout (gitk-style)
# ---------------------------------------------------------------------------

def _compute_lanes(rows: list[dict]) -> list[dict]:
    """Assign each commit a lane index (classic gitk algorithm).

    Rows are in topo order (children before parents, all parents listed once).
    The first-parent chain of each branch occupies a single vertical lane.
    Side branches (second+ parents of merges) get their own lanes.
    """
    lane_of: dict[str, int] = {}
    child_claimed: dict[str, int] = {}  # sha -> lane this child assigned to its first-parent
    next_lane = 0
    total_lanes = 0

    for row in rows:
        sha = row["sha"]
        parents = row["parents"]

        # Claim this sha's lane: either already claimed by a child, or fresh
        if sha in child_claimed:
            lane = child_claimed.pop(sha)
        else:
            lane = next_lane
            next_lane += 1

        lane_of[sha] = lane
        total_lanes = max(total_lanes, lane + 1)

        if not parents:
            continue

        # First parent inherits this commit's lane (main-line continuity)
        lane_of[parents[0]] = lane
        child_claimed[parents[0]] = lane

        # Second+ parents each get a fresh lane
        for p in parents[1:]:
            if p in child_claimed:
                continue
            child_claimed[p] = next_lane
            lane_of[p] = next_lane
            next_lane += 1

    # Attach total_lanes to every row
    for row in rows:
        row["lane"] = lane_of.get(row["sha"], 0)
        row["total_lanes"] = total_lanes

    return rows


# ---------------------------------------------------------------------------
# Commit graph
# ---------------------------------------------------------------------------

def get_graph(repo_path: str, limit: int = 100) -> tuple[list[GraphCommit], int]:
    """Return lane-assigned graph rows (up to `limit` newest commits across all refs)."""
    out = run_git(
        repo_path,
        "log",
        "--all",
        "--topo-order",
        "--parents",
        f"--max-count={limit}",
        "--pretty=format:%H%x1f%P%x1f%an%x1f%ad%x1f%s%x1f%d",
        "--date=iso-strict",
    )

    rows: list[dict] = []
    for line in out.splitlines():
        if not line:
            continue
        parts = line.split("\x1f")
        if len(parts) < 6:
            continue
        sha, parents_str, author, date, subject = parts[0], parts[1], parts[2], parts[3], parts[4]
        decorations = []
        if len(parts) > 6 and parts[6].strip():
            # e.g. "HEAD -> main" or "tag: v1.0" or "origin/main"
            raw = parts[6].strip("() ")
            for token in raw.split(", "):
                token = token.strip()
                if token.startswith("tag: "):
                    decorations.append(token[5:])
                elif token.startswith("HEAD -> "):
                    decorations.append(token[8:])
                elif token and token != "HEAD":
                    decorations.append(token)

        rows.append(
            {
                "sha": sha,
                "parents": parents_str.split() if parents_str else [],
                "subject": subject,
                "author_name": author,
                "author_date": date,
                "decorations": decorations,
            }
        )

    rows = _compute_lanes(rows)
    total = max((r["total_lanes"] for r in rows), default=1)
    commits = [
        GraphCommit(
            sha=r["sha"],
            parents=r["parents"],
            subject=r["subject"],
            author_name=r["author_name"],
            author_date=r["author_date"],
            lane=r["lane"],
            total_lanes=total,
            decorations=r["decorations"],
        )
        for r in rows
    ]
    return commits, total


# ---------------------------------------------------------------------------
# Commit history (per-ref, paginated)
# ---------------------------------------------------------------------------

def list_commits(
    repo_path: str, ref: str = "HEAD", limit: int = 100, offset: int = 0
) -> tuple[list[GraphCommit], int, bool]:
    """Paginated commit list for a ref (branch / sha / --all).

    Returns (commits, total_lanes, has_more).
    """
    if ref == "--all":
        ref_arg = "--all"
    elif ref.startswith("-"):
        raise GitError("invalid_ref", f"ref cannot start with '-': {ref}")
    else:
        ref_arg = ref

    out = run_git(
        repo_path,
        "log",
        ref_arg,
        "--topo-order",
        "--parents",
        f"--skip={offset}",
        f"--max-count={limit + 1}",  # fetch one extra to detect has_more
        "--pretty=format:%H%x1f%P%x1f%an%x1f%ad%x1f%s%x1f%d",
        "--date=iso-strict",
    )

    rows: list[dict] = []
    for line in out.splitlines():
        if not line:
            continue
        parts = line.split("\x1f")
        if len(parts) < 6:
            continue
        sha, parents_str, author, date, subject = parts[0], parts[1], parts[2], parts[3], parts[4]
        decorations = []
        if len(parts) > 6 and parts[6].strip():
            raw = parts[6].strip("() ")
            for token in raw.split(", "):
                token = token.strip()
                if token.startswith("tag: "):
                    decorations.append(token[5:])
                elif token.startswith("HEAD -> "):
                    decorations.append(token[8:])
                elif token and token != "HEAD":
                    decorations.append(token)
        rows.append(
            {
                "sha": sha,
                "parents": parents_str.split() if parents_str else [],
                "subject": subject,
                "author_name": author,
                "author_date": date,
                "decorations": decorations,
            }
        )

    has_more = len(rows) > limit
    rows = rows[:limit]

    # Assign lanes within this window; note: lanes may jump across offset boundaries
    rows = _compute_lanes(rows)
    total = max((r["total_lanes"] for r in rows), default=1)

    commits = [
        GraphCommit(
            sha=r["sha"],
            parents=r["parents"],
            subject=r["subject"],
            author_name=r["author_name"],
            author_date=r["author_date"],
            lane=r["lane"],
            total_lanes=total,
            decorations=r["decorations"],
        )
        for r in rows
    ]
    return commits, total, has_more


# ---------------------------------------------------------------------------
# Commit detail (metadata + diff)
# ---------------------------------------------------------------------------

def get_commit(repo_path: str, sha: str) -> CommitDetail:
    # Metadata + body
    meta_out = run_git(
        repo_path,
        "log",
        f"--max-count=1",
        sha,
        "--pretty=format:%H%x1f%an%x1f%ae%x1f%ad%x1f%s%x1f%b",
        "--date=iso-strict",
    )
    meta_parts = meta_out.split("\x1f")
    if len(meta_parts) < 6:
        raise GitError("object_not_found", f"commit not found: {sha}")
    commit_sha, author_name, author_email, author_date, subject, body = (
        meta_parts[0],
        meta_parts[1],
        meta_parts[2],
        meta_parts[3],
        meta_parts[4],
        meta_parts[5] if len(meta_parts) > 5 else "",
    )

    # Parents
    parent_out = run_git(
        repo_path, "rev-list", "--parents", "-1", sha
    ).strip()
    if parent_out:
        parents = parent_out.split()[1:]
    else:
        parents = []

    # File stats (handles merges via -m --first-parent for correct first-parent diff)
    stat_out = run_git(
        repo_path,
        "show",
        "-m",
        "--first-parent",
        "--format=",
        "--numstat",
        sha,
    )

    files: list[CommitFile] = []
    name_status: dict[str, str] = {}
    if stat_out.strip():
        name_status_raw = run_git(
            repo_path, "diff-tree", "--no-commit-id", "--name-status", "-r", sha
        ).strip()
        for ns_line in name_status_raw.splitlines():
            parts = ns_line.split("\t", 1)
            if len(parts) == 2:
                name_status[parts[1]] = parts[0]

        for stat_line in stat_out.splitlines():
            if not stat_line.strip():
                continue
            parts = stat_line.split("\t")
            if len(parts) < 3:
                continue
            add_s, del_s, path = parts[0], parts[1], parts[2]
            try:
                additions = int(add_s) if add_s != "-" else 0
                deletions = int(del_s) if del_s != "-" else 0
            except ValueError:
                additions, deletions = 0, 0
            status_char = name_status.get(path)
            status: Literal["A", "M", "D", "R", "C"] | None = None
            if status_char == "A":
                status = "A"
            elif status_char == "M":
                status = "M"
            elif status_char == "D":
                status = "D"
            elif status_char == "R":
                status = "R"
            elif status_char == "C":
                status = "C"
            files.append(CommitFile(path=path, additions=additions, deletions=deletions, status=status))

    # Patch (truncated)
    patch_out = run_git(
        repo_path,
        "show",
        "-m",
        "--first-parent",
        "--format=",
        "--patch",
        sha,
    )
    truncated = len(patch_out) > DIFF_CAP
    patch = patch_out[:DIFF_CAP] if truncated else patch_out

    return CommitDetail(
        sha=commit_sha,
        subject=subject,
        author_name=author_name,
        author_date=author_date,
        body=body,
        parents=parents,
        is_merge=len(parents) > 1,
        files=files,
        patch=patch,
        patch_truncated=truncated,
    )


# ---------------------------------------------------------------------------
# Worktree management (feature-branch workflow, MAP-055)
# ---------------------------------------------------------------------------

def _sanitize_worktree_name(ticket_key: str) -> str:
    """Turn a ticket key like 'MAP-123' into a safe directory name component."""
    return ticket_key.lower().replace("-", "_")


def _slugify(s: str) -> str:
    """Turn any string into a valid git branch name component."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", s.strip()).lower().strip("-")
    return slug or "untitled"


def _epic_branch_name(epic_title: str) -> str:
    """Branch name for an epic: epic/<slugified-title>-epic."""
    return f"epic/{_slugify(epic_title)}-epic"


def prepare_worktree(
    repo_path: str,
    ticket_key: str,
    epic_branch: str | None = None,
    base_branch: str = "main",
) -> str:
    """Create a new git-worktree for a ticket's feature branch.

    Returns the absolute path to the worktree directory.
    The worktree is branched from `epic_branch` if set (epic branch must already exist),
    otherwise from `base_branch`. If `epic_branch` is set but the branch doesn't exist,
    it is created first from `base_branch`.

    Worktree path: `.worktrees/feat-{sanitized_key}/` inside `repo_path`.
    Branch name: `feat/{ticket_key}`.

    Raises GitError if the worktree already exists or branch creation fails.
    """
    worktrees_root = os.path.join(repo_path, ".worktrees")
    safe_name = _sanitize_worktree_name(ticket_key)
    worktree_path = os.path.join(worktrees_root, f"feat-{safe_name}")
    branch_name = f"feat/{ticket_key}"

    parent_branch = base_branch
    if epic_branch:
        if not _branch_exists(repo_path, epic_branch):
            _create_branch_from(repo_path, epic_branch, base_branch)
        parent_branch = epic_branch

    os.makedirs(worktrees_root, exist_ok=True)
    run_mutation(
        repo_path, "worktree", "add", "--branch", branch_name, worktree_path, parent_branch
    )
    return worktree_path


def merge_and_cleanup_worktree(
    repo_path: str,
    worktree_path: str,
    ticket_key: str,
    merge_into: str = "main",
) -> None:
    """Merge the ticket's feature branch and remove the worktree.

    - Switches to `merge_into` in the main repo.
    - Merges `feat/{ticket_key}` with --no-ff.
    - Removes the worktree (--force since the agent committed to the feature branch).
    - Deletes the feature branch.
    """
    branch_name = f"feat/{ticket_key}"

    run_mutation(repo_path, "checkout", merge_into)
    run_mutation(repo_path, "merge", "--no-ff", branch_name)
    run_mutation(repo_path, "worktree", "remove", "--force", worktree_path)
    run_mutation(repo_path, "branch", "-d", branch_name)


def cleanup_abandoned_worktree(repo_path: str, ticket_key: str) -> None:
    """Remove a worktree without merging (agent failed or was cancelled).

    Safe to call even if the worktree doesn't exist. The branch is NOT deleted.
    """
    try:
        safe_name = _sanitize_worktree_name(ticket_key)
        worktree_path = os.path.join(repo_path, ".worktrees", f"feat-{safe_name}")
        run_mutation(repo_path, "worktree", "remove", "--force", worktree_path)
    except GitError:
        pass


def _branch_exists(repo_path: str, branch_name: str) -> bool:
    """Check if a local branch exists."""
    result = _run(repo_path, "rev-parse", "--verify", f"--quiet", branch_name)
    return result.returncode == 0


def _create_branch_from(repo_path: str, new_branch: str, from_branch: str) -> None:
    """Create a new branch from an existing branch (without checking it out)."""
    run_mutation(repo_path, "branch", new_branch, from_branch)
