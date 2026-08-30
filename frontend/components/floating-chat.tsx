"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowUpIcon, ChevronRightIcon, XIcon } from "lucide-react";
import {
  ApiError,
  createConversation,
  formatAgentName,
  listAgents,
  listConversationMessages,
  listConversations,
  listRuns,
  postConversationMessage,
  type Agent,
  type ConversationMessage,
} from "@/lib/api";
import {
  readUnreadChatCount,
  useWorkspaceEvents,
  type WorkspaceEvent,
} from "@/components/events-context";
import { AgentAvatar } from "@/components/agent-avatar";
import { Markdown } from "@/components/markdown";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { ChoicePills } from "@/components/choice-pills";
import { parseChoices } from "@/lib/parse-choices";

// Same recipe as onboarding's bubble (components/onboarding/pm-handoff.tsx) so the
// look feels identical to the wizard's handoff chat, just in a smaller panel.
const BUBBLE_TEXT_STYLE = {
  fontSize: "1rem",
  lineHeight: "1.5rem",
  fontFamily: "inherit",
};

/** Derive a short title from the opening message, same as the full /chat page. */
function deriveTitle(message: string): string {
  const trimmed = message.trim();
  if (trimmed.length <= 50) return trimmed;
  const cut = trimmed.slice(0, 50);
  const lastSpace = cut.lastIndexOf(" ");
  const base = (lastSpace > 0 ? cut.slice(0, lastSpace) : cut).trimEnd();
  return `${base}…`;
}

/** Floating PM-chat entry point, mounted once in the workspace layout so its
 * open/closed state survives page navigation. Trigger button shows the PM's
 * avatar bottom-right; the panel reuses the same conversation data as the full
 * /chat page (same most-recent conversation), just rendered with the onboarding
 * bubble style instead of the denser one used there. */
export function FloatingChat({
  workspaceId,
  workspaceKey,
}: {
  workspaceId: string | undefined;
  workspaceKey: string | undefined;
}) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(0);

  const agents = useQuery({
    queryKey: ["agents", workspaceId],
    queryFn: () => listAgents(workspaceId!),
    enabled: !!workspaceId,
  });
  const pm = agents.data?.find((a) => a.role === "pm" && a.enabled);

  // Mirrors header.tsx's badge computation exactly, including its zero-count
  // fallback dot (unreadChatCount reset to 0 but a new agent message arrived after
  // the last read timestamp) — otherwise the trigger and the header badge disagree.
  useEffect(() => {
    if (!workspaceId) return;
    const refresh = () => {
      const counted = readUnreadChatCount(workspaceId);
      if (counted > 0) {
        setUnread(counted);
        return;
      }
      let lastAgent: string | null = null;
      let lastRead: string | null = null;
      try {
        lastAgent = localStorage.getItem(`lastAgentChatAt:${workspaceId}`);
        lastRead = localStorage.getItem(`chatLastReadAt:${workspaceId}`);
      } catch {
        // ignore
      }
      setUnread(lastAgent && (!lastRead || lastAgent > lastRead) ? 1 : 0);
    };
    refresh();
    window.addEventListener("map:agent-chat", refresh);
    window.addEventListener("map:chat-read", refresh);
    return () => {
      window.removeEventListener("map:agent-chat", refresh);
      window.removeEventListener("map:chat-read", refresh);
    };
  }, [workspaceId]);

  function markRead() {
    if (!workspaceId) return;
    try {
      localStorage.setItem(
        `chatLastReadAt:${workspaceId}`,
        new Date().toISOString(),
      );
      localStorage.setItem(`unreadChatCount:${workspaceId}`, "0");
    } catch {
      // ignore
    }
    window.dispatchEvent(
      new CustomEvent("map:chat-read", { detail: { workspaceId } }),
    );
  }

  // While the panel is open, any new agent chat message re-marks the chat as read —
  // same behavior as chat/page.tsx:156-169, otherwise the header badge lights up
  // for a reply the user is already watching stream in live.
  useEffect(() => {
    if (!workspaceId || !open) return;
    window.addEventListener("map:agent-chat", markRead);
    return () => window.removeEventListener("map:agent-chat", markRead);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, open]);

  // Hidden on the full chat page (already open there) and once the panel is open —
  // the panel has its own close button in that case.
  const hideTrigger =
    !workspaceId || !pm || pathname === `/w/${workspaceKey}/chat`;

  if (hideTrigger) return null;

  return (
    <>
      {open && (
        <FloatingChatPanel
          workspaceId={workspaceId}
          workspaceKey={workspaceKey!}
          pm={pm}
          onClose={() => setOpen(false)}
        />
      )}
      <button
        type="button"
        onClick={() => {
          const next = !open;
          setOpen(next);
          if (next) markRead();
        }}
        aria-label={open ? "Tutup chat" : "Buka chat dengan PM"}
        className="fixed right-6 bottom-6 z-50 flex size-14 cursor-pointer items-center justify-center rounded-full bg-background shadow-lg ring-1 ring-black/10 transition-transform hover:scale-105 dark:ring-white/15"
      >
        {open ? (
          <XIcon
            key="x"
            className="size-6 animate-in zoom-in-50 spin-in-45 text-zinc-600 duration-200 dark:text-zinc-300"
          />
        ) : (
          <AgentAvatar
            key="avatar"
            name={pm.name}
            template={pm.avatar_template}
            color={pm.avatar_color}
            size={52}
            className="animate-in zoom-in-50 duration-200"
          />
        )}
        {!open && unread > 0 && (
          <span className="absolute -top-1 -right-1 flex size-5 items-center justify-center rounded-full bg-red-500 text-[10px] font-semibold text-white ring-2 ring-background">
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </button>
    </>
  );
}

function FloatingChatPanel({
  workspaceId,
  workspaceKey,
  pm,
  onClose,
}: {
  workspaceId: string;
  workspaceKey: string;
  pm: Agent;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const bottomRef = useRef<HTMLDivElement>(null);
  const [text, setText] = useState("");

  const conversations = useQuery({
    queryKey: ["conversations", workspaceId],
    queryFn: () => listConversations(workspaceId),
  });
  const active = [...(conversations.data ?? [])].sort((a, b) =>
    (b.last_message_at ?? b.created_at).localeCompare(
      a.last_message_at ?? a.created_at,
    ),
  )[0];

  const messages = useQuery({
    queryKey: ["conversation", active?.id],
    queryFn: () => listConversationMessages(active!.id),
    enabled: !!active,
    refetchInterval: active ? 2000 : false,
  });
  const allMessages = messages.data ?? [];

  const { events: liveEvents } = useWorkspaceEvents();
  const runs = useQuery({
    queryKey: ["runs", workspaceId],
    queryFn: () => listRuns(workspaceId),
    refetchInterval: 2000,
  });
  const chatRuns = (runs.data ?? []).filter(
    (r) => r.conversation_id === active?.id,
  );
  const pmIsTyping = chatRuns.some(
    (r) => r.status === "running" || r.status === "queued",
  );
  const activeRunId = chatRuns.find(
    (r) => r.status === "running" || r.status === "queued",
  )?.id;

  const transcriptEvents = useMemo(() => {
    if (!activeRunId) return [];
    return liveEvents
      .filter(
        (e): e is WorkspaceEvent =>
          e.type === "assistant_text" &&
          typeof e.payload.text === "string" &&
          e.run_id === activeRunId,
      )
      .sort((a, b) => (a.created_at ?? "").localeCompare(b.created_at ?? ""));
  }, [liveEvents, activeRunId]);

  const lastMessage =
    allMessages.length > 0 ? allMessages[allMessages.length - 1] : null;
  const lastIsPm =
    !!lastMessage &&
    !lastMessage.is_system &&
    lastMessage.author_agent_id !== null;
  const activeChoices =
    lastIsPm && !pmIsTyping ? parseChoices(lastMessage!.body).group : null;

  // First time content shows up in a freshly-opened panel, jump straight to the
  // bottom (no visible scroll animation) — only later updates (a live reply
  // arriving while the panel is already open) animate smoothly. useLayoutEffect
  // so the jump happens before the browser paints, avoiding a top-of-thread flash.
  const scrolledOnceRef = useRef(false);
  useLayoutEffect(() => {
    if (!bottomRef.current) return;
    bottomRef.current.scrollIntoView({
      behavior: scrolledOnceRef.current ? "smooth" : "auto",
      block: "end",
    });
    if (allMessages.length > 0 || pmIsTyping) scrolledOnceRef.current = true;
  }, [allMessages.length, transcriptEvents.length, pmIsTyping]);

  const sendMutation = useMutation({
    mutationFn: async (message: string) => {
      let convId = active?.id;
      if (!convId) {
        const created = await createConversation(workspaceId, {
          title: deriveTitle(message),
        });
        convId = created.id;
      }
      return {
        convId,
        message: await postConversationMessage(convId, message),
      };
    },
    onSuccess: ({ convId }) => {
      setText("");
      queryClient.invalidateQueries({ queryKey: ["conversation", convId] });
      queryClient.invalidateQueries({
        queryKey: ["conversations", workspaceId],
      });
    },
    onError: (err: unknown) => {
      toast.error(
        err instanceof ApiError ? err.message : "Gagal mengirim pesan",
      );
    },
  });

  const pmLabel = formatAgentName(pm.name, pm.role);

  return (
    <div className="animate-in fade-in-0 slide-in-from-bottom-4 fixed right-6 bottom-24 z-50 flex h-[32rem] w-96 flex-col overflow-hidden rounded-2xl border border-black/10 bg-background shadow-xl duration-200 dark:border-white/10">
      <div className="flex items-center gap-2 border-b border-black/10 px-4 py-3 dark:border-white/10">
        <AgentAvatar
          name={pm.name}
          template={pm.avatar_template}
          color={pm.avatar_color}
          size={28}
        />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{pmLabel}</p>
        </div>
        <Link
          href={`/w/${workspaceKey}/chat`}
          className="text-xs text-zinc-500 hover:text-foreground hover:underline"
        >
          Chat lengkap →
        </Link>
        <button
          type="button"
          onClick={onClose}
          aria-label="Tutup chat"
          className="cursor-pointer rounded-full p-1 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-foreground dark:hover:bg-zinc-800"
        >
          <XIcon className="size-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4">
        {allMessages.length === 0 && !pmIsTyping ? (
          <p className="flex h-full items-center justify-center text-center text-sm text-zinc-400">
            Tulis pesan pertama untuk PM…
          </p>
        ) : (
          <div className="flex flex-col gap-3">
            {allMessages.map((m) => (
              <ConversationBubble key={m.id} message={m} />
            ))}
            {transcriptEvents.map((ev) => (
              <div
                key={ev.id}
                className="max-w-[85%] rounded-2xl rounded-bl-sm bg-muted px-4 py-3 text-base"
              >
                <Markdown style={BUBBLE_TEXT_STYLE}>
                  {ev.payload.text as string}
                </Markdown>
              </div>
            ))}
            {pmIsTyping && (
              <div className="flex max-w-[85%] items-center gap-2 rounded-2xl rounded-bl-sm bg-muted/50 px-4 py-3 text-sm text-zinc-400 dark:text-zinc-500">
                Sedang mengetik
                <span className="flex gap-1">
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
                  <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current" />
                </span>
              </div>
            )}
            {activeChoices && (
              <ChoicePills
                group={activeChoices}
                disabled={sendMutation.isPending}
                onAnswer={(t) => sendMutation.mutate(t)}
              />
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      {!activeChoices && (
        <form
          className="relative border-t border-black/10 p-3 dark:border-white/10"
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim() || sendMutation.isPending) return;
            sendMutation.mutate(text.trim());
          }}
        >
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Tulis pesan ke PM…"
            disabled={sendMutation.isPending}
            className="h-11 rounded-full pr-11 pl-4"
          />
          <Button
            type="submit"
            size="icon"
            className="absolute top-1/2 right-4 size-8 -translate-y-1/2 rounded-full"
            disabled={!text.trim() || sendMutation.isPending}
          >
            <ArrowUpIcon className="size-4" />
          </Button>
        </form>
      )}
    </div>
  );
}

// Same bubble recipe as onboarding's ConversationBubble (pm-handoff.tsx), scaled
// down slightly (px-4 py-3 text-base vs px-5 py-3.5 text-lg) to fit the narrower panel.
function ConversationBubble({ message }: { message: ConversationMessage }) {
  if (message.is_system) {
    return <SystemBubble body={message.body} />;
  }
  const isOwner = message.author_agent_id === null;
  const { cleanedBody } = parseChoices(message.body);
  return (
    <div
      className={`max-w-[85%] rounded-2xl px-4 py-3 text-base ${
        isOwner
          ? "ml-auto rounded-br-sm bg-primary text-primary-foreground"
          : "rounded-bl-sm bg-muted"
      }`}
    >
      <Markdown
        className={isOwner ? "[&_*]:text-primary-foreground" : undefined}
        style={BUBBLE_TEXT_STYLE}
      >
        {cleanedBody}
      </Markdown>
    </div>
  );
}

const SYSTEM_COLLAPSE_THRESHOLD = 240;

// Same collapse behavior as onboarding's SystemBubble (pm-handoff.tsx) — a
// malformed ```map block makes core/report.py post the agent's whole raw tail
// output as a system comment (can run thousands of characters), which would
// otherwise dominate this panel's small 384x512px scroll area.
function SystemBubble({ body }: { body: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = body.length > SYSTEM_COLLAPSE_THRESHOLD;

  if (isLong && !expanded) {
    return (
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="flex max-w-[85%] items-center gap-1.5 rounded-2xl rounded-bl-sm bg-muted px-4 py-3 text-left text-sm text-zinc-500 hover:text-foreground"
      >
        <ChevronRightIcon className="size-4 shrink-0" />
        Detail teknis ({body.length.toLocaleString("id-ID")} karakter) — klik
        untuk lihat
      </button>
    );
  }

  return (
    <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-muted px-4 py-3 text-sm text-zinc-500">
      <Markdown style={BUBBLE_TEXT_STYLE}>{body}</Markdown>
      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="mt-2 text-xs text-zinc-500 hover:text-foreground"
        >
          Sembunyikan
        </button>
      )}
    </div>
  );
}
