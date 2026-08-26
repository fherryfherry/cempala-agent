const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Typed fetch wrapper for the backend API. `path` is relative to API_BASE_URL, e.g. "/health". */
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });

  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      message = body?.error?.message ?? message;
    } catch {
      // response wasn't JSON; keep statusText
    }
    throw new ApiError(message, res.status);
  }

  return res.json() as Promise<T>;
}

export interface HealthResponse {
  status: string;
  /** opencode version string, or null if the binary wasn't found. */
  opencode: string | null;
  mcp: {
    enabled: boolean;
    api_base: string;
    tools: { name: string; description: string }[];
  };
}

export function getHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health");
}

export type TimeUnit = "hour" | "day";

export type AgentRole = "pm" | "lead" | "engineer" | "designer" | "qa" | "pentester" | "business_analyst" | "system_architect";

export interface Workspace {
  id: string;
  name: string;
  key: string;
  repo_path: string;
  description: string | null;
  paused: boolean;
  guardrails: Record<string, unknown>;
  workflow_prompt: string;
  ticket_counter: number;
  time_unit: TimeUnit;
  timezone: string;
  sprint_creator_roles: AgentRole[];
  created_at: string;
}

export interface WorkspaceCreate {
  name: string;
  key: string;
  repo_path: string;
  description?: string;
}

export function listWorkspaces(): Promise<Workspace[]> {
  return apiFetch<Workspace[]>("/workspaces");
}

export function createWorkspace(body: WorkspaceCreate): Promise<Workspace> {
  return apiFetch<Workspace>("/workspaces", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getWorkspace(workspaceId: string): Promise<Workspace> {
  return apiFetch<Workspace>(`/workspaces/${workspaceId}`);
}

export interface WorkspaceUpdate {
  name?: string;
  repo_path?: string;
  description?: string;
  guardrails?: Record<string, unknown>;
  workflow_prompt?: string;
  time_unit?: TimeUnit;
  timezone?: string;
  sprint_creator_roles?: AgentRole[];
}

export function updateWorkspace(
  workspaceId: string,
  body: WorkspaceUpdate,
): Promise<Workspace> {
  return apiFetch<Workspace>(`/workspaces/${workspaceId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function pauseWorkspace(workspaceId: string): Promise<Workspace> {
  return apiFetch<Workspace>(`/workspaces/${workspaceId}/pause`, { method: "POST" });
}

export function resumeWorkspace(workspaceId: string): Promise<Workspace> {
  return apiFetch<Workspace>(`/workspaces/${workspaceId}/resume`, { method: "POST" });
}

/** Wipes all tickets/sprints (and, via cascade, comments/attachments/runs/events —
 * chat history and Activity are just those) for the workspace. Requires it to
 * already be paused with nothing running/queued. */
export function resetWorkspace(workspaceId: string): Promise<Workspace> {
  return apiFetch<Workspace>(`/workspaces/${workspaceId}/reset`, { method: "POST" });
}

export function getWorkflowPromptDefault(): Promise<{ workflow_prompt: string }> {
  return apiFetch<{ workflow_prompt: string }>("/workspaces/workflow-prompt-default");
}

export type Role = "pm" | "lead" | "engineer" | "designer" | "qa" | "pentester" | "business_analyst" | "system_architect";
export type ToolKind = "opencode" | "claude" | "agy" | "codex";
export type TicketCategory = "feature" | "improvement" | "fix" | "security" | "performance";

/** Display format for every agent name in the UI: "Budi (Engineer)". */
export function formatAgentName(name: string, role?: string): string {
  return role ? `${name} (${role})` : name;
}

export type AvatarTemplate =
  | "person-1"
  | "person-2"
  | "person-3"
  | "person-4"
  | "person-5"
  | "person-6";

export interface Agent {
  id: string;
  workspace_id: string;
  name: string;
  role: Role;
  model: string | null;
  tool_kind: ToolKind;
  system_prompt: string | null;
  avatar_template: AvatarTemplate | null;
  avatar_color: string | null;
  enabled: boolean;
  status: string;
  created_at: string;
  memory_count: number;
}

export interface AgentCreate {
  name: string;
  role: Role;
  model?: string | null;
  tool_kind: ToolKind;
  system_prompt?: string;
  avatar_template?: AvatarTemplate | null;
  avatar_color?: string | null;
}

export interface AgentUpdate {
  name?: string;
  role?: Role;
  model?: string;
  tool_kind?: ToolKind;
  system_prompt?: string;
  enabled?: boolean;
  avatar_template?: AvatarTemplate | null;
  avatar_color?: string | null;
}

export function listAgents(workspaceId: string): Promise<Agent[]> {
  return apiFetch<Agent[]>(`/workspaces/${workspaceId}/agents`);
}

export function createAgent(workspaceId: string, body: AgentCreate): Promise<Agent> {
  return apiFetch<Agent>(`/workspaces/${workspaceId}/agents`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateAgent(agentId: string, body: AgentUpdate): Promise<Agent> {
  return apiFetch<Agent>(`/agents/${agentId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteAgent(agentId: string): Promise<void> {
  return apiFetch<void>(`/agents/${agentId}`, { method: "DELETE" });
}

export interface AgentMemoryEntry {
  id: string;
  agent_id: string;
  note: string;
  origin: "agent" | "owner";
  source_ticket_key: string | null;
  created_at: string;
}

export interface AgentMemoryEntryCreate {
  note: string;
}

export function listAgentMemory(agentId: string): Promise<AgentMemoryEntry[]> {
  return apiFetch<AgentMemoryEntry[]>(`/agents/${agentId}/memory`);
}

export function createAgentMemory(
  agentId: string,
  body: AgentMemoryEntryCreate,
): Promise<AgentMemoryEntry> {
  return apiFetch<AgentMemoryEntry>(`/agents/${agentId}/memory`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function deleteAgentMemory(memoryId: string): Promise<void> {
  return apiFetch<void>(`/agent-memory/${memoryId}`, { method: "DELETE" });
}

export function getModels(): Promise<string[]> {
  return apiFetch<string[]>("/models");
}

export interface OrchestratorModel {
  model: string | null;
}

export function getOrchestratorModel(): Promise<OrchestratorModel> {
  return apiFetch<OrchestratorModel>("/settings/orchestrator-model");
}

export function setOrchestratorModel(model: string | null): Promise<OrchestratorModel> {
  return apiFetch<OrchestratorModel>("/settings/orchestrator-model", {
    method: "PUT",
    body: JSON.stringify({ model }),
  });
}

export type TicketStatus =
  | "backlog"
  | "todo"
  | "in_progress"
  | "review"
  | "qa"
  | "security"
  | "done"
  | "release"
  | "blocked";
export type TicketPriority = "low" | "medium" | "high" | "urgent";

export interface Ticket {
  id: string;
  workspace_id: string;
  key: string;
  title: string;
  description: string | null;
  status: TicketStatus;
  priority: TicketPriority;
  assignee_id: string | null;
  parent_id: string | null;
  category: TicketCategory | null;
  sprint_id: string | null;
  duration_estimate: number | null;
  approved_at: string | null;
  blocked_reason: string | null;
  cost_used: number;
  handoff_depth: number;
  created_at: string;
  updated_at: string;
}

export interface TicketCreate {
  title: string;
  description?: string;
  priority?: TicketPriority;
  assignee_id?: string;
  parent_id?: string;
  category?: TicketCategory;
  sprint_id?: string;
  duration_estimate?: number;
  // Every ticket needs an epic: pass parent_id, or is_new_epic to opt into a new one.
  is_new_epic?: boolean;
}

export interface TicketUpdate {
  title?: string;
  description?: string;
  priority?: TicketPriority;
  assignee_id?: string;
  status?: TicketStatus;
  category?: TicketCategory;
  sprint_id?: string;
  duration_estimate?: number;
  actor_agent_id?: string;
}

export type SprintStatus = "planned" | "active" | "completed";

export interface Sprint {
  id: string;
  workspace_id: string;
  name: string;
  goal: string | null;
  index: number;
  status: SprintStatus;
  duration_estimate: number | null;
  start_date: string | null;
  end_date: string | null;
  created_at: string;
}

export interface SprintCreate {
  name: string;
  goal?: string;
  duration_estimate?: number;
  start_date?: string | null;
  end_date?: string | null;
}

export interface SprintUpdate {
  name?: string;
  goal?: string;
  duration_estimate?: number;
  status?: SprintStatus;
  start_date?: string | null;
  end_date?: string | null;
}

export function listSprints(workspaceId: string): Promise<Sprint[]> {
  return apiFetch<Sprint[]>(`/workspaces/${workspaceId}/sprints`);
}

export function createSprint(workspaceId: string, body: SprintCreate): Promise<Sprint> {
  return apiFetch<Sprint>(`/workspaces/${workspaceId}/sprints`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateSprint(sprintId: string, body: SprintUpdate): Promise<Sprint> {
  return apiFetch<Sprint>(`/sprints/${sprintId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function listTickets(workspaceId: string): Promise<Ticket[]> {
  return apiFetch<Ticket[]>(`/workspaces/${workspaceId}/tickets`);
}

export function createTicket(workspaceId: string, body: TicketCreate): Promise<Ticket> {
  return apiFetch<Ticket>(`/workspaces/${workspaceId}/tickets`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateTicket(key: string, body: TicketUpdate): Promise<Ticket> {
  return apiFetch<Ticket>(`/tickets/${key}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export interface Comment {
  id: string;
  ticket_id: string;
  author_agent_id: string | null;
  is_system: boolean;
  body: string;
  created_at: string;
  mentions: string[];
}

export interface CommentCreate {
  body: string;
  author_agent_id?: string;
}

export type AttachmentOrigin = "upload" | "agent";

export interface Attachment {
  id: string;
  ticket_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  path: string;
  origin: AttachmentOrigin;
  description: string | null;
  created_at: string;
}

export interface ArtifactAttachment extends Attachment {
  ticket_key: string;
  ticket_title: string;
}

export interface ArtifactGroup {
  id: string | null;
  name: string;
  attachments: ArtifactAttachment[];
}

export function listArtifacts(workspaceId: string): Promise<ArtifactGroup[]> {
  return apiFetch<ArtifactGroup[]>(`/workspaces/${workspaceId}/artifacts`);
}

export type RunStatus = "queued" | "running" | "done" | "failed" | "cancelled" | "interrupted";

export interface Run {
  id: string;
  ticket_id: string | null;
  conversation_id: string | null;
  agent_id: string;
  status: RunStatus;
  trigger: string;
  parent_run_id: string | null;
  routine_id: string | null;
  tool_kind: string;
  model: string;
  session_id: string | null;
  tokens_in: number;
  tokens_out: number;
  cost: number;
  report: { status?: string; summary?: string; mention?: string[]; tickets?: unknown[] } | null;
  error: string | null;
  started_at: string | null;
  ended_at: string | null;
}

export type RoutineMode = "idle_only" | "consistent";
export type RoutineStatus = "idle" | "waiting" | "running" | "disabled";

export interface Routine {
  id: string;
  workspace_id: string;
  name: string;
  prompt: string;
  interval_minutes: number;
  mode: RoutineMode;
  agent_id: string | null;
  status: RoutineStatus;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface RoutineCreate {
  name: string;
  prompt: string;
  interval_minutes: number;
  mode: RoutineMode;
  agent_id: string | null;
}

export interface RoutineUpdate {
  name?: string;
  prompt?: string;
  interval_minutes?: number;
  mode?: RoutineMode;
  agent_id?: string | null;
  status?: RoutineStatus;
}

export function listRoutines(workspaceId: string): Promise<Routine[]> {
  return apiFetch<Routine[]>(`/workspaces/${workspaceId}/routines`);
}

export function createRoutine(workspaceId: string, body: RoutineCreate): Promise<Routine> {
  return apiFetch<Routine>(`/workspaces/${workspaceId}/routines`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateRoutine(routineId: string, body: RoutineUpdate): Promise<Routine> {
  return apiFetch<Routine>(`/routines/${routineId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteRoutine(routineId: string): Promise<void> {
  return apiFetch<void>(`/routines/${routineId}`, { method: "DELETE" });
}

export function runRoutineNow(routineId: string): Promise<Routine> {
  return apiFetch<Routine>(`/routines/${routineId}/run`, { method: "POST" });
}

export interface RunEvent {
  id: string;
  run_id: string;
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface RunDetail extends Run {
  events: RunEvent[];
}

export function listRuns(workspaceId: string, status?: string): Promise<Run[]> {
  const qs = status ? `?status=${status}` : "";
  return apiFetch<Run[]>(`/workspaces/${workspaceId}/runs${qs}`);
}

export function getRun(runId: string, opts?: { offset?: number; limit?: number }): Promise<RunDetail> {
  const params = new URLSearchParams();
  if (opts?.offset) params.set("offset", String(opts.offset));
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiFetch<RunDetail>(`/runs/${runId}${qs}`);
}

export function stopRun(runId: string): Promise<Run> {
  return apiFetch<Run>(`/runs/${runId}/stop`, { method: "POST" });
}

export function retryRun(runId: string): Promise<Run> {
  return apiFetch<Run>(`/runs/${runId}/retry`, { method: "POST" });
}

export interface TicketDetail extends Ticket {
  comments: Comment[];
  attachments: Attachment[];
  runs: Run[];
  children: Ticket[];
  parent: Ticket | null;
}

export function getTicket(key: string): Promise<TicketDetail> {
  return apiFetch<TicketDetail>(`/tickets/${key}`);
}

export function listComments(key: string): Promise<Comment[]> {
  return apiFetch<Comment[]>(`/tickets/${key}/comments`);
}

export function createComment(key: string, body: CommentCreate): Promise<Comment> {
  return apiFetch<Comment>(`/tickets/${key}/comments`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function attachmentUrl(id: string, opts?: { inline?: boolean }): string {
  return `${API_BASE_URL}/attachments/${id}${opts?.inline ? "?inline=1" : ""}`;
}

export async function uploadAttachment(key: string, file: File): Promise<Attachment> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE_URL}/tickets/${key}/attachments`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      message = body?.error?.message ?? message;
    } catch {
      // not JSON
    }
    throw new ApiError(message, res.status);
  }
  return res.json() as Promise<Attachment>;
}

export function deleteAttachment(id: string): Promise<void> {
  return apiFetch<void>(`/attachments/${id}`, { method: "DELETE" });
}

export interface Conversation {
  id: string;
  workspace_id: string;
  title: string;
  linked_ticket_key: string | null;
  created_at: string;
  updated_at: string;
  last_message_at: string | null;
}

export interface ConversationCreate {
  title: string;
  linked_ticket_key?: string | null;
}

export interface ConversationMessage {
  id: string;
  conversation_id: string;
  run_id: string | null;
  author_agent_id: string | null;
  is_system: boolean;
  body: string;
  created_at: string;
}

export interface ConversationAttachment {
  id: string;
  conversation_id: string;
  message_id: string | null;
  filename: string;
  content_type: string;
  size_bytes: number;
  path: string;
  created_at: string;
}

export function listConversations(workspaceId: string): Promise<Conversation[]> {
  return apiFetch<Conversation[]>(`/workspaces/${workspaceId}/conversations`);
}

export function createConversation(
  workspaceId: string,
  body: ConversationCreate,
): Promise<Conversation> {
  return apiFetch<Conversation>(`/workspaces/${workspaceId}/conversations`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getConversation(conversationId: string): Promise<Conversation> {
  return apiFetch<Conversation>(`/conversations/${conversationId}`);
}

export function listConversationMessages(
  conversationId: string,
  opts?: { limit?: number; offset?: number },
): Promise<ConversationMessage[]> {
  const params = new URLSearchParams();
  if (opts?.limit) params.set("limit", String(opts.limit));
  if (opts?.offset) params.set("offset", String(opts.offset));
  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiFetch<ConversationMessage[]>(`/conversations/${conversationId}/messages${qs}`);
}

export function postConversationMessage(
  conversationId: string,
  body: string,
): Promise<ConversationMessage> {
  return apiFetch<ConversationMessage>(`/conversations/${conversationId}/messages`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });
}

export function listConversationAttachments(
  conversationId: string,
): Promise<ConversationAttachment[]> {
  return apiFetch<ConversationAttachment[]>(`/conversations/${conversationId}/attachments`);
}

export function conversationAttachmentUrl(id: string): string {
  return `${API_BASE_URL}/conversations/attachments/${id}/download`;
}

export async function uploadConversationAttachment(
  conversationId: string,
  file: File,
): Promise<ConversationAttachment> {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE_URL}/conversations/${conversationId}/attachments`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      message = body?.error?.message ?? message;
    } catch {
      // not JSON
    }
    throw new ApiError(message, res.status);
  }
  return res.json() as Promise<ConversationAttachment>;
}

export function deleteConversationAttachment(id: string): Promise<void> {
  return apiFetch<void>(`/conversations/attachments/${id}`, { method: "DELETE" });
}

// ---------------------------------------------------------------------------
// Git menu
// ---------------------------------------------------------------------------

export interface GitBranch {
  name: string;
  is_current: boolean;
  latest_sha: string;
  latest_subject: string;
}

export interface GitGraphCommit {
  sha: string;
  parents: string[];
  subject: string;
  author_name: string;
  author_date: string;
  lane: number;
  total_lanes: number;
  decorations: string[];
}

export interface GitGraph {
  commits: GitGraphCommit[];
  total_lanes: number;
}

export interface GitCommitList {
  commits: GitGraphCommit[];
  total_lanes: number;
  has_more: boolean;
}

export interface GitCommitFile {
  path: string;
  additions: number;
  deletions: number;
  status: string | null;
}

export interface GitCommitDetail {
  sha: string;
  subject: string;
  author_name: string;
  author_date: string;
  body: string;
  parents: string[];
  is_merge: boolean;
  files: GitCommitFile[];
  patch: string;
  patch_truncated: boolean;
}

export function listGitBranches(workspaceId: string): Promise<GitBranch[]> {
  return apiFetch<GitBranch[]>(`/workspaces/${workspaceId}/git/branches`);
}

export function getGitGraph(
  workspaceId: string,
  opts?: { limit?: number },
): Promise<GitGraph> {
  const params = new URLSearchParams();
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiFetch<GitGraph>(`/workspaces/${workspaceId}/git/graph${qs}`);
}

export function listGitCommits(
  workspaceId: string,
  opts?: { ref?: string; limit?: number; offset?: number },
): Promise<GitCommitList> {
  const params = new URLSearchParams();
  if (opts?.ref) params.set("ref", opts.ref);
  if (opts?.limit) params.set("limit", String(opts.limit));
  if (opts?.offset) params.set("offset", String(opts.offset));
  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiFetch<GitCommitList>(`/workspaces/${workspaceId}/git/commits${qs}`);
}

export function getGitCommit(
  workspaceId: string,
  sha: string,
): Promise<GitCommitDetail> {
  return apiFetch<GitCommitDetail>(
    `/workspaces/${workspaceId}/git/commits/${sha}`,
  );
}
