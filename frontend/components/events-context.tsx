"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

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
