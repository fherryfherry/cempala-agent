"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

export type EventType =
  | "run_started"
  | "assistant_text"
  | "reasoning"
  | "tool_call"
  | "tool_result"
  | "status_change"
  | "comment"
  | "handoff"
  | "error"
  | "run_ended";

export interface WorkspaceEvent {
  id: string;
  run_id: string | null;
  type: EventType;
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
function buildActivityToastMessage(ev: WorkspaceEvent): string | null {
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

const MAX_BUFFER = 200;

interface EventsContextValue {
  status: ConnectionStatus;
  events: WorkspaceEvent[];
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
  const wsIdRef = useRef<string | undefined>(undefined);

  useEffect(() => {
    if (!workspaceId) return;
    wsIdRef.current = workspaceId;
    setStatus("connecting");
    setEvents([]);

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
        queryClient.invalidateQueries({ queryKey: ["tickets", workspaceId] });
        const ticketKey = ev.payload?.ticket_key;
        if (typeof ticketKey === "string") {
          queryClient.invalidateQueries({ queryKey: ["ticket", ticketKey] });
        }

        const message = buildActivityToastMessage(ev);
        if (message) toast(message);
      }
      if (ev.type === "run_started" || ev.type === "run_ended") {
        queryClient.invalidateQueries({ queryKey: ["tickets", workspaceId] });
        queryClient.invalidateQueries({ queryKey: ["agents", workspaceId] });
        queryClient.invalidateQueries({ queryKey: ["runs", workspaceId] });
      }
      if (ev.run_id) {
        queryClient.invalidateQueries({ queryKey: ["run", ev.run_id] });
      }
    };

    return () => {
      source.close();
    };
  }, [workspaceId, queryClient]);

  return (
    <EventsContext.Provider value={{ status, events }}>
      {children}
    </EventsContext.Provider>
  );
}

/** Connection status + rolling event buffer for the current workspace's SSE stream. */
export function useWorkspaceEvents(): EventsContextValue {
  const ctx = useContext(EventsContext);
  if (!ctx) {
    throw new Error("useWorkspaceEvents must be used within an EventsProvider");
  }
  return ctx;
}
