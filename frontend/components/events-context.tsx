"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { XIcon } from "lucide-react";
import { formatDistanceToNowStrict } from "date-fns";
import { AgentAvatar } from "@/components/agent-avatar";
import type { Agent } from "@/lib/api";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

/** Reads the per-workspace unread chat message count stored by EventsProvider
 * (header.tsx shows it as the badge on the Chat nav link). */
export function readUnreadChatCount(workspaceId: string): number {
  try {
    const n = Number.parseInt(localStorage.getItem(`unreadChatCount:${workspaceId}`) ?? "", 10);
    return Number.isFinite(n) && n > 0 ? n : 0;
  } catch {
    return 0;
  }
}

/** Increments the unread chat count; called only for genuinely new (non-replayed)
 * agent chat activity, so the badge stays exact across SSE reconnects. */
function bumpUnreadChatCount(workspaceId: string): void {
  try {
    localStorage.setItem(`unreadChatCount:${workspaceId}`, String(readUnreadChatCount(workspaceId) + 1));
  } catch {
    // storage unavailable — badge just won't persist; SSE still works.
  }
}

export type EventType =
  | "run_started"
  | "assistant_text"
  | "reasoning"
  | "tool_call"
  | "tool_result"
  | "status_change"
  | "comment"
  | "conversation_message"
  | "handoff"
  | "error"
  | "run_ended";

export interface WorkspaceEvent {
  id: string;
  run_id: string | null;
  type: EventType;
  created_at: string | null;
  payload: Record<string, unknown>;
}

export type ConnectionStatus = "connecting" | "open" | "error";

/** Builds a human-readable toast message for "agent activity" events (comment,
 * status_change). Returns null for events that shouldn't produce a toast:
 * - assistant_text/reasoning/tool_call/etc: already shown live in the Activity feed,
 *   would be way too noisy as toasts (one per token-stream chunk).
 * - system comments that just restate "Status changed from X to Y": redundant, the
 *   matching status_change event already produces its own toast.
 * Everything else — real agent-authored comments, and system comments about a run
 * being blocked/failed/rejected — is real "activity" worth surfacing per the
 * original request (balas chat, bikin/pindah/update/komen tiket).
 */
export function buildActivityToastMessage(ev: WorkspaceEvent): string | null {
  if (ev.type === "comment") {
    const p = ev.payload as {
      ticket_key?: string;
      is_system?: boolean;
      author?: string | null;
      body_preview?: string;
    };
    if (p.is_system && p.body_preview?.startsWith("Status changed from")) {
      return null;
    }
    const who = p.is_system ? "System" : (p.author ?? "Agent");
    return `${who} commented on ${p.ticket_key ?? "a ticket"}`;
  }
  if (ev.type === "status_change") {
    const p = ev.payload as {
      ticket_key?: string;
      ticket_title?: string;
      from?: string | null;
      to?: string;
      actor?: string | null;
    };
    if (p.from == null) {
      const title = p.ticket_title ? `: ${p.ticket_title}` : "";
      return `New ticket ${p.ticket_key} created${title}`;
    }
    return p.actor
      ? `${p.actor} moved ${p.ticket_key} to ${p.to}`
      : `${p.ticket_key} moved from ${p.from} to ${p.to}`;
  }
  return null;
}

/** Resolves the display name of whoever caused a comment/status_change event — the same
 * fallback logic buildActivityToastMessage uses inline, extracted so the activity-toast
 * card can key/group by this name (one card per agent) and look up their avatar. */
function resolveActivityActor(ev: WorkspaceEvent): string {
  if (ev.type === "comment") {
    const p = ev.payload as { is_system?: boolean; author?: string | null };
    return p.is_system ? "System" : (p.author ?? "Agent");
  }
  if (ev.type === "status_change") {
    const p = ev.payload as { actor?: string | null };
    return p.actor ?? "System";
  }
  return "System";
}

/** Second line for a notification dropdown item: comment snippet, or the ticket's title
 * when it moved status (skipped on creation — the title's already in line 1). */
function buildNotificationDetail(ev: WorkspaceEvent): string | null {
  if (ev.type === "comment") {
    const p = ev.payload as { body_preview?: string };
    return p.body_preview || null;
  }
  if (ev.type === "status_change") {
    const p = ev.payload as { ticket_title?: string; from?: string | null };
    if (p.from == null) return null;
    return p.ticket_title || null;
  }
  return null;
}

const MAX_BUFFER = 200;
// Separate from MAX_BUFFER: comment/status_change events are a small fraction of the
// raw stream (assistant_text/tool_call chunks dominate during an active run), so a
// dedicated cap keeps notification history from getting diluted out of the window.
const MAX_NOTIFICATIONS = 100;

export interface NotificationItem {
  id: string;
  message: string;
  detail: string | null;
  ticketKey: string | null;
  createdAt: string;
}

interface EventsContextValue {
  status: ConnectionStatus;
  events: WorkspaceEvent[];
  notifications: NotificationItem[];
}

const EventsContext = createContext<EventsContextValue | null>(null);

/** One EventSource per workspace, shared via context. Reconnects handled natively by EventSource. */
export function EventsProvider({
  workspaceId,
  workspaceKey,
  children,
}: {
  workspaceId: string | undefined;
  workspaceKey: string | undefined;
  children: React.ReactNode;
}) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [events, setEvents] = useState<WorkspaceEvent[]>([]);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const wsIdRef = useRef<string | undefined>(undefined);

  // Reset per-workspace state when the active workspace changes (during render,
  // per the React "adjusting state when props change" pattern — avoids the
  // setState-in-effect lint error and the extra render it would trigger).
  const [prevWorkspaceId, setPrevWorkspaceId] = useState(workspaceId);
  if (prevWorkspaceId !== workspaceId) {
    setPrevWorkspaceId(workspaceId);
    setStatus("connecting");
    setEvents([]);
    setNotifications([]);
  }

  // Per-workspace high-water mark of which events have already been toasted.
  // SSE replays the full history on every (re)connect, so without this, a page
  // refresh re-toasts every past activity event ("notifikasinya muncul banyak").
  const lastSeenRef = useRef<Record<string, string>>({});

  // The same full-history replay means a workspace with a lot of past events can fire
  // dozens of invalidateQueries calls within milliseconds of connecting — each one a
  // fresh network request. Batch them into a single flush per key instead of hitting
  // the API once per replayed event (was causing net::ERR_INSUFFICIENT_RESOURCES on
  // workspaces with substantial history).
  const pendingInvalidationsRef = useRef<Set<string>>(new Set());
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  function scheduleInvalidate(queryClient: ReturnType<typeof useQueryClient>, queryKey: unknown[]) {
    pendingInvalidationsRef.current.add(JSON.stringify(queryKey));
    if (flushTimerRef.current) return;
    flushTimerRef.current = setTimeout(() => {
      flushTimerRef.current = null;
      const keys = [...pendingInvalidationsRef.current];
      pendingInvalidationsRef.current.clear();
      for (const key of keys) {
        queryClient.invalidateQueries({ queryKey: JSON.parse(key) });
      }
    }, 250);
  }

  useEffect(() => {
    if (!workspaceId) return;
    wsIdRef.current = workspaceId;

    try {
      lastSeenRef.current[workspaceId] = localStorage.getItem(`notifSeenAt:${workspaceId}`) ?? "";
    } catch {
      lastSeenRef.current[workspaceId] = "";
    }

    const source = new EventSource(
      `${API_BASE_URL}/workspaces/${workspaceId}/events/stream`,
    );

    source.onopen = () => setStatus("open");
    source.onerror = () => setStatus("error");
    source.onmessage = (msg) => {
      const ev = JSON.parse(msg.data) as WorkspaceEvent;
      setEvents((prev) => [...prev.slice(-(MAX_BUFFER - 1)), ev]);

      // coarse invalidation: ticket-affecting events invalidate the tickets list;
      // ticket key isn't reliably in every payload, so we don't try to target ["ticket", key].
      if (ev.type === "status_change" || ev.type === "comment" || ev.type === "handoff") {
        scheduleInvalidate(queryClient, ["tickets", workspaceId]);
        const ticketKey = ev.payload?.ticket_key;
        if (typeof ticketKey === "string") {
          scheduleInvalidate(queryClient, ["ticket", ticketKey]);
        }

        const message = buildActivityToastMessage(ev);
        const createdAt = ev.created_at;
        if (message && createdAt) {
          // Unlike the toast below, the bell's history includes replayed-on-connect
          // events too — that's what makes it "history" rather than a second toast.
          const notifTicketKey = typeof ticketKey === "string" ? ticketKey : null;
          const detail = buildNotificationDetail(ev);
          setNotifications((prev) => [
            ...prev.slice(-(MAX_NOTIFICATIONS - 1)),
            { id: ev.id, message, detail, ticketKey: notifTicketKey, createdAt },
          ]);

          // Toast only genuinely new activity: events older than (or equal to) the
          // last toasted one are history being replayed, not fresh notifications.
          const lastSeen = lastSeenRef.current[workspaceId] ?? "";
          if (createdAt > lastSeen) {
            const goToTicket =
              notifTicketKey && workspaceKey
                ? () => router.push(`/w/${workspaceKey}/ticket/${notifTicketKey}`)
                : undefined;
            const who = resolveActivityActor(ev);
            const agent = workspaceId
              ? queryClient
                  .getQueryData<Agent[]>(["agents", workspaceId])
                  ?.find((a) => a.name === who)
              : undefined;
            const timeAgo = formatDistanceToNowStrict(new Date(createdAt), { addSuffix: true });
            // toast.custom (not plain toast()) — sonner's ToastT has no whole-toast
            // onClick, only a separate action-button onClick, so a click-anywhere
            // notification needs its own rendered content. Sonner only applies its
            // fixed toast width (var(--width)) and default close button to
            // "data-styled" (non-custom) toasts, so both are re-added by hand here
            // to match the look of every other toast.
            //
            // id is keyed by agent (not event) — calling toast.custom again with the
            // same id replaces that card's content and resets its dismiss timer in
            // place, which is what gives "one card per agent, updates instead of
            // stacking" for free from sonner, no extra state needed.
            toast.custom(
              (toastId) => (
                <div
                  onClick={
                    goToTicket
                      ? () => {
                          goToTicket();
                          toast.dismiss(toastId);
                        }
                      : undefined
                  }
                  className={`relative flex w-[var(--width)] items-start gap-3 rounded-md border p-4 pr-8 text-sm shadow-lg ${
                    goToTicket ? "cursor-pointer hover:opacity-90" : ""
                  }`}
                  style={{
                    background: "var(--normal-bg)",
                    color: "var(--normal-text)",
                    borderColor: "var(--normal-border)",
                  }}
                >
                  <AgentAvatar
                    name={who}
                    template={agent?.avatar_template}
                    color={agent?.avatar_color}
                    size={32}
                    className="mt-0.5"
                  />
                  <div className="min-w-0 flex-1">
                    <p>{message}</p>
                    <p className="mt-0.5 text-xs opacity-60">{timeAgo}</p>
                  </div>
                  <button
                    type="button"
                    aria-label="Close toast"
                    onClick={(e) => {
                      e.stopPropagation();
                      toast.dismiss(toastId);
                    }}
                    className="absolute right-2 top-2 rounded-full border border-[var(--normal-border)] bg-[var(--normal-bg)] p-1 text-[var(--normal-text)] opacity-70 hover:opacity-100"
                  >
                    <XIcon className="size-3" />
                  </button>
                </div>
              ),
              { id: `agent-activity-${workspaceId}-${who}` },
            );
            lastSeenRef.current[workspaceId] = createdAt;
            try {
              localStorage.setItem(`notifSeenAt:${workspaceId}`, createdAt);
            } catch {
              // storage unavailable — toast dedupe just won't survive a refresh.
            }
          }
        }
      }
      if (ev.type === "conversation_message") {
        // Chat activity: invalidate the conversation list + the specific
        // conversation, and mark the workspace chat as unread. This is the
        // sole source of the Chat nav bullet — conversations are always
        // PM-owned, so a non-system message here is precisely "the PM
        // replied to the user." Replay-safe: uses the event's own timestamp
        // (not Date.now()) and never regresses an existing mark, since the
        // SSE stream replays history on every (re)connect.
        scheduleInvalidate(queryClient, ["conversations", workspaceId]);
        const conversationId = ev.payload?.conversation_id;
        if (typeof conversationId === "string") {
          scheduleInvalidate(queryClient, ["conversation", conversationId]);
        }
        if (ev.payload?.is_system !== true && ev.created_at) {
          let wrote = false;
          try {
            const prev = localStorage.getItem(`lastAgentChatAt:${workspaceId}`);
            if (!prev || ev.created_at > prev) {
              localStorage.setItem(`lastAgentChatAt:${workspaceId}`, ev.created_at);
              wrote = true;
            }
          } catch {
            // storage unavailable — bullet just won't persist; SSE still works.
          }
          if (wrote) {
            bumpUnreadChatCount(workspaceId);
            window.dispatchEvent(new CustomEvent("map:agent-chat", { detail: { workspaceId, at: ev.created_at } }));
          }
        }
      }
      if (ev.type === "run_started" || ev.type === "run_ended") {
        scheduleInvalidate(queryClient, ["tickets", workspaceId]);
        scheduleInvalidate(queryClient, ["agents", workspaceId]);
        scheduleInvalidate(queryClient, ["runs", workspaceId]);
        // Run payloads carry no ticket_key, so invalidate every open ticket detail
        // (["ticket", key] queries) — the detail page's "running" pill reads runs
        // embedded in TicketDetail and would otherwise go stale mid-run.
        scheduleInvalidate(queryClient, ["ticket"]);
      }
      if (ev.run_id) {
        scheduleInvalidate(queryClient, ["run", ev.run_id]);
      }
    };

    return () => {
      source.close();
      if (flushTimerRef.current) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      pendingInvalidationsRef.current.clear();
    };
  }, [workspaceId, workspaceKey, queryClient, router]);

  return (
    <EventsContext.Provider value={{ status, events, notifications }}>
      {children}
    </EventsContext.Provider>
  );
}

/** Connection status, rolling raw event buffer, and derived notification history for
 * the current workspace's SSE stream. */
export function useWorkspaceEvents(): EventsContextValue {
  const ctx = useContext(EventsContext);
  if (!ctx) {
    throw new Error("useWorkspaceEvents must be used within an EventsProvider");
  }
  return ctx;
}
