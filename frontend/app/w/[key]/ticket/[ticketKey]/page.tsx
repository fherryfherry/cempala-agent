"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  attachmentUrl,
  createComment,
  deleteAttachment,
  getTicket,
  listAgents,
  listWorkspaces,
  uploadAttachment,
  type TicketPriority,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const PRIORITY_VARIANT: Record<TicketPriority, "outline" | "secondary" | "destructive" | "default"> = {
  low: "outline",
  medium: "secondary",
  high: "default",
  urgent: "destructive",
};

export default function TicketDetailPage() {
  const params = useParams<{ key: string; ticketKey: string }>();
  const workspaceKey = params.key;
  const ticketKey = params.ticketKey;
  const queryClient = useQueryClient();

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });
  const workspace = workspaces.data?.find((ws) => ws.key === workspaceKey);

  const agents = useQuery({
    queryKey: ["agents", workspace?.id],
    queryFn: () => listAgents(workspace!.id),
    enabled: !!workspace,
  });

  const ticket = useQuery({
    queryKey: ["ticket", ticketKey],
    queryFn: () => getTicket(ticketKey),
  });

  const agentName = (id: string | null) =>
    id ? (agents.data?.find((a) => a.id === id)?.name ?? id) : null;

  const fileInputRef = useRef<HTMLInputElement>(null);

  const uploadMutation = useMutation({
    mutationFn: (file: File) => uploadAttachment(ticketKey, file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ticket", ticketKey] });
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Upload failed");
    },
  });

  const deleteAttachmentMutation = useMutation({
    mutationFn: (id: string) => deleteAttachment(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["ticket", ticketKey] });
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Delete failed");
    },
  });

  if (workspaces.isLoading || ticket.isLoading) {
    return <p className="px-6 py-10 text-sm text-zinc-500">Loading…</p>;
  }

  if (!workspace) {
    return (
      <p className="px-6 py-10 text-sm text-red-600">
        Workspace &quot;{workspaceKey}&quot; not found.
      </p>
    );
  }

  if (ticket.isError || !ticket.data) {
    return (
      <p className="px-6 py-10 text-sm text-red-600">
        Ticket &quot;{ticketKey}&quot; not found.
      </p>
    );
  }

  const t = ticket.data;

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-6 px-6 py-10">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="font-mono text-xs text-zinc-400">{t.key}</p>
          <h1 className="text-2xl font-semibold tracking-tight">{t.title}</h1>
          <div className="mt-2 flex items-center gap-2">
            <Badge variant={PRIORITY_VARIANT[t.priority]}>{t.priority}</Badge>
            <Badge variant="secondary">{t.status}</Badge>
            {agentName(t.assignee_id) && (
              <span className="text-xs text-zinc-500">{agentName(t.assignee_id)}</span>
            )}
          </div>
        </div>
        <Button
          variant="outline"
          onClick={() => toast.info("Not implemented yet")}
        >
          Run
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Description</CardTitle>
        </CardHeader>
        <CardContent className="whitespace-pre-wrap text-sm text-zinc-700 dark:text-zinc-300">
          {t.description || <span className="text-zinc-400">No description.</span>}
        </CardContent>
      </Card>

      {t.children.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Sub-tickets</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2">
            {t.children.map((child) => (
              <Link
                key={child.id}
                href={`/w/${workspaceKey}/ticket/${child.key}`}
                className="text-sm hover:underline"
              >
                <span className="font-mono text-xs text-zinc-400">{child.key}</span>{" "}
                {child.title}
              </Link>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Attachments</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2">
          {t.attachments.length === 0 && (
            <p className="text-sm text-zinc-400">No attachments.</p>
          )}
          {t.attachments.map((a) => (
            <div key={a.id} className="flex items-center justify-between gap-2 text-sm">
              <a
                href={attachmentUrl(a.id)}
                className="truncate hover:underline"
                download
              >
                {a.filename}
              </a>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => deleteAttachmentMutation.mutate(a.id)}
              >
                Delete
              </Button>
            </div>
          ))}
          <div className="pt-2">
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) uploadMutation.mutate(file);
                e.target.value = "";
              }}
            />
            <Button
              variant="outline"
              size="sm"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadMutation.isPending}
            >
              {uploadMutation.isPending ? "Uploading…" : "Upload attachment"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <CommentsSection
        ticketKey={ticketKey}
        workspaceId={workspace.id}
        comments={t.comments}
        agentName={agentName}
      />
    </div>
  );
}

function CommentsSection({
  ticketKey,
  workspaceId,
  comments,
  agentName,
}: {
  ticketKey: string;
  workspaceId: string;
  comments: import("@/lib/api").Comment[];
  agentName: (id: string | null) => string | null;
}) {
  const queryClient = useQueryClient();
  const agents = useQuery({ queryKey: ["agents", workspaceId], queryFn: () => listAgents(workspaceId) });
  const [body, setBody] = useState("");
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const mutation = useMutation({
    mutationFn: () => createComment(ticketKey, { body }),
    onSuccess: () => {
      setBody("");
      queryClient.invalidateQueries({ queryKey: ["ticket", ticketKey] });
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Failed to post comment");
    },
  });

  const matches =
    mentionQuery !== null
      ? (agents.data ?? []).filter((a) =>
          a.name.toLowerCase().startsWith(mentionQuery.toLowerCase()),
        )
      : [];

  function handleChange(value: string) {
    setBody(value);
    const cursor = textareaRef.current?.selectionStart ?? value.length;
    const upToCursor = value.slice(0, cursor);
    const match = upToCursor.match(/@([a-zA-Z0-9-]*)$/);
    setMentionQuery(match ? match[1] : null);
  }

  function insertMention(name: string) {
    const cursor = textareaRef.current?.selectionStart ?? body.length;
    const upToCursor = body.slice(0, cursor);
    const replaced = upToCursor.replace(/@([a-zA-Z0-9-]*)$/, `@${name} `);
    const newBody = replaced + body.slice(cursor);
    setBody(newBody);
    setMentionQuery(null);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Comments</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-3">
          {comments.length === 0 && <p className="text-sm text-zinc-400">No comments yet.</p>}
          {comments.map((c) => (
            <div
              key={c.id}
              className={
                c.is_system
                  ? "rounded-md border border-dashed border-zinc-300 bg-zinc-50 px-3 py-2 text-xs text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/40"
                  : "rounded-md border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-800"
              }
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">
                  {c.is_system ? "System" : agentName(c.author_agent_id) ?? "Owner"}
                </span>
                <span className="text-xs text-zinc-400">
                  {new Date(c.created_at).toLocaleString()}
                </span>
              </div>
              <p className="mt-1 whitespace-pre-wrap">{c.body}</p>
              {c.mentions.length > 0 && (
                <div className="mt-1 flex gap-1">
                  {c.mentions.map((m) => (
                    <Badge key={m} variant="outline" className="text-xs">
                      @{m}
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>

        <form
          className="relative flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (body.trim()) mutation.mutate();
          }}
        >
          <Textarea
            ref={textareaRef}
            value={body}
            onChange={(e) => handleChange(e.target.value)}
            placeholder="Write a comment… use @ to mention an agent"
          />
          {mentionQuery !== null && matches.length > 0 && (
            <div className="absolute bottom-full left-0 z-10 mb-1 w-56 rounded-md border border-zinc-200 bg-white py-1 shadow-md dark:border-zinc-800 dark:bg-zinc-900">
              {matches.map((a) => (
                <button
                  key={a.id}
                  type="button"
                  className="block w-full px-3 py-1.5 text-left text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
                  onClick={() => insertMention(a.name)}
                >
                  {a.name} <span className="text-xs text-zinc-400">({a.role})</span>
                </button>
              ))}
            </div>
          )}
          <Button type="submit" disabled={mutation.isPending || !body.trim()} className="self-start">
            {mutation.isPending ? "Posting…" : "Post comment"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
