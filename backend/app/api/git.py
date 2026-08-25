import os

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.api.workspaces import _get_workspace_or_404
from app.core.git import (
    CommitDetail,
    GitError,
    GraphCommit,
    get_commit as _get_commit,
    get_graph as _get_graph,
    list_branches as _list_branches,
    list_commits as _list_commits,
)
from app.db.session import get_session
from app.schemas.git import (
    BranchOut,
    CommitDetailOut,
    CommitFileOut,
    CommitListOut,
    GraphCommitOut,
    GraphOut,
)

workspace_git_router = APIRouter(prefix="/workspaces/{workspace_id}/git", tags=["git"])


def _map_git_error(repo_path: str, exc: GitError) -> AppError:
    if exc.args[0] in ("not_a_repo", "object_not_found"):
        return AppError(404, exc.args[0], f"{repo_path}: {exc.stderr or exc.args[1]}")
    return AppError(400, "git_error", f"git command failed: {exc.stderr or exc.args[0]}")


def _to_commit_out(c: GraphCommit) -> GraphCommitOut:
    return GraphCommitOut(
        sha=c.sha,
        parents=c.parents,
        subject=c.subject,
        author_name=c.author_name,
        author_date=c.author_date,
        lane=c.lane,
        total_lanes=c.total_lanes,
        decorations=c.decorations,
    )


@workspace_git_router.get("/branches", response_model=list[BranchOut])
async def list_branches(
    workspace_id: str,
    session: AsyncSession = Depends(get_session),
):
    ws = await _get_workspace_or_404(session, workspace_id)
    repo_path = ws.repo_path
    if not os.path.isdir(repo_path):
        raise AppError(404, "repo_not_found", f"repo_path does not exist: {repo_path}")
    try:
        branches = _list_branches(repo_path)
    except GitError as e:
        raise _map_git_error(repo_path, e)
    return [
        BranchOut(
            name=b.name,
            is_current=b.is_current,
            latest_sha=b.latest_sha,
            latest_subject=b.latest_subject,
        )
        for b in branches
    ]


@workspace_git_router.get("/graph", response_model=GraphOut)
async def get_graph(
    workspace_id: str,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
):
    ws = await _get_workspace_or_404(session, workspace_id)
    repo_path = ws.repo_path
    if not os.path.isdir(repo_path):
        raise AppError(404, "repo_not_found", f"repo_path does not exist: {repo_path}")
    try:
        commits, total = _get_graph(repo_path, limit=limit)
    except GitError as e:
        raise _map_git_error(repo_path, e)
    return GraphOut(commits=[_to_commit_out(c) for c in commits], total_lanes=total)


@workspace_git_router.get("/commits", response_model=CommitListOut)
async def list_commits(
    workspace_id: str,
    ref: str = "HEAD",
    limit: int = 100,
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
):
    ws = await _get_workspace_or_404(session, workspace_id)
    repo_path = ws.repo_path
    if not os.path.isdir(repo_path):
        raise AppError(404, "repo_not_found", f"repo_path does not exist: {repo_path}")
    try:
        commits, total, has_more = _list_commits(repo_path, ref=ref, limit=limit, offset=offset)
    except GitError as e:
        raise _map_git_error(repo_path, e)
    return CommitListOut(
        commits=[_to_commit_out(c) for c in commits],
        total_lanes=total,
        has_more=has_more,
    )


@workspace_git_router.get("/commits/{sha}", response_model=CommitDetailOut)
async def get_commit(
    workspace_id: str,
    sha: str,
    session: AsyncSession = Depends(get_session),
):
    ws = await _get_workspace_or_404(session, workspace_id)
    repo_path = ws.repo_path
    if not os.path.isdir(repo_path):
        raise AppError(404, "repo_not_found", f"repo_path does not exist: {repo_path}")
    try:
        detail: CommitDetail = _get_commit(repo_path, sha)
    except GitError as e:
        raise _map_git_error(repo_path, e)
    return CommitDetailOut(
        sha=detail.sha,
        subject=detail.subject,
        author_name=detail.author_name,
        author_date=detail.author_date,
        body=detail.body,
        parents=detail.parents,
        is_merge=detail.is_merge,
        files=[
            CommitFileOut(
                path=f.path,
                additions=f.additions,
                deletions=f.deletions,
                status=f.status,
            )
            for f in detail.files
        ],
        patch=detail.patch,
        patch_truncated=detail.patch_truncated,
    )
