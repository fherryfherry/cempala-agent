"use client";

import { useState } from "react";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { PlayIcon, PlusIcon, TrashIcon } from "lucide-react";
import {
  ApiError,
  createRoutine,
  deleteRoutine,
  listAgents,
  listRoutines,
  listWorkspaces,
  runRoutineNow,
  updateRoutine,
  type Routine,
  type RoutineCreate,
  type RoutineMode,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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
} from "@/components/ui/dialog";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatTimestamp } from "@/lib/datetime";

const MODE_LABELS: Record<RoutineMode, string> = {
  idle_only: "Saat idle only",
  consistent: "Konsisten",
};

const STATUS_VARIANT: Record<string, "outline" | "secondary" | "default" | "destructive"> = {
  idle: "secondary",
  waiting: "default",
  running: "default",
  disabled: "outline",
};

export default function RoutinesPage() {
  const params = useParams<{ key: string }>();
  const workspaceKey = params.key;

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });
  const workspace = workspaces.data?.find((ws) => ws.key === workspaceKey);

  const routines = useQuery({
    queryKey: ["routines", workspace?.id],
    queryFn: () => listRoutines(workspace!.id),
    enabled: !!workspace,
  });

  const agents = useQuery({
    queryKey: ["agents", workspace?.id],
    queryFn: () => listAgents(workspace!.id),
    enabled: !!workspace,
  });

  const [editing, setEditing] = useState<Routine | null>(null);
  const [creating, setCreating] = useState(false);

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

  const agentName = (id: string | null) => agents.data?.find((a) => a.id === id)?.name ?? "—";

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-6 py-10">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Rutinitas</h1>
          <p className="mt-1 text-sm text-zinc-500">
            Tugas terjadwal yang menjalankan agent tanpa tiket — agent bebas komen ke tiket
            lain, buat backlog, update tiket, atau simpan memory.
          </p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <PlusIcon className="mr-1.5 size-4" /> Tambah rutinitas
        </Button>
      </div>

      {routines.isLoading && <p className="text-sm text-zinc-500">Loading…</p>}

      {!routines.isLoading && (routines.data ?? []).length === 0 && (
        <p className="text-sm text-zinc-400">Belum ada rutinitas. Tambahkan yang pertama.</p>
      )}

      <div className="flex flex-col gap-4">
        {(routines.data ?? []).map((r) => (
          <RoutineCard
            key={r.id}
            routine={r}
            agentName={agentName(r.agent_id)}
            onEdit={() => setEditing(r)}
          />
        ))}
      </div>

      {(creating || editing) && (
        <RoutineFormDialog
          workspaceId={workspace.id}
          routine={editing}
          agents={agents.data ?? []}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
        />
      )}
    </div>
  );
}

function RoutineCard({
  routine,
  agentName,
  onEdit,
}: {
  routine: Routine;
  agentName: string;
  onEdit: () => void;
}) {
  const queryClient = useQueryClient();
  const [confirmDelete, setConfirmDelete] = useState(false);

  const runNow = useMutation({
    mutationFn: () => runRoutineNow(routine.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["routines"] });
      toast.success("Rutinitas dijalankan");
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Gagal menjalankan rutinitas");
    },
  });

  const toggle = useMutation({
    mutationFn: () =>
      updateRoutine(routine.id, {
        status: routine.status === "disabled" ? "idle" : "disabled",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["routines"] });
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Gagal mengubah status");
    },
  });

  const remove = useMutation({
    mutationFn: () => deleteRoutine(routine.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["routines"] });
      toast.success("Rutinitas dihapus");
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Gagal menghapus rutinitas");
    },
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-sm">{routine.name}</CardTitle>
          <div className="flex items-center gap-2">
            <Badge variant={STATUS_VARIANT[routine.status]} className="text-[10px]">
              {routine.status}
            </Badge>
            <Badge variant="outline" className="text-[10px]">
              {MODE_LABELS[routine.mode]}
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="whitespace-pre-wrap text-sm text-zinc-600 dark:text-zinc-300">
          {routine.prompt}
        </p>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-400">
          <span>Agent: {agentName}</span>
          <span>Interval: {routine.interval_minutes} menit</span>
          <span>
            Terakhir jalan: {routine.last_run_at ? formatTimestamp(routine.last_run_at) : "belum"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => runNow.mutate()}
            disabled={runNow.isPending || routine.status === "disabled"}
          >
            <PlayIcon className="mr-1 size-3.5" /> Run now
          </Button>
          <Button variant="outline" size="sm" onClick={onEdit}>
            Edit
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => toggle.mutate()}
            disabled={toggle.isPending}
          >
            {routine.status === "disabled" ? "Aktifkan" : "Nonaktifkan"}
          </Button>
          <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
            <Button
              variant="ghost"
              size="sm"
              className="text-red-600"
              onClick={() => setConfirmDelete(true)}
            >
              <TrashIcon className="mr-1 size-3.5" /> Hapus
            </Button>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Hapus rutinitas &quot;{routine.name}&quot;?</DialogTitle>
              </DialogHeader>
              <p className="text-sm text-zinc-600">
                Rutinitas dan riwayat run-nya akan dihapus. Tidak bisa dibatalkan.
              </p>
              <DialogFooter>
                <Button variant="outline" onClick={() => setConfirmDelete(false)}>
                  Batal
                </Button>
                <Button
                  variant="destructive"
                  disabled={remove.isPending}
                  onClick={() => {
                    setConfirmDelete(false);
                    remove.mutate();
                  }}
                >
                  {remove.isPending ? "Menghapus…" : "Hapus"}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </CardContent>
    </Card>
  );
}

function RoutineFormDialog({
  workspaceId,
  routine,
  agents,
  onClose,
}: {
  workspaceId: string;
  routine: Routine | null;
  agents: { id: string; name: string; role: string }[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState(routine?.name ?? "");
  const [prompt, setPrompt] = useState(routine?.prompt ?? "");
  const [intervalMinutes, setIntervalMinutes] = useState(String(routine?.interval_minutes ?? 30));
  const [mode, setMode] = useState<RoutineMode>(routine?.mode ?? "idle_only");
  const [agentId, setAgentId] = useState<string>(routine?.agent_id ?? agents[0]?.id ?? "");

  const mutation = useMutation({
    mutationFn: () => {
      const body: RoutineCreate = {
        name: name.trim(),
        prompt: prompt.trim(),
        interval_minutes: Math.max(1, Number(intervalMinutes) || 1),
        mode,
        agent_id: agentId || null,
      };
      return routine
        ? updateRoutine(routine.id, body)
        : createRoutine(workspaceId, body);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["routines"] });
      toast.success(routine ? "Rutinitas diperbarui" : "Rutinitas dibuat");
      onClose();
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Gagal menyimpan rutinitas");
    },
  });

  const valid = name.trim() && prompt.trim() && agentId;

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{routine ? "Edit rutinitas" : "Tambah rutinitas"}</DialogTitle>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (valid) mutation.mutate();
          }}
        >
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="routine-name">Nama</Label>
            <Input
              id="routine-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="mis. Cek tiket macet"
              required
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="routine-prompt">Prompt tugas</Label>
            <Textarea
              id="routine-prompt"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              placeholder="mis. Cek semua tiket yang tidak bergerak lebih dari 2 hari dan komen ke tiketnya."
              required
            />
            <p className="text-xs text-zinc-500">
              Agent menjalankan ini tanpa tiket. Ia bisa komen ke tiket lain, buat backlog,
              update tiket, atau simpan memory lewat blok ```map.
            </p>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="routine-interval">Interval (menit)</Label>
              <Input
                id="routine-interval"
                type="number"
                min="1"
                value={intervalMinutes}
                onChange={(e) => setIntervalMinutes(e.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Mode</Label>
              <Select value={mode} onValueChange={(v) => v && setMode(v as RoutineMode)}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="idle_only">Saat idle only</SelectItem>
                  <SelectItem value="consistent">Konsisten</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label>Agent</Label>
              <Select value={agentId} onValueChange={(v) => v && setAgentId(v)}>
                <SelectTrigger>
                  <SelectValue placeholder="Pilih agent">
                    {agents.find((a) => a.id === agentId)?.name ?? "Pilih agent"}
                  </SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {agents.map((a) => (
                    <SelectItem key={a.id} value={a.id}>
                      {a.name} ({a.role})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <p className="text-xs text-zinc-500">
            <strong>Saat idle only</strong>: hanya jalan kalau agent sedang tidak mengerjakan
            run lain (dilewati kalau sibuk). <strong>Konsisten</strong>: antre di belakang run
            agent yang sedang berjalan, tidak pernah terlewat.
          </p>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Batal
            </Button>
            <Button type="submit" disabled={mutation.isPending || !valid}>
              {mutation.isPending ? "Menyimpan…" : "Simpan"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
