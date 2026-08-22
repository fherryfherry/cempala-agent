"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  createWorkspace,
  getHealth,
  listWorkspaces,
  type Workspace,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function Home() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-6 py-10">
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

      <WorkspaceList workspaces={workspaces.data} isLoading={workspaces.isLoading} />
      <CreateWorkspaceForm />
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
        <Link key={ws.id} href={`/w/${ws.key}/agents`}>
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

function CreateWorkspaceForm() {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [repoPath, setRepoPath] = useState("");
  const [repoPathError, setRepoPathError] = useState<string | null>(null);
  const [generalError, setGeneralError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => createWorkspace({ name, key, repo_path: repoPath }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      setName("");
      setKey("");
      setRepoPath("");
      setRepoPathError(null);
      setGeneralError(null);
    },
    onError: (err: unknown) => {
      setRepoPathError(null);
      setGeneralError(null);
      if (err instanceof ApiError && err.status === 422) {
        setRepoPathError(err.message);
      } else if (err instanceof ApiError) {
        setGeneralError(err.message);
      } else {
        setGeneralError("Unexpected error");
      }
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Create workspace</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ws-name">Name</Label>
            <Input
              id="ws-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ws-key">Key</Label>
            <Input
              id="ws-key"
              placeholder="MAP"
              value={key}
              onChange={(e) => setKey(e.target.value.toUpperCase())}
              pattern="[A-Z]{2,5}"
              title="2-5 uppercase letters"
              required
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ws-repo-path">Repo path</Label>
            <Input
              id="ws-repo-path"
              placeholder="/absolute/path/to/repo"
              value={repoPath}
              onChange={(e) => {
                setRepoPath(e.target.value);
                setRepoPathError(null);
              }}
              aria-invalid={repoPathError ? true : undefined}
              required
            />
            {repoPathError && (
              <p className="text-xs text-red-600">{repoPathError}</p>
            )}
          </div>

          {generalError && <p className="text-sm text-red-600">{generalError}</p>}

          <Button type="submit" disabled={mutation.isPending}>
            {mutation.isPending ? "Creating…" : "Create workspace"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
