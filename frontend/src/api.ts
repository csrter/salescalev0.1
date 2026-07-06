const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type Role = "owner" | "admin" | "member" | "client";
export const TEAM_ROLES: Role[] = ["owner", "admin", "member"];
export const ADMIN_ROLES: Role[] = ["owner", "admin"];

export interface Session {
  access_token: string;
  role: Role;
  organization_id: string;
  organization_name: string;
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

export async function signup(
  organizationName: string,
  email: string,
  password: string,
  fullName: string
): Promise<Session> {
  const s = await api<Session>("/api/orgs/signup", {
    method: "POST",
    body: JSON.stringify({
      organization_name: organizationName,
      email,
      password,
      full_name: fullName,
    }),
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

// --- Phase 2: staged changes, audit, creatives, Google surface ---

export interface DiffRow {
  field: string;
  before: unknown;
  after: unknown;
}

export interface PendingChange {
  id: string;
  client_id: string;
  platform: "meta" | "google";
  ad_account_id: string;
  entity_type: string;
  entity_id?: string | null;
  entity_external_id?: string | null;
  entity_name?: string | null;
  action: string;
  payload: Record<string, unknown>;
  diff: DiffRow[];
  status: "pending" | "executed" | "failed" | "canceled";
  error_detail?: string | null;
  expires_at: string;
  executed_at?: string | null;
  created_at: string;
}

export interface AuditEntry {
  id: string;
  client_id: string;
  user_email: string;
  user_name: string;
  platform: string;
  ad_account_external_id?: string | null;
  entity_type: string;
  entity_external_id?: string | null;
  entity_name?: string | null;
  action: string;
  diff: DiffRow[];
  status: string;
  error_detail?: string | null;
  created_at: string;
}

export interface StageChangeBody {
  ad_account_id: string;
  entity_type: string;
  action: string;
  entity_id?: string;
  entity_external_id?: string;
  entity_name?: string;
  payload?: Record<string, unknown>;
}

export const stageChange = (body: StageChangeBody) =>
  api<PendingChange>("/api/manage/changes", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const executeChange = (id: string) =>
  api<PendingChange>(`/api/manage/changes/${id}/execute`, { method: "POST" });

export const cancelChange = (id: string) =>
  api<PendingChange>(`/api/manage/changes/${id}`, { method: "DELETE" });

export const listChanges = (status?: string) =>
  api<PendingChange[]>(
    `/api/manage/changes${status ? `?status=${status}` : ""}`
  );

export const listAudit = (params: Record<string, string>) =>
  api<AuditEntry[]>(`/api/audit-log?${new URLSearchParams(params)}`);

export interface Keyword {
  criterion_id: string;
  text: string;
  match_type: string;
  status?: string | null;
  negative: boolean;
}

export interface SearchTerm {
  search_term: string;
  status: string;
  impressions: number;
  clicks: number;
  cost_micros: number;
  conversions: number;
  ad_group_external_id: string;
  campaign_external_id: string;
}

export interface AssetGroup {
  external_id: string;
  name: string;
  status: string;
  ad_strength?: string | null;
  final_urls: string[];
}

export interface CreativeRow {
  id: string;
  client_id: string;
  platform: string;
  external_id: string;
  name?: string | null;
  title?: string | null;
  body?: string | null;
  thumbnail_url?: string | null;
}
