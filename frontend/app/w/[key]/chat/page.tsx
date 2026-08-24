"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  createComment,
  createTicket,
  deleteAttachment,
  formatAgentName,
  getTicket,
  listAgents,
  listTickets,
  listWorkspaces,
  updateTicket,
  uploadAttachment,
  type Agent,
  type Attachment,
  type Comment,
  type AvatarTemplate,
} from "@/lib/api";
import { useWorkspaceEvents, type WorkspaceEvent } from "@/components/events-context";
import { AgentAvatar } from "@/components/agent-avatar";
import { formatShortTime } from "@/lib/format";
import { Markdown } from "@/components/markdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import {
  ArrowDownIcon,
  EyeIcon,
  FileTextIcon,
  LightbulbIcon,
  ListChecksIcon,
  PaperclipIcon,
  ShieldCheckIcon,
  SparklesIcon,
  TrendingUpIcon,
  XIcon,
  type LucideIcon,
} from "lucide-react";
import { AttachmentPreviewDialog } from "@/components/attachment-preview";

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

/** Quick-send suggestions shown above the composer, like ChatGPT suggestion chips. */
const SUGGESTIONS: { label: string; message: string; icon: LucideIcon }[] = [
  {
    label: "Buat Dokumen Teknikal",
    icon: FileTextIcon,
    message:
      "Tolong buatkan dokumen teknikal lengkap untuk project ini: PRD, TSD, API Spec, dan dokumen pendukung lainnya. Simpan hasilnya sebagai artifact di repo.",
  },
  {
    label: "Progress sampai mana?",
    icon: TrendingUpIcon,
    message:
      "Progress sampai mana? Tolong update status pengerjaan semua tiket yang sedang berjalan.",
  },
  {
    label: "Rencanakan Sprint Berikutnya",
    icon: ListChecksIcon,
    message:
      "Rencanakan sprint berikutnya: pilih tiket dari backlog yang paling prioritas, buat estimasi, dan susun rencananya.",
  },
  {
    label: "Buat Tiket dari Ide Ini",
    icon: LightbulbIcon,
    message:
      "Aku punya ide fitur: [deskripsi ide]. Pecah jadi tiket-tiket yang bisa dikerjakan tim.",
  },
  {
    label: "Review Kode & Keamanan",
    icon: ShieldCheckIcon,
    message:
      "Jalankan review menyeluruh: minta Lead Engineer review kode, QA test, dan Pentester cek keamanan. Lapor hasilnya.",
  },
  {
    label: "Rangkum Aktivitas Hari Ini",
    icon: SparklesIcon,
    message:
      "Rangkum semua aktivitas hari ini: tiket yang selesai, yang masih jalan, dan blocker yang perlu perhatianku.",
  },
];

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

  // Opening the chat page marks the workspace's chat as read (clears the header
  // unread bullet), regardless of which conversation is shown.
  useEffect(() => {
    if (!workspace?.id) return;
    try {
      localStorage.setItem(`chatLastReadAt:${workspace.id}`, new Date().toISOString());
    } catch {
      // ignore
    }
    window.dispatchEvent(new CustomEvent("map:chat-read", { detail: { workspaceId: workspace.id } }));
  }, [workspace?.id]);

  // Conversation shown in the thread panel. `selectedMode` is null until the user
  // explicitly picks one; while null, the latest conversation is auto-shown
  // (derived below — opening the chat page jumps straight into the newest chat).
  const [selectedMode, setSelectedMode] = useState<Mode | null>(null);

  const conversations = [...(tickets.data ?? [])]
    .filter((t) => t.assignee_id === pm?.id && !t.parent_id)
    .sort((a, b) => b.updated_at.localeCompare(a.updated_at));
  const autoMode: Mode | null =
    conversations.length > 0 ? { type: "ticket", key: conversations[0].key } : null;
  const mode: Mode = selectedMode ?? autoMode ?? { type: "draft" };

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

  return (
    <div className="flex h-[calc(100dvh-3.5rem-1px)] w-full min-h-0 flex-col gap-6 px-6 py-10">
      <h1 className="text-2xl font-semibold tracking-tight">Chat with PM</h1>

      <div className="grid min-h-0 flex-1 grid-rows-2 gap-6 lg:grid-cols-[320px_1fr] lg:grid-rows-1">
        <Card className="flex min-h-0 flex-col gap-2 p-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setSelectedMode({ type: "draft" })}
            className={mode.type === "draft" ? "bg-zinc-100 dark:bg-zinc-900/60" : ""}
          >
            + New conversation
          </Button>
          <CardContent className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto p-0">
            {conversations.length === 0 && (
              <p className="px-2 py-2 text-xs text-zinc-500">No conversations yet.</p>
            )}
            {conversations.map((t) => (
              <button
                key={t.id}
                onClick={() => setSelectedMode({ type: "ticket", key: t.key })}
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
          timezone={workspace.timezone}
          pm={pm}
          mode={mode}
          onCreated={(key) => {
            setSelectedMode({ type: "ticket", key });
            queryClient.invalidateQueries({ queryKey: ["tickets", workspace.id] });
          }}
        />
      </div>
    </div>
  );
}

function ThreadPanel({
  workspaceId,
  timezone,
  pm,
  mode,
  onCreated,
}: {
  workspaceId: string;
  timezone: string;
  pm: Agent;
  mode: Mode;
  onCreated: (key: string) => void;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");
  const [stagedFile, setStagedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // Whether the user was scrolled near the bottom before the latest content change —
  // new messages auto-scroll only when true, so scrolling up to read history doesn't
  // get yanked back down by an incoming message.
  const stickToBottomRef = useRef(true);
  const [hasNewMessage, setHasNewMessage] = useState(false);
  const [previewAttachment, setPreviewAttachment] = useState<Attachment | null>(null);

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

  const comments: Comment[] = (ticket.data?.comments ?? []).filter((c) => !c.is_system);
  const attachments = ticket.data?.attachments ?? [];
  const pmIsTyping = (ticket.data?.runs ?? []).some(
    (r) => r.agent_id === pm.id && (r.status === "running" || r.status === "queued"),
  );
  const activeRun = (ticket.data?.runs ?? []).find(
    (r) => r.agent_id === pm.id && (r.status === "running" || r.status === "queued"),
  );

  // Live SSE assistant_text chunks for this ticket's PM run. On mount the SSE
  // stream replays the workspace's event history, so a run already in progress
  // when the page loaded is covered without any extra fetch.
  const { events: liveEvents } = useWorkspaceEvents();

  const pmLabel = formatAgentName(pm.name, pm.role);
  const transcriptEvents = useMemo(() => {
    if (!ticketKey || !activeRun) return null;
    return liveEvents
      .filter(
        (e): e is WorkspaceEvent =>
          e.type === "assistant_text" &&
          typeof e.payload.text === "string" &&
          e.run_id === activeRun.id,
      )
      .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
  }, [liveEvents, activeRun, ticketKey]);

  // Opening/switching a conversation always jumps straight to the latest message.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    stickToBottomRef.current = true;
    setHasNewMessage(false);
  }, [ticketKey]);

  // New messages/typing keep pinned to the bottom while the user hasn't scrolled up
  // to read history; otherwise hold the scroll position and surface the "new
  // message" button instead (tracked by handleScroll below).
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    } else {
      setHasNewMessage(true);
    }
  }, [comments.length, transcriptEvents, pmIsTyping]);

  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    stickToBottomRef.current = nearBottom;
    if (nearBottom) setHasNewMessage(false);
  }

  function scrollToBottom() {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    stickToBottomRef.current = true;
    setHasNewMessage(false);
  }

  const sendMutation = useMutation({
    mutationFn: async ({ message, file }: { message: string; file: File | null }) => {
      const body = `@${pm.name} ${message}`;
      let key: string;
      if (mode.type === "draft") {
        const newTicket = await createTicket(workspaceId, {
          title: deriveTitle(message),
          description: message,
          assignee_id: pm.id,
          is_new_epic: true,
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

  function handleSuggestion(suggestion: (typeof SUGGESTIONS)[number]) {
    if (sendMutation.isPending) return;
    sendMutation.mutate({ message: suggestion.message, file: null });
  }

  return (
    <Card className="flex h-full min-h-0 flex-col gap-4 p-4">
      <div className="relative min-h-0 flex-1">
        <div ref={scrollRef} onScroll={handleScroll} className="h-full overflow-y-auto">
          {mode.type === "draft" && (
            <p className="flex h-full items-center justify-center text-sm text-zinc-400">
              Start a new conversation with the PM…
            </p>
          )}
          {isTicket && ticket.isLoading && (
            <p className="text-sm text-zinc-500">Loading conversation…</p>
          )}
          {isTicket && ticket.data && (
            <ChatMessages
              key={ticketKey ?? "none"}
              comments={comments}
              transcriptEvents={transcriptEvents}
              pmName={pm.name}
              pmLabel={pmLabel}
              pmAvatar={{
                template: pm.avatar_template,
                color: pm.avatar_color,
              }}
              pmIsTyping={pmIsTyping}
              timezone={timezone}
            />
          )}
        </div>

        {hasNewMessage && (
          <button
            type="button"
            onClick={scrollToBottom}
            className="absolute bottom-3 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full bg-zinc-900 px-3 py-1.5 text-xs font-medium text-white shadow-lg hover:bg-zinc-800 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-200"
          >
            <ArrowDownIcon className="size-3.5" />
            Pesan baru
          </button>
        )}
      </div>

      {isTicket && attachments.length > 0 && (
        <div className="flex flex-wrap gap-2 border-t border-black/5 pt-3 dark:border-white/5">
          {attachments.map((a) => (
            <div
              key={a.id}
              className="flex items-center gap-1.5 rounded-full border border-black/10 py-1 pr-1 pl-2.5 text-xs dark:border-white/10"
            >
              <button
                type="button"
                onClick={() => setPreviewAttachment(a)}
                className="flex min-w-0 items-center gap-1 hover:underline"
                title="Preview file"
              >
                <EyeIcon className="size-3 shrink-0 text-zinc-500" />
                <span className="max-w-40 truncate">{a.filename}</span>
              </button>
              {a.origin === "agent" ? (
                <span
                  title="Dipublikasikan agent (artifacts) — tidak bisa dihapus dari chat"
                  className="rounded-full bg-zinc-100 px-1.5 py-0.5 text-[10px] text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400"
                >
                  agent
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => deleteAttachmentMutation.mutate(a.id)}
                  className="rounded-full p-0.5 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800"
                  aria-label={`Remove ${a.filename}`}
                >
                  <XIcon className="size-3" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {previewAttachment && (
        <AttachmentPreviewDialog
          attachment={previewAttachment}
          onClose={() => setPreviewAttachment(null)}
        />
      )}

      <form
        className="flex flex-col gap-2 border-t border-black/5 pt-4 dark:border-white/5"
        onSubmit={(e) => {
          e.preventDefault();
          handleSend();
        }}
      >
        <div className="flex flex-col items-end gap-1.5">
          {SUGGESTIONS.map((s) => (
            <button
              key={s.label}
              type="button"
              onClick={() => handleSuggestion(s)}
              disabled={sendMutation.isPending}
              className="flex cursor-pointer items-center gap-1.5 rounded-full border border-black/10 px-3 py-1.5 text-xs text-zinc-600 hover:bg-zinc-100 disabled:opacity-50 dark:border-white/10 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              <s.icon className="size-3.5 shrink-0" />
              {s.label}
            </button>
          ))}
        </div>
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

/**
 * Message list for one conversation with the latest-10-first pagination:
 * the 10 newest messages render at the bottom; a "Tampilkan lebih banyak" button
 * above them reveals 10 more (older) messages per click.
 */
function ChatMessages({
  comments,
  transcriptEvents,
  pmName,
  pmLabel,
  pmAvatar,
  pmIsTyping,
  timezone,
}: {
  comments: Comment[];
  transcriptEvents: WorkspaceEvent[] | null;
  pmName: string;
  pmLabel: string;
  pmAvatar: { template: AvatarTemplate | null; color: string | null };
  pmIsTyping: boolean;
  timezone: string;
}) {
  const [visibleCount, setVisibleCount] = useState(10);
  const visible = comments.slice(-visibleCount);
  const hiddenCount = comments.length - visible.length;

  return (
    <div className="flex flex-col gap-3">
      {comments.length === 0 && !pmIsTyping && transcriptEvents === null && (
        <p className="text-sm text-zinc-400">No messages yet.</p>
      )}
      {hiddenCount > 0 && (
        <button
          type="button"
          className="self-center rounded-md border border-black/10 px-3 py-1 text-xs text-zinc-500 hover:bg-zinc-100 dark:border-white/10 dark:hover:bg-zinc-800"
          onClick={() => setVisibleCount((n) => n + 10)}
        >
          Tampilkan lebih banyak ({hiddenCount} pesan lagi)
        </button>
      )}
      {visible.map((c) => {
        const isOwner = c.author_agent_id === null;
        const text = isOwner ? stripMentionPrefix(c.body, pmName) : c.body;
        return (
          <div key={c.id} className={`flex ${isOwner ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[75%] rounded-lg px-3 py-2 text-sm ${
                isOwner
                  ? "bg-blue-600 text-white"
                  : "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
              }`}
            >
              <p className="mb-0.5 flex items-center gap-1.5 text-[10px] font-medium opacity-70">
                {!isOwner && <AgentAvatar name={pmName} {...pmAvatar} size={14} />}
                <span>{isOwner ? "You" : pmLabel}</span>
                <span className="font-normal">{formatShortTime(c.created_at, timezone)}</span>
              </p>
              <Markdown invert={isOwner}>{text}</Markdown>
            </div>
          </div>
        );
      })}
      {(transcriptEvents ?? []).map((ev) => (
        <div key={ev.id} className="flex justify-start">
          <div className="max-w-[75%] rounded-lg bg-zinc-100 px-3 py-2 text-sm text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100">
            <p className="mb-0.5 flex items-center gap-1.5 text-[10px] font-medium opacity-70">
              <AgentAvatar name={pmName} {...pmAvatar} size={14} />
              <span>{pmLabel} · typing</span>
              <span className="font-normal">{formatShortTime(ev.created_at, timezone)}</span>
            </p>
            <Markdown>{ev.payload.text as string}</Markdown>
          </div>
        </div>
      ))}
      {pmIsTyping && (
        <div className="flex justify-start">
          <div className="max-w-[75%] rounded-lg bg-zinc-100 px-3 py-2 text-sm text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
            <p className="mb-0.5 flex items-center gap-1.5 text-[10px] font-medium opacity-70">
              <AgentAvatar name={pmName} {...pmAvatar} size={14} />
              <span>{pmLabel}</span>
            </p>
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
  );
}
