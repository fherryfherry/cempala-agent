"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { DownloadIcon } from "lucide-react";
import {
  attachmentUrl,
  listArtifacts,
  listWorkspaces,
  type ArtifactAttachment,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatTimestamp } from "@/lib/datetime";
import { AttachmentPreviewDialog } from "@/components/attachment-preview";
export default function ArtifactsPage() {
  const params = useParams<{ key: string }>();
  const workspaceKey = params.key;

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });
  const workspace = workspaces.data?.find((ws) => ws.key === workspaceKey);

  const artifacts = useQuery({
    queryKey: ["artifacts", workspace?.id],
    queryFn: () => listArtifacts(workspace!.id),
    enabled: !!workspace,
  });

  const [selected, setSelected] = useState<ArtifactAttachment | null>(null);
  const [activeGroupId, setActiveGroupId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  // Per-group pagination: newest 6 first, "Tampilkan lebih banyak" reveals 6 more.
  const [visibleCounts, setVisibleCounts] = useState<Record<string, number>>({});
  const groupKey = (id: string | null) => id ?? "ungrouped";

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

  const groups = artifacts.data ?? [];
  const q = search.trim().toLowerCase();
  const visibleGroups =
    activeGroupId === null ? groups : groups.filter((g) => g.id === activeGroupId);

  const renderIndex = (inCard: boolean) => (
    <ul className={inCard ? "flex gap-1 overflow-x-auto" : "flex flex-col gap-1"}>
      <li>
        <button
          type="button"
          onClick={() => setActiveGroupId(null)}
          className={`cursor-pointer whitespace-nowrap rounded-md px-2 py-1 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800 ${
            activeGroupId === null
              ? "bg-zinc-100 font-medium text-foreground dark:bg-zinc-800"
              : "text-zinc-600 dark:text-zinc-400"
          }`}
        >
          Semua
        </button>
      </li>
      {groups.map((g) => {
        const key = groupKey(g.id);
        const active = activeGroupId === key;
        return (
          <li key={key}>
            <button
              type="button"
              onClick={() => setActiveGroupId(active ? null : key)}
              className={`cursor-pointer whitespace-nowrap rounded-md px-2 py-1 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800 ${
                active
                  ? "bg-zinc-100 font-medium text-foreground dark:bg-zinc-800"
                  : "text-zinc-600 dark:text-zinc-400"
              }`}
            >
              {g.name}
            </button>
          </li>
        );
      })}
    </ul>
  );

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-6 px-6 py-10">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Artifacts</h1>
        <p className="mt-1 text-sm text-zinc-500">
          File yang dihasilkan agent, dikelompokkan otomatis.
        </p>
      </div>

      <input
        type="search"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="Cari artifact (nama file, deskripsi, tiket)…"
        className="w-full rounded-md border border-zinc-200 bg-background px-3 py-2 text-sm outline-none focus:border-zinc-400 dark:border-zinc-800 dark:focus:border-zinc-600"
      />

      {artifacts.isLoading && <p className="text-sm text-zinc-500">Loading…</p>}

      {!artifacts.isLoading && groups.length === 0 && (
        <p className="text-sm text-zinc-400">Belum ada artifact yang dihasilkan agent.</p>
      )}

      <div className="flex items-start gap-6">
        <aside className="sticky top-16 hidden w-44 shrink-0 md:block">
          <p className="mb-2 px-2 text-xs font-medium tracking-wide text-zinc-400 uppercase">
            Kelompok
          </p>
          {renderIndex(false)}
        </aside>

        <div className="flex min-w-0 flex-1 flex-col gap-6">
          {groups.length > 0 && (
            <div className="md:hidden">
              <p className="mb-2 text-xs font-medium tracking-wide text-zinc-400 uppercase">
                Kelompok
              </p>
              {renderIndex(true)}
            </div>
          )}

          {visibleGroups.map((group) => {
            const key = groupKey(group.id);
            const ordered = [...group.attachments]
              .filter((a) => {
                if (!q) return true;
                return (
                  a.filename.toLowerCase().includes(q) ||
                  (a.description ?? "").toLowerCase().includes(q) ||
                  a.ticket_key.toLowerCase().includes(q) ||
                  a.ticket_title.toLowerCase().includes(q)
                );
              })
              .sort((a, b) => b.created_at.localeCompare(a.created_at));
            const hidden = Math.max(0, ordered.length - (visibleCounts[key] ?? 6));
            const visible = ordered.slice(0, visibleCounts[key] ?? 6);
            return (
              <Card key={key}>
                <CardHeader>
                  <CardTitle className="text-sm">{group.name}</CardTitle>
                </CardHeader>
                <CardContent className="flex flex-col gap-2">
                  {ordered.length === 0 && q && (
                    <p className="text-sm text-zinc-400">
                      Tidak ada artifact yang cocok dengan &quot;{search.trim()}&quot;.
                    </p>
                  )}
                  {hidden > 0 && (
                    <button
                      type="button"
                      className="self-center rounded-md border border-black/10 px-3 py-1 text-xs text-zinc-500 hover:bg-zinc-100 dark:border-white/10 dark:hover:bg-zinc-800"
                      onClick={() =>
                        setVisibleCounts((s) => ({ ...s, [key]: (s[key] ?? 6) + 6 }))
                      }
                    >
                      Tampilkan lebih banyak ({hidden} file lagi)
                    </button>
                  )}
                  {visible.map((a) => (
              <div
                key={a.id}
                className="flex items-center justify-between gap-2 border-b border-black/5 pb-2 text-sm last:border-b-0 last:pb-0 dark:border-white/5"
              >
                <div className="min-w-0">
                  <button
                    type="button"
                    onClick={() => setSelected(a)}
                    className="cursor-pointer truncate text-left underline-offset-4 hover:underline"
                  >
                    {a.filename}
                  </button>
                  {a.description && (
                    <p className="mt-0.5 truncate text-xs text-zinc-500">{a.description}</p>
                  )}
                  <div className="mt-0.5 flex items-center gap-2 text-xs text-zinc-400">
                    <Link
                      href={`/w/${workspaceKey}/ticket/${a.ticket_key}`}
                      className="font-mono hover:underline"
                    >
                      {a.ticket_key}
                    </Link>
                    <span className="truncate">{a.ticket_title}</span>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Badge variant="outline" className="text-[10px]">
                    {formatTimestamp(a.created_at, workspace.timezone)}
                  </Badge>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    nativeButton={false}
                    render={
                      <a href={attachmentUrl(a.id)} download aria-label={`Download ${a.filename}`} />
                    }
                  >
                    <DownloadIcon className="size-3.5" />
                  </Button>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>
      );
      })}
        </div>
      </div>

      {selected && (
        <AttachmentPreviewDialog attachment={selected} onClose={() => setSelected(null)} />
      )}
    </div>
  );
}
