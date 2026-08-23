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
