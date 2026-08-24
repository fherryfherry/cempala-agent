"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { listAgents, listWorkspaces } from "@/lib/api";
import { cn } from "@/lib/utils";
import { AgentAvatars } from "@/components/agent-avatars";
import { LogoBanner } from "@/components/logo";
import { NotificationBell } from "@/components/notification-bell";
import { WorkspaceSwitcher } from "@/components/workspace-switcher";

function readLastAgentChat(workspaceId: string): string | null {
  try {
    return localStorage.getItem(`lastAgentChatAt:${workspaceId}`);
  } catch {
    return null;
  }
}

function readChatLastRead(workspaceId: string): string | null {
  try {
    return localStorage.getItem(`chatLastReadAt:${workspaceId}`);
  } catch {
    return null;
  }
}

export function Header() {
  const params = useParams<{ key?: string }>();
  const activeKey = params?.key;
  const pathname = usePathname();
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: listWorkspaces,
    enabled: !!activeKey,
  });
  const activeWorkspace = workspaces.data?.find((ws) => ws.key === activeKey);
  const workspaceId = activeWorkspace?.id;
  const workspaceTimezone = activeWorkspace?.timezone;
  const agents = useQuery({
    queryKey: ["agents", workspaceId],
    queryFn: () => listAgents(workspaceId!),
    enabled: !!workspaceId,
  });

  // Unread chat bullet: localStorage-tracked last agent comment vs last time the
  // chat page was opened, kept in sync across pages via a custom window event
  // (the Header lives outside the workspace's EventsProvider).
  const [hasUnreadChat, setHasUnreadChat] = useState(false);
  useEffect(() => {
    if (!workspaceId) return;
    const refresh = () => {
      const lastAgent = readLastAgentChat(workspaceId);
      const lastRead = readChatLastRead(workspaceId);
      setHasUnreadChat(!!lastAgent && (!lastRead || lastAgent > lastRead));
    };
    refresh();
    window.addEventListener("map:agent-chat", refresh);
    window.addEventListener("map:chat-read", refresh);
    return () => {
      window.removeEventListener("map:agent-chat", refresh);
      window.removeEventListener("map:chat-read", refresh);
    };
  }, [workspaceId]);

  // Opening the chat page marks it read.
  useEffect(() => {
    if (!workspaceId) return;
    if (pathname === `/w/${activeKey}/chat`) {
      try {
        localStorage.setItem(`chatLastReadAt:${workspaceId}`, new Date().toISOString());
      } catch {
        // ignore
      }
      window.dispatchEvent(new CustomEvent("map:chat-read", { detail: { workspaceId } }));
    }
  }, [pathname, activeKey, workspaceId]);

  return (
    <header className="sticky top-0 z-50 border-b border-black/[.08] bg-background dark:border-white/[.145]">
      <div className="flex h-14 items-center gap-4 px-6">
        <Link
          href="/"
          className="flex items-center font-semibold"
          aria-label="CEMPALA — home"
        >
          {/* Same variant as the home page's banner (just shrunk to fit the navbar) so the
              wordmark reads identically everywhere; hidden on the home page itself, which
              already shows the full-size banner, to avoid doubling up. */}
          <LogoBanner
            variant="home"
            className={cn("text-[3px]", pathname === "/" && "hidden")}
          />
        </Link>

        {activeKey && (
          <>
            <nav className="flex h-14 items-stretch gap-0.5 text-sm text-zinc-600 dark:text-zinc-400">
              <Link
                href={`/w/${activeKey}/dashboard`}
                aria-current={pathname === `/w/${activeKey}/dashboard` ? "page" : undefined}
                className={cn(
                  "relative flex items-center px-3 hover:bg-zinc-100 hover:text-foreground dark:hover:bg-zinc-800",
                  pathname === `/w/${activeKey}/dashboard` &&
                    "bg-zinc-100 text-foreground dark:bg-zinc-800",
                )}
              >
                Dashboard
              </Link>
              <Link
                href={`/w/${activeKey}/chat`}
                aria-current={pathname === `/w/${activeKey}/chat` ? "page" : undefined}
                className={cn(
                  "relative flex items-center px-3 hover:bg-zinc-100 hover:text-foreground dark:hover:bg-zinc-800",
                  pathname === `/w/${activeKey}/chat` &&
                    "bg-zinc-100 text-foreground dark:bg-zinc-800",
                )}
              >
                Chat
                {hasUnreadChat && (
                  <span
                    aria-label="New PM messages"
                    className="absolute top-1.5 right-1.5 flex size-2 rounded-full bg-red-500 ring-2 ring-white dark:ring-zinc-950"
                  />
                )}
              </Link>
              <Link
                href={`/w/${activeKey}/board`}
                aria-current={pathname === `/w/${activeKey}/board` ? "page" : undefined}
                className={cn(
                  "flex items-center px-3 hover:bg-zinc-100 hover:text-foreground dark:hover:bg-zinc-800",
                  pathname === `/w/${activeKey}/board` &&
                    "bg-zinc-100 text-foreground dark:bg-zinc-800",
                )}
              >
                Board
              </Link>
              <Link
                href={`/w/${activeKey}/timeline`}
                aria-current={pathname === `/w/${activeKey}/timeline` ? "page" : undefined}
                className={cn(
                  "flex items-center px-3 hover:bg-zinc-100 hover:text-foreground dark:hover:bg-zinc-800",
                  pathname === `/w/${activeKey}/timeline` &&
                    "bg-zinc-100 text-foreground dark:bg-zinc-800",
                )}
              >
                Timeline
              </Link>
              <Link
                href={`/w/${activeKey}/activity`}
                aria-current={pathname === `/w/${activeKey}/activity` ? "page" : undefined}
                className={cn(
                  "flex items-center px-3 hover:bg-zinc-100 hover:text-foreground dark:hover:bg-zinc-800",
                  pathname === `/w/${activeKey}/activity` &&
                    "bg-zinc-100 text-foreground dark:bg-zinc-800",
                )}
              >
                Activity
              </Link>
              <Link
                href={`/w/${activeKey}/artifacts`}
                aria-current={pathname === `/w/${activeKey}/artifacts` ? "page" : undefined}
                className={cn(
                  "flex items-center px-3 hover:bg-zinc-100 hover:text-foreground dark:hover:bg-zinc-800",
                  pathname === `/w/${activeKey}/artifacts` &&
                    "bg-zinc-100 text-foreground dark:bg-zinc-800",
                )}
              >
                Artifacts
              </Link>
              <Link
                href={`/w/${activeKey}/routines`}
                aria-current={pathname === `/w/${activeKey}/routines` ? "page" : undefined}
                className={cn(
                  "flex items-center px-3 hover:bg-zinc-100 hover:text-foreground dark:hover:bg-zinc-800",
                  pathname === `/w/${activeKey}/routines` &&
                    "bg-zinc-100 text-foreground dark:bg-zinc-800",
                )}
              >
                Rutinitas
              </Link>
              <Link
                href={`/w/${activeKey}/agents`}
                aria-current={pathname === `/w/${activeKey}/agents` ? "page" : undefined}
                className={cn(
                  "flex items-center px-3 hover:bg-zinc-100 hover:text-foreground dark:hover:bg-zinc-800",
                  pathname === `/w/${activeKey}/agents` &&
                    "bg-zinc-100 text-foreground dark:bg-zinc-800",
                )}
              >
                Agents
              </Link>
              <Link
                href={`/w/${activeKey}/settings`}
                aria-current={pathname === `/w/${activeKey}/settings` ? "page" : undefined}
                className={cn(
                  "flex items-center px-3 hover:bg-zinc-100 hover:text-foreground dark:hover:bg-zinc-800",
                  pathname === `/w/${activeKey}/settings` &&
                    "bg-zinc-100 text-foreground dark:bg-zinc-800",
                )}
              >
                Settings
              </Link>
            </nav>

            <div className="ml-auto flex items-center gap-3">
              <WorkspaceSwitcher workspaces={workspaces.data ?? []} activeKey={activeKey} />
              {agents.data && agents.data.length > 0 && (
                <AgentAvatars agents={agents.data} workspaceId={workspaceId!} workspaceKey={activeKey} />
              )}
              {workspaceId && (
                <NotificationBell
                  workspaceId={workspaceId}
                  workspaceKey={activeKey}
                  timezone={workspaceTimezone}
                />
              )}
            </div>
          </>
        )}
      </div>
    </header>
  );
}
