"use client";

import { useQuery } from "@tanstack/react-query";
import { DownloadIcon } from "lucide-react";
import { attachmentUrl, type Attachment } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Markdown } from "@/components/markdown";

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

type Previewable = Pick<Attachment, "id" | "filename" | "content_type" | "size_bytes"> & {
  ticket_key?: string;
};

export function isMarkdown(a: Previewable): boolean {
  const name = a.filename.toLowerCase();
  return (
    a.content_type === "text/markdown" ||
    name.endsWith(".md") ||
    name.endsWith(".markdown")
  );
}

export function isText(a: Previewable): boolean {
  if (isMarkdown(a)) return true;
  const name = a.filename.toLowerCase();
  return (
    a.content_type.startsWith("text/") ||
    ["application/json", "application/xml", "application/javascript"].includes(a.content_type) ||
    [".txt", ".log", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini"].some(
      (ext) => name.endsWith(ext),
    )
  );
}

export function isImage(a: Previewable): boolean {
  return a.content_type.startsWith("image/");
}

export function isPdf(a: Previewable): boolean {
  return a.content_type === "application/pdf" || a.filename.toLowerCase().endsWith(".pdf");
}

export function AttachmentPreviewDialog({
  attachment,
  onClose,
  url,
}: {
  attachment: Previewable;
  onClose: () => void;
  /** Override the download/preview URL (conversation attachments live under a
   * different route than ticket attachments). */
  url?: string;
}) {
  const src = url ?? attachmentUrl(attachment.id);
  const isTextFile = isText(attachment);
  const textQuery = useQuery({
    queryKey: ["artifact-content", attachment.id],
    queryFn: async () => {
      const res = await fetch(src);
      if (!res.ok) throw new Error(res.statusText);
      return res.text();
    },
    enabled: isTextFile,
  });

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-full sm:max-w-4xl lg:max-w-5xl">
        <DialogHeader>
          <DialogTitle className="truncate pr-8">{attachment.filename}</DialogTitle>
          <DialogDescription className="flex flex-wrap items-center gap-2">
            <span>{attachment.content_type}</span>
            <span>·</span>
            <span>{formatBytes(attachment.size_bytes)}</span>
            {attachment.ticket_key && (
              <>
                <span>·</span>
                <span>{attachment.ticket_key}</span>
              </>
            )}
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[70vh] min-h-0 overflow-y-auto">
          {isMarkdown(attachment) && textQuery.data && (
            <Markdown>{textQuery.data}</Markdown>
          )}
          {isTextFile && !isMarkdown(attachment) && (
            <pre className="max-h-full whitespace-pre-wrap rounded-md border border-zinc-200 bg-zinc-50 p-3 text-xs leading-relaxed dark:border-zinc-800 dark:bg-zinc-900/60">
              {textQuery.data ?? "Loading…"}
            </pre>
          )}
          {isImage(attachment) && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={src + (src.includes("?") ? "&inline=1" : "?inline=1")}
              alt={attachment.filename}
              className="mx-auto max-h-[55vh] max-w-full object-contain"
            />
          )}
          {isPdf(attachment) && (
            <iframe
              src={src + (src.includes("?") ? "&inline=1" : "?inline=1")}
              title={attachment.filename}
              className="h-[60vh] w-full rounded-md border border-zinc-200 dark:border-zinc-800"
            />
          )}
          {!isTextFile && !isImage(attachment) && !isPdf(attachment) && (
            <div className="flex flex-col items-center gap-3 py-10 text-sm text-zinc-500">
              <p>Preview tidak didukung untuk tipe file ini.</p>
              <Button
                variant="outline"
                nativeButton={false}
                render={<a href={src} download />}
              >
                <DownloadIcon className="mr-1.5 size-3.5" /> Download
              </Button>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
