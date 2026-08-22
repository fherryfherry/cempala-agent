"use client";

import { useMemo, useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  getRun,
  listAgents,
  listRuns,
  listTickets,
  listWorkspaces,
  stopRun,
  type Run,
  type RunEvent,
} from "@/lib/api";
import { useWorkspaceEvents, type EventType, type WorkspaceEvent } from "@/components/events-context";
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
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const filteredEvents = useMemo(
    () => (typeFilter === "all" ? events : events.filter((e) => e.type === typeFilter)),
    [events, typeFilter],
  );

  const agentName = (id: string) => agents.data?.find((a) => a.id === id)?.name ?? id;
  const ticketKey = (id: string) => tickets.data?.find((t) => t.id === id)?.key ?? id;

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

  return (
    <div className="flex w-full flex-1 flex-col gap-6 px-6 py-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{workspace.name} — Activity</h1>
          <p className="mt-1 text-sm text-zinc-500">
            SSE: <span className={sseStatus === "open" ? "text-green-600" : "text-zinc-400"}>{sseStatus}</span>
          </p>
        </div>
      </div>

      <div className="grid flex-1 grid-cols-1 gap-6 lg:grid-cols-[320px_1fr]">
        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Runs</CardTitle>
            </CardHeader>
            <CardContent className="flex max-h-80 flex-col gap-1 overflow-y-auto p-0">
              {runs.isLoading && <p className="px-3 py-2 text-xs text-zinc-500">Loading…</p>}
              {sortedRuns.length === 0 && (
                <p className="px-3 py-2 text-xs text-zinc-500">No runs yet.</p>
              )}
              {sortedRuns.map((r) => (
                <button
                  key={r.id}
                  onClick={() => setSelectedRunId(r.id)}
                  className={`flex flex-col gap-1 border-b border-black/5 px-3 py-2 text-left text-xs last:border-b-0 hover:bg-zinc-50 dark:border-white/5 dark:hover:bg-zinc-900/40 ${
                    selectedRunId === r.id ? "bg-zinc-100 dark:bg-zinc-900/60" : ""
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-mono text-zinc-500">{ticketKey(r.ticket_id)}</span>
                    <Badge variant={RUN_STATUS_VARIANT[r.status] ?? "outline"}>{r.status}</Badge>
                  </div>
                  <span className="text-zinc-500">{agentName(r.agent_id)}</span>
                </button>
              ))}
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
            <CardContent className="flex max-h-96 flex-col-reverse gap-1 overflow-y-auto p-0">
              {filteredEvents.length === 0 && (
                <p className="px-3 py-2 text-xs text-zinc-500">No events yet.</p>
              )}
              {[...filteredEvents].reverse().map((ev) => (
                <LiveEventRow
                  key={ev.id}
                  event={ev}
                  onClick={() => ev.run_id && setSelectedRunId(ev.run_id)}
                />
              ))}
            </CardContent>
          </Card>
        </div>

        <div>
          {selectedRunId ? (
            <RunDetailPanel
              runId={selectedRunId}
              agentName={agentName}
              ticketKey={ticketKey}
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
  ticketKey,
}: {
  runId: string;
  agentName: (id: string) => string;
  ticketKey: (id: string) => string;
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

  return (
    <Card className="flex h-full flex-col gap-4">
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div>
          <CardTitle className="flex items-center gap-2">
            <span className="font-mono text-sm">{ticketKey(run.ticket_id)}</span>
            <Badge variant={RUN_STATUS_VARIANT[run.status] ?? "outline"}>{run.status}</Badge>
          </CardTitle>
          <p className="mt-1 text-xs text-zinc-500">
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
