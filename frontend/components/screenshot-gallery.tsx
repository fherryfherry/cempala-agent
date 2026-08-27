"use client";

import { useState } from "react";
import { attachmentUrl, type Attachment } from "@/lib/api";
import { AttachmentPreviewDialog, isImage } from "@/components/attachment-preview";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function ScreenshotGallery({ attachments }: { attachments: Attachment[] }) {
  const images = attachments.filter(isImage);
  const [open, setOpen] = useState<Attachment | null>(null);

  if (images.length === 0) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Result</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4">
        {images.map((a) => (
          <button
            key={a.id}
            type="button"
            onClick={() => setOpen(a)}
            className="overflow-hidden rounded-md border border-zinc-200 hover:opacity-80 dark:border-zinc-800"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={attachmentUrl(a.id, { inline: true })}
              alt={a.filename}
              className="aspect-video w-full object-cover"
            />
          </button>
        ))}
      </CardContent>
      {open && <AttachmentPreviewDialog attachment={open} onClose={() => setOpen(null)} />}
    </Card>
  );
}
