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
