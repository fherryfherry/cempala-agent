"use client";

import { useMemo } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  formatAgentName,
  listAgents,
  listRuns,
  listSprints,
  listTickets,
  listWorkspaces,
} from "@/lib/api";
import { useWorkspaceEvents } from "@/components/events-context";
import { formatTimestamp } from "@/lib/datetime";
import { runStats, ticketCounts } from "@/lib/dashboard-stats";
import { AgentAvatar } from "@/components/agent-avatar";
import { AgentStatusDot } from "@/components/agent-status";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const ACTIVE_STATUSES = ["todo", "in_progress", "review", "qa", "security", "release"];
const RUN_STATUS_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  queued: "outline",
  running: "default",
  done: "secondary",
  failed: "destructive",
  cancelled: "outline",
  interrupted: "outline",
};

export default function DashboardPage() {
  const params = useParams<{ key: string }>();
  const workspaceKey = params.key;

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });
  const workspace = workspaces.data?.find((ws) => ws.key === workspaceKey);

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
  const runs = useQuery({
    queryKey: ["runs", workspace?.id],
    queryFn: () => listRuns(workspace!.id),
    enabled: !!workspace,
  });
  const sprints = useQuery({
    queryKey: ["sprints", workspace?.id],
    queryFn: () => listSprints(workspace!.id),
    enabled: !!workspace,
  });
  const { status: sseStatus } = useWorkspaceEvents();

  const counts = useMemo(() => ticketCounts(tickets.data), [tickets.data]);
  const liveRuns = useMemo(() => runStats(runs.data), [runs.data]);
  const activeSprint = sprints.data?.find((s) => s.status === "active");

  const recentRuns = useMemo(() => {
    const withTime = (runs.data ?? []).filter((r) => r.started_at);
    return withTime
      .sort((a, b) => (b.started_at ?? "").localeCompare(a.started_at ?? ""))
      .slice(0, 8);
  }, [runs.data]);

  const ticketById = useMemo(
    () => new Map((tickets.data ?? []).map((t) => [t.id, t])),
    [tickets.data],
  );
  const agentById = useMemo(
    () => new Map((agents.data ?? []).map((a) => [a.id, a])),
    [agents.data],
  );

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

  const allTickets = tickets.data ?? [];
  const doneCount = counts["done"] ?? 0;
  const blockedTickets = allTickets.filter((t) => t.status === "blocked");
  const activeCount = ACTIVE_STATUSES.reduce((acc, s) => acc + (counts[s] ?? 0), 0);
  const donePct =
    allTickets.length === 0 ? 0 : Math.round((doneCount / allTickets.length) * 100);

  // Alerts: blocked tickets, failed runs (recent), unstarted epics. Capped at 6 below.
  const alerts: { kind: "error" | "info"; text: string; href: string }[] = [];
  for (const t of blockedTickets) {
    alerts.push({
      kind: "error",
      text: `${t.key} blocked: ${t.blocked_reason ?? "no reason"}`,
      href: `/w/${workspaceKey}/ticket/${t.key}`,
    });
  }
  for (const r of recentRuns) {
    if (r.status === "failed") {
      const t = ticketById.get(r.ticket_id);
      alerts.push({
        kind: "error",
        text: `${t?.key ?? r.ticket_id} failed${r.error ? `: ${r.error}` : ""}`,
        href: t ? `/w/${workspaceKey}/ticket/${t.key}` : `/w/${workspaceKey}/activity`,
      });
    }
  }
  for (const t of allTickets) {
    if (t.status === "backlog" && t.parent_id === null) {
      alerts.push({
        kind: "info",
        text: `${t.key} unstarted epic: ${t.title}`,
        href: `/w/${workspaceKey}/ticket/${t.key}`,
      });
    }
  }

  return (
    <div className="flex w-full flex-1 flex-col gap-6 px-6 py-10">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboard</h1>
          <p className="mt-1 text-sm text-zinc-500">
            {workspace.name}
            {activeSprint ? ` · ${activeSprint.name}` : ""} · SSE:{" "}
            <span className={sseStatus === "open" ? "text-green-600" : "text-zinc-400"}>
              {sseStatus}
            </span>
          </p>
        </div>
        <Link
          href={`/w/${workspaceKey}/board`}
          className={cn(buttonVariants({ variant: "outline" }))}
        >
          Open board
        </Link>
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard label="Total tickets" value={String(allTickets.length)} />
        <StatCard
          label="Done"
          value={String(doneCount)}
          sub={allTickets.length === 0 ? "—" : `${donePct}% of all`}
          tone={doneCount > 0 ? "green" : undefined}
        />
        <StatCard
          label="In progress / review"
          value={String(activeCount)}
          sub={liveRuns.running > 0 ? `${liveRuns.running} running now` : undefined}
          tone={activeCount > 0 ? "amber" : undefined}
        />
        <StatCard
          label="Blocked"
          value={String(blockedTickets.length)}
          tone={blockedTickets.length > 0 ? "red" : undefined}
        />
      </div>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm">Agents</CardTitle>
            <Link
              href={`/w/${workspaceKey}/agents`}
              className="text-xs text-zinc-500 hover:text-zinc-800"
            >
              Manage
            </Link>
          </CardHeader>
          <CardContent className="flex max-h-80 flex-col gap-1 overflow-y-auto p-0">
            {agents.data && agents.data.length === 0 && (
              <p className="px-4 py-3 text-xs text-zinc-500">No agents yet.</p>
            )}
            {(agents.data ?? []).map((a) => (
              <div
                key={a.id}
                className="flex items-center gap-2.5 border-b border-black/5 px-4 py-2 text-sm last:border-b-0 dark:border-white/5"
              >
                <AgentStatusDot status={a.status} title={`${a.name}: ${a.status}`} />
                <AgentAvatar
                  name={a.name}
                  template={a.avatar_template}
                  color={a.avatar_color}
                  size={26}
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{formatAgentName(a.name, a.role)}</div>
                  <div className="truncate text-xs text-zinc-500">{a.model}</div>
                </div>
                <Badge
                  variant={a.status === "working" ? "default" : "outline"}
                  className="capitalize"
                >
                  {a.status}
                </Badge>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm">Recent runs</CardTitle>
            <Link
              href={`/w/${workspaceKey}/activity`}
              className="text-xs text-zinc-500 hover:text-zinc-800"
            >
              View all
            </Link>
          </CardHeader>
          <CardContent className="flex max-h-80 flex-col gap-1 overflow-y-auto p-0">
            {recentRuns.length === 0 && (
              <p className="px-4 py-3 text-xs text-zinc-500">No runs yet.</p>
            )}
            {recentRuns.map((r) => {
              const t = ticketById.get(r.ticket_id);
              const agent = agentById.get(r.agent_id);
              return (
                <Link
                  key={r.id}
                  href={t ? `/w/${workspaceKey}/ticket/${t.key}` : `/w/${workspaceKey}/activity`}
                  className="flex items-center gap-2.5 border-b border-black/5 px-4 py-2 text-sm last:border-b-0 hover:bg-zinc-50 dark:border-white/5 dark:hover:bg-zinc-900/40"
                >
                  <span className="w-16 shrink-0 font-mono text-xs text-zinc-500">
                    {t?.key ?? "—"}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{t?.title ?? "—"}</span>
                  <span className="hidden shrink-0 items-center gap-1.5 sm:flex">
                    {agent && (
                      <AgentAvatar
                        name={agent.name}
                        template={agent.avatar_template}
                        color={agent.avatar_color}
                        size={18}
                      />
                    )}
                    <span className="text-xs text-zinc-500">{agent?.name ?? "?"}</span>
                  </span>
                  <Badge variant={RUN_STATUS_VARIANT[r.status] ?? "outline"} className="shrink-0">
                    {r.status}
                  </Badge>
                  <span className="hidden w-14 shrink-0 text-right text-xs text-zinc-400 md:block">
                    {formatTimestamp(r.started_at!, workspace.timezone)}
                  </span>
                </Link>
              );
            })}
          </CardContent>
        </Card>
      </div>

      {alerts.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {alerts.slice(0, 6).map((a, i) => (
            <Link
              key={i}
              href={a.href}
              className={cn(
                "flex items-center gap-2 rounded-lg px-3 py-2 text-sm",
                a.kind === "error"
                  ? "bg-red-50 text-red-800 dark:bg-red-950/40 dark:text-red-200"
                  : "bg-blue-50 text-blue-800 dark:bg-blue-950/40 dark:text-blue-200",
              )}
            >
              <span
                className={cn(
                  "size-2 shrink-0 rounded-full",
                  a.kind === "error" ? "bg-red-500" : "bg-blue-500",
                )}
              />
              <span className="truncate">{a.text}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function StatCard({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "green" | "amber" | "red";
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-xs font-normal tracking-wide text-zinc-500 uppercase">
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-1">
        <span
          className={cn(
            "text-3xl font-semibold tracking-tight tabular-nums",
            tone === "green" && "text-emerald-600 dark:text-emerald-400",
            tone === "amber" && "text-amber-600 dark:text-amber-400",
            tone === "red" && "text-red-600 dark:text-red-400",
          )}
        >
          {value}
        </span>
        {sub && <span className="text-xs text-zinc-500">{sub}</span>}
      </CardContent>
    </Card>
  );
}
