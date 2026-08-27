"use client";

import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ApiError, createAgent, updateWorkspace, type ToolKind } from "@/lib/api";
import type { TemplateSlot } from "@/lib/agent-templates";

interface ChecklistItem {
  label: string;
  status: "pending" | "running" | "done" | "failed";
  error?: string;
}

function errorMessage(err: unknown): string {
  return err instanceof ApiError ? err.message : "Unexpected error";
}

/** Provisions the workspace description + agent squad, then hands off to the PM chat
 * (`onDone`) — it no longer creates a ticket directly; the PM proposes epic/sprint/
 * tickets itself once the owner is chatting with it (see PmHandoff). */
export function BuildingScreen({
  workspaceId,
  description,
  slots,
  names,
  toolKind,
  model,
  onDone,
}: {
  workspaceId: string;
  description: string;
  slots: TemplateSlot[];
  names: string[];
  toolKind: ToolKind;
  model: string;
  onDone: (pm: { id: string; name: string } | null) => void;
}) {
  const started = useRef(false);
  const [items, setItems] = useState<ChecklistItem[]>([
    { label: "Menyimpan deskripsi workspace", status: "pending" },
    ...slots.map((s) => ({ label: `Menyiapkan agent ${s.label}`, status: "pending" as const })),
  ]);

  const setStatus = (i: number, status: ChecklistItem["status"], error?: string) =>
    setItems((prev) => prev.map((it, idx) => (idx === i ? { ...it, status, error } : it)));

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    (async () => {
      const failures: string[] = [];
      let pm: { id: string; name: string } | null = null;

      setStatus(0, "running");
      try {
        await updateWorkspace(workspaceId, { description });
        setStatus(0, "done");
      } catch (err) {
        setStatus(0, "failed", errorMessage(err));
        failures.push(`deskripsi workspace (${errorMessage(err)})`);
      }

      for (let i = 0; i < slots.length; i++) {
        const idx = i + 1;
        const slot = slots[i];
        setStatus(idx, "running");
        try {
          const agent = await createAgent(workspaceId, {
            name: names[i],
            role: slot.role,
            model,
            tool_kind: toolKind,
            avatar_template: slot.avatar_template,
            avatar_color: slot.avatar_color,
          });
          if (slot.role === "pm") pm = { id: agent.id, name: agent.name };
          setStatus(idx, "done");
        } catch (err) {
          setStatus(idx, "failed", errorMessage(err));
          failures.push(`agent ${slot.label} (${errorMessage(err)})`);
        }
      }

      if (failures.length > 0) {
        toast.error(`Beberapa langkah gagal: ${failures.join(", ")}. Bisa dilengkapi lagi dari Settings/Agents.`);
      }
      onDone(pm);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-8 px-6 py-10">
      <p className="animate-pulse text-xl font-semibold tracking-tight">
        Membangun workspace Anda…
      </p>
      <ul className="flex w-full max-w-sm flex-col gap-2">
        {items.map((item, i) => (
          <li key={i} className="flex flex-col gap-0.5 text-sm">
            <span className="flex items-center gap-3">
              <StatusIcon status={item.status} />
              <span
                className={
                  item.status === "pending" ? "text-zinc-400" : "text-foreground"
                }
              >
                {item.label}
              </span>
            </span>
            {item.error && (
              <span className="pl-7 text-xs text-red-600">{item.error}</span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

function StatusIcon({ status }: { status: ChecklistItem["status"] }) {
  if (status === "done") return <span className="text-emerald-600">✓</span>;
  if (status === "failed") return <span className="text-red-600">✗</span>;
  if (status === "running")
    return <span className="animate-spin text-zinc-500">◌</span>;
  return <span className="text-zinc-300">○</span>;
}
