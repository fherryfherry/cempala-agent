"""GET /api/fs/browse — list subdirectories of a host path, for the onboarding wizard's
repo-path folder picker.

Not a security boundary (ADR-010): the backend already runs with the user's own
privileges and `--dir` is a working directory, not a sandbox, so browsing the host
filesystem read-only is no new exposure. Directories only (no file contents), dotfiles
hidden by default.
"""

import os

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.errors import AppError

router = APIRouter(prefix="/fs", tags=["fs"])


class FsEntry(BaseModel):
    name: str
    path: str


class FsBrowseOut(BaseModel):
    path: str
    parent: str | None
    dirs: list[FsEntry]


@router.get("/browse", response_model=FsBrowseOut)
async def browse(path: str | None = None):
    target = os.path.abspath(os.path.expanduser(path or "~"))
    if not os.path.isdir(target):
        raise AppError(404, "not_found", f"not a directory: {target}")

    try:
        names = os.listdir(target)
    except PermissionError:
        raise AppError(403, "permission_denied", f"cannot list directory: {target}")

    dirs = sorted(
        name
        for name in names
        if not name.startswith(".") and os.path.isdir(os.path.join(target, name))
    )
    parent = os.path.dirname(target)
    if parent == target:
        parent = None

    return FsBrowseOut(
        path=target,
        parent=parent,
        dirs=[FsEntry(name=name, path=os.path.join(target, name)) for name in dirs],
    )
