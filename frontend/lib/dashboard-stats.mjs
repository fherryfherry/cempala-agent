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

/** Runs bucketed into UTC calendar days for the last `days` days, oldest→newest,
 * including empty days. `now` is injectable for deterministic tests. */
export function runActivityPerDay(runs, days = 14, now = new Date()) {
  const byDate = new Map();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now);
    d.setUTCDate(d.getUTCDate() - i);
    byDate.set(d.toISOString().slice(0, 10), { done: 0, failed: 0, cost: 0 });
  }
  for (const r of runs ?? []) {
    if (!r.started_at) continue;
    const bucket = byDate.get(r.started_at.slice(0, 10));
    if (!bucket) continue;
    if (r.status === "done") bucket.done += 1;
    else if (r.status === "failed") bucket.failed += 1;
    bucket.cost += r.cost ?? 0;
  }
  return [...byDate.entries()].map(([date, counts]) => ({ date, ...counts }));
}

/** Sum of run.cost grouped by agent_id, sorted descending. */
export function costByAgent(runs) {
  const totals = new Map();
  for (const r of runs ?? []) {
    totals.set(r.agent_id, (totals.get(r.agent_id) ?? 0) + (r.cost ?? 0));
  }
  return [...totals.entries()]
    .map(([agentId, cost]) => ({ agentId, cost }))
    .sort((a, b) => b.cost - a.cost);
}
