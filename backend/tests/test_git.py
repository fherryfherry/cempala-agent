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
