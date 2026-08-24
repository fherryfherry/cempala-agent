"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  getWorkflowPromptDefault,
  listWorkspaces,
  pauseWorkspace,
  resetWorkspace,
  resumeWorkspace,
  updateWorkspace,
  type AgentRole,
  type TimeUnit,
  type Workspace,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { listTimezones } from "@/lib/timezones";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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

const ROLE_OPTIONS: { value: AgentRole; label: string }[] = [
  { value: "pm", label: "PM" },
  { value: "lead", label: "Lead" },
  { value: "engineer", label: "Engineer" },
  { value: "designer", label: "Designer" },
  { value: "qa", label: "QA" },
  { value: "pentester", label: "Pentester" },
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
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-zinc-500">{workspace.repo_path}</p>
      </div>

      <WorkspaceInfoForm workspace={workspace} />
      <TimeUnitForm workspace={workspace} />
      <SprintCreatorRolesForm workspace={workspace} />
      <TimezoneForm workspace={workspace} />
      <WorkflowPromptForm workspace={workspace} />
      <GuardrailsForm workspace={workspace} />
      <PauseResumeCard workspace={workspace} pause={pause} resume={resume} />
      <ResetDataCard workspace={workspace} />
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
              Doesn&apos;t exist yet? It&apos;ll be created — at that exact path if absolute, or
              under <code className="font-mono">workspaces/&lt;name&gt;</code> if you just type a
              name. Agents run in this folder with full permissions. Validation here is a
              convenience check, not a security boundary.
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

function TimeUnitForm({ workspace }: { workspace: Workspace }) {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (time_unit: TimeUnit) => updateWorkspace(workspace.id, { time_unit }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["workspaces"], (old: Workspace[] | undefined) =>
        old?.map((ws) => (ws.id === updated.id ? updated : ws)),
      );
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      toast.success("Time unit saved");
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Failed to save time unit");
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Unit waktu (Timeline & estimasi sprint)</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-1.5">
          <Label>Satuan durasi</Label>
          <Select
            value={workspace.time_unit}
            onValueChange={(v) => v && mutation.mutate(v as TimeUnit)}
          >
            <SelectTrigger className="w-40">
              <SelectValue>{(v: TimeUnit) => (v === "hour" ? "JAM" : "HARI")}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="hour">JAM</SelectItem>
              <SelectItem value="day">HARI</SelectItem>
            </SelectContent>
          </Select>
          <p className="text-xs text-zinc-500">
            Dipakai PM untuk estimasi durasi sprint/tiket, dan untuk lebar blok di Timeline.
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function SprintCreatorRolesForm({ workspace }: { workspace: Workspace }) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<AgentRole[]>(workspace.sprint_creator_roles ?? ["pm"]);

  const mutation = useMutation({
    mutationFn: (roles: AgentRole[]) => updateWorkspace(workspace.id, { sprint_creator_roles: roles }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["workspaces"], (old: Workspace[] | undefined) =>
        old?.map((ws) => (ws.id === updated.id ? updated : ws)),
      );
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      toast.success("Sprint creator roles saved");
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Failed to save sprint creator roles");
    },
  });

  const dirty =
    JSON.stringify([...selected].sort()) !==
    JSON.stringify([...(workspace.sprint_creator_roles ?? ["pm"])].sort());

  function toggle(role: AgentRole) {
    setSelected((prev) =>
      prev.includes(role) ? prev.filter((r) => r !== role) : [...prev, role],
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Sprint creation</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            {ROLE_OPTIONS.map((opt) => {
              const active = selected.includes(opt.value);
              return (
                <button
                  key={opt.value}
                  type="button"
                  onClick={() => toggle(opt.value)}
                  aria-pressed={active}
                  className={`cursor-pointer rounded-full border px-3 py-1 text-sm transition-colors ${
                    active
                      ? "border-primary bg-primary/10 font-medium text-foreground"
                      : "border-zinc-300 text-zinc-500 hover:bg-zinc-100 dark:border-zinc-700 dark:hover:bg-zinc-800"
                  }`}
                >
                  {opt.label}
                </button>
              );
            })}
          </div>
          <p className="text-xs text-zinc-500">
            Role yang boleh mendeklarasikan <code className="font-mono">sprints:</code> di blok
            ```map (membuat/memperbarui sprint). Default: PM saja.
          </p>
          <div>
            <Button
              variant="outline"
              size="sm"
              disabled={mutation.isPending || !dirty}
              onClick={() => mutation.mutate(selected)}
            >
              {mutation.isPending ? "Saving…" : "Save sprint creator roles"}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function TimezoneForm({ workspace }: { workspace: Workspace }) {
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: (timezone: string) => updateWorkspace(workspace.id, { timezone }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["workspaces"], (old: Workspace[] | undefined) =>
        old?.map((ws) => (ws.id === updated.id ? updated : ws)),
      );
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      toast.success("Timezone saved");
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Failed to save timezone");
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Zona waktu (tampilan timestamp)</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-1.5">
          <Label>Timezone</Label>
          <Select
            value={workspace.timezone}
            onValueChange={(v) => v && mutation.mutate(v)}
          >
            <SelectTrigger className="w-72">
              <SelectValue placeholder="Pilih timezone" />
            </SelectTrigger>
            <SelectContent>
              {listTimezones().map((tz) => (
                <SelectItem key={tz.value} value={tz.value}>
                  {tz.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-zinc-500">
            Dipakai untuk semua timestamp di UI (detail tiket, komentar, activity).
            Default Asia/Jakarta (WIB).
          </p>
        </div>
      </CardContent>
    </Card>
  );
}

function WorkflowPromptForm({ workspace }: { workspace: Workspace }) {
  const queryClient = useQueryClient();
  const [prompt, setPrompt] = useState(workspace.workflow_prompt ?? "");
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => updateWorkspace(workspace.id, { workflow_prompt: prompt }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["workspaces"], (old: Workspace[] | undefined) =>
        old?.map((ws) => (ws.id === updated.id ? updated : ws)),
      );
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      setError(null);
      toast.success("Workflow prompt saved");
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Failed to save workflow prompt");
    },
  });

  const defaultPrompt = useQuery({
    queryKey: ["workflow-prompt-default"],
    queryFn: getWorkflowPromptDefault,
    retry: false,
  });

  const dirty = prompt !== (workspace.workflow_prompt ?? "");

  function handleReset() {
    const text = defaultPrompt.data?.workflow_prompt;
    if (text != null) {
      setPrompt(text);
      setError(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Workflow prompt</CardTitle>
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
            <Label htmlFor="settings-workflow-prompt">
              How should the team work? (injected into every agent prompt)
            </Label>
            <Textarea
              id="settings-workflow-prompt"
              value={prompt}
              onChange={(e) => {
                setPrompt(e.target.value);
                setError(null);
              }}
              rows={2}
              className="max-h-16 overflow-y-auto transition-[max-height] duration-200 focus:max-h-64"
              placeholder="Contoh: PM selalu breakdown dulu sebelum eksekusi. Lead wajib cek lint. QA boleh langsung bikin test tanpa perlu menunggu…"
            />
            <p className="text-xs text-zinc-500">
              Disisipkan ke prompt semua agent (PM, Lead, Engineer, dst) sebelum blok
              ```map. Kosongkan untuk perilaku bawaan.
            </p>
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <div className="flex items-center gap-2">
            <Button type="submit" disabled={mutation.isPending || !dirty}>
              {mutation.isPending ? "Saving…" : "Save workflow prompt"}
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={defaultPrompt.isLoading || prompt === (defaultPrompt.data?.workflow_prompt ?? "")}
              onClick={handleReset}
            >
              Reset to default
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

function ResetDataCard({ workspace }: { workspace: Workspace }) {
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);

  const mutation = useMutation({
    mutationFn: () => resetWorkspace(workspace.id),
    onSuccess: (updated) => {
      queryClient.setQueryData(["workspaces"], (old: Workspace[] | undefined) =>
        old?.map((ws) => (ws.id === updated.id ? updated : ws)),
      );
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      queryClient.invalidateQueries({ queryKey: ["tickets", workspace.id] });
      queryClient.invalidateQueries({ queryKey: ["sprints", workspace.id] });
      queryClient.invalidateQueries({ queryKey: ["runs", workspace.id] });
      setConfirmOpen(false);
      toast.success("Workspace data reset");
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Reset failed");
    },
  });

  return (
    <Card className="border-red-300 dark:border-red-900">
      <CardHeader>
        <CardTitle>Reset data</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-zinc-600">
          Deletes every ticket in &quot;{workspace.name}&quot; — including their comments (chat
          history), attachments, runs, and Activity events — and restarts the ticket key
          counter. Agents and workspace settings are kept.
        </p>
        {!workspace.paused && (
          <p className="text-xs text-amber-600 dark:text-amber-500">
            Pause the workspace first — reset refuses to run while anything could still be
            writing to the tickets it&apos;s about to delete.
          </p>
        )}
        <div>
          <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
            <DialogTrigger
              render={
                <Button variant="destructive" disabled={!workspace.paused}>
                  Reset data
                </Button>
              }
            />
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Reset all data in &quot;{workspace.name}&quot;?</DialogTitle>
              </DialogHeader>
              <p className="text-sm text-zinc-600">
                This permanently deletes every ticket, chat, and activity record in this
                workspace. It cannot be undone.
              </p>
              <DialogFooter>
                <Button variant="outline" onClick={() => setConfirmOpen(false)}>
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  disabled={mutation.isPending}
                  onClick={() => mutation.mutate()}
                >
                  {mutation.isPending ? "Resetting…" : "Reset data"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
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
