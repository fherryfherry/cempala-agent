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

/** Runs bucketed into UTC calendar days for the last `days` days, oldest→newest,
 * including empty days. UTC bucketing (not workspace-local) — a coarse trend
 * chart doesn't need per-workspace timezone precision. `now` is injectable for
 * deterministic tests. */
export function runActivityPerDay(
  runs: Run[] | undefined,
  days = 14,
  now: Date = new Date(),
): { date: string; done: number; failed: number; cost: number }[] {
  const byDate = new Map<string, { done: number; failed: number; cost: number }>();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now);
    d.setUTCDate(d.getUTCDate() - i);
    byDate.set(d.toISOString().slice(0, 10), { done: 0, failed: 0, cost: 0 });
  }
  for (const r of runs ?? []) {
    if (!r.started_at) continue;
    const bucket = byDate.get(r.started_at.slice(0, 10));
    if (!bucket) continue; // outside the window
    if (r.status === "done") bucket.done += 1;
    else if (r.status === "failed") bucket.failed += 1;
    bucket.cost += r.cost ?? 0;
  }
  return [...byDate.entries()].map(([date, counts]) => ({ date, ...counts }));
}

/** Sum of run.cost grouped by agent_id, sorted descending. Returns agentId, not
 * the agent object — the caller already has agents loaded and maps id -> name/color,
 * same pattern as the rest of the page. */
export function costByAgent(runs: Run[] | undefined): { agentId: string; cost: number }[] {
  const totals = new Map<string, number>();
  for (const r of runs ?? []) {
    totals.set(r.agent_id, (totals.get(r.agent_id) ?? 0) + (r.cost ?? 0));
  }
  return [...totals.entries()]
    .map(([agentId, cost]) => ({ agentId, cost }))
    .sort((a, b) => b.cost - a.cost);
}
