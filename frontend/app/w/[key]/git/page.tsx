"use client";

import { useState, useCallback } from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { RefreshCwIcon } from "lucide-react";
import {
  listGitBranches,
  listGitCommits,
  getGitCommit,
  listWorkspaces,
  type GitBranch,
  type GitGraphCommit,
  type GitCommitDetail,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { formatShortTime } from "@/lib/datetime";
import { cn } from "@/lib/utils";

const LANE_W = 16;
const ROW_H = 28;
const DOT_R = 4;

const BADGE_COLORS: Record<string, string> = {
  branch: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
  tag: "bg-amber-100 text-amber-700 dark:bg-amber-900 dark:text-amber-300",
};

function GitGraphRow({
  commit,
  totalLanes,
  isSelected,
  onSelect,
}: {
  commit: GitGraphCommit;
  totalLanes: number;
  isSelected: boolean;
  onSelect: () => void;
}) {
  const x = commit.lane * LANE_W + LANE_W / 2;
  const width = Math.max(totalLanes, 1) * LANE_W;

  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "flex w-full cursor-pointer items-start gap-2 border-b border-black/5 px-2 py-1 text-left text-xs hover:bg-zinc-50 dark:border-white/5 dark:hover:bg-zinc-800/50",
        isSelected && "bg-blue-50 dark:bg-blue-950/30",
      )}
    >
      {/* SVG graph column */}
      <svg
        width={width}
        height={ROW_H}
        viewBox={`0 0 ${width} ${ROW_H}`}
        className="shrink-0 overflow-visible"
        aria-hidden
      >
        {/* Lane columns */}
        {Array.from({ length: totalLanes }).map((_, i) => (
          <line
            key={i}
            x1={i * LANE_W + LANE_W / 2}
            y1={0}
            x2={i * LANE_W + LANE_W / 2}
            y2={ROW_H}
            stroke="currentColor"
            className="text-zinc-200 dark:text-zinc-700"
            strokeWidth={1}
            strokeDasharray={i === 0 ? undefined : "2,2"}
          />
        ))}
        {/* Commit dot */}
        <circle
          cx={x}
          cy={ROW_H / 2}
          r={DOT_R}
          fill="currentColor"
          className={isSelected ? "text-blue-600 dark:text-blue-400" : "text-zinc-500"}
        />
        {/* Branch lines: from dot to first parent (vertical/angled) */}
        {commit.parents.length > 0 && (
          <line
            x1={x}
            y1={ROW_H / 2}
            x2={x}
            y2={ROW_H}
            stroke="currentColor"
            className="text-zinc-300 dark:text-zinc-600"
            strokeWidth={1.5}
          />
        )}
      </svg>

      {/* Commit info */}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="font-mono text-zinc-400">{commit.sha.slice(0, 7)}</span>
          <span className="truncate font-medium">{commit.subject}</span>
        </div>
        <div className="flex items-center gap-1.5 mt-0.5 text-zinc-400">
          <span>{commit.author_name}</span>
          <span>&middot;</span>
          <span>{formatShortTime(commit.author_date)}</span>
          {commit.decorations.map((d) => (
            <Badge
              key={d}
              variant="outline"
              className={cn(
                "text-[10px] px-1 py-0",
                d === "HEAD" ? "" : d.startsWith("tag:") ? BADGE_COLORS.tag : BADGE_COLORS.branch,
              )}
            >
              {d.startsWith("tag: ") ? d.slice(5) : d}
            </Badge>
          ))}
          {commit.parents.length > 1 && (
            <Badge variant="outline" className="text-[10px] px-1 py-0 text-zinc-400">
              merge
            </Badge>
          )}
        </div>
      </div>
    </button>
  );
}

function DiffLine({
  line,
  className,
}: {
  line: string;
  className?: string;
}) {
  if (line.startsWith("+") && !line.startsWith("+++")) {
    return <div className={cn("pl-4 text-green-600 dark:text-green-400", className)}>{line}</div>;
  }
  if (line.startsWith("-") && !line.startsWith("---")) {
    return <div className={cn("pl-4 text-red-600 dark:text-red-400", className)}>{line}</div>;
  }
  if (line.startsWith("@@")) {
    return <div className={cn("pl-4 text-blue-600 dark:text-blue-400 font-medium", className)}>{line}</div>;
  }
  if (line.startsWith("diff ") || line.startsWith("index ") || line.startsWith("---") || line.startsWith("+++")) {
    return null;
  }
  return <div className={cn("pl-4 text-zinc-600 dark:text-zinc-300", className)}>{line}</div>;
}

function CommitDetail({
  commit,
  workspaceId,
  workspaceTimezone,
}: {
  commit: GitGraphCommit;
  workspaceId: string;
  workspaceTimezone: string;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["git-commit", workspaceId, commit.sha],
    queryFn: () => getGitCommit(workspaceId, commit.sha),
    refetchInterval: false,
  });

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-sm text-zinc-400">
        Loading commit detail…
      </div>
    );
  }

  if (!data) return null;

  return (
    <div className="flex flex-col gap-4 overflow-y-auto">
      {/* Metadata */}
      <div className="rounded-lg border border-zinc-200 bg-zinc-50 p-3 dark:border-zinc-700 dark:bg-zinc-800/50">
        <h3 className="mb-2 break-all font-mono text-xs text-zinc-500">
          {data.sha}
        </h3>
        <p className="mb-1 font-medium">{data.subject}</p>
        <p className="mb-2 whitespace-pre-wrap text-xs text-zinc-500">{data.body}</p>
        <div className="flex items-center gap-2 text-xs text-zinc-400">
          <span>{data.author_name}</span>
          <span>&middot;</span>
          <span>{formatShortTime(data.author_date, workspaceTimezone)}</span>
          {data.is_merge && (
            <Badge variant="outline" className="text-[10px]">
              merge
            </Badge>
          )}
        </div>
        {data.parents.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1 text-xs text-zinc-400">
            <span>parents:</span>
            {data.parents.map((p) => (
              <span key={p} className="font-mono">
                {p.slice(0, 7)}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* File stats */}
      {data.files.length > 0 && (
        <div>
          <h4 className="mb-2 text-xs font-medium text-zinc-500 uppercase tracking-wide">
            Changed files ({data.files.length})
          </h4>
          <div className="rounded-lg border border-zinc-200 dark:border-zinc-700 overflow-hidden">
            {data.files.map((f) => (
              <div
                key={f.path}
                className="flex items-center justify-between gap-4 border-b border-black/5 px-3 py-1.5 text-xs last:border-b-0 dark:border-white/5"
              >
                <span className="truncate font-mono text-zinc-700 dark:text-zinc-300">
                  {f.path}
                </span>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-green-600 dark:text-green-400">+{f.additions}</span>
                  <span className="text-red-600 dark:text-red-400">-{f.deletions}</span>
                  {f.status && (
                    <Badge
                      variant="outline"
                      className={cn(
                        "text-[10px]",
                        f.status === "A" && "text-green-600",
                        f.status === "D" && "text-red-600",
                        f.status === "M" && "text-yellow-600",
                      )}
                    >
                      {f.status}
                    </Badge>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Diff */}
      {data.patch && (
        <div>
          <div className="mb-2 flex items-center justify-between">
            <h4 className="text-xs font-medium text-zinc-500 uppercase tracking-wide">
              Diff
            </h4>
            {data.patch_truncated && (
              <span className="text-xs text-amber-500">truncated (2 MB limit)</span>
            )}
          </div>
          <pre className="overflow-x-auto rounded-lg border border-zinc-200 bg-zinc-50 p-3 text-[11px] leading-relaxed dark:border-zinc-700 dark:bg-zinc-900">
            {data.patch.split("\n").map((line, i) => (
              <DiffLine key={i} line={line} />
            ))}
          </pre>
        </div>
      )}
    </div>
  );
}

export default function GitPage() {
  const params = useParams<{ key: string }>();
  const workspaceKey = params.key;

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });
  const workspace = workspaces.data?.find((ws) => ws.key === workspaceKey);

  const [selectedBranch, setSelectedBranch] = useState<string>("");
  const [selectedCommit, setSelectedCommit] = useState<GitGraphCommit | null>(null);
  const [commitOffset, setCommitOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const PAGE_SIZE = 100;

  const workspaceId = workspace?.id;
  const workspaceTimezone = workspace?.timezone ?? "Asia/Jakarta";

  const branches = useQuery({
    queryKey: ["git-branches", workspaceId],
    queryFn: () => listGitBranches(workspaceId!),
    enabled: !!workspaceId,
    refetchInterval: 30000,
  });

  const commits = useQuery({
    queryKey: ["git-commits", workspaceId, selectedBranch, commitOffset],
    queryFn: () =>
      listGitCommits(workspaceId!, {
        ref: selectedBranch || "HEAD",
        limit: PAGE_SIZE,
        offset: commitOffset,
      }),
    enabled: !!workspaceId,
    refetchInterval: 30000,
    placeholderData: (prev) => prev,
  });

  const handleRefresh = useCallback(() => {
    setSelectedCommit(null);
    setCommitOffset(0);
    setHasMore(false);
    branches.refetch();
    commits.refetch();
  }, [branches, commits]);

  const handleSelectCommit = useCallback((c: GitGraphCommit) => {
    setSelectedCommit(c);
    setCommitOffset(0);
  }, []);

  const handleLoadMore = useCallback(() => {
    const newOffset = commitOffset + PAGE_SIZE;
    setCommitOffset(newOffset);
    // append
    listGitCommits(workspaceId!, {
      ref: selectedBranch || "HEAD",
      limit: PAGE_SIZE,
      offset: newOffset,
    }).then((data) => {
      setHasMore(data.has_more);
    });
  }, [workspaceId, selectedBranch, commitOffset]);

  if (workspaces.isLoading) {
    return <p className="px-6 py-10 text-sm text-zinc-500">Loading workspace…</p>;
  }
  if (!workspace) {
    return (
      <p className="px-6 py-10 text-sm text-red-600">
        Workspace &quot;{workspaceKey}&quot; not found.
      </p>
    );
  }

  if (!workspaceId) {
    return (
      <p className="px-6 py-10 text-sm text-zinc-500">Workspace ID not available.</p>
    );
  }

  const currentCommits = commits.data?.commits ?? [];
  const totalLanes = commits.data?.total_lanes ?? 1;
  const stillHasMore = commits.data?.has_more ?? hasMore;

  return (
    <div className="flex h-full flex-1 flex-col overflow-hidden">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b border-black/8 px-6 py-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">Git</h1>
          <p className="mt-0.5 truncate font-mono text-xs text-zinc-400">
            {workspace.repo_path}
          </p>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleRefresh}
          disabled={branches.isFetching || commits.isFetching}
          className="gap-1.5"
        >
          <RefreshCwIcon
            className={cn(
              "size-3.5",
              (branches.isFetching || commits.isFetching) && "animate-spin",
            )}
          />
          Refresh
        </Button>
      </div>

      {/* Branch chips */}
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-b border-black/5 px-4 py-2">
        <button
          type="button"
          onClick={() => {
            setSelectedBranch("");
            setSelectedCommit(null);
            setCommitOffset(0);
            setHasMore(false);
          }}
          className={cn(
            "rounded-full border px-2 py-0.5 text-xs",
            !selectedBranch
              ? "border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-700 dark:bg-blue-900 dark:text-blue-300"
              : "border-zinc-200 text-zinc-500 hover:border-zinc-300 hover:text-zinc-700 dark:border-zinc-700 dark:text-zinc-400 dark:hover:border-zinc-600",
          )}
        >
          All
        </button>
        {branches.data?.map((b) => (
          <button
            key={b.name}
            type="button"
            onClick={() => {
              setSelectedBranch(b.name);
              setSelectedCommit(null);
              setCommitOffset(0);
              setHasMore(false);
            }}
            className={cn(
              "rounded-full border px-2 py-0.5 text-xs",
              selectedBranch === b.name
                ? "border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-700 dark:bg-blue-900 dark:text-blue-300"
                : "border-zinc-200 text-zinc-500 hover:border-zinc-300 hover:text-zinc-700 dark:border-zinc-700 dark:text-zinc-400 dark:hover:border-zinc-600",
            )}
          >
            {b.name}
            {b.is_current && <span className="ml-1 text-blue-500">*</span>}
          </button>
        ))}
        {branches.isLoading && (
          <span className="text-xs text-zinc-400">Loading branches…</span>
        )}
      </div>

      {/* Body: commit list + detail */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: commit list */}
        <div className="flex w-2/5 min-w-64 flex-col overflow-y-auto border-r border-black/5 dark:border-white/5">
          {commits.isLoading && <p className="p-4 text-sm text-zinc-400">Loading…</p>}
          {!commits.isLoading && currentCommits.length === 0 && (
            <p className="p-4 text-sm text-zinc-400">No commits found.</p>
          )}
          {currentCommits.map((c) => (
            <GitGraphRow
              key={c.sha}
              commit={c}
              totalLanes={totalLanes}
              isSelected={selectedCommit?.sha === c.sha}
              onSelect={() => handleSelectCommit(c)}
            />
          ))}
          {stillHasMore && (
            <button
              type="button"
              onClick={handleLoadMore}
              className="self-center my-3 rounded-md border border-zinc-200 px-4 py-1.5 text-xs text-zinc-500 hover:bg-zinc-50 dark:border-zinc-700 dark:hover:bg-zinc-800"
            >
              Load more ({PAGE_SIZE})
            </button>
          )}
        </div>

        {/* Right: commit detail */}
        <div className="flex-1 overflow-y-auto bg-white dark:bg-zinc-950">
          {selectedCommit ? (
            <div className="p-4">
              <CommitDetail
                commit={selectedCommit}
                workspaceId={workspaceId}
                workspaceTimezone={workspaceTimezone}
              />
            </div>
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-zinc-400">
              Click a commit to see details
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
