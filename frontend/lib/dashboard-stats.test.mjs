import { ticketCounts, runStats, sumCost, runActivityPerDay, costByAgent } from "./dashboard-stats.mjs";

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

// --- runActivityPerDay ---
const activity = runActivityPerDay(
  [
    { status: "done", started_at: "2026-08-24T10:00:00Z", cost: 1, agent_id: "a1" },
    { status: "failed", started_at: "2026-08-24T11:00:00Z", cost: 0.5, agent_id: "a1" },
    { status: "done", started_at: "2026-08-10T10:00:00Z", cost: 99, agent_id: "a1" }, // outside window
  ],
  7,
  new Date("2026-08-25T00:00:00Z"),
);
if (activity.length !== 7) throw new Error("runActivityPerDay length: " + activity.length);
const lastDay = activity[activity.length - 1];
if (lastDay.date !== "2026-08-25") throw new Error("runActivityPerDay last bucket: " + lastDay.date);
const aug24 = activity.find((b) => b.date === "2026-08-24");
if (aug24.done !== 1 || aug24.failed !== 1 || Math.abs(aug24.cost - 1.5) > 1e-9) {
  throw new Error("runActivityPerDay counts: " + JSON.stringify(aug24));
}
if (activity.reduce((acc, b) => acc + b.done + b.failed, 0) !== 2) {
  throw new Error("runActivityPerDay dropped out-of-window run");
}
if (runActivityPerDay(undefined, 3, new Date("2026-08-25")).length !== 3) {
  throw new Error("runActivityPerDay(undefined)");
}

// --- costByAgent ---
const byAgent = costByAgent([
  { agent_id: "a1", cost: 1 },
  { agent_id: "a2", cost: 3 },
  { agent_id: "a1", cost: 0.5 },
]);
if (JSON.stringify(byAgent) !== JSON.stringify([{ agentId: "a2", cost: 3 }, { agentId: "a1", cost: 1.5 }])) {
  throw new Error("costByAgent wrong: " + JSON.stringify(byAgent));
}
if (costByAgent([]).length !== 0) throw new Error("costByAgent(empty)");
if (costByAgent(undefined).length !== 0) throw new Error("costByAgent(undefined)");

console.log("dashboard-stats: all tests passed");
