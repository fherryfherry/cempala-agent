import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  createConversation,
  listAgents,
  listConversationMessages,
  listConversations,
  listRuns,
  postConversationMessage,
} from "@/lib/api";
import { parseChoices } from "@/lib/parse-choices";

/** Derive a short title from the opening message, same as the full /chat page. */
function deriveTitle(message: string): string {
  const trimmed = message.trim();
  if (trimmed.length <= 50) return trimmed;
  const cut = trimmed.slice(0, 50);
  const lastSpace = cut.lastIndexOf(" ");
  const base = (lastSpace > 0 ? cut.slice(0, lastSpace) : cut).trimEnd();
  return `${base}…`;
}

/** Shared "is the PM waiting on an approval right now" state — the most recent
 * conversation's last message, parsed for a trailing ~~~choices block, plus the
 * mutation to answer it. Backs both the floating chat panel and the global
 * approval modal so they agree on exactly what counts as a pending approval. */
export function usePmApproval(workspaceId: string | undefined) {
  const queryClient = useQueryClient();

  const agents = useQuery({
    queryKey: ["agents", workspaceId],
    queryFn: () => listAgents(workspaceId!),
    enabled: !!workspaceId,
  });
  const pm = agents.data?.find((a) => a.role === "pm" && a.enabled);

  const conversations = useQuery({
    queryKey: ["conversations", workspaceId],
    queryFn: () => listConversations(workspaceId!),
    enabled: !!workspaceId,
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

  const runs = useQuery({
    queryKey: ["runs", workspaceId],
    queryFn: () => listRuns(workspaceId!),
    enabled: !!workspaceId,
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

  const lastMessage =
    allMessages.length > 0 ? allMessages[allMessages.length - 1] : null;
  const lastIsPm =
    !!lastMessage &&
    !lastMessage.is_system &&
    lastMessage.author_agent_id !== null;
  const activeChoices =
    lastIsPm && !pmIsTyping ? parseChoices(lastMessage!.body).group : null;

  const sendMutation = useMutation({
    mutationFn: async (message: string) => {
      let convId = active?.id;
      if (!convId) {
        const created = await createConversation(workspaceId!, {
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

  return {
    pm,
    active,
    allMessages,
    pmIsTyping,
    activeRunId,
    lastMessage,
    activeChoices,
    sendMutation,
  };
}
