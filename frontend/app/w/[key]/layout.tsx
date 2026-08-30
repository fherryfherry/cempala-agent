"use client";

import { useParams, usePathname } from "next/navigation";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listWorkspaces } from "@/lib/api";
import { TerminalSession } from "@/components/terminal-session";
import { FloatingChat } from "@/components/floating-chat";
import { ApprovalModal } from "@/components/approval-modal";

// The SSE connection (EventsProvider) lives one level up, in EventsShell (root
// layout) — Header needs it too, and there's only ever one workspace "active" at a
// time regardless of which layout owns the connection.
export default function WorkspaceLayout({ children }: LayoutProps<"/w/[key]">) {
  const params = useParams<{ key: string }>();
  const pathname = usePathname();
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });
  const workspace = workspaces.data?.find((ws) => ws.key === params.key);

  // Floating chat's open state lives here (not inside FloatingChat) so
  // ApprovalModal can suppress itself while the panel is already showing the
  // same pills inline — otherwise both surfaces would render for the same PM
  // question, each with its own independent, unsynchronized send mutation.
  const [chatOpen, setChatOpen] = useState(false);
  const onChatPage = pathname === `/w/${params.key}/chat`;

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
      <TerminalSession workspaceId={workspace?.id} workspaceKey={params.key} />
      <FloatingChat
        workspaceId={workspace?.id}
        workspaceKey={params.key}
        open={chatOpen}
        onOpenChange={setChatOpen}
      />
      <ApprovalModal
        workspaceId={workspace?.id}
        suppressed={onChatPage || chatOpen}
      />
    </>
  );
}
