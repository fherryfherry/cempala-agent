"use client";

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Agent, Run, Ticket, TicketStatus } from "@/lib/api";
import { costByAgent, runActivityPerDay, ticketCounts } from "@/lib/dashboard-stats";
import { STATUS_HEX } from "@/lib/ticket-style";
import { avatarColorOf } from "@/components/agent-avatar";

const STATUS_ORDER: TicketStatus[] = [
  "backlog",
  "todo",
  "in_progress",
  "review",
  "qa",
  "security",
  "done",
  "blocked",
];

function statusLabel(status: string): string {
  return status.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

function formatCost(value: number): string {
  return `$${value.toFixed(2)}`;
}

function formatDate(date: string): string {
  return date.slice(5).replace("-", "/");
}

function Empty({ text }: { text: string }) {
  return <p className="px-4 py-3 text-xs text-zinc-500">{text}</p>;
}

export function TicketStatusChart({ tickets }: { tickets: Ticket[] }) {
  const counts = useMemo(() => ticketCounts(tickets), [tickets]);
  if (tickets.length === 0) return <Empty text="No tickets yet." />;

  // recharts v3 fails to render ANY segment of a stacked bar if one of the
  // stacked dataKeys is 0 for every data point (recharts/recharts#6235) — only
  // stack statuses that actually have tickets.
  const presentStatuses = STATUS_ORDER.filter((s) => (counts[s] ?? 0) > 0);
  const data = [{ name: "Tickets", ...Object.fromEntries(presentStatuses.map((s) => [s, counts[s]])) }];

  return (
    <div className="flex flex-col gap-3">
      <ResponsiveContainer width="100%" height={90}>
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 8, bottom: 0, left: 0 }}>
          <XAxis type="number" hide />
          <YAxis type="category" dataKey="name" hide />
          <Tooltip formatter={(value, name) => [value, statusLabel(String(name))]} />
          {presentStatuses.map((status) => (
            <Bar key={status} dataKey={status} stackId="status" fill={STATUS_HEX[status]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
      <div className="flex flex-wrap gap-x-3 gap-y-1.5">
        {presentStatuses.map((status) => (
          <span key={status} className="flex items-center gap-1.5 text-xs text-zinc-600 dark:text-zinc-400">
            <span
              className="size-2.5 shrink-0 rounded-sm"
              style={{ backgroundColor: STATUS_HEX[status] }}
            />
            {statusLabel(status)} ({counts[status]})
          </span>
        ))}
      </div>
    </div>
  );
}

export function RunActivityChart({ runs }: { runs: Run[] }) {
  const data = useMemo(() => runActivityPerDay(runs, 14), [runs]);
  if (runs.length === 0) return <Empty text="No runs yet." />;

  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -20 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} className="stroke-zinc-200 dark:stroke-zinc-800" />
        <XAxis dataKey="date" tickFormatter={formatDate} fontSize={11} stroke="#a1a1aa" />
        <YAxis allowDecimals={false} fontSize={11} stroke="#a1a1aa" />
        <Tooltip labelFormatter={(label) => formatDate(String(label))} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
        <Line type="monotone" dataKey="done" name="Done" stroke={STATUS_HEX.done} strokeWidth={2} dot={false} />
        <Line
          type="monotone"
          dataKey="failed"
          name="Failed"
          stroke={STATUS_HEX.blocked}
          strokeWidth={2}
          dot={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function CostOverTimeChart({ runs }: { runs: Run[] }) {
  const data = useMemo(() => runActivityPerDay(runs, 14), [runs]);
  if (runs.length === 0) return <Empty text="No runs yet." />;

  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} className="stroke-zinc-200 dark:stroke-zinc-800" />
        <XAxis dataKey="date" tickFormatter={formatDate} fontSize={11} stroke="#a1a1aa" />
        <YAxis tickFormatter={formatCost} fontSize={11} stroke="#a1a1aa" width={56} />
        <Tooltip
          labelFormatter={(label) => formatDate(String(label))}
          formatter={(value) => formatCost(Number(value))}
        />
        <Area
          type="monotone"
          dataKey="cost"
          name="Cost"
          stroke={STATUS_HEX.in_progress}
          fill={STATUS_HEX.in_progress}
          fillOpacity={0.15}
          strokeWidth={2}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function CostByAgentChart({ runs, agents }: { runs: Run[]; agents: Agent[] }) {
  const agentById = useMemo(() => new Map(agents.map((a) => [a.id, a])), [agents]);
  const data = useMemo(() => {
    return costByAgent(runs)
      .filter((row) => row.cost > 0)
      .map((row) => {
        const agent = agentById.get(row.agentId);
        return {
          name: agent?.name ?? "Unknown",
          cost: row.cost,
          color: avatarColorOf(agent?.name ?? "Unknown", agent?.avatar_color),
        };
      });
  }, [runs, agentById]);

  if (data.length === 0) return <Empty text="No cost data yet." />;

  return (
    <ResponsiveContainer width="100%" height={Math.max(120, data.length * 36)}>
      <BarChart data={data} layout="vertical" margin={{ top: 0, right: 16, bottom: 0, left: 8 }}>
        <XAxis type="number" tickFormatter={formatCost} fontSize={11} stroke="#a1a1aa" />
        <YAxis type="category" dataKey="name" width={90} fontSize={12} stroke="#a1a1aa" />
        <Tooltip formatter={(value) => formatCost(Number(value))} />
        <Bar dataKey="cost" radius={[0, 4, 4, 0]}>
          {data.map((row) => (
            <Cell key={row.name} fill={row.color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
