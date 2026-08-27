"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FolderIcon, ArrowUpIcon } from "lucide-react";
import { ApiError, browseFs } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

/** Folder picker over the host filesystem (GET /api/fs/browse) — lets the user click
 * through directories instead of typing an absolute repo path by hand. */
export function FolderBrowser({
  onSelect,
  onClose,
}: {
  onSelect: (path: string) => void;
  onClose: () => void;
}) {
  const [path, setPath] = useState<string | undefined>(undefined);

  const browse = useQuery({
    queryKey: ["fs-browse", path],
    queryFn: () => browseFs(path),
  });

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>Pilih folder repo</DialogTitle>
          <DialogDescription>
            Klik folder untuk masuk. Pilih folder saat ini kalau sudah di lokasi yang benar.
          </DialogDescription>
        </DialogHeader>

        <div className="flex min-w-0 flex-col gap-2">
          <p className="min-w-0 overflow-x-auto rounded-md bg-muted px-2.5 py-1.5 font-mono text-xs whitespace-nowrap">
            {browse.data?.path ?? "…"}
          </p>

          <div className="flex max-h-64 flex-col gap-0.5 overflow-y-auto">
            {browse.isLoading && <p className="px-2 py-1 text-sm text-zinc-500">Memuat…</p>}
            {browse.isError && (
              <p className="px-2 py-1 text-sm text-red-600">
                {browse.error instanceof ApiError ? browse.error.message : "Gagal memuat folder"}
              </p>
            )}
            {browse.data?.parent && (
              <button
                type="button"
                onClick={() => setPath(browse.data!.parent!)}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted"
              >
                <ArrowUpIcon className="size-4 shrink-0 text-zinc-400" />
                ..
              </button>
            )}
            {browse.data?.dirs.map((d) => (
              <button
                key={d.path}
                type="button"
                onClick={() => setPath(d.path)}
                className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted"
              >
                <FolderIcon className="size-4 shrink-0 text-zinc-400" />
                {d.name}
              </button>
            ))}
            {browse.data && browse.data.dirs.length === 0 && (
              <p className="px-2 py-1 text-sm text-zinc-500">Tidak ada subfolder di sini.</p>
            )}
          </div>
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={onClose}>
            Batal
          </Button>
          <Button
            disabled={!browse.data}
            onClick={() => browse.data && onSelect(browse.data.path)}
          >
            Pilih folder ini
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
