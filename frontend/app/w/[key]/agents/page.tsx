"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  createAgent,
  createAgentMemory,
  deleteAgent,
  deleteAgentMemory,
  getModels,
  listAgentMemory,
  listAgents,
  listWorkspaces,
  updateAgent,
  type Agent,
  type Role,
  type ToolKind,
} from "@/lib/api";
import { formatTimestamp } from "@/lib/datetime";
import {
  AGENT_TEMPLATES,
  suggestAgentName,
  suggestSlotNames,
  type AgentTemplate,
} from "@/lib/agent-templates";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { AgentStatusBadge } from "@/components/agent-status";
import { AgentAvatar } from "@/components/agent-avatar";
import { AvatarPicker, type AvatarSelection } from "@/components/avatar-picker";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const ROLES: Role[] = [
  "pm",
  "business_analyst",
  "lead",
  "system_architect",
  "engineer",
  "designer",
  "qa",
  "pentester",
];

const TOOL_KINDS: { value: ToolKind; enabled: boolean }[] = [
  { value: "opencode", enabled: true },
  { value: "claude", enabled: true },
  { value: "agy", enabled: false },
  { value: "codex", enabled: false },
];

/** Provider prefixes each tool supports (from `opencode models`). null = all
 * providers; empty array = no models yet (tool not implemented). */
const TOOL_MODEL_PROVIDERS: Record<ToolKind, string[] | null> = {
  opencode: null,
  claude: [],
  agy: [],
  codex: [],
};

/** `claude` has no `opencode models`-style listing command — its `--model` flag
 * takes a fixed set of aliases instead of a `provider/model` string. */
const CLAUDE_MODEL_ALIASES = ["sonnet", "opus", "fable"];

function modelsForTool(toolKind: ToolKind, models: string[]): string[] {
  if (toolKind === "claude") return CLAUDE_MODEL_ALIASES;
  const providers = TOOL_MODEL_PROVIDERS[toolKind];
  if (!providers) return models;
  return models.filter((m) => providers.some((p) => m.startsWith(`${p}/`)));
}

/** Model selector filtered by the chosen tool. Falls back to free-text when the
 * model list is unavailable or the current value is not in the tool's list. */
function ModelSelect({
  toolKind,
  model,
  onModelChange,
  models,
  isLoading,
  isError,
  errorMessage,
}: {
  toolKind: ToolKind;
  model: string;
  onModelChange: (v: string) => void;
  models: string[] | undefined;
  isLoading: boolean;
  isError: boolean;
  errorMessage: string;
}) {
  const available = modelsForTool(toolKind, models ?? []);

  // `claude` has its own static alias list — an opencode `/api/models` fetch
  // failure (isError/isLoading) is irrelevant to it, so skip straight to the picker.
  if (toolKind !== "claude" && isError) {
    return (
      <>
        <Input
          placeholder="provider/model"
          value={model}
          onChange={(e) => onModelChange(e.target.value)}
          required
        />
        <p className="text-xs text-red-600">{errorMessage}</p>
      </>
    );
  }

  if (toolKind !== "claude" && isLoading) {
    return (
      <Select value={model} onValueChange={(v) => onModelChange(v ?? "")}>
        <SelectTrigger className="w-full">
          <SelectValue placeholder="Loading…" />
        </SelectTrigger>
        <SelectContent className="max-w-[min(32rem,90vw)]">
          {available.map((m) => (
            <SelectItem key={m} value={m}>
              {m}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }

  if (available.length === 0) {
    return <p className="text-xs text-zinc-500">No models available for this tool.</p>;
  }

  if (model.trim() !== "" && !available.includes(model)) {
    return (
      <Input
        placeholder="provider/model"
        value={model}
        onChange={(e) => onModelChange(e.target.value)}
        required
      />
    );
  }

  return (
    <Select value={model} onValueChange={(v) => onModelChange(v ?? "")}>
      <SelectTrigger className="w-full">
        <SelectValue placeholder="Select a model" />
      </SelectTrigger>
      <SelectContent className="max-w-[min(32rem,90vw)]">
        {available.map((m) => (
          <SelectItem key={m} value={m}>
            {m}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

export default function AgentsPage() {
  const params = useParams<{ key: string }>();
  const workspaceKey = params.key;
  const [creating, setCreating] = useState(false);
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [deletingAgent, setDeletingAgent] = useState<Agent | null>(null);
  const [viewingMemoryAgent, setViewingMemoryAgent] = useState<Agent | null>(null);

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

  const hasAgents = (agents.data?.length ?? 0) > 0;
  const showCreateForm = creating || !hasAgents;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-6 py-10">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Agents</h1>
          <p className="mt-1 text-sm text-zinc-500">{workspace.repo_path}</p>
        </div>
        {!creating && (
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setTemplatesOpen(true)}>
              Templates
            </Button>
            {hasAgents && <Button onClick={() => setCreating(true)}>Add agent</Button>}
          </div>
        )}
      </div>

      <div className="flex flex-col gap-3">
        {!creating && (
          <>
            {agents.isLoading && <p className="text-sm text-zinc-500">Loading agents…</p>}
            {!hasAgents && !agents.isLoading && (
              <p className="text-sm text-zinc-500">No agents yet — create one below.</p>
            )}
            {agents.data?.map((agent) => (
              <Card key={agent.id}>
                <CardHeader>
                  <CardTitle className="flex flex-wrap items-center gap-2">
                    <AgentAvatar
                      name={agent.name}
                      template={agent.avatar_template}
                      color={agent.avatar_color}
                      size={28}
                    />
                    <span>{agent.name} ({agent.role})</span>
                    <Badge variant="outline">{agent.tool_kind}</Badge>
                    <AgentStatusBadge status={agent.status} />
                    <Button
                      variant="ghost"
                      size="sm"
                      className="ml-auto"
                      onClick={() => setViewingMemoryAgent(agent)}
                    >
                      Memory ({agent.memory_count})
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => setEditingAgent(agent)}
                    >
                      Edit
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-red-600 hover:text-red-700 hover:bg-red-50 dark:text-red-400 dark:hover:text-red-300 dark:hover:bg-red-950/40"
                      onClick={() => setDeletingAgent(agent)}
                    >
                      Delete
                    </Button>
                  </CardTitle>
                </CardHeader>
                <CardContent className="text-xs text-zinc-500">{agent.model}</CardContent>
              </Card>
            ))}
          </>
        )}
      </div>

      {showCreateForm && (
        <CreateAgentForm
          workspaceId={workspace.id}
          onCreated={() => setCreating(false)}
          onCancel={() => setCreating(false)}
        />
      )}

      {editingAgent && (
        <EditAgentDialog
          agent={editingAgent}
          workspaceId={workspace.id}
          onClose={() => setEditingAgent(null)}
        />
      )}

      {templatesOpen && (
        <SquadTemplateDialog
          workspaceId={workspace.id}
          existingNames={agents.data?.map((a) => a.name) ?? []}
          onClose={() => setTemplatesOpen(false)}
        />
      )}

      {deletingAgent && (
        <DeleteAgentDialog
          agent={deletingAgent}
          onClose={() => setDeletingAgent(null)}
        />
      )}

      {viewingMemoryAgent && (
        <AgentMemoryDialog
          agent={viewingMemoryAgent}
          workspaceTimezone={workspace.timezone}
          onClose={() => setViewingMemoryAgent(null)}
        />
      )}
    </div>
  );
}

function CreateAgentForm({
  workspaceId,
  onCreated,
  onCancel,
}: {
  workspaceId: string;
  onCreated: () => void;
  onCancel: () => void;
}) {
  const queryClient = useQueryClient();
  const agents = useQuery({ queryKey: ["agents", workspaceId], queryFn: () => listAgents(workspaceId) });
  const [name, setName] = useState("");
  const [nameTouched, setNameTouched] = useState(false);
  const [role, setRole] = useState<Role>("engineer");
  const [model, setModel] = useState("");
  const [toolKind, setToolKind] = useState<ToolKind>("opencode");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [avatar, setAvatar] = useState<AvatarSelection>({ template: null, color: null });
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
        avatar_template: avatar.template,
        avatar_color: avatar.color,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents", workspaceId] });
      setName("");
      setNameTouched(false);
      setModel("");
      setSystemPrompt("");
      setAvatar({ template: null, color: null });
      setError(null);
      onCreated();
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Unexpected error");
    },
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          Create agent
          <Button type="button" variant="ghost" size="sm" onClick={onCancel}>
            Cancel
          </Button>
        </CardTitle>
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
            <Label htmlFor="agent-role">Role</Label>
            <Select value={role} onValueChange={(v) => setRole(v as Role)}>
              <SelectTrigger id="agent-role">
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
            <Label>Model</Label>
            <ModelSelect
              toolKind={toolKind}
              model={model}
              onModelChange={setModel}
              models={models.data}
              isLoading={models.isLoading}
              isError={models.isError}
              errorMessage={
                models.error instanceof ApiError
                  ? models.error.message
                  : "Could not load models — run `opencode auth login`."
              }
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Avatar</Label>
            <AvatarPicker value={avatar} onChange={setAvatar} previewName={effectiveName} />
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

function SquadTemplateDialog({
  workspaceId,
  existingNames,
  onClose,
}: {
  workspaceId: string;
  existingNames: string[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const models = useQuery({ queryKey: ["models"], queryFn: getModels, retry: false });

  const [selected, setSelected] = useState<AgentTemplate>(AGENT_TEMPLATES[0]);
  const [names, setNames] = useState<string[]>(() =>
    suggestSlotNames(AGENT_TEMPLATES[0].slots, existingNames),
  );
  const [toolKind, setToolKind] = useState<ToolKind>("opencode");
  const [model, setModel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [createdCount, setCreatedCount] = useState<number | null>(null);

  const namesValid = names.every((n) => n.trim().length > 0);
  const namesUnique = new Set(names.map((n) => n.trim())).size === names.length;
  const namesDistinct = names.every(
    (n) => !existingNames.some((e) => e.toLowerCase() === n.trim().toLowerCase()),
  );
  const duplicateWithExisting = !namesDistinct;

  const selectTemplate = (t: AgentTemplate) => {
    setSelected(t);
    setNames(suggestSlotNames(t.slots, existingNames));
    setCreatedCount(null);
    setError(null);
  };

  const mutation = useMutation({
    mutationFn: async () => {
      const created: string[] = [];
      const failed: { name: string; reason: string }[] = [];
      for (let i = 0; i < selected.slots.length; i++) {
        const slot = selected.slots[i];
        const agentName = names[i].trim();
        try {
          await createAgent(workspaceId, {
            name: agentName,
            role: slot.role,
            model,
            tool_kind: toolKind,
            system_prompt: undefined,
            avatar_template: slot.avatar_template,
            avatar_color: slot.avatar_color,
          });
          created.push(agentName);
        } catch (err) {
          failed.push({
            name: agentName,
            reason: err instanceof ApiError ? err.message : "Unexpected error",
          });
        }
      }
      return { created, failed };
    },
    onSuccess: ({ created, failed }) => {
      queryClient.invalidateQueries({ queryKey: ["agents", workspaceId] });
      setCreatedCount(created.length);
      setNames(suggestSlotNames(selected.slots, [...existingNames, ...created]));
      if (failed.length > 0) {
        setError(
          `${failed.length} agent gagal dibuat: ${failed
            .map((f) => `${f.name} (${f.reason})`)
            .join(", ")}`,
        );
      }
    },
  });

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Agent templates</DialogTitle>
          <DialogDescription>
            Pilih template untuk membuat beberapa agent sekaligus — pilih satu model yang dipakai
            semua agent.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            {AGENT_TEMPLATES.map((t) => (
              <button
                key={t.id}
                type="button"
                onClick={() => selectTemplate(t)}
                className={`flex items-center justify-between gap-4 rounded-lg border px-4 py-3 text-left transition-colors ${
                  selected.id === t.id
                    ? "border-primary bg-accent/50"
                    : "border-border hover:bg-accent/30"
                }`}
              >
                <div>
                  <p className="text-sm font-medium">{t.name}</p>
                  <p className="text-xs text-zinc-500">{t.description}</p>
                </div>
                <Badge variant="outline">{t.slots.length} agents</Badge>
              </button>
            ))}
          </div>

          {selected && (
            <div className="flex flex-col gap-2 rounded-lg border border-border p-4">
              <p className="text-sm font-medium">{selected.name}</p>
              {selected.slots.map((slot, i) => (
                <div key={slot.role} className="flex items-center gap-2">
                  <AgentAvatar
                    name={names[i] || slot.label}
                    template={slot.avatar_template}
                    color={slot.avatar_color}
                    size={20}
                    className="shrink-0"
                  />
                  <span className="w-32 shrink-0 text-xs text-zinc-500">{slot.label}</span>
                  <Input
                    value={names[i]}
                    onChange={(e) => {
                      const next = [...names];
                      next[i] = e.target.value;
                      setNames(next);
                    }}
                    className="h-8"
                  />
                </div>
              ))}
            </div>
          )}

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
            <Label>Model (satu untuk semua agent)</Label>
            <ModelSelect
              toolKind={toolKind}
              model={model}
              onModelChange={setModel}
              models={models.data}
              isLoading={models.isLoading}
              isError={models.isError}
              errorMessage={
                models.error instanceof ApiError
                  ? models.error.message
                  : "Could not load models — run `opencode auth login`."
              }
            />
          </div>

          {!namesValid && <p className="text-sm text-red-600">Semua nama harus diisi.</p>}
          {!namesUnique && <p className="text-sm text-red-600">Nama agent harus unik.</p>}
          {duplicateWithExisting && (
            <p className="text-sm text-red-600">Nama agent sudah dipakai di workspace.</p>
          )}
          {error && <p className="text-sm text-red-600">{error}</p>}
          {createdCount !== null && !error && (
            <p className="text-sm text-emerald-600">
              {createdCount} agent dibuat. Tutup dialog atau pilih template lain untuk membuat lagi.
            </p>
          )}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button
            disabled={
              mutation.isPending || !model || !namesValid || !namesUnique || duplicateWithExisting
            }
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? "Creating…" : "Create squad"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EditAgentDialog({
  agent,
  workspaceId,
  onClose,
}: {
  agent: Agent;
  workspaceId: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(agent.name);
  const [toolKind, setToolKind] = useState<ToolKind>(agent.tool_kind as ToolKind);
  const [model, setModel] = useState(agent.model);
  const [systemPrompt, setSystemPrompt] = useState(agent.system_prompt ?? "");
  const [avatar, setAvatar] = useState<AvatarSelection>({
    template: agent.avatar_template,
    color: agent.avatar_color,
  });
  const [error, setError] = useState<string | null>(null);

  const models = useQuery({ queryKey: ["models"], queryFn: getModels, retry: false });

  const mutation = useMutation({
    mutationFn: () =>
      updateAgent(agent.id, {
        name,
        tool_kind: toolKind,
        model,
        system_prompt: systemPrompt || undefined,
        avatar_template: avatar.template,
        avatar_color: avatar.color,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents", workspaceId] });
      onClose();
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Unexpected error");
    },
  });

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Edit agent — {agent.name}</DialogTitle>
          <DialogDescription>
            Update profile, model, and avatar. Saved changes apply immediately.
          </DialogDescription>
        </DialogHeader>

        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-agent-name">Name</Label>
            <Input
              id="edit-agent-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Tool</Label>
            <Select
              value={toolKind}
              onValueChange={(v) => {
                setToolKind(v as ToolKind);
                setModel(""); // previous model may not exist for the new tool
              }}
            >
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
            <Label>Model</Label>
            <ModelSelect
              toolKind={toolKind}
              model={model}
              onModelChange={setModel}
              models={models.data}
              isLoading={models.isLoading}
              isError={models.isError}
              errorMessage={
                models.error instanceof ApiError
                  ? models.error.message
                  : "Could not load models — run `opencode auth login`."
              }
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Avatar</Label>
            <AvatarPicker value={avatar} onChange={setAvatar} previewName={name} />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-agent-system-prompt">System prompt</Label>
            <Textarea
              id="edit-agent-system-prompt"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
            />
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <DialogFooter className="pt-2">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? "Saving…" : "Save changes"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteAgentDialog({
  agent,
  onClose,
}: {
  agent: Agent;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: () => deleteAgent(agent.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents", agent.workspace_id] });
      toast.success(`Agent "${agent.name}" deleted`);
      onClose();
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Delete failed");
    },
  });

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete agent &quot;{agent.name}&quot;?</DialogTitle>
          <DialogDescription>
            This permanently removes the agent from this workspace. Tickets already assigned to
            it become unassigned; its runs and history are kept. This cannot be undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={mutation.isPending}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? "Deleting…" : "Delete agent"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AgentMemoryDialog({
  agent,
  workspaceTimezone,
  onClose,
}: {
  agent: Agent;
  workspaceTimezone: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");

  const memory = useQuery({
    queryKey: ["agent-memory", agent.id],
    queryFn: () => listAgentMemory(agent.id),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["agent-memory", agent.id] });

  const createMutation = useMutation({
    mutationFn: () => createAgentMemory(agent.id, { note: note.trim() }),
    onSuccess: () => {
      setNote("");
      invalidate();
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Failed to add memory note");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (memoryId: string) => deleteAgentMemory(memoryId),
    onSuccess: invalidate,
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Failed to delete memory note");
    },
  });

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Memory — {agent.name}</DialogTitle>
          <DialogDescription>
            Catatan lintas tiket yang diingat {agent.name} supaya tidak mengulang kesalahan yang
            sama. Agent menulis catatan lewat laporannya sendiri; kamu bisa menambah atau
            menghapus catatan di sini.
          </DialogDescription>
        </DialogHeader>

        <div className="flex max-h-[50vh] flex-col gap-3 overflow-y-auto">
          {memory.isLoading && <p className="text-sm text-zinc-500">Loading…</p>}
          {!memory.isLoading && (memory.data?.length ?? 0) === 0 && (
            <p className="text-sm text-zinc-500">Belum ada catatan.</p>
          )}
          {memory.data?.map((entry) => (
            <div
              key={entry.id}
              className="flex items-start justify-between gap-3 rounded-lg border border-border p-3"
            >
              <div className="flex flex-col gap-1">
                <p className="text-sm">{entry.note}</p>
                <p className="text-xs text-zinc-500">
                  {entry.origin === "agent" ? "Ditulis agent" : "Ditambah manual"}
                  {entry.source_ticket_key ? ` · ${entry.source_ticket_key}` : ""}
                  {" · "}
                  {formatTimestamp(entry.created_at, workspaceTimezone)}
                </p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                className="shrink-0 text-red-600 hover:text-red-700 hover:bg-red-50 dark:text-red-400 dark:hover:text-red-300 dark:hover:bg-red-950/40"
                disabled={deleteMutation.isPending}
                onClick={() => deleteMutation.mutate(entry.id)}
              >
                Delete
              </Button>
            </div>
          ))}
        </div>

        <form
          className="flex items-start gap-2 border-t border-border pt-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (note.trim()) createMutation.mutate();
          }}
        >
          <Textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Tambah catatan manual, mis. hal yang harus selalu diingat agent ini…"
            className="min-h-16"
          />
          <Button type="submit" disabled={createMutation.isPending || !note.trim()}>
            {createMutation.isPending ? "Adding…" : "Add"}
          </Button>
        </form>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
