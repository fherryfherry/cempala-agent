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
