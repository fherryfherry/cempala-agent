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
