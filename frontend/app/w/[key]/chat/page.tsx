"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  conversationAttachmentUrl,
  createConversation,
  deleteConversationAttachment,
  formatAgentName,
  listAgents,
  listArtifacts,
  listConversationAttachments,
  listConversationMessages,
  listConversations,
  listRuns,
  listTickets,
  listWorkspaces,
  postConversationMessage,
  uploadConversationAttachment,
  type Agent,
  type Conversation,
  type ConversationAttachment,
  type ConversationMessage,
  type AvatarTemplate,
} from "@/lib/api";
import { useWorkspaceEvents, type WorkspaceEvent } from "@/components/events-context";
import { AgentAvatar } from "@/components/agent-avatar";
import { formatShortTime } from "@/lib/format";
import { Markdown } from "@/components/markdown";
import { MentionAutocomplete, type MentionOption } from "@/components/mention-autocomplete";
import { linkifyMentions } from "@/lib/mention-link";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  EyeIcon,
  FileTextIcon,
  LightbulbIcon,
  ListChecksIcon,
  MicIcon,
  PaperclipIcon,
  PlusIcon,
  ShieldCheckIcon,
  SparklesIcon,
  TrendingUpIcon,
  XIcon,
  type LucideIcon,
} from "lucide-react";
import { AttachmentPreviewDialog } from "@/components/attachment-preview";
import { ChoicePills } from "@/components/choice-pills";
import { parseChoices } from "@/lib/parse-choices";

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

/** Web Speech API constructor, resolved once at module load (window may not exist in SSR). */
function getSpeechRecognitionCtor(): typeof SpeechRecognition | undefined {
  if (typeof window === "undefined") return undefined;
  return (
    (window as Window & { SpeechRecognition?: typeof SpeechRecognition }).SpeechRecognition ??
    (window as Window & { webkitSpeechRecognition?: typeof SpeechRecognition }).webkitSpeechRecognition
  );
}

/** Derive a short title from the opening chat message: first ~50 chars, cut at a word boundary. */
function deriveTitle(message: string): string {
  const trimmed = message.trim();
  if (trimmed.length <= 50) return trimmed;
  const cut = trimmed.slice(0, 50);
  const lastSpace = cut.lastIndexOf(" ");
  const base = (lastSpace > 0 ? cut.slice(0, lastSpace) : cut).trimEnd();
  return `${base}…`;
}

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

  const conversations = useQuery({
    queryKey: ["conversations", workspace?.id],
    queryFn: () => listConversations(workspace!.id),
    enabled: !!workspace,
  });

  // Opening the chat page marks the workspace's chat as read (clears the header
  // unread badge), regardless of which conversation is shown.
  useEffect(() => {
    if (!workspace?.id) return;
    try {
      localStorage.setItem(`chatLastReadAt:${workspace.id}`, new Date().toISOString());
      localStorage.setItem(`unreadChatCount:${workspace.id}`, "0");
    } catch {
      // ignore
    }
    window.dispatchEvent(new CustomEvent("map:chat-read", { detail: { workspaceId: workspace.id } }));
  }, [workspace?.id]);

  // While the user is ON the chat page, any new agent chat message (agent
  // comments or conversation messages, via SSE) re-marks the chat as read — the
  // page is showing them, so the header badge must stay off even when a reply
  // arrives mid-view. Listening for the same custom event the header uses keeps
  // the two in sync without reading SSE directly here.
  useEffect(() => {
    if (!workspace?.id) return;
    const onAgentChat = () => {
      try {
        localStorage.setItem(`chatLastReadAt:${workspace.id}`, new Date().toISOString());
        localStorage.setItem(`unreadChatCount:${workspace.id}`, "0");
      } catch {
        // ignore
      }
      window.dispatchEvent(new CustomEvent("map:chat-read", { detail: { workspaceId: workspace.id } }));
    };
    window.addEventListener("map:agent-chat", onAgentChat);
    return () => window.removeEventListener("map:agent-chat", onAgentChat);
  }, [workspace?.id]);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Draft mode = "+ New chat" pressed: the composer is shown immediately without a
  // title/ticket form; the conversation is created on first send, titled from the
  // message (same flow as the pre-conversation chat page).
  const [draftMode, setDraftMode] = useState(false);

  const sorted = [...(conversations.data ?? [])].sort((a, b) =>
    (b.last_message_at ?? b.created_at).localeCompare(a.last_message_at ?? a.created_at),
  );
  const activeId = selectedId ?? sorted[0]?.id ?? null;
  const active = sorted.find((c) => c.id === activeId) ?? null;

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
            onClick={() => setDraftMode(true)}
            className={draftMode ? "bg-zinc-100 dark:bg-zinc-900/60" : ""}
          >
            + New chat
          </Button>
          <CardContent className="flex min-h-0 flex-1 flex-col gap-1 overflow-y-auto p-0">
            {sorted.length === 0 && !draftMode && (
              <p className="px-2 py-2 text-xs text-zinc-500">No conversations yet.</p>
            )}
            {sorted.map((c) => (
              <button
                key={c.id}
                onClick={() => {
                  setSelectedId(c.id);
                  setDraftMode(false);
                }}
                className={`flex flex-col gap-1 border-b border-black/5 px-3 py-2 text-left text-xs last:border-b-0 hover:bg-zinc-50 dark:border-white/5 dark:hover:bg-zinc-900/40 ${
                  c.id === activeId ? "bg-zinc-100 dark:bg-zinc-900/60" : ""
                }`}
              >
                <span className="flex items-center gap-1.5">
                  <span className="truncate font-medium text-zinc-800 dark:text-zinc-200">
                    {c.title}
                  </span>
                  {c.linked_ticket_key && (
                    <Link
                      href={`/w/${workspaceKey}/ticket/${c.linked_ticket_key}`}
                      onClick={(e) => e.stopPropagation()}
                      className="shrink-0 font-mono text-[10px] text-zinc-400 hover:underline"
                    >
                      {c.linked_ticket_key}
                    </Link>
                  )}
                </span>
                <span className="truncate text-zinc-400">
                  {c.last_message_at
                    ? formatShortTime(c.last_message_at, workspace.timezone)
                    : "Belum ada pesan"}
                </span>
              </button>
            ))}
          </CardContent>
        </Card>

        {active && !draftMode ? (
          <ThreadPanel
            key={active.id}
            workspaceId={workspace.id}
            workspaceKey={workspace.key}
            timezone={workspace.timezone}
            pm={pm}
            conversation={active}
          />
        ) : (
          <ThreadPanel
            key="draft"
            workspaceId={workspace.id}
            workspaceKey={workspace.key}
            timezone={workspace.timezone}
            pm={pm}
            conversation={null}
            onCreated={(id) => {
              setDraftMode(false);
              setSelectedId(id);
              queryClient.invalidateQueries({ queryKey: ["conversations", workspace.id] });
            }}
          />
        )}
      </div>
    </div>
  );
}

function ThreadPanel({
  workspaceId,
  workspaceKey,
  timezone,
  pm,
  conversation,
  onCreated,
}: {
  workspaceId: string;
  workspaceKey: string;
  timezone: string;
  pm: Agent;
  conversation: Conversation | null;
  onCreated?: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");
  const [stagedFile, setStagedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const stickToBottomRef = useRef(true);
  const [hasNewMessage, setHasNewMessage] = useState(false);
  const [previewAttachment, setPreviewAttachment] = useState<ConversationAttachment | null>(null);
  const [attachOpen, setAttachOpen] = useState(false);
  const [suggestionsHovered, setSuggestionsHovered] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [speechSupported] = useState(() => getSpeechRecognitionCtor() !== undefined);
  const attachHideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attachOpenTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const suggestionsHideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const speechRecognitionRef = useRef<SpeechRecognition | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function handleSuggestionsEnter() {
    if (suggestionsHideTimer.current) clearTimeout(suggestionsHideTimer.current);
    setSuggestionsHovered(true);
  }

  function handleSuggestionsLeave() {
    if (suggestionsHideTimer.current) clearTimeout(suggestionsHideTimer.current);
    suggestionsHideTimer.current = setTimeout(() => setSuggestionsHovered(false), 400);
  }

  useEffect(() => {
    const ctor = getSpeechRecognitionCtor();
    if (ctor) speechRecognitionRef.current = new ctor();
  }, []);

  useEffect(() => {
    if (!isRecording) return;
    const rec = speechRecognitionRef.current;
    if (!rec) return;
    rec.lang = "en-US";
    rec.interimResults = true;
    rec.continuous = true;
    rec.onresult = (e) => {
      const text = Array.from(e.results)
        .map((r) => r[0].transcript)
        .join("");
      setDraft(text);
    };
    rec.onend = () => setIsRecording(false);
    rec.onerror = () => setIsRecording(false);
    rec.start();
    return () => {
      try {
        rec.stop();
      } catch {
        // already stopped
      }
    };
  }, [isRecording]);

  function handleAttachEnter() {
    if (attachHideTimer.current) clearTimeout(attachHideTimer.current);
    if (attachOpenTimer.current) clearTimeout(attachOpenTimer.current);
    setAttachOpen(true);
  }

  function handleAttachLeave() {
    if (attachOpenTimer.current) clearTimeout(attachOpenTimer.current);
    attachOpenTimer.current = setTimeout(() => {
      if (attachHideTimer.current) clearTimeout(attachHideTimer.current);
      attachHideTimer.current = setTimeout(() => setAttachOpen(false), 200);
    }, 300);
  }

  function handleMicClick() {
    if (!speechRecognitionRef.current) return;
    if (isRecording) {
      try {
        speechRecognitionRef.current.stop();
      } catch {
        // already stopped
      }
      setIsRecording(false);
      return;
    }
    setIsRecording(true);
  }

  const messages = useQuery({
    queryKey: ["conversation", conversation?.id],
    queryFn: () => listConversationMessages(conversation!.id),
    enabled: !!conversation,
    refetchInterval: conversation ? 2000 : false,
  });

  const attachments = useQuery({
    queryKey: ["conversation-attachments", conversation?.id],
    queryFn: () => listConversationAttachments(conversation!.id),
    enabled: !!conversation,
  });

  const agents = useQuery({
    queryKey: ["agents", workspaceId],
    queryFn: () => listAgents(workspaceId),
    enabled: !!workspaceId,
  });
  const artifacts = useQuery({
    queryKey: ["artifacts", workspaceId],
    queryFn: () => listArtifacts(workspaceId),
    enabled: !!workspaceId,
  });
  const tickets = useQuery({
    queryKey: ["tickets", workspaceId],
    queryFn: () => listTickets(workspaceId),
    enabled: !!workspaceId,
  });

  const mentionOptions: MentionOption[] = useMemo(() => {
    const opts: MentionOption[] = (agents.data ?? [])
      .filter((a) => a.enabled)
      .map((a) => ({
        id: `agent-${a.id}`,
        label: a.name,
        sublabel: formatAgentName(a.name, a.role),
        group: "Agents",
        insert: a.name,
      }));
    for (const g of artifacts.data ?? []) {
      for (const a of g.attachments) {
        opts.push({
          id: `artifact-${a.id}`,
          label: a.filename,
          sublabel: g.name,
          group: "Artifacts",
          insert: a.filename,
        });
      }
    }
    for (const t of tickets.data ?? []) {
      opts.push({
        id: `ticket-${t.id}`,
        label: t.key,
        sublabel: t.title,
        group: "Tickets",
        insert: t.key,
      });
    }
    return opts;
  }, [agents.data, artifacts.data, tickets.data]);

  const pmLabel = formatAgentName(pm.name, pm.role);
  const pmAvatar = {
    template: pm.avatar_template as AvatarTemplate | null,
    color: pm.avatar_color,
  };

  // Live SSE assistant_text chunks for this conversation's active chat run.
  const { events: liveEvents } = useWorkspaceEvents();
  const activeRun = useQuery({
    queryKey: ["runs", workspaceId],
    queryFn: () => listRuns(workspaceId),
    enabled: !!workspaceId,
    refetchInterval: 2000,
  });
  const chatRuns = (activeRun.data ?? []).filter((r) => r.conversation_id === conversation?.id);
  const pmIsTyping = chatRuns.some((r) => r.status === "running" || r.status === "queued");
  const activeRunId = chatRuns.find(
    (r) => r.status === "running" || r.status === "queued",
  )?.id;

  const transcriptEvents = useMemo(() => {
    if (!activeRunId) return null;
    return liveEvents
      .filter(
        (e): e is WorkspaceEvent =>
          e.type === "assistant_text" &&
          typeof e.payload.text === "string" &&
          e.run_id === activeRunId,
      )
      .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
  }, [liveEvents, activeRunId]);

  const allMessages = messages.data ?? [];

  // Quick-reply pills only for the PM's latest message (see lib/parse-choices.ts) —
  // once the owner answers, that message stops being "last" and pills disappear with
  // it. Suppressed while a run is in flight so a stale question can't be answered
  // out of order.
  const lastMessage = allMessages.length > 0 ? allMessages[allMessages.length - 1] : null;
  const lastIsPm = !!lastMessage && !lastMessage.is_system && lastMessage.author_agent_id !== null;
  const activeChoices = lastIsPm && !pmIsTyping ? parseChoices(lastMessage!.body).group : null;

  // Opening/switching a conversation always jumps straight to the latest message.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    stickToBottomRef.current = true;
    setHasNewMessage(false);
  }, [conversation?.id]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    if (stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    } else {
      setHasNewMessage(true);
    }
  }, [allMessages.length, transcriptEvents, pmIsTyping]);

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
      // Draft mode: the conversation doesn't exist yet — create it on first send,
      // titled from the message (same flow as the pre-conversation chat page).
      let convId = conversation?.id;
      if (!convId) {
        const created = await createConversation(workspaceId, {
          title: deriveTitle(message),
        });
        convId = created.id;
      }
      if (file) await uploadConversationAttachment(convId, file);
      return { convId, message: await postConversationMessage(convId, message) };
    },
    onSuccess: ({ convId }) => {
      setDraft("");
      setStagedFile(null);
      queryClient.invalidateQueries({ queryKey: ["conversation", convId] });
      queryClient.invalidateQueries({ queryKey: ["conversations", workspaceId] });
      queryClient.invalidateQueries({ queryKey: ["conversation-attachments", convId] });
      if (conversation === null && onCreated) onCreated(convId);
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Failed to send message");
    },
  });

  const deleteAttachmentMutation = useMutation({
    mutationFn: (id: string) => deleteConversationAttachment(id),
    onSuccess: () => {
      if (conversation) {
        queryClient.invalidateQueries({ queryKey: ["conversation-attachments", conversation.id] });
      }
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

  function insertMention(insert: string) {
    const cursor = textareaRef.current?.selectionStart ?? draft.length;
    const upToCursor = draft.slice(0, cursor);
    const replaced = upToCursor.replace(/@([a-zA-Z0-9][a-zA-Z0-9-]*)$/, `@${insert} `);
    const newDraft = replaced + draft.slice(cursor);
    setDraft(newDraft);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }

  const mentionCatalog = useMemo(
    () => ({
      agents: agents.data ?? [],
      artifacts: (artifacts.data ?? []).flatMap((g) => g.attachments),
      tickets: tickets.data ?? [],
    }),
    [agents.data, artifacts.data, tickets.data],
  );

  return (
    <Card className="flex h-full min-h-0 flex-col gap-2 p-3">
      <div className="relative min-h-0 flex-1">
        <div ref={scrollRef} onScroll={handleScroll} className="h-full overflow-y-auto">
          {conversation === null && (
            <p className="flex h-full items-center justify-center text-sm text-zinc-400">
              Start a new conversation with the PM…
            </p>
          )}
          {conversation !== null && messages.isLoading && (
            <p className="text-sm text-zinc-500">Loading conversation…</p>
          )}
          {conversation !== null && !messages.isLoading && allMessages.length === 0 && !pmIsTyping && (
            <p className="flex h-full items-center justify-center text-sm text-zinc-400">
              Belum ada pesan. Tulis pesan pertama untuk PM…
            </p>
          )}
          {allMessages.length > 0 && (
            <ChatMessages
              messages={allMessages}
              transcriptEvents={transcriptEvents}
              pmName={pm.name}
              pmLabel={pmLabel}
              pmAvatar={pmAvatar}
              pmIsTyping={pmIsTyping}
              timezone={timezone}
              workspaceKey={workspaceKey}
              mentionCatalog={mentionCatalog}
            />
          )}
          {activeChoices && (
            <div className="mt-3">
              <ChoicePills
                group={activeChoices}
                disabled={sendMutation.isPending}
                onAnswer={(text) => sendMutation.mutate({ message: text, file: null })}
              />
            </div>
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

        <div
          className={`absolute right-2 bottom-3 flex flex-col items-end gap-3 pt-3 ${
            suggestionsHovered ? "pointer-events-auto" : "pointer-events-none"
          }`}
          onMouseEnter={handleSuggestionsEnter}
          onMouseLeave={handleSuggestionsLeave}
        >
          {SUGGESTIONS.map((s, i) => (
            <button
              key={s.label}
              type="button"
              onClick={() => handleSuggestion(s)}
              disabled={sendMutation.isPending}
              style={{ transitionDelay: `${(SUGGESTIONS.length - 1 - i) * 30}ms` }}
              className={`flex translate-y-2 cursor-pointer items-center gap-1.5 rounded-full border border-black/10 bg-white/60 px-3 py-1.5 text-xs text-zinc-600 opacity-0 shadow-sm backdrop-blur transition-all duration-200 hover:bg-white/90 dark:border-white/10 dark:bg-zinc-900/60 dark:text-zinc-300 dark:hover:bg-zinc-900/90 ${
                suggestionsHovered ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0"
              }`}
            >
              <s.icon className="size-3.5 shrink-0" />
              {s.label}
            </button>
          ))}
          <button
            type="button"
            className="pointer-events-auto flex cursor-pointer items-center gap-1.5 rounded-full border border-black/10 bg-white/60 px-3 py-1.5 text-xs font-medium text-zinc-600 shadow-sm backdrop-blur hover:bg-white/90 dark:border-white/10 dark:bg-zinc-900/60 dark:text-zinc-300 dark:hover:bg-zinc-900/90"
          >
            <SparklesIcon className="size-3.5 shrink-0" />
            Saran
          </button>
        </div>
      </div>

      {conversation !== null && attachments.data && attachments.data.length > 0 && (
        <div className="flex flex-wrap gap-1 border-t border-black/5 pt-1.5 dark:border-white/5">
          {attachments.data.map((a) => (
            <div
              key={a.id}
              className="flex items-center gap-0.5 rounded-full border border-black/10 py-0.5 pr-0.5 pl-1.5 text-xs dark:border-white/10"
            >
              <button
                type="button"
                onClick={() => setPreviewAttachment(a)}
                className="flex min-w-0 cursor-pointer items-center gap-0.5 hover:underline"
                title="Preview file"
              >
                <EyeIcon className="size-2.5 shrink-0 text-zinc-500" />
                <span className="max-w-36 truncate">{a.filename}</span>
              </button>
              <button
                type="button"
                onClick={() => deleteAttachmentMutation.mutate(a.id)}
                className="rounded-full p-px text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700 dark:hover:bg-zinc-800"
                aria-label={`Remove ${a.filename}`}
              >
                <XIcon className="size-2.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      {previewAttachment && (
        <AttachmentPreviewDialog
          attachment={{
            id: previewAttachment.id,
            filename: previewAttachment.filename,
            content_type: previewAttachment.content_type,
            size_bytes: previewAttachment.size_bytes,
          }}
          url={conversationAttachmentUrl(previewAttachment.id)}
          onClose={() => setPreviewAttachment(null)}
        />
      )}

      {!activeChoices && (
      <form
        className="flex flex-col gap-2 pt-0.5"
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
        <div className="flex items-center gap-2 rounded-[1.75rem] border border-black/10 bg-white/60 p-2 backdrop-blur focus-within:border-zinc-400 dark:border-white/10 dark:bg-zinc-900/60 dark:focus-within:border-zinc-600">
          <div className="relative">
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              onMouseEnter={handleAttachEnter}
              onMouseLeave={handleAttachLeave}
              disabled={sendMutation.isPending}
              aria-label="Attach a file"
              className="flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-full text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 disabled:opacity-50 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
            >
              {attachOpen ? <XIcon className="size-5" /> : <PlusIcon className="size-5" />}
            </button>

            <div
              className={`absolute bottom-12 left-0 flex flex-col gap-1.5 ${
                attachOpen ? "pointer-events-auto" : "pointer-events-none"
              }`}
              onMouseEnter={handleAttachEnter}
              onMouseLeave={handleAttachLeave}
            >
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={sendMutation.isPending}
                className={`flex translate-y-2 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-full border border-black/10 bg-white/90 px-3 py-1.5 text-xs text-zinc-600 opacity-0 shadow-sm backdrop-blur transition-all duration-200 hover:bg-zinc-100 disabled:opacity-50 dark:border-white/10 dark:bg-zinc-800/90 dark:text-zinc-300 dark:hover:bg-zinc-700 ${
                  attachOpen ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0"
                }`}
              >
                <PaperclipIcon className="size-3.5 shrink-0" />
                Attach file
              </button>
            </div>
          </div>

          <MentionAutocomplete
            value={draft}
            onChange={setDraft}
            options={mentionOptions}
            textareaRef={textareaRef}
            onInsert={insertMention}
            placeholder={
              conversation === null
                ? "Start a new conversation with the PM…"
                : "Tulis pesan ke PM…"
            }
            disabled={sendMutation.isPending}
            className="max-h-48 flex-1 resize-none overflow-y-auto border-0 bg-transparent px-2 py-2 text-sm leading-5 text-zinc-900 outline-none placeholder:text-zinc-400 dark:text-zinc-100 dark:placeholder:text-zinc-500"
          />

          <button
            type="button"
            onClick={handleMicClick}
            disabled={sendMutation.isPending || !speechSupported}
            aria-label={isRecording ? "Stop recording" : "Speak to type"}
            title={speechSupported ? (isRecording ? "Stop recording" : "Speak to type") : "Speech input not supported"}
            className={`flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-full disabled:opacity-40 ${
              isRecording
                ? "animate-pulse bg-red-500 text-white"
                : "text-zinc-500 hover:bg-zinc-100 hover:text-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
            }`}
          >
            <MicIcon className="size-5" />
          </button>

          <button
            type="submit"
            disabled={!draft.trim() || sendMutation.isPending}
            aria-label="Send message"
            className="flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-full bg-zinc-900 text-white transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-30 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-zinc-300"
          >
            <ArrowUpIcon className="size-5" />
          </button>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) setStagedFile(file);
            setAttachOpen(false);
            e.target.value = "";
          }}
        />
      </form>
      )}
    </Card>
  );
}

function ChatMessages({
  messages,
  transcriptEvents,
  pmName,
  pmLabel,
  pmAvatar,
  pmIsTyping,
  timezone,
  workspaceKey,
  mentionCatalog,
}: {
  messages: ConversationMessage[];
  transcriptEvents: WorkspaceEvent[] | null;
  pmName: string;
  pmLabel: string;
  pmAvatar: { template: AvatarTemplate | null; color: string | null };
  pmIsTyping: boolean;
  timezone: string;
  workspaceKey: string;
  mentionCatalog: { agents: { name: string }[]; artifacts: { filename: string }[]; tickets: { key: string }[] };
}) {
  const [visibleCount, setVisibleCount] = useState(20);
  const visible = messages.slice(-visibleCount);
  const hiddenCount = messages.length - visible.length;

  return (
    <div className="flex flex-col gap-3">
      {hiddenCount > 0 && (
        <button
          type="button"
          className="self-center rounded-md border border-black/10 px-3 py-1 text-xs text-zinc-500 hover:bg-zinc-100 dark:border-white/10 dark:hover:bg-zinc-800"
          onClick={() => setVisibleCount((n) => n + 20)}
        >
          Tampilkan lebih banyak ({hiddenCount} pesan lagi)
        </button>
      )}
      {visible.map((m) => {
        if (m.is_system) {
          return (
            <div key={m.id} className="flex justify-center">
              <div className="flex max-w-[85%] flex-col gap-0.5 rounded-lg border border-dashed border-zinc-300 bg-zinc-50 px-3 py-1.5 text-xs text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/40 dark:text-zinc-400">
                <p className="flex items-center gap-1.5 text-[10px] font-medium opacity-80">
                  <span>System</span>
                  <span className="font-normal">{formatShortTime(m.created_at, timezone)}</span>
                </p>
                <Markdown>{linkifyMentions(m.body, workspaceKey, mentionCatalog)}</Markdown>
              </div>
            </div>
          );
        }
        const isOwner = m.author_agent_id === null;
        // The ~~~choices fence (if any) is rendered as pills below the thread, not
        // as raw fenced text inside the bubble.
        const { cleanedBody } = parseChoices(m.body);
        return (
          <div key={m.id} className={`flex ${isOwner ? "justify-end" : "justify-start"}`}>
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
                <span className="font-normal">{formatShortTime(m.created_at, timezone)}</span>
              </p>
              <Markdown invert={isOwner}>
                {linkifyMentions(cleanedBody, workspaceKey, mentionCatalog)}
              </Markdown>
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
          <div className="rounded-lg bg-zinc-100 px-3 py-2 text-sm text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
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
