"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  createTicket,
  formatAgentName,
  listAgents,
  listSprints,
  listTickets,
  listWorkspaces,
  updateTicket,
  type TicketCategory,
  type TicketPriority,
  type TicketStatus,
} from "@/lib/api";
import { CATEGORY_LABELS, CATEGORY_VARIANT, PRIORITY_VARIANT } from "@/lib/ticket-style";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { AgentStatusDot } from "@/components/agent-status";
import { AgentAvatar } from "@/components/agent-avatar";
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
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

const COLUMNS: { status: TicketStatus; label: string }[] = [
  { status: "backlog", label: "Backlog" },
  { status: "todo", label: "Todo" },
  { status: "in_progress", label: "In Progress" },
  { status: "review", label: "Review" },
  { status: "qa", label: "QA" },
  { status: "security", label: "Security" },
  { status: "done", label: "Done" },
  { status: "release", label: "Release" },
  { status: "blocked", label: "Blocked" },
];

const ALL_SPRINTS = "__all__";

export default function BoardPage() {
  const params = useParams<{ key: string }>();
  const workspaceKey = params.key;
  const queryClient = useQueryClient();

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });
  const workspace = workspaces.data?.find((ws) => ws.key === workspaceKey);

  const tickets = useQuery({
    queryKey: ["tickets", workspace?.id],
    queryFn: () => listTickets(workspace!.id),
    enabled: !!workspace,
  });

  const agents = useQuery({
    queryKey: ["agents", workspace?.id],
    queryFn: () => listAgents(workspace!.id),
    enabled: !!workspace,
  });

  const sprints = useQuery({
    queryKey: ["sprints", workspace?.id],
    queryFn: () => listSprints(workspace!.id),
    enabled: !!workspace,
  });

  const [draggedKey, setDraggedKey] = useState<string | null>(null);
  // `null` = no explicit user choice yet -> default to the active sprint (falling back
  // to "all") until the user picks something themselves, without needing an effect.
  const [sprintOverride, setSprintOverride] = useState<string | null>(null);
  const activeSprintId = sprints.data?.find((s) => s.status === "active")?.id;
  const selectedSprintId = sprintOverride ?? activeSprintId ?? ALL_SPRINTS;

  const moveMutation = useMutation({
    mutationFn: (vars: { key: string; status: TicketStatus }) =>
      updateTicket(vars.key, { status: vars.status }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tickets", workspace?.id] });
    },
    onError: (err: unknown) => {
      const message = err instanceof ApiError ? err.message : "Unexpected error";
      toast.error(message);
      // card was never optimistically moved, so no revert needed — just refetch
      // to be safe in case of stale cache.
      queryClient.invalidateQueries({ queryKey: ["tickets", workspace?.id] });
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

  const agentDisplay = (id: string | null) => {
    const a = agents.data?.find((x) => x.id === id);
    return a ? formatAgentName(a.name, a.role) : null;
  };
  const agentStatus = (id: string | null) =>
    id ? agents.data?.find((a) => a.id === id)?.status : undefined;
  const agentOf = (id: string | null) => agents.data?.find((x) => x.id === id);
  const sprintName = (id: string | null) => sprints.data?.find((s) => s.id === id)?.name ?? null;

  const visibleTickets = (tickets.data ?? []).filter(
    (t) => selectedSprintId === ALL_SPRINTS || t.sprint_id === selectedSprintId,
  );

  const orderedSprints = [...(sprints.data ?? [])].sort((a, b) =>
    a.status === "active" ? -1 : b.status === "active" ? 1 : a.index - b.index,
  );
  const SPRINT_LIMIT = 20;
  const visibleSprints = orderedSprints.slice(0, SPRINT_LIMIT);
  const overflowSprints = orderedSprints.slice(SPRINT_LIMIT);

  return (
    <div className="flex w-full flex-1 flex-col gap-6 px-6 py-10">
      <div className="flex items-center justify-between gap-6">
        <h1 className="shrink-0 text-2xl font-semibold tracking-tight">Board</h1>
        <div className="flex min-w-0 flex-1 items-center gap-4">
          <div className="-mb-3 flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto pb-3">
            <Button
              size="sm"
              variant={selectedSprintId === ALL_SPRINTS ? "default" : "outline"}
              onClick={() => setSprintOverride(ALL_SPRINTS)}
            >
              All sprints
            </Button>
            {visibleSprints.map((s) => (
              <Button
                key={s.id}
                size="sm"
                variant={selectedSprintId === s.id ? "default" : "outline"}
                onClick={() => setSprintOverride(s.id)}
              >
                {s.name}
                {s.status === "active" && " •"}
              </Button>
            ))}
            {overflowSprints.length > 0 && (
              <Popover>
                <PopoverTrigger
                  render={
                    <Button size="sm" variant="outline">
                      Lihat Lainnya
                    </Button>
                  }
                />
                <PopoverContent className="w-56 p-1">
                  {overflowSprints.map((s) => (
                    <button
                      key={s.id}
                      className="w-full cursor-pointer rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted"
                      onClick={() => setSprintOverride(s.id)}
                    >
                      {s.name}
                      {s.status === "active" ? " (active)" : ""}
                    </button>
                  ))}
                </PopoverContent>
              </Popover>
            )}
          </div>
          <CreateTicketDialog workspaceId={workspace.id} />
        </div>
      </div>

      <div className="flex flex-1 gap-4 overflow-x-auto pb-4">
        {COLUMNS.map((col) => {
          const colTickets = visibleTickets.filter((t) => t.status === col.status);
          return (
            <div
              key={col.status}
              className="flex w-64 shrink-0 flex-col gap-3 rounded-lg bg-zinc-50 p-3 dark:bg-zinc-900/40"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                if (!draggedKey) return;
                moveMutation.mutate({ key: draggedKey, status: col.status });
                setDraggedKey(null);
              }}
            >
              <div className="flex items-center justify-between px-1">
                <h2 className="text-sm font-medium">{col.label}</h2>
                <span className="text-xs text-zinc-400">{colTickets.length}</span>
              </div>

              <div className="flex flex-col gap-2">
                {colTickets.map((t) => {
                  return (
                    <Card
                      key={t.id}
                      draggable
                      onDragStart={() => setDraggedKey(t.key)}
                      onDragEnd={() => setDraggedKey(null)}
                      className="gap-2 py-3 cursor-grab active:cursor-grabbing"
                    >
                      <CardHeader className="px-3">
                        <CardTitle className="flex items-center justify-between gap-2 text-xs font-mono text-zinc-400">
                          <Link
                            href={`/w/${workspaceKey}/ticket/${t.key}`}
                            className="hover:text-foreground hover:underline"
                            draggable={false}
                            onClick={(e) => e.stopPropagation()}
                          >
                            {t.key}
                          </Link>
                          <span className="flex items-center gap-1">
                            {t.category && (
                              <Badge variant={CATEGORY_VARIANT[t.category]}>
                                {CATEGORY_LABELS[t.category]}
                              </Badge>
                            )}
                            <Badge variant={PRIORITY_VARIANT[t.priority]}>{t.priority}</Badge>
                          </span>
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="flex flex-col gap-1 px-3 text-sm">
                        <span>{t.title}</span>
                        {sprintName(t.sprint_id) && (
                          <Badge variant="outline" className="w-fit px-1.5 py-0 text-[10px]">
                            {sprintName(t.sprint_id)}
                          </Badge>
                        )}
                        {agentDisplay(t.assignee_id) && (
                          <span className="flex items-center gap-1.5 text-xs text-zinc-500">
                            {agentOf(t.assignee_id) && (
                              <AgentAvatar
                                name={agentOf(t.assignee_id)!.name}
                                template={agentOf(t.assignee_id)!.avatar_template}
                                color={agentOf(t.assignee_id)!.avatar_color}
                                size={16}
                              />
                            )}
                            {agentStatus(t.assignee_id) && (
                              <AgentStatusDot status={agentStatus(t.assignee_id)!} />
                            )}
                            {agentDisplay(t.assignee_id)}
                          </span>
                        )}
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// Sentinel Select value for "this ticket starts a brand-new epic" — every ticket
// otherwise needs a real parent (epic), see backend's is_new_epic/parent_id gate.
const NEW_EPIC = "__new_epic__";

function CreateTicketDialog({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<TicketPriority>("medium");
  const [category, setCategory] = useState<TicketCategory | "">("");
  const [assigneeId, setAssigneeId] = useState<string>("");
  const [parentChoice, setParentChoice] = useState<string>("");

  const agents = useQuery({
    queryKey: ["agents", workspaceId],
    queryFn: () => listAgents(workspaceId),
  });
  const tickets = useQuery({
    queryKey: ["tickets", workspaceId],
    queryFn: () => listTickets(workspaceId),
  });
  // Epics are meant to be reusable feature-area containers (docs/03-agent-design.md
  // §3, ADR-012) — surface open ones first so the picker nudges toward reuse instead
  // of spawning a fresh epic per request.
  const epicCandidates = [...(tickets.data ?? [])]
    .filter((t) => !t.parent_id)
    .sort((a, b) => {
      const aOpen = a.status !== "done" && a.status !== "release" ? 0 : 1;
      const bOpen = b.status !== "done" && b.status !== "release" ? 0 : 1;
      return aOpen !== bOpen ? aOpen - bOpen : a.title.localeCompare(b.title);
    });
  const epicChildCount = (epicId: string) =>
    (tickets.data ?? []).filter((t) => t.parent_id === epicId).length;

  const mutation = useMutation({
    mutationFn: () =>
      createTicket(workspaceId, {
        title,
        description: description || undefined,
        priority,
        category: category || undefined,
        assignee_id: assigneeId || undefined,
        ...(parentChoice === NEW_EPIC
          ? { is_new_epic: true }
          : { parent_id: parentChoice }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tickets", workspaceId] });
      setTitle("");
      setDescription("");
      setPriority("medium");
      setCategory("");
      setAssigneeId("");
      setParentChoice("");
      setOpen(false);
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Unexpected error");
    },
  });

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={<Button>Create ticket</Button>} />
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create ticket</DialogTitle>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ticket-title">Title</Label>
            <Input
              id="ticket-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="ticket-description">Description</Label>
            <Textarea
              id="ticket-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Priority</Label>
            <Select value={priority} onValueChange={(v) => setPriority(v as TicketPriority)}>
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(["low", "medium", "high", "urgent"] as TicketPriority[]).map((p) => (
                  <SelectItem key={p} value={p}>
                    {p}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Category</Label>
            <Select value={category} onValueChange={(v) => setCategory((v as TicketCategory) ?? "")}>
              <SelectTrigger>
                <SelectValue placeholder="Uncategorized" />
              </SelectTrigger>
              <SelectContent>
                {(["feature", "improvement", "fix", "security", "performance"] as TicketCategory[]).map(
                  (c) => (
                    <SelectItem key={c} value={c}>
                      {c}
                    </SelectItem>
                  ),
                )}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Assignee</Label>
            <Select value={assigneeId} onValueChange={(v) => setAssigneeId(v ?? "")}>
              <SelectTrigger>
                <SelectValue placeholder="Unassigned">
                  {(value: string) => {
                    const a = (agents.data ?? []).find((x) => x.id === value);
                    return a ? `${a.name} (${a.role})` : "Unassigned";
                  }}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                {(agents.data ?? []).map((a) => (
                  <SelectItem key={a.id} value={a.id}>
                    {a.name} ({a.role})
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Epic</Label>
            <Select value={parentChoice} onValueChange={(v) => setParentChoice(v ?? "")}>
              <SelectTrigger>
                <SelectValue placeholder="Pilih epic…">
                  {(value: string) => {
                    if (value === NEW_EPIC) return "New epic (no parent)";
                    const t = epicCandidates.find((e) => e.id === value);
                    return t ? `${t.key} — ${t.title}` : "Pilih epic…";
                  }}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NEW_EPIC}>New epic (no parent)</SelectItem>
                {epicCandidates.map((t) => (
                  <SelectItem key={t.id} value={t.id}>
                    {t.key} — {t.title} ({epicChildCount(t.id)} tiket)
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-xs text-zinc-500">
              Epic = area fitur besar yang bisa dipakai berkali-kali. Pilih yang sudah ada
              kalau cocok — bikin baru hanya untuk area yang benar-benar baru.
            </p>
          </div>

          <DialogFooter>
            <Button type="submit" disabled={mutation.isPending || !title || !parentChoice}>
              {mutation.isPending ? "Creating…" : "Create ticket"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
