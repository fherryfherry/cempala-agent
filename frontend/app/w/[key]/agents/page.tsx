"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ApiError,
  createAgent,
  getModels,
  listAgents,
  listWorkspaces,
  type Role,
  type ToolKind,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { AgentStatusBadge } from "@/components/agent-status";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const ROLES: Role[] = ["pm", "lead", "engineer", "designer", "qa", "pentester"];

// Common Indonesian first names, used to suggest a natural agent name instead of a
// role-number slug (e.g. "Budi" instead of "eng-1").
const NAME_POOL = [
  "Budi", "Siti", "Andi", "Dewi", "Rian", "Putri", "Agus", "Rina", "Dedi", "Yuni",
  "Fajar", "Wulan", "Hendra", "Lestari", "Bayu", "Sari", "Eka", "Novi", "Wahyu", "Indah",
  "Rizky", "Ayu", "Teguh", "Fitri", "Arif", "Maya", "Dimas", "Ratna", "Yoga", "Citra",
];

function suggestAgentName(existingNames: string[]): string {
  const taken = new Set(existingNames);
  const free = NAME_POOL.find((n) => !taken.has(n));
  if (free) return free;
  // pool exhausted — fall back to numbered variants of the first name.
  let n = 2;
  while (taken.has(`${NAME_POOL[0]}${n}`)) n++;
  return `${NAME_POOL[0]}${n}`;
}
const TOOL_KINDS: { value: ToolKind; enabled: boolean }[] = [
  { value: "opencode", enabled: true },
  { value: "claude", enabled: false },
  { value: "agy", enabled: false },
  { value: "codex", enabled: false },
];

export default function AgentsPage() {
  const params = useParams<{ key: string }>();
  const workspaceKey = params.key;

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });
  const workspace = workspaces.data?.find((ws) => ws.key === workspaceKey);

  const agents = useQuery({
    queryKey: ["agents", workspace?.id],
    queryFn: () => listAgents(workspace!.id),
    enabled: !!workspace,
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
        <h1 className="text-2xl font-semibold tracking-tight">
          {workspace.name} — Agents
        </h1>
        <p className="mt-1 text-sm text-zinc-500">{workspace.repo_path}</p>
      </div>

      <div className="flex flex-col gap-3">
        {agents.isLoading && <p className="text-sm text-zinc-500">Loading agents…</p>}
        {agents.data?.length === 0 && (
          <p className="text-sm text-zinc-500">No agents yet — create one below.</p>
        )}
        {agents.data?.map((agent) => (
          <Card key={agent.id}>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2">
                <span>{agent.name}</span>
                <Badge variant="secondary">{agent.role}</Badge>
                <Badge variant="outline">{agent.tool_kind}</Badge>
                <AgentStatusBadge status={agent.status} />
              </CardTitle>
            </CardHeader>
            <CardContent className="text-xs text-zinc-500">{agent.model}</CardContent>
          </Card>
        ))}
      </div>

      <CreateAgentForm workspaceId={workspace.id} />
    </div>
  );
}

function CreateAgentForm({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const agents = useQuery({ queryKey: ["agents", workspaceId], queryFn: () => listAgents(workspaceId) });
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [role, setRole] = useState<Role>("engineer");
  const [model, setModel] = useState("");
  const [toolKind, setToolKind] = useState<ToolKind>("opencode");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);

  const existingNames = agents.data?.map((a) => a.name) ?? [];
  const suggestedName = suggestAgentName(existingNames);
  const effectiveName = nameTouched ? name : suggestedName;

  const models = useQuery({ queryKey: ["models"], queryFn: getModels, retry: false });

  const mutation = useMutation({
    mutationFn: () =>
      createAgent(workspaceId, {
        name: effectiveName,
        role,
        model,
        tool_kind: toolKind,
        system_prompt: systemPrompt || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents", workspaceId] });
      setName("");
      setNameTouched(false);
      setModel("");
      setSystemPrompt("");
      setError(null);
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Unexpected error");
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Create agent</CardTitle>
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
            <Label htmlFor="agent-name">Name</Label>
            <Input
              id="agent-name"
              value={effectiveName}
              onChange={(e) => {
                setName(e.target.value);
                setNameTouched(true);
              }}
              required
            />
            {!nameTouched && (
              <p className="text-xs text-zinc-500">
                Suggested — edit to use a different name.
              </p>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Role</Label>
            <Select value={role} onValueChange={(v) => setRole(v as Role)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ROLES.map((r) => (
                  <SelectItem key={r} value={r}>
                    {r}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Model</Label>
            {models.isError ? (
              <>
                <Input
                  placeholder="provider/model"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  required
                />
                <p className="text-xs text-red-600">
                  {models.error instanceof ApiError
                    ? models.error.message
                    : "Could not load models — run `opencode auth login`."}
                </p>
              </>
            ) : (
              <Select value={model} onValueChange={(v) => setModel(v ?? "")}>
                <SelectTrigger>
                  <SelectValue placeholder={models.isLoading ? "Loading…" : "Select a model"} />
                </SelectTrigger>
                <SelectContent>
                  {(models.data ?? []).map((m) => (
                    <SelectItem key={m} value={m}>
                      {m}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Tool</Label>
            <Select value={toolKind} onValueChange={(v) => setToolKind(v as ToolKind)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {TOOL_KINDS.map((t) => (
                  <SelectItem key={t.value} value={t.value} disabled={!t.enabled}>
                    {t.value}
                    {!t.enabled ? " (coming soon)" : ""}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="agent-system-prompt">System prompt (optional)</Label>
            <Textarea
              id="agent-system-prompt"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
            />
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <Button type="submit" disabled={mutation.isPending || !model}>
            {mutation.isPending ? "Creating…" : "Create agent"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
