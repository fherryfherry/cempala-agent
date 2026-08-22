"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  createTicket,
  listAgents,
  listTickets,
  listWorkspaces,
  updateTicket,
  type Ticket,
  type TicketPriority,
  type TicketStatus,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
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

const COLUMNS: { status: TicketStatus; label: string }[] = [
  { status: "backlog", label: "Backlog" },
  { status: "todo", label: "Todo" },
  { status: "in_progress", label: "In Progress" },
  { status: "review", label: "Review" },
  { status: "qa", label: "QA" },
  { status: "security", label: "Security" },
  { status: "done", label: "Done" },
  { status: "blocked", label: "Blocked" },
];

const PRIORITY_VARIANT: Record<TicketPriority, "outline" | "secondary" | "destructive" | "default"> = {
  low: "outline",
  medium: "secondary",
  high: "default",
  urgent: "destructive",
};

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

  const [draggedKey, setDraggedKey] = useState<string | null>(null);

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

  const agentName = (id: string | null) =>
    id ? (agents.data?.find((a) => a.id === id)?.name ?? id) : null;

  return (
    <div className="flex w-full flex-1 flex-col gap-6 px-6 py-10">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{workspace.name} — Board</h1>
          <p className="mt-1 text-sm text-zinc-500">{workspace.repo_path}</p>
        </div>
        <CreateTicketDialog workspaceId={workspace.id} />
      </div>

      <div className="flex flex-1 gap-4 overflow-x-auto pb-4">
        {COLUMNS.map((col) => {
          const colTickets = (tickets.data ?? []).filter((t) => t.status === col.status);
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
                {colTickets.map((t) => (
                  <Card
                    key={t.id}
                    draggable
                    onDragStart={() => setDraggedKey(t.key)}
                    onDragEnd={() => setDraggedKey(null)}
                    className="cursor-grab gap-2 py-3 active:cursor-grabbing"
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
                        <Badge variant={PRIORITY_VARIANT[t.priority]}>{t.priority}</Badge>
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="flex flex-col gap-1 px-3 text-sm">
                      <span>{t.title}</span>
                      {agentName(t.assignee_id) && (
                        <span className="text-xs text-zinc-500">
                          {agentName(t.assignee_id)}
                        </span>
                      )}
                    </CardContent>
                  </Card>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CreateTicketDialog({ workspaceId }: { workspaceId: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [priority, setPriority] = useState<TicketPriority>("medium");
  const [assigneeId, setAssigneeId] = useState<string>("");

  const agents = useQuery({
    queryKey: ["agents", workspaceId],
    queryFn: () => listAgents(workspaceId),
  });

  const mutation = useMutation({
    mutationFn: () =>
      createTicket(workspaceId, {
        title,
        description: description || undefined,
        priority,
        assignee_id: assigneeId || undefined,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tickets", workspaceId] });
      setTitle("");
      setDescription("");
      setPriority("medium");
      setAssigneeId("");
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
            <Label>Assignee</Label>
            <Select value={assigneeId} onValueChange={(v) => setAssigneeId(v ?? "")}>
              <SelectTrigger>
                <SelectValue placeholder="Unassigned" />
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

          <DialogFooter>
            <Button type="submit" disabled={mutation.isPending || !title}>
              {mutation.isPending ? "Creating…" : "Create ticket"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
