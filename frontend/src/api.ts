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
  is_superadmin?: boolean;
  email_verified?: boolean;
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
  // Only treat a 401 as an expired session when we actually sent one. A 401
  // on an unauthenticated call (e.g. wrong login credentials) must surface as
  // an error the form can show — not silently reload the page.
  if (resp.status === 401 && session) {
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

export const oauthStart = (provider: "google" | "meta") =>
  api<{ url: string }>(`/api/auth/oauth/${provider}/start`);

/** After a social-login redirect (token in the URL fragment), fetch the full
 * session for that token and persist it. */
export async function sessionFromToken(token: string): Promise<Session> {
  setSession({ access_token: token } as Session);
  const s = await api<Session>("/api/auth/me");
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

export const createClient = (body: { name: string; internal_notes?: string }) =>
  api<Client>("/api/clients", { method: "POST", body: JSON.stringify(body) });

// --- Account recovery / verification (no auth required) ---

export const verifyEmail = (token: string) =>
  api<{ ok: boolean }>("/api/auth/verify-email", {
    method: "POST",
    body: JSON.stringify({ token }),
  });

export const resendVerification = () =>
  api<{ ok: boolean }>("/api/auth/resend-verification", { method: "POST" });

export const forgotPassword = (email: string) =>
  api<{ ok: boolean }>("/api/auth/forgot-password", {
    method: "POST",
    body: JSON.stringify({ email }),
  });

export const resetPassword = (token: string, newPassword: string) =>
  api<{ ok: boolean }>("/api/auth/reset-password", {
    method: "POST",
    body: JSON.stringify({ token, new_password: newPassword }),
  });

// --- Billing ---

export interface Subscription {
  plan: OrgPlan;
  status: string | null;
  billing_enabled: boolean;
}

export const getSubscription = () =>
  api<Subscription>("/api/billing/subscription");

export const startCheckout = (plan: OrgPlan) =>
  api<{ url: string }>("/api/billing/checkout", {
    method: "POST",
    body: JSON.stringify({ plan }),
  });

export const openBillingPortal = () =>
  api<{ url: string }>("/api/billing/portal", { method: "POST" });

// --- Per-org platform API credentials (bring-your-own app) ---

export interface IntegrationStatus {
  provider: "meta" | "google";
  configured: boolean;
  source: "organization" | "global" | "none";
  public_id: string | null;
}

export const listIntegrations = () =>
  api<IntegrationStatus[]>("/api/integrations");

export const setMetaCreds = (body: { app_id: string; app_secret: string }) =>
  api<IntegrationStatus>("/api/integrations/meta", {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const setGoogleCreds = (body: {
  client_id: string;
  client_secret: string;
  developer_token: string;
  login_customer_id?: string;
}) =>
  api<IntegrationStatus>("/api/integrations/google", {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const deleteIntegration = (provider: "meta" | "google") =>
  api<IntegrationStatus>(`/api/integrations/${provider}`, { method: "DELETE" });

// --- Platform catalog (GET /api/platforms) ---
// The set of ad platforms is served by the backend registry rather than
// hardcoded here, so a newly-registered platform appears in the UI with no
// frontend change.

export interface Platform {
  id: string;
  name: string;
  status: "live" | "scaffold" | "stub";
  coming_soon: boolean;
  connectable: boolean;
  supports_conversions: boolean;
  supports_lead_forms: boolean;
  supports_byo_creds: boolean;
}

export const getPlatforms = () => api<Platform[]>("/api/platforms");

export interface Connection {
  id: string;
  client_id: string;
  platform: string;
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

// --- Org admin console: team members ---

export interface TeamMember {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  client_id: string | null;
  is_active: boolean;
  created_at: string;
}

export const listMembers = () => api<TeamMember[]>("/api/orgs/me/members");

export const addMember = (body: {
  email: string;
  password: string;
  full_name: string;
  role: "admin" | "member";
}) =>
  api<TeamMember>("/api/orgs/me/members", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const updateMember = (
  id: string,
  body: { role?: "admin" | "member"; is_active?: boolean }
) =>
  api<TeamMember>(`/api/orgs/me/members/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

// --- Platform super-admin (cross-tenant) ---

export interface AdminStats {
  organizations: number;
  users: number;
  clients: number;
  active_connections: number;
  signups_last_30d: number;
}

export type OrgStatus = "active" | "suspended";
export type OrgPlan = "starter" | "pro" | "agency";
export const ORG_PLANS: OrgPlan[] = ["starter", "pro", "agency"];

export interface AdminOrgRow {
  id: string;
  name: string;
  created_at: string;
  status: OrgStatus;
  plan: OrgPlan;
  user_count: number;
  client_count: number;
  connection_count: number;
  contact_count: number;
}

export interface AdminOrgDetail {
  id: string;
  name: string;
  created_at: string;
  status: OrgStatus;
  plan: OrgPlan;
  users: TeamMember[];
  clients: { id: string; name: string; status: string }[];
}

export interface AdminSignupPoint {
  date: string;
  count: number;
}

export interface PasswordResetResult {
  user_id: string;
  email: string;
  temporary_password: string;
}

export const adminStats = () => api<AdminStats>("/api/admin/stats");
export const adminOrgs = () => api<AdminOrgRow[]>("/api/admin/organizations");
export const adminOrg = (id: string) =>
  api<AdminOrgDetail>(`/api/admin/organizations/${id}`);
export const adminSignups = (days = 30) =>
  api<AdminSignupPoint[]>(`/api/admin/signups?days=${days}`);
export const updateOrg = (
  id: string,
  body: { status?: OrgStatus; plan?: OrgPlan }
) =>
  api<AdminOrgDetail>(`/api/admin/organizations/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
export const resetUserPassword = (userId: string) =>
  api<PasswordResetResult>(`/api/admin/users/${userId}/reset-password`, {
    method: "POST",
  });
