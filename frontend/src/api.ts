const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export interface Session {
  access_token: string;
  role: "team" | "client";
  client_id: string | null;
  full_name: string;
}

export function getSession(): Session | null {
  const raw = localStorage.getItem("session");
  return raw ? (JSON.parse(raw) as Session) : null;
}

export function setSession(s: Session | null) {
  if (s) localStorage.setItem("session", JSON.stringify(s));
  else localStorage.removeItem("session");
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const session = getSession();
  const resp = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(session ? { Authorization: `Bearer ${session.access_token}` } : {}),
      ...init?.headers,
    },
  });
  if (resp.status === 401) {
    setSession(null);
    window.location.reload();
    throw new Error("Session expired");
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail ?? `HTTP ${resp.status}`);
  }
  return (await resp.json()) as T;
}

export async function login(email: string, password: string): Promise<Session> {
  const s = await api<Session>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setSession(s);
  return s;
}

export interface Client {
  id: string;
  name: string;
  status: string;
  internal_notes?: string | null;
}

export interface Connection {
  id: string;
  client_id: string;
  platform: "meta" | "google";
  status: string;
  error_detail?: string | null;
  connected_at?: string | null;
}

export interface AdAccount {
  id: string;
  client_id: string;
  platform: "meta" | "google";
  external_id: string;
  name: string;
  currency?: string | null;
  status?: string | null;
}

export interface Campaign {
  id: string;
  platform: "meta" | "google";
  external_id: string;
  name: string;
  status?: string | null;
  objective?: string | null;
  daily_budget_micros?: number | null;
}

export interface AdGroup {
  id: string;
  platform: "meta" | "google";
  name: string;
  status?: string | null;
}

export interface AdRow {
  id: string;
  platform: "meta" | "google";
  name: string;
  status?: string | null;
}
