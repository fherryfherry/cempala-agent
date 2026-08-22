"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  listWorkspaces,
  pauseWorkspace,
  resumeWorkspace,
  updateWorkspace,
  type Workspace,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

const GUARDRAIL_FIELDS: { key: string; label: string; step: string }[] = [
  { key: "run_timeout_sec", label: "Run timeout (s)", step: "1" },
  { key: "max_cost_per_run", label: "Max cost per run ($)", step: "0.01" },
  { key: "max_cost_per_ticket", label: "Max cost per ticket ($)", step: "0.01" },
  { key: "max_handoff_depth", label: "Max handoff depth", step: "1" },
  { key: "loop_threshold", label: "Loop threshold", step: "1" },
  { key: "max_concurrent_runs", label: "Max concurrent runs", step: "1" },
];

export default function SettingsPage() {
  const params = useParams<{ key: string }>();
  const workspaceKey = params.key;
  const queryClient = useQueryClient();

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });
  const workspace = workspaces.data?.find((ws) => ws.key === workspaceKey);

  const pause = useMutation({
    mutationFn: () => pauseWorkspace(workspace!.id),
    onSuccess: (updated) => {
      queryClient.setQueryData(["workspaces"], (old: Workspace[] | undefined) =>
        old?.map((ws) => (ws.id === updated.id ? updated : ws)),
      );
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      toast.success("Workspace paused");
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Pause failed");
    },
  });

  const resume = useMutation({
    mutationFn: () => resumeWorkspace(workspace!.id),
    onSuccess: (updated) => {
      queryClient.setQueryData(["workspaces"], (old: Workspace[] | undefined) =>
        old?.map((ws) => (ws.id === updated.id ? updated : ws)),
      );
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      toast.success("Workspace resumed");
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Resume failed");
    },
  });

  if (workspaces.isLoading) {
    return <p className="px-6 py-10 text-sm text-zinc-500">Loading workspace…</p>;
  }

  if (!workspace) {
    return (
      <p className="px-6 py-10 text-sm text-red-600">
        Workspace &quot;{workspaceKey}&quot; not found.
      </p>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-6 py-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{workspace.name} — Settings</h1>
        <p className="mt-1 text-sm text-zinc-500">{workspace.repo_path}</p>
      </div>

      <WorkspaceInfoForm workspace={workspace} />
      <GuardrailsForm workspace={workspace} />
      <PauseResumeCard workspace={workspace} pause={pause} resume={resume} />
      <SecurityWarningCard />
    </div>
  );
}

function WorkspaceInfoForm({ workspace }: { workspace: Workspace }) {
  const queryClient = useQueryClient();
  const [repoPath, setRepoPath] = useState(workspace.repo_path);
  const [repoPathError, setRepoPathError] = useState<string | null>(null);
  const [generalError, setGeneralError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => updateWorkspace(workspace.id, { repo_path: repoPath.trim() }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["workspaces"], (old: Workspace[] | undefined) =>
        old?.map((ws) => (ws.id === updated.id ? updated : ws)),
      );
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      setRepoPathError(null);
      setGeneralError(null);
      toast.success("Repo path updated");
    },
    onError: (err: unknown) => {
      setRepoPathError(null);
      setGeneralError(null);
      if (err instanceof ApiError && err.status === 422) {
        setRepoPathError(err.message);
      } else {
        setGeneralError(err instanceof ApiError ? err.message : "Update failed");
      }
    },
  });

  const dirty = repoPath.trim() !== workspace.repo_path;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Repo path</CardTitle>
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
            <Label htmlFor="settings-repo-path">Working directory for agents</Label>
            <Input
              id="settings-repo-path"
              placeholder="/absolute/path/to/repo"
              value={repoPath}
              onChange={(e) => {
                setRepoPath(e.target.value);
                setRepoPathError(null);
              }}
              aria-invalid={repoPathError ? true : undefined}
              required
            />
            {repoPathError && <p className="text-xs text-red-600">{repoPathError}</p>}
            <p className="text-xs text-zinc-500">
              Agents run in this folder with full permissions. Validation here is a convenience
              check, not a security boundary.
            </p>
          </div>

          {generalError && <p className="text-sm text-red-600">{generalError}</p>}

          <div>
            <Button type="submit" disabled={mutation.isPending || !dirty}>
              {mutation.isPending ? "Saving…" : "Save repo path"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function GuardrailsForm({ workspace }: { workspace: Workspace }) {
  const queryClient = useQueryClient();
  const [values, setValues] = useState<Record<string, string>>(
    () =>
      Object.fromEntries(
        GUARDRAIL_FIELDS.map((f) => [
          f.key,
          String((workspace.guardrails as Record<string, unknown>)[f.key] ?? ""),
        ]),
      ) as Record<string, string>,
  );
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [generalError, setGeneralError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => {
      const parsed: Record<string, number> = {};
      const nextErrors: Record<string, string> = {};
      for (const f of GUARDRAIL_FIELDS) {
        const raw = values[f.key].trim();
        if (raw === "") {
          nextErrors[f.key] = "Required";
          continue;
        }
        const num = Number(raw);
        if (!Number.isFinite(num) || num < 0) {
          nextErrors[f.key] = "Must be a non-negative number";
          continue;
        }
        parsed[f.key] = num;
      }
      if (Object.keys(nextErrors).length > 0) {
        setErrors(nextErrors);
        throw new Error("validation");
      }
      setErrors({});
      return updateWorkspace(workspace.id, { guardrails: parsed });
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(["workspaces"], (list: Workspace[] | undefined) =>
        list?.map((ws) => (ws.id === updated.id ? updated : ws)),
      );
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      setGeneralError(null);
      toast.success("Guardrails saved");
    },
    onError: (err: unknown) => {
      if (err instanceof Error && err.message === "validation") return;
      setGeneralError(
        err instanceof ApiError ? err.message : "Failed to save guardrails",
      );
    },
  });

  const dirty = GUARDRAIL_FIELDS.some(
    (f) =>
      values[f.key]?.trim() !== String((workspace.guardrails as Record<string, unknown>)[f.key] ?? ""),
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle>Guardrails</CardTitle>
      </CardHeader>
      <CardContent>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
        >
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            {GUARDRAIL_FIELDS.map((f) => (
              <div key={f.key} className="flex flex-col gap-1.5">
                <Label htmlFor={`guardrail-${f.key}`}>{f.label}</Label>
                <Input
                  id={`guardrail-${f.key}`}
                  type="number"
                  min="0"
                  step={f.step}
                  value={values[f.key]}
                  onChange={(e) => {
                    setValues((prev) => ({ ...prev, [f.key]: e.target.value }));
                    setErrors((prev) => ({ ...prev, [f.key]: "" }));
                  }}
                  aria-invalid={errors[f.key] ? true : undefined}
                />
                {errors[f.key] && <p className="text-xs text-red-600">{errors[f.key]}</p>}
              </div>
            ))}
          </div>

          <p className="text-xs text-zinc-500">
            Every trip leaves a system comment naming the guardrail. Applies to the next run;
            no restart needed.
          </p>

          {generalError && <p className="text-sm text-red-600">{generalError}</p>}

          <div>
            <Button type="submit" disabled={mutation.isPending || !dirty}>
              {mutation.isPending ? "Saving…" : "Save guardrails"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function PauseResumeCard({
  workspace,
  pause,
  resume,
}: {
  workspace: Workspace;
  pause: { mutate: () => void; isPending: boolean };
  resume: { mutate: () => void; isPending: boolean };
}) {
  const [confirmOpen, setConfirmOpen] = useState(false);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Kill switch</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {workspace.paused ? (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-zinc-600">
              This workspace is paused. All runs are stopped and new runs are rejected until
              you resume.
            </p>
            <div>
              <Button onClick={() => resume.mutate()} disabled={resume.isPending}>
                {resume.isPending ? "Resuming…" : "Resume workspace"}
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            <p className="text-sm text-zinc-600">
              Stops every running agent (kills the opencode subprocesses) and rejects new
              runs until resumed.
            </p>
            <div>
              <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
                <DialogTrigger render={<Button variant="destructive">Pause workspace</Button>} />
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>Pause workspace?</DialogTitle>
                  </DialogHeader>
                  <p className="text-sm text-zinc-600">
                    This immediately cancels all running and queued runs in &quot;
                    {workspace.name}&quot; and kills their processes. Agents that were in the
                    middle of work may leave the repo in an unfinished state.
                  </p>
                  <DialogFooter>
                    <Button variant="outline" onClick={() => setConfirmOpen(false)}>
                      Cancel
                    </Button>
                    <Button
                      variant="destructive"
                      disabled={pause.isPending}
                      onClick={() => {
                        setConfirmOpen(false);
                        pause.mutate();
                      }}
                    >
                      {pause.isPending ? "Pausing…" : "Pause workspace"}
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function SecurityWarningCard() {
  return (
    <Card className="border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/30">
      <CardHeader>
        <CardTitle className="text-red-700 dark:text-red-400">Security warning</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-2 text-sm text-red-800 dark:text-red-300">
        <p>
          Agents run with <code className="font-mono">opencode --auto</code>, which approves
          every permission request. They can execute any command with your user&apos;s
          privileges — <code className="font-mono">--dir</code> sets a working directory, not a
          sandbox.
        </p>
        <p>
          Only point the portal at repositories you trust, on a machine you control. Never
          expose this backend beyond <code className="font-mono">127.0.0.1</code>, and keep
          production secrets out of <code className="font-mono">repo_path</code>.
        </p>
        <p className="text-xs opacity-80">
          This warning cannot be dismissed. Details: docs/02-tsd.md §7, ADR-010.
        </p>
      </CardContent>
    </Card>
  );
}
