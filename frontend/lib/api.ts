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
}

export function getHealth(): Promise<HealthResponse> {
  return apiFetch<HealthResponse>("/health");
}

export interface Workspace {
  id: string;
  name: string;
  key: string;
  repo_path: string;
  paused: boolean;
  guardrails: Record<string, unknown>;
  ticket_counter: number;
  created_at: string;
}

export interface WorkspaceCreate {
  name: string;
  key: string;
  repo_path: string;
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

export type Role = "pm" | "lead" | "engineer" | "designer" | "qa" | "pentester";
export type ToolKind = "opencode" | "claude" | "agy" | "codex";

export interface Agent {
  id: string;
  workspace_id: string;
  name: string;
  role: Role;
  model: string;
  tool_kind: ToolKind;
  system_prompt: string | null;
  enabled: boolean;
  status: string;
  created_at: string;
}

export interface AgentCreate {
  name: string;
  role: Role;
  model: string;
  tool_kind: ToolKind;
  system_prompt?: string;
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

export function getModels(): Promise<string[]> {
  return apiFetch<string[]>("/models");
}

export type TicketStatus =
  | "backlog"
  | "todo"
  | "in_progress"
  | "review"
  | "qa"
  | "security"
  | "done"
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
}

export interface TicketUpdate {
  title?: string;
  description?: string;
  priority?: TicketPriority;
  assignee_id?: string;
  status?: TicketStatus;
  actor_agent_id?: string;
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

export interface Attachment {
  id: string;
  ticket_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  path: string;
  created_at: string;
}

export interface Run {
  id: string;
  ticket_id: string;
  agent_id: string;
  status: string;
  trigger: string;
  tool_kind: string;
  model: string;
  cost: number;
  started_at: string | null;
  ended_at: string | null;
}

export interface TicketDetail extends Ticket {
  comments: Comment[];
  attachments: Attachment[];
  runs: Run[];
  children: Ticket[];
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

export function attachmentUrl(id: string): string {
  return `${API_BASE_URL}/attachments/${id}`;
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
