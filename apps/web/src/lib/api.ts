import type { Session } from "./types";

export class ApiError extends Error {
  status: number;
  code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

let session: Session | null = null;

export function setSession(next: Session | null) {
  session = next;
}

export function getSession() {
  return session;
}

export async function api<T>(path: string, options: RequestInit = {}, allowRefresh = true): Promise<T> {
  const headers = new Headers(options.headers);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (session?.accessToken) {
    headers.set("Authorization", `Bearer ${session.accessToken}`);
  }
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401 && allowRefresh && session?.refreshToken && !path.startsWith("/api/v1/auth/")) {
    const refreshed = await refreshSession();
    if (refreshed) {
      return api<T>(path, options, false);
    }
  }
  if (response.status === 204) {
    return undefined as T;
  }
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const code = body?.error?.code ?? "error";
    const message = body?.error?.message ?? "Request failed.";
    throw new ApiError(response.status, code, message);
  }
  return body as T;
}

export async function refreshSession(): Promise<boolean> {
  if (!session?.refreshToken) {
    return false;
  }
  const response = await fetch("/api/v1/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: session.refreshToken }),
  });
  if (!response.ok) {
    session = null;
    return false;
  }
  const body = await response.json();
  session = { accessToken: body.access_token, refreshToken: body.refresh_token };
  return true;
}
