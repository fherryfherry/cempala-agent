"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { listAgents, listWorkspaces } from "@/lib/api";
import { AgentStatusDot } from "@/components/agent-status";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export function Header() {
  const params = useParams<{ key?: string }>();
  const activeKey = params?.key;
  const workspaces = useQuery({
    queryKey: ["workspaces"],
    queryFn: listWorkspaces,
    enabled: !!activeKey,
  });
  const workspaceId = workspaces.data?.find((ws) => ws.key === activeKey)?.id;
  const agents = useQuery({
    queryKey: ["agents", workspaceId],
    queryFn: () => listAgents(workspaceId!),
    enabled: !!workspaceId,
  });

  return (
    <header className="border-b border-black/[.08] dark:border-white/[.145]">
      <div className="mx-auto flex h-14 max-w-5xl items-center gap-4 px-6">
        <Link href="/" className="font-semibold tracking-tight">
          Multi-Agent Portal
        </Link>

        {activeKey && (
          <>
            <Select
              value={activeKey}
              onValueChange={(value) => {
                window.location.href = `/w/${value}/agents`;
              }}
            >
              <SelectTrigger size="sm">
                <SelectValue placeholder={activeKey} />
              </SelectTrigger>
              <SelectContent>
                {(workspaces.data ?? []).map((ws) => (
                  <SelectItem key={ws.id} value={ws.key}>
                    {ws.name} ({ws.key})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <nav className="flex items-center gap-3 text-sm text-zinc-600 dark:text-zinc-400">
              <Link href={`/w/${activeKey}/board`} className="hover:text-foreground">
                Board
              </Link>
              <Link href={`/w/${activeKey}/agents`} className="hover:text-foreground">
                Agents
              </Link>
              <Link href={`/w/${activeKey}/activity`} className="hover:text-foreground">
                Activity
              </Link>
            </nav>

            {agents.data && agents.data.length > 0 && (
              <div className="ml-auto flex items-center gap-1.5">
                {agents.data.map((a) => (
                  <AgentStatusDot key={a.id} status={a.status} title={`${a.name}: ${a.status}`} />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </header>
  );
}
