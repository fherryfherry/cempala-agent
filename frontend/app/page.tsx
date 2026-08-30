"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQueries, useQuery } from "@tanstack/react-query";
import { getHealth, listAgents, listWorkspaces, type Workspace } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LogoBanner } from "@/components/logo";
import { AgentAvatars } from "@/components/agent-avatars";
import { AgentStatusDot } from "@/components/agent-status";

export default function Home() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });

  const hasWorkspaces = (workspaces.data?.length ?? 0) > 0;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-6 py-10">
      <div className="flex flex-col items-center gap-2">
        <LogoBanner variant="home" className="overflow-x-auto text-[13px]" />
        <p className="text-xs tracking-wide text-zinc-500 uppercase">
          The Dalang of Your Multi-Agent Software House
        </p>
      </div>

      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Workspaces</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Backend:{" "}
            {health.isLoading
              ? "checking…"
              : health.isError
                ? "unreachable"
                : `${health.data?.status} (opencode: ${health.data?.opencode ?? "not found"})`}
          </p>
        </div>
        {hasWorkspaces && (
          <Button nativeButton={false} render={<Link href="/onboarding">Add workspace</Link>} />
        )}
      </div>

      <WorkspaceList workspaces={workspaces.data} isLoading={workspaces.isLoading} />
      {!workspaces.isLoading && !hasWorkspaces && (
        <Card>
          <CardHeader>
            <CardTitle>Belum ada workspace</CardTitle>
          </CardHeader>
          <CardContent>
            <Button nativeButton={false} render={<Link href="/onboarding">Buat workspace pertama</Link>} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function WorkspaceList({
  workspaces,
  isLoading,
}: {
  workspaces: Workspace[] | undefined;
  isLoading: boolean;
}) {
  const agentQueries = useQueries({
    queries: (workspaces ?? []).map((ws) => ({
      queryKey: ["agents", ws.id],
      queryFn: () => listAgents(ws.id),
    })),
  });

  if (isLoading) return <p className="text-sm text-zinc-500">Loading workspaces…</p>;
  if (!workspaces || workspaces.length === 0) {
    return <p className="text-sm text-zinc-500">No workspaces yet — create one below.</p>;
  }

  const runningIds = new Set(
    workspaces
      .filter((ws, i) => agentQueries[i].data?.some((a) => a.enabled && a.status === "working"))
      .map((ws) => ws.id),
  );
  const active = workspaces.filter((ws) => runningIds.has(ws.id));
  const inactive = workspaces.filter((ws) => !runningIds.has(ws.id));

  return (
    <div className="flex flex-col gap-6">
      <WorkspaceGroup title="Active" workspaces={active} />
      <WorkspaceGroup title="Inactive" workspaces={inactive} />
    </div>
  );
}

function WorkspaceGroup({ title, workspaces }: { title: string; workspaces: Workspace[] }) {
  if (workspaces.length === 0) return null;
  return (
    <section className="flex flex-col gap-2">
      <h2 className="text-sm font-medium text-zinc-500">
        {title} <span className="text-zinc-400">({workspaces.length})</span>
      </h2>
      <div className="flex flex-col gap-3">
        {workspaces.map((ws) => (
          <WorkspaceCard key={ws.id} workspace={ws} />
        ))}
      </div>
    </section>
  );
}

function WorkspaceCard({ workspace: ws }: { workspace: Workspace }) {
  const router = useRouter();
  const agents = useQuery({
    queryKey: ["agents", ws.id],
    queryFn: () => listAgents(ws.id),
  });
  const enabledAgents = agents.data?.filter((a) => a.enabled) ?? [];
  const isRunning = enabledAgents.some((a) => a.status === "working");

  return (
    <Card
      onClick={() => router.push(`/w/${ws.key}/dashboard`)}
      className="cursor-pointer transition-colors hover:bg-muted/50"
    >
      <CardHeader>
        <CardTitle className="flex items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <span>{ws.name}</span>
            <span className="text-xs font-normal text-zinc-500">{ws.key}</span>
          </span>
          <span className="flex items-center gap-1.5 text-xs font-normal text-zinc-500">
            <AgentStatusDot status={isRunning ? "working" : "idle"} />
            {isRunning ? "Running" : "Idle"}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="flex items-center justify-between gap-2">
        <span className="text-xs text-zinc-500">{ws.repo_path}</span>
        {enabledAgents.length > 0 && (
          <div onClick={(e) => e.stopPropagation()}>
            <AgentAvatars agents={enabledAgents} workspaceId={ws.id} workspaceKey={ws.key} />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
