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
  createTicket,
  deleteAttachment,
  getTicket,
  listAgents,
  listTickets,
  listWorkspaces,
  updateTicket,
  uploadAttachment,
  type Comment,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { PaperclipIcon, XIcon } from "lucide-react";

/** Derive a short title from the opening chat message: first ~50 chars, cut at a word boundary. */
function deriveTitle(message: string): string {
  const trimmed = message.trim();
  if (trimmed.length <= 50) return trimmed;
  const cut = trimmed.slice(0, 50);
  const lastSpace = cut.lastIndexOf(" ");
  const base = (lastSpace > 0 ? cut.slice(0, lastSpace) : cut).trimEnd();
  return `${base}…`;
}

/** The mention prefix that triggers the PM's run is a backend implementation detail — hide it from the owner's own bubble. */
function stripMentionPrefix(body: string, pmName: string): string {
  const prefix = `@${pmName} `;
  return body.startsWith(prefix) ? body.slice(prefix.length) : body;
}

type Mode = { type: "draft" } | { type: "ticket"; key: string };

export default function ChatPage() {
  const params = useParams<{ key: string }>();
  const workspaceKey = params.key;
  const queryClient = useQueryClient();

  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: listWorkspaces });
  const workspace = workspaces.data?.find((ws) => ws.key === workspaceKey);

  const agents = useQuery({
    queryKey: ["agents", workspace?.id],
    queryFn: () => listAgents(workspace!.id),
    enabled: !!workspace,
  });
  const pm = agents.data?.find((a) => a.role === "pm" && a.enabled);

  const tickets = useQuery({
    queryKey: ["tickets", workspace?.id],
    queryFn: () => listTickets(workspace!.id),
    enabled: !!workspace && !!pm,
  });

  const [mode, setMode] = useState<Mode>({ type: "draft" });

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
  if (agents.isLoading) {
    return <p className="px-6 py-10 text-sm text-zinc-500">Loading agents…</p>;
  }
  if (!pm) {
    return (
      <div className="px-6 py-10">
        <p className="text-sm text-zinc-500">
          No enabled PM agent in this workspace yet. Chat needs a PM to talk to —{" "}
          <Link href={`/w/${workspaceKey}/agents`} className="underline">
            create one on the Agents page
          </Link>
          .
        </p>
      </div>
    );
  }

  const conversations = [...(tickets.data ?? [])]
    .filter((t) => t.assignee_id === pm.id && !t.parent_id)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));

  return (
    <div className="flex w-full flex-1 flex-col gap-6 px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">{workspace.name} — Chat with PM</h1>

      <div className="grid flex-1 grid-cols-1 gap-6 lg:grid-cols-[320px_1fr]">
        <Card className="flex flex-col gap-2 p-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setMode({ type: "draft" })}
            className={mode.type === "draft" ? "bg-zinc-100 dark:bg-zinc-900/60" : ""}
          >
            + New conversation
          </Button>
          <CardContent className="flex flex-col gap-1 overflow-y-auto p-0">
            {conversations.length === 0 && (
              <p className="px-2 py-2 text-xs text-zinc-500">No conversations yet.</p>
            )}
            {conversations.map((t) => (
              <button
                key={t.id}
                onClick={() => setMode({ type: "ticket", key: t.key })}
                className={`flex flex-col gap-1 border-b border-black/5 px-3 py-2 text-left text-xs last:border-b-0 hover:bg-zinc-50 dark:border-white/5 dark:hover:bg-zinc-900/40 ${
                  mode.type === "ticket" && mode.key === t.key ? "bg-zinc-100 dark:bg-zinc-900/60" : ""
                }`}
              >
                <span className="truncate font-medium text-zinc-800 dark:text-zinc-200">{t.title}</span>
                <Badge variant="secondary" className="w-fit">
                  {t.status}
                </Badge>
              </button>
            ))}
          </CardContent>
        </Card>

        <ThreadPanel
          workspaceId={workspace.id}
          pm={pm}
          mode={mode}
          onCreated={(key) => {
            setMode({ type: "ticket", key });
            queryClient.invalidateQueries({ queryKey: ["tickets", workspace.id] });
          }}
        />
      </div>
    </div>
  );
}

function ThreadPanel({
  workspaceId,
  pm,
  mode,
  onCreated,
}: {
  workspaceId: string;
  pm: { id: string; name: string };
  mode: Mode;
  onCreated: (key: string) => void;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");
  const [stagedFile, setStagedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const isTicket = mode.type === "ticket";
  const ticketKey = isTicket ? mode.key : null;

  // The backend now publishes live `comment`/`status_change` SSE events (events-context.tsx
  // invalidates this query on receipt), so this poll is just a redundant safety net for a
  // missed/dropped SSE message — cheap enough to leave as belt-and-suspenders for MVP chat.
  const ticket = useQuery({
    queryKey: ["ticket", ticketKey],
    queryFn: () => getTicket(ticketKey!),
    enabled: !!ticketKey,
    refetchInterval: ticketKey ? 2000 : false,
  });

  const sendMutation = useMutation({
    mutationFn: async ({ message, file }: { message: string; file: File | null }) => {
      const body = `@${pm.name} ${message}`;
      let key: string;
      if (mode.type === "draft") {
        const newTicket = await createTicket(workspaceId, {
          title: deriveTitle(message),
          description: message,
          assignee_id: pm.id,
        });
        await updateTicket(newTicket.key, { status: "todo" });
        key = newTicket.key;
      } else {
        key = mode.key;
      }
      // Attachments are ticket-level (not per-comment) — the next run picks up
      // whatever's on the ticket via `-f`, so upload before/independent of the comment.
      if (file) await uploadAttachment(key, file);
      await createComment(key, { body });
      return key;
    },
    onSuccess: (key) => {
      setDraft("");
      setStagedFile(null);
      queryClient.invalidateQueries({ queryKey: ["ticket", key] });
      if (mode.type === "draft") onCreated(key);
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Failed to send message");
    },
  });

  const deleteAttachmentMutation = useMutation({
    mutationFn: (id: string) => deleteAttachment(id),
    onSuccess: () => {
      if (ticketKey) queryClient.invalidateQueries({ queryKey: ["ticket", ticketKey] });
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Failed to remove attachment");
    },
  });

  function handleSend() {
    const message = draft.trim();
    if (!message || sendMutation.isPending) return;
    sendMutation.mutate({ message, file: stagedFile });
  }

  const comments: Comment[] = (ticket.data?.comments ?? []).filter((c) => !c.is_system);
  const attachments = ticket.data?.attachments ?? [];
  const pmIsTyping = (ticket.data?.runs ?? []).some(
    (r) => r.agent_id === pm.id && (r.status === "running" || r.status === "queued"),
  );

  return (
    <Card className="flex h-full flex-col gap-4 p-4">
      <div className="flex-1 overflow-y-auto">
        {mode.type === "draft" && (
          <p className="flex h-full items-center justify-center text-sm text-zinc-400">
            Start a new conversation with the PM…
          </p>
        )}
        {isTicket && ticket.isLoading && (
          <p className="text-sm text-zinc-500">Loading conversation…</p>
        )}
        {isTicket && ticket.data && (
          <div className="flex flex-col gap-3">
            {comments.length === 0 && !pmIsTyping && (
              <p className="text-sm text-zinc-400">No messages yet.</p>
            )}
            {comments.map((c) => {
              const isOwner = c.author_agent_id === null;
              const text = isOwner ? stripMentionPrefix(c.body, pm.name) : c.body;
              return (
                <div key={c.id} className={`flex ${isOwner ? "justify-end" : "justify-start"}`}>
                  <div
                    className={`max-w-[75%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                      isOwner
                        ? "bg-blue-600 text-white"
                        : "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
                    }`}
                  >
                    <p className="mb-0.5 text-[10px] font-medium opacity-70">
                      {isOwner ? "You" : pm.name}
                    </p>
                    {text}
                  </div>
                </div>
              );
            })}
            {pmIsTyping && (
              <div className="flex justify-start">
                <div className="max-w-[75%] rounded-lg bg-zinc-100 px-3 py-2 text-sm text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
                  <p className="mb-0.5 text-[10px] font-medium opacity-70">{pm.name}</p>
                  <span className="inline-flex items-center gap-1">
                    Sedang mengetik
                    <span className="flex gap-0.5">
                      <span className="h-1 w-1 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
                      <span className="h-1 w-1 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
                      <span className="h-1 w-1 animate-bounce rounded-full bg-current" />
                    </span>
                  </span>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {isTicket && attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 border-t border-black/5 pt-3 dark:border-white/5">
          {attachments.map((a) => (
            <div
              key={a.id}
              className="flex items-center gap-1.5 rounded-full border border-black/10 py-1 pr-1 pl-2.5 text-xs dark:border-white/10"
            >
              <a href={attachmentUrl(a.id)} className="max-w-40 truncate hover:underline">
                {a.filename}
              </a>
              <button
                type="button"
                onClick={() => deleteAttachmentMutation.mutate(a.id)}
                className="rounded-full p-0.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800"
                aria-label={`Remove ${a.filename}`}
              >
                <XIcon className="size-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      <form
        className="flex flex-col gap-2 border-t border-black/5 pt-4 dark:border-white/5"
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
      >
        {stagedFile && (
          <div className="flex w-fit items-center gap-1.5 rounded-full border border-black/10 py-1 pr-1 pl-2.5 text-xs dark:border-white/10">
            <span className="max-w-48 truncate">{stagedFile.name}</span>
            <button
              type="button"
              onClick={() => setStagedFile(null)}
              className="rounded-full p-0.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800"
              aria-label="Remove attachment"
            >
              <XIcon className="size-3" />
            </button>
          </div>
        )}
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={mode.type === "draft" ? "Start a new conversation with the PM…" : "Reply to the PM…"}
          autoFocus
          disabled={sendMutation.isPending}
        />
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) setStagedFile(file);
            e.target.value = "";
          }}
        />
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="icon"
            onClick={() => fileInputRef.current?.click()}
            disabled={sendMutation.isPending}
            aria-label="Attach a file"
          >
            <PaperclipIcon className="size-4" />
          </Button>
          <Button type="submit" disabled={!draft.trim() || sendMutation.isPending}>
            {sendMutation.isPending ? "Sending…" : "Send"}
          </Button>
        </div>
      </form>
    </Card>
  );
}
