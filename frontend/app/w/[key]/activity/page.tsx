"use client";

import { useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  formatAgentName,
  getRun,
  listAgents,
  listRuns,
  listTickets,
  listWorkspaces,
  retryRun,
  stopRun,
  type Agent,
  type Run,
  type RunEvent,
} from "@/lib/api";
import { useWorkspaceEvents, type EventType, type WorkspaceEvent } from "@/components/events-context";
import { AgentAvatar } from "@/components/agent-avatar";
import { formatTimestamp } from "@/lib/datetime";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const EVENT_TYPES: EventType[] = [
  "run_started",
  "assistant_text",
  "reasoning",
  "tool_call",
  "tool_result",
  "status_change",
  "comment",
  "handoff",
  "error",
  "run_ended",
];

const RUN_STATUS_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  queued: "outline",
  running: "default",
  done: "secondary",
  failed: "destructive",
  cancelled: "outline",
  interrupted: "destructive",
};

const EVENT_PAGE_LIMIT = 500;

export default function ActivityPage() {
  const params = useParams<{ key: string }>();
  const workspaceKey = params.key;
  const queryClient = useQueryClient();

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });
  const workspace = workspaces.data?.find((ws) => ws.key === workspaceKey);

  const runs = useQuery({
    queryKey: ["runs", workspace?.id],
    queryFn: () => listRuns(workspace!.id),
    enabled: !!workspace,
  });

  const agents = useQuery({
    queryKey: ["agents", workspace?.id],
    queryFn: () => listAgents(workspace!.id),
    enabled: !!workspace,
  });

  const tickets = useQuery({
    queryKey: ["tickets", workspace?.id],
    queryFn: () => listTickets(workspace!.id),
    enabled: !!workspace,
  });

  const { status: sseStatus, events } = useWorkspaceEvents();
  const [typeFilter, setTypeFilter] = useState<EventType | "all">("all");
  const [agentFilter, setAgentFilter] = useState<string>("all");
  // null -> auto-select the latest run (so the page opens on the newest activity).
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  // Infinite-scroll pagination: 20 visible at a time, more revealed on scroll.
  const PAGE_SIZE = 20;
  const [runLimit, setRunLimit] = useState(PAGE_SIZE);
  const [feedLimit, setFeedLimit] = useState(PAGE_SIZE);

  const filteredEvents = useMemo(
    () => (typeFilter === "all" ? events : events.filter((e) => e.type === typeFilter)),
    [events, typeFilter],
  );

  function handleRunsScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 8) {
      setRunLimit((n) => Math.min(n + PAGE_SIZE, agentFilteredRuns.length));
    }
  }

  function handleFeedScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - 8) {
      setFeedLimit((n) => Math.min(n + PAGE_SIZE, filteredEvents.length));
    }
  }

  const agentName = (id: string) => {
    const a = agents.data?.find((x) => x.id === id);
    return a ? formatAgentName(a.name, a.role) : id;
  };
  const agentOf = (id: string | null) => agents.data?.find((x) => x.id === id);
  const ticketKey = (id: string | null) =>
    id ? tickets.data?.find((t) => t.id === id)?.key ?? id : "rutinitas";

  const retryFromList = useMutation({
    mutationFn: (oldRunId: string) => retryRun(oldRunId),
    onSuccess: (newRun) => {
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      queryClient.invalidateQueries({ queryKey: ["tickets", workspace?.id] });
      setSelectedRunId(newRun.id);
      toast.success("Retrying — new run started");
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Unexpected error");
    },
  });

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

  const sortedRuns = [...(runs.data ?? [])].sort((a, b) =>
    (b.started_at ?? "").localeCompare(a.started_at ?? ""),
  );
  const agentFilteredRuns =
    agentFilter === "all" ? sortedRuns : sortedRuns.filter((r) => r.agent_id === agentFilter);

  // Auto-open the latest run on page load (unless the user already picked one).
  const effectiveSelectedRunId = selectedRunId ?? agentFilteredRuns[0]?.id ?? null;

  // Reset the pagination window when the filter changes, so the top of the list
  // (the newest runs) is always visible first.
  if (runLimit !== PAGE_SIZE && agentFilteredRuns.length < runLimit) {
    setRunLimit(PAGE_SIZE);
  }

  return (
    <div className="flex w-full flex-1 flex-col gap-6 px-6 py-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Activity</h1>
          <p className="mt-1 text-sm text-zinc-500">
            SSE: <span className={sseStatus === "open" ? "text-green-600" : "text-zinc-400"}>{sseStatus}</span>
          </p>
        </div>
      </div>

      <div className="grid flex-1 grid-cols-1 gap-6 lg:grid-cols-[320px_1fr]">
        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-2">
              <CardTitle className="text-sm">Runs</CardTitle>
              <Select value={agentFilter} onValueChange={(v) => setAgentFilter(v ?? "all")}>
                <SelectTrigger size="sm">
                  <SelectValue placeholder="All agents" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All agents</SelectItem>
                  {(agents.data ?? []).map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {formatAgentName(a.name, a.role)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </CardHeader>
            <CardContent
              className="flex max-h-80 flex-col gap-1 overflow-y-auto p-0"
              onScroll={handleRunsScroll}
            >
              {runs.isLoading && <p className="px-3 py-2 text-xs text-zinc-500">Loading…</p>}
              {agentFilteredRuns.length === 0 && (
                <p className="px-3 py-2 text-xs text-zinc-500">No runs yet.</p>
              )}
              {agentFilteredRuns.slice(0, runLimit).map((r) => {
                const retryable = r.status === "failed" || r.status === "interrupted";
                const retrying = retryFromList.isPending && retryFromList.variables === r.id;
                return (
                  <div
                    key={r.id}
                    role="button"
                    tabIndex={0}
                    onClick={() => setSelectedRunId(r.id)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") setSelectedRunId(r.id);
                    }}
                    className={`flex flex-col gap-1 border-b border-black/5 px-3 py-2 text-left text-xs last:border-b-0 hover:bg-zinc-50 dark:border-white/5 dark:hover:bg-zinc-900/40 ${
                      effectiveSelectedRunId === r.id ? "bg-zinc-100 dark:bg-zinc-900/60" : ""
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono text-zinc-500">{ticketKey(r.ticket_id)}</span>
                      <div className="flex items-center gap-1">
                        <Badge variant={RUN_STATUS_VARIANT[r.status] ?? "outline"}>{r.status}</Badge>
                        {retryable && (
                          <Button
                            variant="ghost"
                            size="xs"
                            className="h-5 px-1.5 text-[10px]"
                            disabled={retrying}
                            onClick={(e) => {
                              e.stopPropagation();
                              retryFromList.mutate(r.id);
                            }}
                          >
                            {retrying ? "Retrying…" : "Retry"}
                          </Button>
                        )}
                      </div>
                    </div>
                    <span className="flex items-center gap-1.5 text-zinc-500">
                      {agentOf(r.agent_id) && (
                        <AgentAvatar
                          name={agentOf(r.agent_id)!.name}
                          template={agentOf(r.agent_id)!.avatar_template}
                          color={agentOf(r.agent_id)!.avatar_color}
                          size={16}
                        />
                      )}
                      {agentName(r.agent_id)}
                    </span>
                    <span className="text-[10px] text-zinc-400">
                      {r.started_at ? formatTimestamp(r.started_at, workspace.timezone) : "—"}
                    </span>
                  </div>
                );
              })}
              {agentFilteredRuns.length > runLimit && (
                <p className="px-3 py-1.5 text-center text-[10px] text-zinc-400">
                  Scroll untuk lebih banyak…
                </p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between gap-2">
              <CardTitle className="text-sm">Live feed</CardTitle>
              <Select value={typeFilter} onValueChange={(v) => setTypeFilter((v as EventType | "all") ?? "all")}>
                <SelectTrigger size="sm">
                  <SelectValue placeholder="All types" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All types</SelectItem>
                  {EVENT_TYPES.map((t) => (
                    <SelectItem key={t} value={t}>
                      {t}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </CardHeader>
            <CardContent
              className="flex max-h-96 flex-col-reverse gap-1 overflow-y-auto p-0"
              onScroll={handleFeedScroll}
            >
              {filteredEvents.length === 0 && (
                <p className="px-3 py-2 text-xs text-zinc-500">No events yet.</p>
              )}
              {[...filteredEvents].reverse().slice(0, feedLimit).map((ev) => (
                <LiveEventRow
                  key={ev.id}
                  event={ev}
                  onClick={() => ev.run_id && setSelectedRunId(ev.run_id)}
                />
              ))}
              {filteredEvents.length > feedLimit && (
                <p className="px-3 py-1.5 text-center text-[10px] text-zinc-400">
                  Scroll untuk lebih banyak…
                </p>
              )}
            </CardContent>
          </Card>
        </div>

        <div>
          {effectiveSelectedRunId ? (
            <RunDetailPanel
              runId={effectiveSelectedRunId}
              agentName={agentName}
              agentOf={agentOf}
              ticketKey={ticketKey}
              onRetried={(newRunId) => setSelectedRunId(newRunId)}
            />
          ) : (
            <Card className="flex h-full items-center justify-center">
              <p className="text-sm text-zinc-500">Select a run to see its detail.</p>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

function LiveEventRow({ event, onClick }: { event: WorkspaceEvent; onClick: () => void }) {
  const summary = summarizeEvent(event);
  return (
    <button
      onClick={onClick}
      className="flex items-start gap-2 border-b border-black/5 px-3 py-1.5 text-left text-xs last:border-b-0 hover:bg-zinc-50 dark:border-white/5 dark:hover:bg-zinc-900/40"
    >
      <Badge variant="outline" className="shrink-0">
        {event.type}
      </Badge>
      <span className="truncate text-zinc-500">{summary}</span>
    </button>
  );
}

function summarizeEvent(event: WorkspaceEvent): string {
  const p = event.payload as Record<string, unknown>;
  switch (event.type) {
    case "assistant_text":
      return typeof p.text === "string" ? p.text.slice(0, 140) : "";
    case "run_started":
      return typeof p.prompt === "string" ? p.prompt.slice(0, 140) : "run started";
    case "run_ended":
      return `status: ${p.status ?? "?"}${p.error ? ` — ${p.error}` : ""}`;
    case "tool_call":
      return typeof p.tool === "string" ? `tool: ${p.tool}` : JSON.stringify(p).slice(0, 140);
    case "error":
      return typeof p.error === "string" ? p.error.slice(0, 140) : JSON.stringify(p).slice(0, 140);
    default:
      return JSON.stringify(p).slice(0, 140);
  }
}

function RunDetailPanel({
  runId,
  agentName,
  agentOf,
  ticketKey,
  onRetried,
}: {
  runId: string;
  agentName: (id: string) => string;
  agentOf: (id: string) => Agent | undefined;
  ticketKey: (id: string | null) => string;
  onRetried?: (newRunId: string) => void;
}) {
  const queryClient = useQueryClient();
  const [extraEvents, setExtraEvents] = useState<RunEvent[]>([]);
  const [loadedRunId, setLoadedRunId] = useState(runId);
  const [loadingMore, setLoadingMore] = useState(false);
  const [lastPageFull, setLastPageFull] = useState(false);

  if (loadedRunId !== runId) {
    // run switched — drop the "load more" state accumulated for the previous run.
    setLoadedRunId(runId);
    setExtraEvents([]);
    setLastPageFull(false);
  }

  const query = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getRun(runId, { limit: EVENT_PAGE_LIMIT }),
  });

  const stopMutation = useMutation({
    mutationFn: () => stopRun(runId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["run", runId] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Unexpected error");
    },
  });

  const retryMutation = useMutation({
    mutationFn: () => retryRun(runId),
    onSuccess: (newRun) => {
      queryClient.invalidateQueries({ queryKey: ["run", runId] });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      toast.success("Retrying — new run started");
      onRetried?.(newRun.id);
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Unexpected error");
    },
  });

  if (query.isLoading) {
    return (
      <Card className="p-6">
        <p className="text-sm text-zinc-500">Loading run…</p>
      </Card>
    );
  }
  if (!query.data) {
    return (
      <Card className="p-6">
        <p className="text-sm text-red-600">Could not load run.</p>
      </Card>
    );
  }

  const run: Run = query.data;
  const events = [...query.data.events, ...extraEvents];
  const transcript = events
    .filter((e) => e.type === "assistant_text")
    .map((e) => e.payload.text)
    .filter((t): t is string => typeof t === "string")
    .join("\n\n");

  async function loadMore() {
    setLoadingMore(true);
    try {
      const next = await getRun(runId, { offset: events.length, limit: EVENT_PAGE_LIMIT });
      setExtraEvents((prev) => [...prev, ...next.events]);
      setLastPageFull(next.events.length === EVENT_PAGE_LIMIT);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Failed to load more events");
    } finally {
      setLoadingMore(false);
    }
  }

  const canLoadMore = events.length > 0 && (events.length === EVENT_PAGE_LIMIT || lastPageFull);
  const canStop = run.status === "running" || run.status === "queued";
  const canRetry = run.status === "failed" || run.status === "interrupted";

  return (
    <Card className="flex h-full flex-col gap-4">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle className="flex items-center gap-2">
            <span className="font-mono text-sm">{ticketKey(run.ticket_id)}</span>
            <Badge variant={RUN_STATUS_VARIANT[run.status] ?? "outline"}>{run.status}</Badge>
          </CardTitle>
          <p className="mt-1 flex items-center gap-1.5 text-xs text-zinc-500">
            {agentOf(run.agent_id) && (
              <AgentAvatar
                name={agentOf(run.agent_id)!.name}
                template={agentOf(run.agent_id)!.avatar_template}
                color={agentOf(run.agent_id)!.avatar_color}
                size={16}
              />
            )}
            {agentName(run.agent_id)} · {run.model} · {run.tool_kind}
          </p>
        </div>
        {canStop && (
          <Button
            variant="destructive"
            size="sm"
            disabled={stopMutation.isPending}
            onClick={() => stopMutation.mutate()}
          >
            {stopMutation.isPending ? "Stopping…" : "Stop"}
          </Button>
        )}
        {canRetry && (
          <Button
            variant="outline"
            size="sm"
            disabled={retryMutation.isPending}
            onClick={() => retryMutation.mutate()}
          >
            {retryMutation.isPending ? "Retrying…" : "Retry"}
          </Button>
        )}
      </CardHeader>

      <CardContent className="flex flex-1 flex-col gap-4 overflow-y-auto">
        {run.error && (
          <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-300">
            <p className="font-semibold">Run failed / blocked</p>
            <p className="mt-1 whitespace-pre-wrap">{run.error}</p>
          </div>
        )}

        <div className="grid grid-cols-2 gap-2 text-xs text-zinc-500 sm:grid-cols-4">
          <div>Cost: {run.cost.toFixed(4)}</div>
          <div>Tokens in: {run.tokens_in}</div>
          <div>Tokens out: {run.tokens_out}</div>
          <div>Trigger: {run.trigger}</div>
        </div>

        {run.report && (
          <div className="rounded-md border border-black/10 px-3 py-2 text-sm dark:border-white/10">
            <p className="font-semibold">map block</p>
            <p className="mt-1 text-xs text-zinc-500">status: {run.report.status ?? "—"}</p>
            {run.report.mention && run.report.mention.length > 0 && (
              <p className="text-xs text-zinc-500">mention: {run.report.mention.join(", ")}</p>
            )}
            {run.report.summary && <p className="mt-1 whitespace-pre-wrap">{run.report.summary}</p>}
          </div>
        )}

        {transcript && (
          <div>
            <p className="mb-1 text-xs font-semibold text-zinc-500">Transcript</p>
            <div className="whitespace-pre-wrap rounded-md bg-zinc-50 p-3 text-sm dark:bg-zinc-900/40">
              {transcript}
            </div>
          </div>
        )}

        <div>
          <p className="mb-1 text-xs font-semibold text-zinc-500">Events ({events.length})</p>
          <div className="flex flex-col gap-1">
            {events.map((ev) => (
              <div
                key={ev.id}
                className="flex items-start gap-2 border-b border-black/5 py-1 text-xs last:border-b-0 dark:border-white/5"
              >
                <Badge variant="outline" className="shrink-0">
                  {ev.type}
                </Badge>
                <span className="truncate text-zinc-500">{JSON.stringify(ev.payload).slice(0, 200)}</span>
              </div>
            ))}
          </div>
          {canLoadMore && (
            <Button variant="outline" size="sm" className="mt-2" disabled={loadingMore} onClick={loadMore}>
              {loadingMore ? "Loading…" : "Load more"}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
