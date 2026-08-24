"use client";

import { useState } from "react";
import Link from "next/link";
import { BellIcon } from "lucide-react";
import { useWorkspaceEvents } from "@/components/events-context";
import { formatShortTime } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

function readReadAt(workspaceId: string): string {
  try {
    return localStorage.getItem(`notifBellReadAt:${workspaceId}`) ?? "";
  } catch {
    return "";
  }
}

export function NotificationBell({
  workspaceId,
  workspaceKey,
  timezone,
}: {
  workspaceId: string;
  workspaceKey: string;
  timezone?: string;
}) {
  const { notifications } = useWorkspaceEvents();
  const [open, setOpen] = useState(false);
  const [readAt, setReadAt] = useState(() => readReadAt(workspaceId));
  const [visibleCount, setVisibleCount] = useState(10);
  // Re-derive readAt/visibleCount when the workspace changes, without an effect
  // (React's documented pattern for resetting state during render on a prop change).
  const [readAtWorkspaceId, setReadAtWorkspaceId] = useState(workspaceId);
  if (workspaceId !== readAtWorkspaceId) {
    setReadAtWorkspaceId(workspaceId);
    setReadAt(readReadAt(workspaceId));
    setVisibleCount(10);
  }

  const items = [...notifications].reverse(); // newest first
  const visible = items.slice(0, visibleCount);
  const hiddenCount = items.length - visible.length;
  const unreadCount = items.filter((n) => n.createdAt > readAt).length;

  function markAllRead() {
    const latest = items[0]?.createdAt ?? new Date().toISOString();
    setReadAt(latest);
    try {
      localStorage.setItem(`notifBellReadAt:${workspaceId}`, latest);
    } catch {
      // storage unavailable — read state just won't persist across reloads.
    }
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            className="relative rounded-md p-1.5 text-zinc-500 hover:bg-zinc-100 hover:text-foreground dark:hover:bg-zinc-800"
            aria-label="Notifications"
          >
            <BellIcon className="size-4.5" />
            {unreadCount > 0 && (
              <span className="absolute -top-0.5 -right-0.5 flex min-w-[16px] items-center justify-center rounded-full bg-red-500 px-1 text-[10px] leading-[16px] font-semibold text-white">
                {unreadCount > 9 ? "9+" : unreadCount}
              </span>
            )}
          </button>
        }
      />
      <PopoverContent>
        <div className="flex items-center justify-between border-b border-black/5 px-3 py-2 dark:border-white/5">
          <span className="text-sm font-medium">Notifications</span>
          <Button variant="ghost" size="sm" onClick={markAllRead} disabled={unreadCount === 0}>
            Mark all as read
          </Button>
        </div>
        <div className="max-h-80 overflow-y-auto">
          {items.length === 0 && (
            <p className="p-4 text-center text-xs text-zinc-500">No notifications yet.</p>
          )}
          {visible.map((item) => {
            const unread = item.createdAt > readAt;
            const row = (
              <div
                className={`flex flex-col gap-0.5 border-b border-black/5 px-3 py-2 text-xs last:border-b-0 dark:border-white/5 ${
                  unread ? "bg-blue-50 dark:bg-blue-950/20" : ""
                }`}
              >
                <span className="text-zinc-700 dark:text-zinc-300">{item.message}</span>
                <span className="text-[10px] text-zinc-400">{formatShortTime(item.createdAt, timezone)}</span>
              </div>
            );
            return item.ticketKey ? (
              <Link
                key={item.id}
                href={`/w/${workspaceKey}/ticket/${item.ticketKey}`}
                className="block hover:bg-zinc-50 dark:hover:bg-zinc-900/50"
                onClick={() => setOpen(false)}
              >
                {row}
              </Link>
            ) : (
              <div key={item.id}>{row}</div>
            );
          })}
          {hiddenCount > 0 && (
            <button
              type="button"
              className="w-full border-t border-black/5 px-3 py-2 text-center text-xs text-zinc-500 hover:bg-zinc-50 dark:border-white/5 dark:hover:bg-zinc-900/50"
              onClick={() => setVisibleCount((n) => n + 10)}
            >
              Tampilkan lebih banyak ({hiddenCount} lagi)
            </button>
          )}
        </div>
      </PopoverContent>
    </Popover>
  );
}
