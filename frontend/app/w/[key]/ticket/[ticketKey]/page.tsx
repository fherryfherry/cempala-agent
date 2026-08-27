"use client";

import { useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ApiError,
  attachmentUrl,
  createComment,
  deleteAttachment,
  formatAgentName,
  getTicket,
  listAgents,
  listSprints,
  listWorkspaces,
  uploadAttachment,
  type TicketCategory,
  type TicketPriority,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Markdown } from "@/components/markdown";
import { ScreenshotGallery } from "@/components/screenshot-gallery";
import { AgentAvatar } from "@/components/agent-avatar";
import { MentionAutocomplete, type MentionOption } from "@/components/mention-autocomplete";
import { linkifyMentions } from "@/lib/mention-link";
import { formatTimestamp } from "@/lib/datetime";

const PRIORITY_VARIANT: Record<TicketPriority, "outline" | "secondary" | "destructive" | "default"> = {
  low: "outline",
  medium: "secondary",
  high: "default",
  urgent: "destructive",
};

const CATEGORY_LABELS: Record<TicketCategory, string> = {
  feature: "feature",
  improvement: "improvement",
  fix: "fix",
  security: "security",
  performance: "performance",
};

const CATEGORY_VARIANT: Record<TicketCategory, "outline" | "secondary" | "destructive" | "default"> = {
  feature: "default",
  improvement: "secondary",
  fix: "destructive",
  security: "outline",
  performance: "default",
};

const UNIT_LABEL: Record<string, string> = { hour: "jam", day: "hari" };

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

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

  const sprints = useQuery({
    queryKey: ["sprints", workspace?.id],
    queryFn: () => listSprints(workspace!.id),
    enabled: !!workspace,
  });

  const agentName = (id: string | null) => {
    const a = agents.data?.find((x) => x.id === id);
    return a ? formatAgentName(a.name, a.role) : id ? id : null;
  };
  const agentOf = (id: string | null) => agents.data?.find((x) => x.id === id);
  const sprintOf = (id: string | null) => sprints.data?.find((s) => s.id === id);

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
          <p className="mt-1 text-xs text-zinc-400">
            Created {formatTimestamp(t.created_at, workspace.timezone)} · Updated{" "}
            {formatTimestamp(t.updated_at, workspace.timezone)}
          </p>
          <div className="mt-2 flex items-center gap-2">
            {t.parent_id === null && (
              <Badge className="bg-violet-600 text-white hover:bg-violet-600">epic</Badge>
            )}
            <Badge variant={PRIORITY_VARIANT[t.priority]}>{t.priority}</Badge>
            <Badge variant="secondary">{t.status}</Badge>
            {t.category && (
              <Badge variant={CATEGORY_VARIANT[t.category]}>{CATEGORY_LABELS[t.category]}</Badge>
            )}
            {sprintOf(t.sprint_id) && (
              <Badge variant="outline">{sprintOf(t.sprint_id)!.name}</Badge>
            )}
            {t.duration_estimate != null && (
              <span className="text-xs text-zinc-500">
                {t.duration_estimate} {UNIT_LABEL[workspace.time_unit] ?? workspace.time_unit}
              </span>
            )}
            {agentName(t.assignee_id) && (
              <span className="flex items-center gap-1.5 text-xs text-zinc-500">
                {agentOf(t.assignee_id) && (
                  <AgentAvatar
                    name={agentOf(t.assignee_id)!.name}
                    template={agentOf(t.assignee_id)!.avatar_template}
                    color={agentOf(t.assignee_id)!.avatar_color}
                    size={16}
                  />
                )}
                {agentName(t.assignee_id)}
              </span>
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
        <CardContent className="text-sm text-zinc-700 dark:text-zinc-300">
          {t.description ? (
            <Markdown>{t.description}</Markdown>
          ) : (
            <span className="text-zinc-400">No description.</span>
          )}
        </CardContent>
      </Card>

      {t.status === "blocked" && (
        <Card className="border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950/30">
          <CardHeader>
            <CardTitle className="text-sm text-red-700 dark:text-red-400">Blocked reason</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-red-800 dark:text-red-300">
            {t.blocked_reason ? (
              <p className="whitespace-pre-wrap">{t.blocked_reason}</p>
            ) : (
              <p className="text-red-500">No reason recorded.</p>
            )}
          </CardContent>
        </Card>
      )}

      {t.parent && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Epic</CardTitle>
          </CardHeader>
          <CardContent>
            <Link
              href={`/w/${workspaceKey}/ticket/${t.parent.key}`}
              className="text-sm hover:underline"
            >
              <span className="font-mono text-xs text-zinc-400">{t.parent.key}</span>{" "}
              {t.parent.title}
            </Link>
          </CardContent>
        </Card>
      )}

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

      <ScreenshotGallery attachments={t.attachments} />

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
              <div className="flex min-w-0 flex-col gap-0.5">
                <a
                  href={attachmentUrl(a.id)}
                  className="truncate hover:underline"
                  download
                >
                  {a.filename}
                </a>
                <span className="text-xs text-zinc-400">
                  {formatBytes(a.size_bytes)} · {formatTimestamp(a.created_at, workspace.timezone)}
                </span>
              </div>
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
        workspaceKey={workspaceKey}
        timezone={workspace.timezone}
        comments={t.comments}
        agentName={agentName}
      />
    </div>
  );
}

function CommentsSection({
  ticketKey,
  workspaceId,
  workspaceKey,
  timezone,
  comments,
  agentName,
}: {
  ticketKey: string;
  workspaceId: string;
  workspaceKey: string;
  timezone: string;
  comments: import("@/lib/api").Comment[];
  agentName: (id: string | null) => string | null;
}) {
  const queryClient = useQueryClient();
  const agents = useQuery({ queryKey: ["agents", workspaceId], queryFn: () => listAgents(workspaceId) });
  const [body, setBody] = useState("");
  const [visibleCount, setVisibleCount] = useState(15);
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

  const mentionOptions: MentionOption[] = (agents.data ?? [])
    .filter((a) => a.enabled)
    .map((a) => ({
      id: `agent-${a.id}`,
      label: a.name,
      sublabel: a.role,
      group: "Agents",
      insert: a.name,
    }));

  const agentOf = (id: string | null) => agents.data?.find((x) => x.id === id);
  const visible = comments.slice(-visibleCount);
  const hiddenCount = comments.length - visible.length;

  function insertMention(name: string) {
    const cursor = textareaRef.current?.selectionStart ?? body.length;
    const upToCursor = body.slice(0, cursor);
    const replaced = upToCursor.replace(/@([a-zA-Z0-9][a-zA-Z0-9-]*)$/, `@${name} `);
    const newBody = replaced + body.slice(cursor);
    setBody(newBody);
    requestAnimationFrame(() => textareaRef.current?.focus());
  }

  const mentionCatalog = useMemo(
    () => ({
      agents: agents.data ?? [],
      artifacts: [],
      tickets: [],
    }),
    [agents.data],
  );

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Comments</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <div className="flex flex-col gap-3">
          {comments.length === 0 && <p className="text-sm text-zinc-400">No comments yet.</p>}
          {hiddenCount > 0 && (
            <button
              type="button"
              className="self-center rounded-md border border-black/10 px-3 py-1 text-xs text-zinc-500 hover:bg-zinc-100 dark:border-white/10 dark:hover:bg-zinc-800"
              onClick={() => setVisibleCount((n) => n + 15)}
            >
              Tampilkan lebih banyak ({hiddenCount} komentar lagi)
            </button>
          )}
          {visible.map((c) => (
            <div
              key={c.id}
              className={
                c.is_system
                  ? "rounded-md border border-dashed border-zinc-300 bg-zinc-50 px-3 py-2 text-xs text-zinc-500 dark:border-zinc-700 dark:bg-zinc-900/40"
                  : "rounded-md border border-zinc-200 px-3 py-2 text-sm dark:border-zinc-800"
              }
            >
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 font-medium">
                  {!c.is_system && agentOf(c.author_agent_id) && (
                    <AgentAvatar
                      name={agentOf(c.author_agent_id)!.name}
                      template={agentOf(c.author_agent_id)!.avatar_template}
                      color={agentOf(c.author_agent_id)!.avatar_color}
                      size={16}
                    />
                  )}
                  {c.is_system ? "System" : agentName(c.author_agent_id) ?? "Owner"}
                </span>
                <span className="text-xs text-zinc-400">
                  {formatTimestamp(c.created_at, timezone)}
                </span>
              </div>
              <Markdown className="mt-1">
                {linkifyMentions(c.body, workspaceKey, mentionCatalog)}
              </Markdown>
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
          <MentionAutocomplete
            value={body}
            onChange={setBody}
            options={mentionOptions}
            textareaRef={textareaRef}
            onInsert={insertMention}
            placeholder="Write a comment… use @ to mention an agent"
            className="min-h-20 w-full resize-y rounded-md border border-zinc-200 bg-background px-3 py-2 text-sm outline-none focus:border-zinc-400 dark:border-zinc-800 dark:focus:border-zinc-600"
          />
          <Button type="submit" disabled={mutation.isPending || !body.trim()} className="self-start">
            {mutation.isPending ? "Posting…" : "Post comment"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
