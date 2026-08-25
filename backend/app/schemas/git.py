from pydantic import BaseModel


class BranchOut(BaseModel):
    name: str
    is_current: bool
    latest_sha: str
    latest_subject: str


class GraphCommitOut(BaseModel):
    sha: str
    parents: list[str]
    subject: str
    author_name: str
    author_date: str
    lane: int
    total_lanes: int
    decorations: list[str]


class GraphOut(BaseModel):
    commits: list[GraphCommitOut]
    total_lanes: int


class CommitFileOut(BaseModel):
    path: str
    additions: int
    deletions: int
    status: str | None = None


class CommitDetailOut(BaseModel):
    sha: str
    subject: str
    author_name: str
    author_date: str
    body: str
    parents: list[str]
    is_merge: bool
    files: list[CommitFileOut]
    patch: str
    patch_truncated: bool


class CommitListOut(BaseModel):
    commits: list[GraphCommitOut]
    total_lanes: int
    has_more: bool
