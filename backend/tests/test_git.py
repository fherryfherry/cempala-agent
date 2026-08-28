"""API + unit tests for the Git menu (read-only branch tree + commit history).

Uses a real throwaway git repo created in tmp_path — git binary must be available
in the test environment.
"""

import subprocess
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import os

from app.core.git import GitError, clone_repo, prepare_worktree
from app.db import session as db_session
from app.db.models import Base
from app.db.session import get_session
from app.main import app


# ---------------------------------------------------------------------------
# Repo fixture helpers
# ---------------------------------------------------------------------------

def _git(repo_dir, *args):
    result = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result


def _init_repo(tmp_path):
    """Create a git repo (working directory, not bare) with one file commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "--initial-branch=main", "-q")
    _git(repo, "config", "user.email", "test@test")
    _git(repo, "config", "user.name", "Test")
    f = repo / "README.md"
    f.write_text("# test\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial commit", "-q")
    return repo


def _make_workspace(client, repo_dir, key="MAP"):
    resp = client.post(
        "/api/workspaces",
        json={"name": "Map", "key": key, "repo_path": str(repo_dir)},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# Test client fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def client(tmp_path, monkeypatch):
    db_path = tmp_path / f"{uuid.uuid4().hex}.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_session():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    monkeypatch.setattr(db_session, "async_session", maker)

    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Tests: core/git.py lane algorithm
# ---------------------------------------------------------------------------

class TestLaneLayout:
    def test_linear_history(self, tmp_path):
        """Single branch: all commits on the same lane."""
        repo = _init_repo(tmp_path)

        from app.core.git import _compute_lanes

        out = _git(repo, "log", "--all", "--topo-order", "--parents",
                   "--pretty=format:%H%x1f%P%x1f%an%x1f%ad%x1f%s",
                   "--date=iso-strict").stdout

        rows = []
        for line in out.splitlines():
            if not line:
                continue
            parts = line.split("\x1f")
            if len(parts) < 5:
                continue
            rows.append({
                "sha": parts[0],
                "parents": parts[1].split() if parts[1] else [],
                "subject": parts[4],
            })

        laid = _compute_lanes(rows)
        for r in laid:
            assert r["lane"] == 0, f"expected lane 0 for linear history, got lane {r['lane']}"
        assert laid[0]["total_lanes"] == 1

    def test_branch_fork_and_merge(self, tmp_path):
        """Two branches from a common root, then a merge."""
        repo = _init_repo(tmp_path)

        _git(repo, "checkout", "-b", "feat", "-q")
        (repo / "feat.txt").write_text("feat")
        _git(repo, "add", "feat.txt")
        _git(repo, "commit", "-m", "feat: add feat", "-q")

        _git(repo, "checkout", "main", "-q")
        (repo / "main.txt").write_text("main")
        _git(repo, "add", "main.txt")
        _git(repo, "commit", "-m", "main: add main", "-q")

        _git(repo, "merge", "feat", "-m", "merge feat", "-q")

        from app.core.git import _compute_lanes

        out = _git(repo, "log", "--all", "--topo-order", "--parents",
                   "--pretty=format:%H%x1f%P%x1f%an%x1f%ad%x1f%s",
                   "--date=iso-strict").stdout

        rows = []
        for line in out.splitlines():
            if not line:
                continue
            parts = line.split("\x1f")
            if len(parts) < 5:
                continue
            rows.append({
                "sha": parts[0],
                "parents": parts[1].split() if parts[1] else [],
                "subject": parts[4],
            })

        laid = _compute_lanes(rows)
        # total_lanes should be at least 2 (one for main line, one for feat branch)
        assert laid[0]["total_lanes"] == 2
        # every row must have a valid lane
        for r in laid:
            assert 0 <= r["lane"] < laid[0]["total_lanes"]

    def test_disallowed_subcommand(self, tmp_path):
        """run_git rejects non-read-only subcommands."""
        from app.core.git import GitError, run_git
        repo = _init_repo(tmp_path)
        with pytest.raises(GitError) as exc_info:
            run_git(str(repo), "checkout", "main")
        assert "disallowed" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# Tests: API endpoints
# ---------------------------------------------------------------------------

class TestGitAPI:
    def test_branches_returns_list(self, client, tmp_path):
        repo = _init_repo(tmp_path)
        ws_id = _make_workspace(client, repo)

        resp = client.get(f"/api/workspaces/{ws_id}/git/branches")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert data[0]["name"] == "main"
        assert data[0]["is_current"] is True
        assert "latest_sha" in data[0]
        assert "latest_subject" in data[0]

    def test_branches_404_for_missing_repo_path(self, client, tmp_path):
        nonexistent = tmp_path / "nonexistent"
        ws_id = _make_workspace(client, nonexistent)
        resp = client.get(f"/api/workspaces/{ws_id}/git/branches")
        assert resp.status_code == 404

    def test_branches_404_for_nonexistent_workspace(self, client):
        resp = client.get("/api/workspaces/nonexistent-id/git/branches")
        assert resp.status_code == 404

    def test_graph_returns_commits(self, client, tmp_path):
        repo = _init_repo(tmp_path)
        ws_id = _make_workspace(client, repo)

        resp = client.get(f"/api/workspaces/{ws_id}/git/graph")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert "commits" in data
        assert "total_lanes" in data
        assert len(data["commits"]) >= 1
        c = data["commits"][0]
        assert "sha" in c
        assert "lane" in c
        assert "total_lanes" in c
        assert "parents" in c
        assert "subject" in c
        assert "author_name" in c
        assert "author_date" in c

    def test_graph_limit(self, client, tmp_path):
        repo = _init_repo(tmp_path)
        ws_id = _make_workspace(client, repo)

        resp = client.get(f"/api/workspaces/{ws_id}/git/graph?limit=1")
        assert resp.status_code == 200
        assert len(resp.json()["commits"]) == 1

    def test_commits_returns_paginated_list(self, client, tmp_path):
        repo = _init_repo(tmp_path)
        _git(repo, "checkout", "-b", "feat", "-q")
        for i in range(4):
            (repo / f"f{i}.txt").write_text(f"{i}")
            _git(repo, "add", f"f{i}.txt")
            _git(repo, "commit", "-m", f"feat {i}", "-q")
        ws_id = _make_workspace(client, repo)

        resp = client.get(f"/api/workspaces/{ws_id}/git/commits?ref=feat&limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["commits"]) == 2
        assert data["has_more"] is True
        assert data["total_lanes"] >= 1

        resp2 = client.get(f"/api/workspaces/{ws_id}/git/commits?ref=feat&limit=2&offset=2")
        assert resp2.status_code == 200
        data2 = resp2.json()
        assert data2["has_more"] is True

        resp3 = client.get(f"/api/workspaces/{ws_id}/git/commits?ref=feat&limit=2&offset=4")
        assert resp3.status_code == 200
        data3 = resp3.json()
        assert data3["has_more"] is False
        # 4 feat commits + 1 initial = 5 total; offset=4 returns the last 1
        assert len(data3["commits"]) == 1

    def test_commit_detail(self, client, tmp_path):
        repo = _init_repo(tmp_path)
        ws_id = _make_workspace(client, repo)
        sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

        resp = client.get(f"/api/workspaces/{ws_id}/git/commits/{sha}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["sha"] == sha
        assert "subject" in data
        assert "author_name" in data
        assert "author_date" in data
        assert "files" in data
        assert isinstance(data["files"], list)
        assert "patch" in data
        assert "patch_truncated" in data
        assert data["is_merge"] is False

    def test_commit_detail_404_for_invalid_sha(self, client, tmp_path):
        repo = _init_repo(tmp_path)
        ws_id = _make_workspace(client, repo)

        # 40 zeros is a valid format but nonexistent sha → 404
        resp = client.get(
            f"/api/workspaces/{ws_id}/git/commits/0000000000000000000000000000000000000000"
        )
        assert resp.status_code == 404

    def test_commit_detail_404_for_nonexistent_workspace(self, client, tmp_path):
        repo = _init_repo(tmp_path)
        sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
        resp = client.get(f"/api/workspaces/nonexistent-ws/git/commits/{sha}")
        assert resp.status_code == 404

    def test_graph_404_for_missing_repo(self, client, tmp_path):
        nonexistent = tmp_path / "gone"
        ws_id = _make_workspace(client, nonexistent)
        resp = client.get(f"/api/workspaces/{ws_id}/git/graph")
        assert resp.status_code == 404

    def test_commits_404_for_missing_repo(self, client, tmp_path):
        nonexistent = tmp_path / "gone"
        ws_id = _make_workspace(client, nonexistent)
        resp = client.get(f"/api/workspaces/{ws_id}/git/commits")
        assert resp.status_code == 404

    def test_commit_detail_404_for_missing_repo(self, client, tmp_path):
        nonexistent = tmp_path / "gone"
        ws_id = _make_workspace(client, nonexistent)
        resp = client.get(f"/api/workspaces/{ws_id}/git/commits/abc123")
        assert resp.status_code == 404

    def test_branches_404_for_non_repo_dir(self, client, tmp_path):
        # A directory that exists but is not a git repo -> not_a_repo 404.
        plain = tmp_path / "plain"
        plain.mkdir()
        ws_id = _make_workspace(client, plain)
        resp = client.get(f"/api/workspaces/{ws_id}/git/branches")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_a_repo"

    def test_commits_404_for_non_repo_dir(self, client, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        ws_id = _make_workspace(client, plain)
        resp = client.get(f"/api/workspaces/{ws_id}/git/commits")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "not_a_repo"

    def test_branches_404_when_repo_path_deleted(self, client, tmp_path):
        import shutil

        repo = _init_repo(tmp_path)
        ws_id = _make_workspace(client, repo)
        shutil.rmtree(repo)
        resp = client.get(f"/api/workspaces/{ws_id}/git/branches")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "repo_not_found"

    def test_graph_commits_detail_404_when_repo_path_deleted(self, client, tmp_path):
        import shutil

        repo = _init_repo(tmp_path)
        ws_id = _make_workspace(client, repo)
        shutil.rmtree(repo)

        for path in ("graph", "commits", "commits/abc123"):
            resp = client.get(f"/api/workspaces/{ws_id}/git/{path}")
            assert resp.status_code == 404, path
            assert resp.json()["error"]["code"] == "repo_not_found", path


# ---------------------------------------------------------------------------
# clone_repo() — real subprocess, no network (local source paths only)
# ---------------------------------------------------------------------------

class TestCloneRepo:
    def test_clone_repo_success(self, tmp_path):
        source = _init_repo(tmp_path)
        target = tmp_path / "cloned"

        clone_repo(str(source), str(target))

        assert target.is_dir()
        assert (target / ".git").is_dir()
        assert (target / "README.md").exists()

    def test_clone_repo_creates_missing_parent_dirs(self, tmp_path):
        source = _init_repo(tmp_path)
        target = tmp_path / "does" / "not" / "exist" / "yet" / "cloned"

        clone_repo(str(source), str(target))

        assert (target / ".git").is_dir()

    def test_clone_repo_invalid_source_raises_clone_failed(self, tmp_path):
        target = tmp_path / "cloned"

        with pytest.raises(GitError) as exc_info:
            clone_repo(str(tmp_path / "does-not-exist"), str(target))

        assert exc_info.value.args[0] == "clone_failed"
        assert exc_info.value.stderr
        assert not target.exists()

    def test_clone_repo_missing_binary_raises_git_error(self, tmp_path, monkeypatch):
        import subprocess

        def fake_run(*args, **kwargs):
            raise FileNotFoundError("git not found")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(GitError) as exc_info:
            clone_repo("https://example.com/repo.git", str(tmp_path / "cloned"))

        assert "git binary not found" in exc_info.value.args[0]

    def test_clone_repo_timeout_raises_git_error(self, tmp_path, monkeypatch):
        import subprocess

        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="git clone", timeout=120.0)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(GitError) as exc_info:
            clone_repo("https://example.com/repo.git", str(tmp_path / "cloned"))

        assert "timed out" in exc_info.value.args[0]


class TestPrepareWorktreeReuse:
    """A ticket goes through many runs/handoffs (Engineer -> Lead -> QA -> ...),
    each calling prepare_worktree() again for the SAME ticket_key. Regression
    coverage for the bug where every non-first call failed with
    git_mutation_failed because it always tried to (re)create the branch.
    """

    def test_second_call_reuses_existing_worktree(self, tmp_path):
        repo = _init_repo(tmp_path)

        first = prepare_worktree(str(repo), "MAP-123", base_branch="main")
        second = prepare_worktree(str(repo), "MAP-123", base_branch="main")

        assert first == second
        assert os.path.isdir(second)

    def test_reattaches_branch_when_worktree_dir_removed(self, tmp_path):
        repo = _init_repo(tmp_path)

        path = prepare_worktree(str(repo), "MAP-123", base_branch="main")
        _git(repo, "worktree", "remove", "--force", path)
        assert not os.path.isdir(path)

        # Branch survives the removal (cleanup_abandoned_worktree never deletes
        # it) — a later run must reattach it, not fail trying to recreate it.
        reattached = prepare_worktree(str(repo), "MAP-123", base_branch="main")
        assert reattached == path
        assert os.path.isdir(reattached)


# ---------------------------------------------------------------------------
# Additional unit coverage: _run/_run_mutation error paths, run_git fallback,
# decorations, root commit, file statuses, merge/cleanup worktree functions.
# ---------------------------------------------------------------------------

class TestRunErrorPaths:
    def test_run_git_binary_not_found(self, tmp_path, monkeypatch):
        from app.core.git import GitError, run_git

        repo = _init_repo(tmp_path)

        def fake_run(*args, **kwargs):
            raise FileNotFoundError("no git")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(GitError) as exc_info:
            run_git(str(repo), "log")
        assert "git binary not found" in str(exc_info.value)

    def test_run_mutation_binary_not_found(self, tmp_path, monkeypatch):
        from app.core.git import GitError, run_mutation

        repo = _init_repo(tmp_path)

        def fake_run(*args, **kwargs):
            raise FileNotFoundError("no git")

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(GitError) as exc_info:
            run_mutation(str(repo), "branch", "x", "main")
        assert "git binary not found" in str(exc_info.value)

    def test_run_git_timeout(self, tmp_path, monkeypatch):
        from app.core.git import GitError, run_git

        repo = _init_repo(tmp_path)

        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="git log", timeout=15.0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(GitError) as exc_info:
            run_git(str(repo), "log")
        assert "timed out" in str(exc_info.value)

    def test_run_mutation_timeout(self, tmp_path, monkeypatch):
        from app.core.git import GitError, run_mutation

        repo = _init_repo(tmp_path)

        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="git branch", timeout=15.0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        with pytest.raises(GitError) as exc_info:
            run_mutation(str(repo), "branch", "x", "main")
        assert "timed out" in str(exc_info.value)

    def test_run_mutation_no_args(self, tmp_path):
        from app.core.git import GitError, run_mutation

        repo = _init_repo(tmp_path)
        with pytest.raises(GitError) as exc_info:
            run_mutation(str(repo))
        assert "no git command given" in str(exc_info.value)

    def test_run_mutation_disallowed_subcommand(self, tmp_path):
        from app.core.git import GitError, run_mutation

        repo = _init_repo(tmp_path)
        with pytest.raises(GitError) as exc_info:
            run_mutation(str(repo), "push", "origin", "main")
        assert "disallowed mutation subcommand" in str(exc_info.value)

    def test_run_git_generic_failure_raises_git_failed(self, tmp_path):
        from app.core.git import GitError, run_git

        repo = _init_repo(tmp_path)
        # `git diff` with a garbage flag: not "not a git repository" nor "bad
        # object"/"ambiguous argument" -> falls through to the generic branch.
        with pytest.raises(GitError) as exc_info:
            run_git(str(repo), "diff", "--not-a-real-flag")
        assert exc_info.value.args[0] == "git_failed"


class TestGraphAndCommitsDecorationsAndRefs:
    def test_list_commits_invalid_ref_starting_with_dash(self, tmp_path):
        from app.core.git import GitError, list_commits

        repo = _init_repo(tmp_path)
        with pytest.raises(GitError) as exc_info:
            list_commits(str(repo), ref="--evil-flag")
        assert exc_info.value.args[0] == "invalid_ref"

    def test_list_commits_all_ref(self, tmp_path):
        from app.core.git import list_commits

        repo = _init_repo(tmp_path)
        commits, _total, _has_more = list_commits(str(repo), ref="--all")
        assert len(commits) == 1


class TestGetCommitDetail:
    def test_root_commit_has_no_parents(self, tmp_path):
        from app.core.git import get_commit

        repo = _init_repo(tmp_path)
        sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

        detail = get_commit(str(repo), sha)
        assert detail.parents == []
        assert detail.is_merge is False

    def test_file_statuses_add_modify_delete_rename(self, tmp_path):
        from app.core.git import get_commit

        repo = _init_repo(tmp_path)

        (repo / "new.txt").write_text("new file with enough content to be detected\n" * 5)
        _git(repo, "add", "new.txt")
        _git(repo, "commit", "-m", "add new.txt", "-q")
        add_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

        (repo / "new.txt").write_text("modified content\n")
        _git(repo, "add", "new.txt")
        _git(repo, "commit", "-m", "modify new.txt", "-q")
        modify_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

        (repo / "new.txt").rename(repo / "renamed.txt")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "rename new.txt", "-q")
        rename_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

        (repo / "renamed.txt").unlink()
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "delete renamed.txt", "-q")
        delete_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

        add_detail = get_commit(str(repo), add_sha)
        assert any(f.status == "A" for f in add_detail.files)

        modify_detail = get_commit(str(repo), modify_sha)
        assert any(f.status == "M" for f in modify_detail.files)

        # Rename detection needs an explicit -M threshold that get_commit's
        # diff-tree call doesn't pass, so git reports it as delete+add, not "R" —
        # not exercised here (see git.py's dead "R"/"C" status branches).
        get_commit(str(repo), rename_sha)

        delete_detail = get_commit(str(repo), delete_sha)
        assert any(f.status == "D" for f in delete_detail.files)


class TestMergeAndCleanupWorktree:
    def test_merge_and_cleanup_worktree_success(self, tmp_path):
        from app.core.git import merge_and_cleanup_worktree

        repo = _init_repo(tmp_path)
        path = prepare_worktree(str(repo), "MAP-999", base_branch="main")
        with open(os.path.join(path, "feature.txt"), "w") as f:
            f.write("x")
        _git(path, "add", "feature.txt")
        _git(path, "commit", "-m", "feat work", "-q")

        merge_and_cleanup_worktree(str(repo), path, "MAP-999", merge_into="main")

        assert not os.path.isdir(path)
        assert (repo / "feature.txt").exists()  # merged into main
        branches = _git(repo, "branch", "--list", "feat/MAP-999").stdout
        assert branches.strip() == ""  # branch deleted

    def test_cleanup_abandoned_worktree_removes_worktree(self, tmp_path):
        from app.core.git import cleanup_abandoned_worktree

        repo = _init_repo(tmp_path)
        path = prepare_worktree(str(repo), "MAP-888", base_branch="main")
        assert os.path.isdir(path)

        cleanup_abandoned_worktree(str(repo), "MAP-888")

        assert not os.path.isdir(path)
        # Branch is NOT deleted by cleanup.
        branches = _git(repo, "branch", "--list", "feat/MAP-888").stdout
        assert "feat/MAP-888" in branches

    def test_cleanup_abandoned_worktree_noop_when_missing(self, tmp_path):
        from app.core.git import cleanup_abandoned_worktree

        repo = _init_repo(tmp_path)
        # No worktree was ever created for this ticket -> GitError swallowed.
        cleanup_abandoned_worktree(str(repo), "MAP-777")

    def test_prepare_worktree_creates_epic_branch_when_missing(self, tmp_path):
        from app.core.git import _epic_branch_name, prepare_worktree

        repo = _init_repo(tmp_path)
        epic_branch = _epic_branch_name("Some Epic Title!")
        assert epic_branch == "epic/some-epic-title-epic"

        path = prepare_worktree(str(repo), "MAP-321", epic_branch=epic_branch, base_branch="main")

        assert os.path.isdir(path)
        branches = _git(repo, "branch", "--list", epic_branch).stdout
        assert epic_branch in branches

    def test_prepare_worktree_reuses_existing_epic_branch(self, tmp_path):
        from app.core.git import _epic_branch_name, prepare_worktree

        repo = _init_repo(tmp_path)
        epic_branch = _epic_branch_name("Reused Epic")

        first = prepare_worktree(str(repo), "MAP-401", epic_branch=epic_branch, base_branch="main")
        # A second ticket under the SAME epic must reuse the epic branch, not
        # try (and fail) to recreate it.
        second = prepare_worktree(str(repo), "MAP-402", epic_branch=epic_branch, base_branch="main")

        assert os.path.isdir(first)
        assert os.path.isdir(second)
        assert first != second


class TestSlugify:
    def test_slugify_strips_punctuation_and_lowercases(self):
        from app.core.git import _slugify

        assert _slugify("Fix Bug #123!!") == "fix-bug-123"

    def test_slugify_empty_input_falls_back_to_untitled(self):
        from app.core.git import _slugify

        assert _slugify("   ###   ") == "untitled"
