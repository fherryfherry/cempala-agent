import type { TicketCategory, TicketPriority, TicketStatus } from "@/lib/api";

export const PRIORITY_VARIANT: Record<
  TicketPriority,
  "outline" | "secondary" | "destructive" | "default"
> = {
  low: "outline",
  medium: "secondary",
  high: "default",
  urgent: "destructive",
};

export const CATEGORY_LABELS: Record<TicketCategory, string> = {
  feature: "feature",
  improvement: "improvement",
  fix: "fix",
  security: "security",
  performance: "performance",
};

export const CATEGORY_VARIANT: Record<
  TicketCategory,
  "outline" | "secondary" | "destructive" | "default"
> = {
  feature: "default",
  improvement: "secondary",
  fix: "destructive",
  security: "outline",
  performance: "default",
};

/** Timeline block background color, keyed by category — falls back to a neutral
 * shade for uncategorized tickets so every block still reads as distinct. */
export const CATEGORY_BLOCK_COLOR: Record<TicketCategory, string> = {
  feature: "bg-violet-500",
  improvement: "bg-blue-500",
  fix: "bg-red-500",
  security: "bg-amber-500",
  performance: "bg-emerald-500",
};

export const UNCATEGORIZED_BLOCK_COLOR = "bg-zinc-400 dark:bg-zinc-600";

/** Timeline block background color, keyed by ticket status — status wins over
 * category here so the progress of a ticket is readable at a glance. */
export const STATUS_BLOCK_COLOR: Record<TicketStatus, string> = {
  backlog: "bg-zinc-400 dark:bg-zinc-600",
  todo: "bg-slate-500",
  in_progress: "bg-blue-600",
  review: "bg-violet-600",
  qa: "bg-amber-500",
  security: "bg-rose-600",
  done: "bg-emerald-600",
  blocked: "bg-red-600",
};

/** Literal hex twin of STATUS_BLOCK_COLOR for contexts needing a real color
 * value (e.g. recharts fill/stroke props, which can't take Tailwind classes). */
export const STATUS_HEX: Record<TicketStatus, string> = {
  backlog: "#a1a1aa", // zinc-400
  todo: "#64748b", // slate-500
  in_progress: "#2563eb", // blue-600
  review: "#7c3aed", // violet-600
  qa: "#f59e0b", // amber-500
  security: "#e11d48", // rose-600
  done: "#059669", // emerald-600
  blocked: "#dc2626", // red-600
};
