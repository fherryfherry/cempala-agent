"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { getHealth, listWorkspaces, type Workspace } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LogoBanner } from "@/components/logo";

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
  if (isLoading) return <p className="text-sm text-zinc-500">Loading workspaces…</p>;
  if (!workspaces || workspaces.length === 0) {
    return <p className="text-sm text-zinc-500">No workspaces yet — create one below.</p>;
  }

  return (
    <div className="flex flex-col gap-3">
      {workspaces.map((ws) => (
        <Link key={ws.id} href={`/w/${ws.key}/dashboard`}>
          <Card className="transition-colors hover:bg-muted/50">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <span>{ws.name}</span>
                <span className="text-xs font-normal text-zinc-500">{ws.key}</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="text-xs text-zinc-500">{ws.repo_path}</CardContent>
          </Card>
        </Link>
      ))}
    </div>
  );
}
