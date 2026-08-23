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
