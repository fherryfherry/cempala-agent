# Dashboard (Workspace Overview) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-workspace Dashboard page — live agent status + recent runs + alerts + ticket progress/cost summary — as the first nav item and default workspace landing page.

**Architecture:** Pure frontend. Data comes from existing endpoints (`listWorkspaces`, `listAgents`, `listTickets`, `listRuns`, `listSprints`) via React Query, and updates live because the existing SSE `EventsProvider` (frontend/components/events-context.tsx:140-189) already invalidates `["tickets", wsId]`, `["runs", wsId]`, `["agents", wsId]` on `status_change`/`comment`/`run_started`/`run_ended` events. No backend change. Stats logic is a pure module (`frontend/lib/dashboard-stats.ts`) so numbers are unit-testable without a test framework (run via plain Node on a `.mjs` twin).

**Tech Stack:** Next.js App Router (client pages), TanStack Query, Tailwind v4 + shadcn/ui (Card, Badge, Button), existing `lib/api.ts`, `lib/datetime.ts`, `lib/ticket-style.ts`, `components/agent-avatar.tsx`, `components/agent-status.tsx`.

**Conventions to follow:**
- Copy the page skeleton pattern from `frontend/app/w/[key]/board/page.tsx:60-127`: `"use client"`, `useParams<{ key: string }>()`, `useQuery({ queryKey: ["tickets", workspace?.id], queryFn: () => listTickets(workspace!.id), enabled: !!workspace })`, loading `<p className="px-6 py-10 text-sm text-zinc-500">Loading workspace…</p>`, not-found `<p className="px-6 py-10 text-sm text-red-600">Workspace "{key}" not found.</p>`, page root `flex w-full flex-1 flex-col gap-6 px-6 py-10`.
- Doc conventions: docs are the spec, English; code/comments in English. New feature doc entry as `MAP-034 · Dashboard workspace · S · Engineer` appended to `docs/04-tasks.md` (after MAP-033). No doc decisions change, so `docs/06-adr.md` untouched.
- Worktree: repo has `.worktrees/dev2` at a040555 (detached) — the current main tree at `eee516a` already has an **uncommitted change set** (many ` M` files). **Never `git add .`** — stage only the specific files listed in each task.
- No `make` targets beyond existing `make test` (backend pytest — untouched), and `cd frontend && npm run lint` (eslint) for frontend checks.

**Route naming:** `/w/[key]/dashboard` (not root `/w/[key]`) — explicit, matches sibling pages; landing is achieved by redirecting the workspace card link in the home page.

---

### Task 1: Pure stats module + tests

**Files:**
- Create: `frontend/lib/dashboard-stats.ts` (typed, used by the page)
- Create: `frontend/lib/dashboard-stats.mjs` (duplicate logic, plain JS — Node-runnable)
- Create: `frontend/lib/dashboard-stats.test.mjs`

*Why two files:* the frontend has no test runner (`frontend/package.json` only has `dev`/`build`/`start`/`lint`; no vitest/jest). The `.mjs` twin lets the test execute in Node. Keep them identical in behavior; the `.ts` imports `@/lib/api` types only.

- [ ] **Step 1: Write the failing tests**

Create `frontend/lib/dashboard-stats.test.mjs`:

```js
import { ticketCounts, runStats, sumCost } from "./dashboard-stats.mjs";

// --- ticketCounts ---
const counts = ticketCounts([
  { status: "todo" }, { status: "done" }, { status: "done" }, { status: "blocked" },
]);
if (JSON.stringify(counts) !== JSON.stringify({ todo: 1, done: 2, blocked: 1 })) {
  throw new Error("ticketCounts wrong: " + JSON.stringify(counts));
}
if (JSON.stringify(ticketCounts([])) !== "{}") throw new Error("ticketCounts(empty)");
if (JSON.stringify(ticketCounts(undefined)) !== "{}") throw new Error("ticketCounts(undefined)");

// --- runStats ---
const rs = runStats([
  { status: "running" }, { status: "running" }, { status: "failed" }, { status: "queued" }, { status: "done" },
]);
if (JSON.stringify(rs) !== JSON.stringify({ running: 2, failed: 1, queued: 1 })) {
  throw new Error("runStats wrong: " + JSON.stringify(rs));
}
if (JSON.stringify(runStats([])) !== JSON.stringify({ running: 0, failed: 0, queued: 0 })) throw new Error("runStats(empty)");
if (JSON.stringify(runStats(undefined)) !== JSON.stringify({ running: 0, failed: 0, queued: 0 })) throw new Error("runStats(undefined)");

// --- sumCost ---
if (Math.abs(sumCost([{ cost_used: 0.5 }, { cost_used: 1.25 }]) - 1.75) > 1e-9) throw new Error("sumCost");
if (sumCost([]) !== 0) throw new Error("sumCost(empty)");
if (sumCost(undefined) !== 0) throw new Error("sumCost(undefined)");

console.log("dashboard-stats: all tests passed");
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node frontend/lib/dashboard-stats.test.mjs`

Expected: `Error [ERR_MODULE_NOT_FOUND]: Cannot find module '.../dashboard-stats.mjs'`

- [ ] **Step 3: Implement the `.mjs` module**

Create `frontend/lib/dashboard-stats.mjs`:

```js
/** Ticket status → count. Never throws; unknown/missing input → empty counts. */
export function ticketCounts(tickets) {
  const counts = {};
  for (const t of tickets ?? []) {
    counts[t.status] = (counts[t.status] ?? 0) + 1;
  }
  return counts;
}

/** Counts of currently-live run statuses (queued/running) plus failed. */
export function runStats(runs) {
  const counts = { running: 0, failed: 0, queued: 0 };
  for (const r of runs ?? []) {
    if (Object.hasOwn(counts, r.status)) counts[r.status] += 1;
  }
  return counts;
}

/** Sum of ticket.cost_used (USD). */
export function sumCost(tickets) {
  return (tickets ?? []).reduce((acc, t) => acc + (t.cost_used ?? 0), 0);
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node frontend/lib/dashboard-stats.test.mjs`

Expected: `dashboard-stats: all tests passed`

- [ ] **Step 5: Add the typed module for the page**

Create `frontend/lib/dashboard-stats.ts` (same logic, typed; used by the page):

```ts
import type { Run, Ticket } from "@/lib/api";

export function ticketCounts(tickets: Ticket[] | undefined): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const t of tickets ?? []) {
    counts[t.status] = (counts[t.status] ?? 0) + 1;
  }
  return counts;
}

export function runStats(
  runs: Run[] | undefined,
): { running: number; failed: number; queued: number } {
  const counts = { running: 0, failed: 0, queued: 0 };
  for (const r of runs ?? []) {
    if (Object.hasOwn(counts, r.status)) counts[r.status as keyof typeof counts] += 1;
  }
  return counts;
}

export function sumCost(tickets: Ticket[] | undefined): number {
  return (tickets ?? []).reduce((acc, t) => acc + (t.cost_used ?? 0), 0);
}
```

- [ ] **Step 6: Run test again (still passes) + typecheck**

Run: `node frontend/lib/dashboard-stats.test.mjs && cd frontend && npx tsc --noEmit`

Expected: test passes; `tsc` clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/lib/dashboard-stats.ts frontend/lib/dashboard-stats.mjs frontend/lib/dashboard-stats.test.mjs
git commit -m "feat: dashboard stats helpers with unit tests (MAP-034)"
```

---

### Task 2: Dashboard page

**Files:**
- Create: `frontend/app/w/[key]/dashboard/page.tsx`

Page sections (top→bottom), all live-updating via SSE invalidation:

1. **Header row** — "Dashboard" + workspace name + `SSE: open/…` status (same pattern as activity page), right-aligned "Open board" link.
2. **4 stat cards** (Card + big tabular numbers): Total tickets, Done (+ % of all), Active (todo+in_progress+review+qa+security+release), Blocked. Red tone when >0, green when done>0.
3. **Agent status list** — one row per agent: `AgentStatusDot`, `AgentAvatar`, name (role), model, status Badge.
4. **Recent runs** — last 8 runs sorted by `started_at desc`: ticket key, title, agent avatar+name, run status Badge, `formatTimestamp(r.started_at, workspace.timezone)`. Each run row links to the ticket.
5. **Alerts section** — (a) blocked tickets (red, `blocked_reason`), (b) failed runs in recent 8 (red, error text), (c) unstarted epics — `status==="backlog" && parent_id===null` (blue). Cap 6 items, each links to the ticket.

- [ ] **Step 1: Write the page**

Create `frontend/app/w/[key]/dashboard/page.tsx` with the full implementation below:

```tsx
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

  const ticketById = useMemo(() => new Map((tickets.data ?? []).map((t) => [t.id, t])), [tickets.data]);
  const agentById = useMemo(() => new Map((agents.data ?? []).map((a) => [a.id, a])), [agents.data]);

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
  const donePct = allTickets.length === 0 ? 0 : Math.round((doneCount / allTickets.length) * 100);

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
            <span className={sseStatus === "open" ? "text-green-600" : "text-zinc-400"}>{sseStatus}</span>
          </p>
        </div>
        <Link href={`/w/${workspaceKey}/board`} className={cn(buttonVariants({ variant: "outline" }))}>
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
            <Link href={`/w/${workspaceKey}/agents`} className="text-xs text-zinc-500 hover:text-zinc-800">
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
                <AgentAvatar name={a.name} template={a.avatar_template} color={a.avatar_color} size={26} />
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{formatAgentName(a.name, a.role)}</div>
                  <div className="truncate text-xs text-zinc-500">{a.model}</div>
                </div>
                <Badge variant={a.status === "working" ? "default" : "outline"} className="capitalize">
                  {a.status}
                </Badge>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle className="text-sm">Recent runs</CardTitle>
            <Link href={`/w/${workspaceKey}/activity`} className="text-xs text-zinc-500 hover:text-zinc-800">
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
                  <span className="w-16 shrink-0 font-mono text-xs text-zinc-500">{t?.key ?? "—"}</span>
                  <span className="min-w-0 flex-1 truncate">{t?.title ?? "—"}</span>
                  <span className="hidden shrink-0 items-center gap-1.5 sm:flex">
                    {agent && (
                      <AgentAvatar name={agent.name} template={agent.avatar_template} color={agent.avatar_color} size={18} />
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
        <CardTitle className="text-xs font-normal tracking-wide text-zinc-500 uppercase">{label}</CardTitle>
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
```

Note: `sumCost` is intentionally not used by the page for now (kept for the cost stat to be added later) — do not import it in the page.

- [ ] **Step 2: Verify with lint + typecheck**

Run: `cd frontend && npm run lint && npx tsc --noEmit`

Expected: no errors. Fix any real lint/type errors.

- [ ] **Step 3: Manual smoke test**

Run: `make dev`, open `http://localhost:3000/w/<key>/dashboard` in a workspace with agents/runs.

Expected: stat cards populate; agent list shows status dots; recent runs list; alerts show for blocked/failed/unstarted-epic; a live run updates the "running now" sub-count and status badges without a refresh.

- [ ] **Step 4: Commit**

```bash
git add frontend/app/w/[key]/dashboard/page.tsx
git commit -m "feat: workspace dashboard overview page (MAP-034)"
```

---

### Task 3: Header nav + default landing

**Files:**
- Modify: `frontend/components/header.tsx`
- Modify: `frontend/app/page.tsx`

- [ ] **Step 1: Add Dashboard as the first nav link**

In `frontend/components/header.tsx`, inside the `<nav>` block, before the Chat link, insert:

```tsx
<Link
  href={`/w/${activeKey}/dashboard`}
  aria-current={pathname === `/w/${activeKey}/dashboard` ? "page" : undefined}
  className={cn(
    "relative flex items-center px-3 hover:bg-zinc-100 hover:text-foreground dark:hover:bg-zinc-800",
    pathname === `/w/${activeKey}/dashboard` &&
      "bg-zinc-100 text-foreground dark:bg-zinc-800",
  )}
>
  Dashboard
</Link>
```

- [ ] **Step 2: Point the home workspace card at the dashboard**

In `frontend/app/page.tsx`, change the workspace card link `href={`/w/${ws.key}/chat`}` to `href={`/w/${ws.key}/dashboard`}`.

- [ ] **Step 3: Verify**

Run: `cd frontend && npm run lint && npx tsc --noEmit`, then `make dev`; click a workspace card → lands on Dashboard; nav highlights Dashboard; Chat still reachable.

- [ ] **Step 4: Commit**

```bash
git add frontend/components/header.tsx frontend/app/page.tsx
git commit -m "feat: dashboard as first nav item and workspace landing page (MAP-034)"
```

---

### Task 4: Docs — register MAP-034

**Files:**
- Modify: `docs/04-tasks.md`

- [ ] **Step 1: Add the task entry**

In `docs/04-tasks.md`, after the MAP-033 block (end of M3), append:

```markdown
### MAP-034 · Dashboard workspace · S · Engineer
`/w/[key]/dashboard`: stat cards (total, done, active, blocked), per-agent status,
8 latest runs, and alerts (blocked, failed, epic not started). Default landing & first nav
item. Pure composition of data from existing API + SSE invalidation; no new endpoint.
**Dep:** MAP-032, MAP-021
**AC:** dashboard auto-refreshes while agents work (no reload); every alert
links to the related ticket; stats are correct for an empty workspace (no runs/agents).
```

Update the header line 1 to `# Task Breakdown — MAP-001 … MAP-034`.

- [ ] **Step 2: Commit**

```bash
git add docs/04-tasks.md
git commit -m "docs: register MAP-034 dashboard task"
```

---

## Execution Handoff

Two execution options:
1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks
2. **Inline Execution** — execute in-session with checkpoints
