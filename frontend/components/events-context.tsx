"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

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
  children,
}: {
  workspaceId: string | undefined;
  children: React.ReactNode;
}) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [events, setEvents] = useState<WorkspaceEvent[]>([]);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const wsIdRef = useRef<string | undefined>(undefined);

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
    setStatus("connecting");
    setEvents([]);
    setNotifications([]);

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
            toast(message, { id: `activity-${ev.id}` });
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
  }, [workspaceId, queryClient]);

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
