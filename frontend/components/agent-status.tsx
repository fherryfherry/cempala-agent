import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

/** Shared status → visual treatment map, used by header, board, and agents page (MAP-025). */
const STATUS_COLOR: Record<string, string> = {
  idle: "bg-zinc-400",
  working: "bg-blue-500",
  error: "bg-red-500",
  disabled: "bg-zinc-300 dark:bg-zinc-700",
};

const STATUS_BADGE_VARIANT: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  idle: "secondary",
  working: "default",
  error: "destructive",
  disabled: "outline",
};

export function AgentStatusDot({ status, title }: { status: string; title?: string }) {
  return (
    <span
      title={title ?? status}
      className={cn(
        "inline-block size-2 shrink-0 rounded-full",
        STATUS_COLOR[status] ?? "bg-zinc-400",
      )}
    />
  );
}

export function AgentStatusBadge({ status }: { status: string }) {
  return <Badge variant={STATUS_BADGE_VARIANT[status] ?? "outline"}>{status}</Badge>;
}
