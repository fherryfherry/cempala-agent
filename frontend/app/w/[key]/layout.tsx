"use client";

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { listWorkspaces } from "@/lib/api";

// The SSE connection (EventsProvider) lives one level up, in EventsShell (root
// layout) — Header needs it too, and there's only ever one workspace "active" at a
// time regardless of which layout owns the connection.
export default function WorkspaceLayout({ children }: LayoutProps<"/w/[key]">) {
  const params = useParams<{ key: string }>();
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });
  const workspace = workspaces.data?.find((ws) => ws.key === params.key);

  return (
    <>
      {workspace?.paused && (
        <div
          role="alert"
          className="border-y border-red-300 bg-red-600 px-6 py-2 text-center text-sm font-medium text-white dark:border-red-900"
        >
          Workspace paused — all runs stopped, new runs rejected until resumed.
        </div>
      )}
      {children}
    </>
  );
}
