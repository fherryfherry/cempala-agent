"use client";

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { listWorkspaces } from "@/lib/api";
import { EventsProvider } from "@/components/events-context";

export default function WorkspaceLayout({ children }: LayoutProps<"/w/[key]">) {
  const params = useParams<{ key: string }>();
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });
  const workspaceId = workspaces.data?.find((ws) => ws.key === params.key)?.id;

  return <EventsProvider workspaceId={workspaceId}>{children}</EventsProvider>;
}
