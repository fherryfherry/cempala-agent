"use client";

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { listWorkspaces } from "@/lib/api";
import { EventsProvider } from "@/components/events-context";

/** Resolves the active workspace from the route the same way Header/WorkspaceLayout
 * do, and opens the SSE connection one level above Header — so Header (which lives
 * in the root layout, outside any per-workspace subtree) can also read it. */
export function EventsShell({ children }: { children: React.ReactNode }) {
  const params = useParams<{ key?: string }>();
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: listWorkspaces,
    enabled: !!params?.key,
  });
  const workspaceId = workspaces.data?.find((ws) => ws.key === params?.key)?.id;

  return (
    <EventsProvider workspaceId={workspaceId} workspaceKey={params?.key}>
      {children}
    </EventsProvider>
  );
}
