"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowUpIcon, ChevronRightIcon } from "lucide-react";
import {
  ApiError,
  createConversation,
  listConversationMessages,
  listSprints,
  postConversationMessage,
  type ConversationMessage,
} from "@/lib/api";
import { Markdown } from "@/components/markdown";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { ChoicePills } from "@/components/choice-pills";
import type { Turn } from "@/app/onboarding/page";
import { parseChoices } from "@/lib/parse-choices";

// Tailwind Typography's `prose-*` size modifiers set their own font-size on the
// prose root, unreliable to override via className alone — this is Tailwind's own
// `text-lg` value (`font-size`/`line-height`), forced via inline style so Markdown
// content renders pixel-identical to the plain-text bubbles elsewhere in the wizard.
// `fontFamily: inherit` guards against Typography's font stack winning too.
const BUBBLE_TEXT_STYLE = { fontSize: "1.125rem", lineHeight: "1.75rem", fontFamily: "inherit" };

/** Final wizard phase: hands off to a live chat with the PM agent. The PM checks the
 * repo for existing docs (or asks what to build first), proposes an epic/sprint/ticket
 * breakdown, and — once the owner approves — the existing approval-gate + auto-schedule
 * machinery in the backend (conversations.py / orchestrator.py) creates everything for
 * real and queues the assignees' runs. This component just watches for the resulting
 * active sprint and redirects into the workspace once the team is moving. */
export function PmHandoff({
  workspaceId,
  workspaceKey,
  turns,
  description,
}: {
  workspaceId: string;
  workspaceKey: string;
  turns: Turn[];
  description: string;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const started = useRef(false);
  const redirected = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [leaving, setLeaving] = useState(false);

  const messagesKey = ["onboarding-conv-messages", conversationId];
  const messages = useQuery({
    queryKey: messagesKey,
    queryFn: () => listConversationMessages(conversationId!),
    enabled: !!conversationId,
    refetchInterval: 2000,
  });

  const sprints = useQuery({
    queryKey: ["onboarding-sprints", workspaceId],
    queryFn: () => listSprints(workspaceId),
    enabled: !!conversationId,
    refetchInterval: 2000,
  });

  // Derived, not stored — the last message's author tells us whether we're waiting
  // on the PM (last message is ours) or caught up (last message is the PM's/system's).
  const waitingReply =
    !messages.data || messages.data.length === 0
      ? true
      : messages.data[messages.data.length - 1].author_agent_id === null;

  // Quick-reply pills only for the PM's latest message (see parse-choices.ts) — once
  // the owner answers, that message stops being "last" and the pills disappear with it.
  const lastMessage = messages.data?.[messages.data.length - 1] ?? null;
  const lastIsPm = !!lastMessage && !lastMessage.is_system && lastMessage.author_agent_id !== null;
  const activeChoices = lastIsPm ? parseChoices(lastMessage!.body).group : null;

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        const conv = await createConversation(workspaceId, { title: "Onboarding" });
        setConversationId(conv.id);
        const instruction =
          `Halo! Workspace ini baru dibuat lewat wizard onboarding. Konteks dari owner soal project ini: "${description}".\n\n` +
          "Tolong cek dulu apakah sudah ada dokumen teknikal (README, docs/, dsb) di repo ini. " +
          "Kalau sudah ada, analisis dan rangkum sebagai konteks. Kalau belum ada, tanya saya dulu: pekerjaan pertama yang mau dikerjakan apa. " +
          "Setelah itu, susun proposal epic + sprint pertama + breakdown tiket + assignment ke agent yang tersedia, dan minta persetujuan saya sebelum dieksekusi.";
        const first = await postConversationMessage(conv.id, instruction);
        queryClient.setQueryData<ConversationMessage[]>(
          ["onboarding-conv-messages", conv.id],
          [first],
        );
      } catch (err) {
        toast.error(err instanceof ApiError ? err.message : "Gagal memulai chat dengan PM");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.data?.length, waitingReply]);

  useEffect(() => {
    if (redirected.current) return;
    const active = sprints.data?.some((s) => s.status === "active");
    if (!active) return;
    redirected.current = true;
    // Read by the dashboard page on mount to show a one-time welcome toast —
    // sessionStorage survives the navigation, a prop/state wouldn't.
    try {
      sessionStorage.setItem("onboarding:justFinished", workspaceKey);
    } catch {
      // storage unavailable (private mode, etc.) — the redirect itself still works,
      // just without the welcome toast on arrival.
    }
    // setTimeout, not a direct call — setState synchronously in an effect body
    // triggers cascading-render warnings; deferring one tick avoids that while
    // still firing effectively immediately.
    setTimeout(() => setLeaving(true), 0);
    setTimeout(() => router.push(`/w/${workspaceKey}/dashboard`), 600);
  }, [sprints.data, router, workspaceKey]);

  const sendMutation = useMutation({
    mutationFn: (body: string) => postConversationMessage(conversationId!, body),
    onSuccess: (sent) => {
      setText("");
      queryClient.setQueryData<ConversationMessage[]>(messagesKey, (old) =>
        old ? [...old, sent] : [sent],
      );
    },
    onError: (err: unknown) => {
      toast.error(err instanceof ApiError ? err.message : "Gagal mengirim pesan");
    },
  });

  return (
    <div
      className={`flex h-[calc(100dvh-3.5rem)] w-full flex-col transition-opacity duration-500 ${
        leaving ? "pointer-events-none opacity-0" : "opacity-100"
      }`}
    >
      <div className="flex-1 overflow-y-auto px-6 py-6">
        <div className="mx-auto flex max-w-2xl flex-col gap-3">
          {turns.map((t, i) => (
            <div key={`turn-${i}`} className="flex flex-col gap-3">
              <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-muted px-5 py-3.5 text-lg">
                {t.bot}
              </div>
              <div className="ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-5 py-3.5 text-lg text-primary-foreground">
                {t.user}
              </div>
            </div>
          ))}
          {(messages.data ?? []).map((m) => (
            <ConversationBubble key={m.id} message={m} />
          ))}
          {activeChoices && (
            <ChoicePills
              group={activeChoices}
              disabled={sendMutation.isPending}
              onAnswer={(text) => sendMutation.mutate(text)}
            />
          )}
          {waitingReply && conversationId && (
            <div className="flex max-w-[85%] items-center gap-2 rounded-2xl rounded-bl-sm bg-muted px-5 py-3.5 text-lg">
              PM sedang mengetik
              <span className="flex gap-1">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-current" />
              </span>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>
      {!activeChoices && (
        <form
          className="relative mx-auto mb-6 w-full max-w-2xl px-6"
          onSubmit={(e) => {
            e.preventDefault();
            if (!conversationId || !text.trim()) return;
            sendMutation.mutate(text.trim());
          }}
        >
          <Input
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Balas PM di sini…"
            disabled={!conversationId || sendMutation.isPending}
            className="h-14 rounded-full pr-14 pl-5 text-lg md:text-lg"
          />
          <Button
            type="submit"
            size="icon"
            className="absolute top-1/2 right-2 size-10 -translate-y-1/2 rounded-full"
            disabled={!conversationId || !text.trim() || sendMutation.isPending}
          >
            <ArrowUpIcon className="size-5" />
          </Button>
        </form>
      )}
    </div>
  );
}

// Same shape as the scripted turns on the wizard's earlier steps (page.tsx) — bubble
// radius/color/padding/size all match, so the handoff into real PM chat doesn't feel
// like a different UI. Only the wrapper (own justify-start/end div, since messages
// arrive one at a time here rather than as fixed bot/user pairs) differs.
function ConversationBubble({ message }: { message: ConversationMessage }) {
  if (message.is_system) {
    return <SystemBubble body={message.body} />;
  }

  const isOwner = message.author_agent_id === null;
  // The ~~~choices fence (if any) is rendered as pills elsewhere, not as raw text.
  const { cleanedBody } = parseChoices(message.body);
  return (
    <div
      className={`max-w-[85%] rounded-2xl px-5 py-3.5 text-lg ${
        isOwner
          ? "ml-auto rounded-br-sm bg-primary text-primary-foreground"
          : "rounded-bl-sm bg-muted"
      }`}
    >
      {/* `invert` assumes the bubble bg tracks the site's own light/dark toggle (true
          for bg-muted), but bg-primary is the opposite of it in this theme (light bg
          in dark mode) — force the color explicitly instead of guessing via invert. */}
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

// System messages include raw agent-output dumps (e.g. a malformed ```map block —
// core/report.py posts the agent's whole tail output verbatim so nothing is lost),
// which can run thousands of characters. Collapsed by default so one bad run doesn't
// take over the chat; still fully available behind a click, never dropped.
function SystemBubble({ body }: { body: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = body.length > SYSTEM_COLLAPSE_THRESHOLD;

  if (isLong && !expanded) {
    return (
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="flex max-w-[85%] items-center gap-1.5 rounded-2xl rounded-bl-sm bg-muted px-5 py-3.5 text-left text-sm text-zinc-500 hover:text-foreground"
      >
        <ChevronRightIcon className="size-4 shrink-0" />
        Detail teknis ({body.length.toLocaleString("id-ID")} karakter) — klik untuk lihat
      </button>
    );
  }

  return (
    <div className="max-w-[85%] rounded-2xl rounded-bl-sm bg-muted px-5 py-3.5 text-lg">
      <Markdown style={BUBBLE_TEXT_STYLE}>{body}</Markdown>
      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="mt-2 text-sm text-zinc-500 hover:text-foreground"
        >
          Sembunyikan
        </button>
      )}
    </div>
  );
}
