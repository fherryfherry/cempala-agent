"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  createRole,
  deleteRole,
  getHealth,
  listRoles,
  updateRole,
  type RoleDef,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export default function GlobalSettingsPage() {
  const health = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const mcp = health.data?.mcp;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-8 px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>

      <RolesCard />

      <Card>
        <CardHeader>
          <CardTitle>MCP</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 text-sm">
          {health.isLoading ? (
            <p className="text-zinc-500">Checking…</p>
          ) : health.isError ? (
            <p className="text-red-600">Backend unreachable.</p>
          ) : (
            <>
              <p>
                Status:{" "}
                <span className={mcp?.enabled ? "text-green-600" : "text-zinc-500"}>
                  {mcp?.enabled ? "enabled" : "disabled"}
                </span>
              </p>
              <p className="text-zinc-500">
                API base: <code className="font-mono">{mcp?.api_base}</code>
              </p>
              <p className="text-xs text-zinc-500">
                Every agent run gets a fresh, local MCP server (stdio, no network exposure)
                proxying these tools to the ticket API — this is global for the whole portal,
                not configurable per workspace.
              </p>
              {mcp?.enabled && mcp.tools.length > 0 && (
                <ul className="flex flex-col gap-2 border-t border-black/[.08] pt-3 dark:border-white/[.145]">
                  {mcp.tools.map((tool) => (
                    <li key={tool.name}>
                      <span className="font-mono text-xs">{tool.name}</span>
                      <p className="text-xs text-zinc-500">{tool.description}</p>
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

const FLAG_LABELS: { key: keyof Pick<RoleDef, "may_declare_tickets" | "may_manage_artifacts" | "is_reviewer">; label: string; hint: string }[] = [
  {
    key: "may_declare_tickets",
    label: "May create tickets",
    hint: "Bisa membuat tiket baru dan mengedit tiket yang sudah ada saat bekerja.",
  },
  {
    key: "may_manage_artifacts",
    label: "May manage artifacts",
    hint: "Bisa mengatur dan mengelompokkan berkas hasil kerja di menu Artifacts.",
  },
  {
    key: "is_reviewer",
    label: "Reviewer",
    hint: "Melihat ringkasan review sebelumnya, supaya tidak mengulang feedback yang sama kalau proses review-nya berulang.",
  },
];

function RolesCard() {
  const queryClient = useQueryClient();
  const roles = useQuery({ queryKey: ["roles"], queryFn: listRoles });
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<RoleDef | null>(null);
  const [deleting, setDeleting] = useState<RoleDef | null>(null);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <div>
          <CardTitle>Roles</CardTitle>
          <p className="mt-1 text-xs text-zinc-500">
            Definisi role global untuk semua workspace — label, prompt default, dan izin.
            PM tidak bisa dihapus dan izinnya terkunci.
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>New role</Button>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">
        {roles.isLoading && <p className="text-sm text-zinc-500">Loading…</p>}
        {roles.isError && (
          <p className="text-sm text-red-600">
            {roles.error instanceof ApiError ? roles.error.message : "Failed to load roles"}
          </p>
        )}
        {roles.data?.map((role) => (
          <div
            key={role.key}
            className="flex items-center gap-3 rounded-lg border border-border px-4 py-3"
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium">{role.name}</span>
                <code className="rounded bg-black/[.04] px-1.5 py-0.5 font-mono text-xs dark:bg-white/[.08]">
                  {role.key}
                </code>
                {role.is_builtin && <Badge variant="outline">built-in</Badge>}
                <span className="text-xs text-zinc-500">
                  {role.agent_count} agent
                </span>
              </div>
              {role.description && (
                <p className="mt-0.5 truncate text-xs text-zinc-500">{role.description}</p>
              )}
            </div>
            <Button variant="ghost" size="sm" onClick={() => setEditing(role)}>
              Edit
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="text-red-600 hover:text-red-700 hover:bg-red-50 dark:text-red-400 dark:hover:text-red-300 dark:hover:bg-red-950/40"
              disabled={role.is_builtin}
              onClick={() => setDeleting(role)}
            >
              Delete
            </Button>
          </div>
        ))}
      </CardContent>

      {(creating || editing) && (
        <RoleDialog
          role={editing}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
        />
      )}

      {deleting && (
        <DeleteRoleDialog
          role={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => {
            queryClient.invalidateQueries({ queryKey: ["roles"] });
            toast.success(`Role "${deleting.name}" deleted`);
            setDeleting(null);
          }}
        />
      )}
    </Card>
  );
}

function RoleFlagCheckbox({
  checked,
  disabled,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint: string;
}) {
  return (
    <label
      className={`flex items-start gap-3 rounded-lg border border-border p-3 ${
        disabled ? "opacity-60" : "cursor-pointer"
      }`}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4"
      />
      <span className="flex flex-col gap-0.5">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-xs text-zinc-500">{hint}</span>
        {disabled && <span className="text-xs text-zinc-500">Built-in — terkunci.</span>}
      </span>
    </label>
  );
}

function RoleDialog({
  role,
  onClose,
}: {
  role: RoleDef | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const isPm = role?.key === "pm";
  const [key, setKey] = useState(role?.key ?? "");
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [systemPrompt, setSystemPrompt] = useState(role?.system_prompt ?? "");
  const [mayDeclareTickets, setMayDeclareTickets] = useState(role?.may_declare_tickets ?? false);
  const [mayManageArtifacts, setMayManageArtifacts] = useState(role?.may_manage_artifacts ?? false);
  const [isReviewer, setIsReviewer] = useState(role?.is_reviewer ?? false);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () =>
      role
        ? updateRole(role.key, {
            name,
            description: description || null,
            system_prompt: systemPrompt || null,
            may_declare_tickets: mayDeclareTickets,
            may_manage_artifacts: mayManageArtifacts,
            is_reviewer: isReviewer,
          })
        : createRole({
            key,
            name,
            description: description || null,
            system_prompt: systemPrompt || null,
            may_declare_tickets: mayDeclareTickets,
            may_manage_artifacts: mayManageArtifacts,
            is_reviewer: isReviewer,
          }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["roles"] });
      toast.success(role ? `Role "${name}" updated` : `Role "${name}" created`);
      onClose();
    },
    onError: (err: unknown) => {
      setError(err instanceof ApiError ? err.message : "Unexpected error");
    },
  });

  const keyValid = /^[a-z][a-z0-9_]*$/.test(key.trim());

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>{role ? `Edit role — ${role.name}` : "New role"}</DialogTitle>
          <DialogDescription>
            Role global dipakai semua workspace. Key tidak bisa diubah setelah dibuat.
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
            <Label htmlFor="role-key">Key (slug, immutable)</Label>
            {role ? (
              <code className="rounded bg-black/[.04] px-2 py-1.5 font-mono text-sm dark:bg-white/[.08]">
                {role.key}
              </code>
            ) : (
              <>
                <Input
                  id="role-key"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  placeholder="scrum_master"
                  required
                />
                {!keyValid && key && (
                  <p className="text-xs text-red-600">
                    Key: huruf kecil + angka/underscore, diawali huruf (mis. scrum_master).
                  </p>
                )}
              </>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="role-name">Name</Label>
            <Input
              id="role-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="role-description">Description (optional)</Label>
            <Input
              id="role-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="role-prompt">System prompt (default)</Label>
            <Textarea
              id="role-prompt"
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={5}
              className="max-h-32 overflow-y-auto transition-[max-height] duration-200 focus:max-h-96"
            />
            <p className="text-xs text-zinc-500">
              Prompt default role — agent dengan system prompt sendiri tetap memakai punyanya.
            </p>
          </div>

          <div className="flex flex-col gap-2">
            <Label>Permissions</Label>
            {FLAG_LABELS.map((f) => {
              const checked =
                f.key === "may_declare_tickets"
                  ? mayDeclareTickets
                  : f.key === "may_manage_artifacts"
                    ? mayManageArtifacts
                    : isReviewer;
              const set = (v: boolean) =>
                f.key === "may_declare_tickets"
                  ? setMayDeclareTickets(v)
                  : f.key === "may_manage_artifacts"
                    ? setMayManageArtifacts(v)
                    : setIsReviewer(v);
              return (
                <RoleFlagCheckbox
                  key={f.key}
                  checked={checked}
                  disabled={isPm}
                  onChange={set}
                  label={f.label}
                  hint={f.hint}
                />
              );
            })}
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}

          <DialogFooter className="pt-2">
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={mutation.isPending || !name.trim() || (!role && !keyValid)}
            >
              {mutation.isPending ? "Saving…" : role ? "Save changes" : "Create role"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DeleteRoleDialog({
  role,
  onClose,
  onDeleted,
}: {
  role: RoleDef;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const mutation = useMutation({
    mutationFn: () => deleteRole(role.key),
    onSuccess: onDeleted,
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Delete failed");
    },
  });

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete role &quot;{role.name}&quot;?</DialogTitle>
          <DialogDescription>
            Menghapus role bersifat permanen. Role yang masih dipakai agent tidak bisa
            dihapus sampai agent-nya dipindah ke role lain.
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
            {mutation.isPending ? "Deleting…" : "Delete role"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
