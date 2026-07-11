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
  // Org requires 2FA and this user hasn't set it up — gate to enrollment.
  mfa_setup_required?: boolean;
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
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

// When the account has 2FA, /login returns a challenge instead of a session.
export interface LoginChallenge {
  mfa_required: true;
  method: "totp" | "email" | "sms";
  challenge_token: string;
}
export type LoginResult = Session | LoginChallenge;

export function isMfaChallenge(r: LoginResult): r is LoginChallenge {
  return (r as LoginChallenge).mfa_required === true;
}

export async function login(email: string, password: string): Promise<LoginResult> {
  const r = await api<LoginResult>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  if (!isMfaChallenge(r)) setSession(r);
  return r;
}

/** Second step of a 2FA login: exchange the challenge + code for a session. */
export async function loginMfa(challenge_token: string, code: string): Promise<Session> {
  const s = await api<Session>("/api/auth/login/mfa", {
    method: "POST",
    body: JSON.stringify({ challenge_token, code }),
  });
  setSession(s);
  return s;
}

// --- 2FA enrollment / management ---

export interface MfaStatus {
  method: "totp" | "email" | "sms" | null;
  phone_hint: string | null;
  backup_codes_remaining: number;
}
export interface TotpSetup {
  secret: string;
  otpauth_uri: string;
}
export interface MfaEnabled {
  method: string;
  backup_codes: string[];
}

export const getMfaStatus = () => api<MfaStatus>("/api/mfa");
export const totpSetup = () => api<TotpSetup>("/api/mfa/totp/setup", { method: "POST" });
export const totpEnable = (code: string) =>
  api<MfaEnabled>("/api/mfa/totp/enable", { method: "POST", body: JSON.stringify({ code }) });
export const emailMfaSetup = () => api<{ ok: boolean }>("/api/mfa/email/setup", { method: "POST" });
export const emailMfaEnable = (code: string) =>
  api<MfaEnabled>("/api/mfa/email/enable", { method: "POST", body: JSON.stringify({ code }) });
export const smsMfaSetup = (phone: string) =>
  api<{ ok: boolean }>("/api/mfa/sms/setup", { method: "POST", body: JSON.stringify({ phone }) });
export const smsMfaEnable = (code: string) =>
  api<MfaEnabled>("/api/mfa/sms/enable", { method: "POST", body: JSON.stringify({ code }) });
export const disableMfa = (password: string) =>
  api<{ ok: boolean }>("/api/mfa/disable", { method: "POST", body: JSON.stringify({ password }) });

// --- Active sessions / devices ---

export interface SessionInfo {
  id: string;
  user_agent: string | null;
  ip: string | null;
  created_at: string;
  last_seen_at: string;
  current: boolean;
}
export const getSessions = () => api<SessionInfo[]>("/api/auth/sessions");
export const revokeSession = (id: string) =>
  api<{ ok: boolean }>(`/api/auth/sessions/${id}`, { method: "DELETE" });
export const logoutEverywhere = () =>
  api<{ ok: boolean }>("/api/auth/logout-all", { method: "POST" });

// --- Organization (security policy) ---

export interface Org {
  id: string;
  name: string;
  require_mfa: boolean;
  created_at: string;
}
export const getMyOrg = () => api<Org>("/api/orgs/me");
export const setRequireMfa = (require_mfa: boolean) =>
  api<Org>("/api/orgs/me/require-mfa", { method: "PUT", body: JSON.stringify({ require_mfa }) });

/** The org's own "house" prospect pipeline lives on a hidden client that the
 * server gets-or-creates. Team-only; not returned by GET /api/clients. */
export const getHouseClient = () =>
  api<{ client_id: string }>("/api/orgs/me/house-client");

/** Refresh the persisted session from /me (e.g. after enrolling 2FA clears the
 * mfa_setup_required gate). */
export async function refreshSession(): Promise<Session> {
  const s = await api<Session>("/api/auth/me");
  setSession(s);
  return s;
}

export const oauthStart = (provider: "google" | "meta") =>
  api<{ url: string }>(`/api/auth/oauth/${provider}/start`);

// The desktop (Electron) app injects this bridge via preload; on web it's
// undefined. See electron-app/preload.js.
declare global {
  interface Window {
    salescale?: {
      isDesktop?: boolean;
      openExternal?: (url: string) => void;
    };
  }
}

/** Send the user to an OAuth authorize URL. In the desktop app the UI is
 * served from file://, so navigating the window to an external URL would
 * hijack the app — open it in the system browser instead (the OAuth callback
 * returns to the local backend on 127.0.0.1:8000). On web, navigate normally. */
export function openAuthUrl(url: string): void {
  if (window.salescale?.isDesktop && window.salescale.openExternal) {
    window.salescale.openExternal(url);
  } else {
    window.location.href = url;
  }
}

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

// --- White-label branding (Owner/Admin settings surface) ---

export interface OrgBranding {
  product_name: string;
  logo_url: string | null;
  favicon_url: string | null;
  colors: Record<string, string>;
  email_from_name: string | null;
  email_from_address: string | null;
  apply_to_team: boolean;
}

export interface CustomDomainState {
  domain: string | null;
  verified: boolean;
  verification_token: string | null;
  txt_record_name: string | null;
}

export interface BrandingConfig {
  branding: OrgBranding;
  custom_domain: CustomDomainState;
  white_labeling_available: boolean;
}

export const getOrgBranding = () => api<BrandingConfig>("/api/orgs/me/branding");

export const setOrgBranding = (body: Partial<OrgBranding>) =>
  api<{ branding: OrgBranding }>("/api/orgs/me/branding", {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const clearOrgBranding = () =>
  api<void>("/api/orgs/me/branding", { method: "DELETE" });

export const setCustomDomain = (domain: string) =>
  api<{
    domain: string;
    verified: boolean;
    verification_token: string;
    txt_record_name: string;
    instructions: string;
  }>("/api/orgs/me/custom-domain", {
    method: "PUT",
    body: JSON.stringify({ domain }),
  });

export const verifyCustomDomain = () =>
  api<{ domain: string; verified: boolean; detail?: string }>(
    "/api/orgs/me/custom-domain/verify",
    { method: "POST" }
  );

export const clearCustomDomain = () =>
  api<void>("/api/orgs/me/custom-domain", { method: "DELETE" });

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

export const removeMember = (id: string, reassignToUserId?: string) =>
  api<{ ok: boolean }>(`/api/orgs/me/members/${id}`, {
    method: "DELETE",
    body: JSON.stringify({ reassign_to_user_id: reassignToUserId ?? null }),
  });

export const transferOwnership = (memberId: string) =>
  api<{ ok: boolean }>("/api/orgs/me/transfer-ownership", {
    method: "POST",
    body: JSON.stringify({ member_id: memberId }),
  });

// --- Phase 13: invites, seats, memberships ---

export interface Invite {
  id: string;
  email: string;
  role: "admin" | "member";
  status: "pending" | "accepted" | "revoked" | "expired";
  invited_by_user_id: string;
  expires_at: string;
  created_at: string;
}

export interface SeatUsage {
  used: number;
  pending_invites: number;
  limit: number | null; // null = unlimited
  plan: string;
}

export interface MembershipAuditEntry {
  id: string;
  actor_email: string;
  actor_name: string;
  action: string;
  target_email: string | null;
  detail: Record<string, unknown> | null;
  created_at: string;
}

export const listInvites = () => api<Invite[]>("/api/orgs/me/invites");
export const sendInvite = (body: { email: string; role: "admin" | "member" }) =>
  api<Invite>("/api/orgs/me/invites", { method: "POST", body: JSON.stringify(body) });
export const resendInvite = (id: string) =>
  api<Invite>(`/api/orgs/me/invites/${id}/resend`, { method: "POST" });
export const revokeInvite = (id: string) =>
  api<Invite>(`/api/orgs/me/invites/${id}`, { method: "DELETE" });

export const getSeatUsage = () => api<SeatUsage>("/api/orgs/me/seats");
export const listMembershipAudit = () =>
  api<MembershipAuditEntry[]>("/api/orgs/me/membership-audit");

// Invite redemption (pre-auth; the token is the credential).
export interface InviteLookup {
  organization_name: string;
  email: string;
  role: "admin" | "member";
  status: "pending" | "accepted" | "revoked" | "expired";
  account_exists: boolean;
}

export const lookupInvite = (token: string) =>
  api<InviteLookup>(`/api/orgs/invites/lookup?token=${encodeURIComponent(token)}`);

/** Existing, logged-in user joins the inviting org (and switches to it). */
export async function acceptInvite(token: string): Promise<Session> {
  const s = await api<Session>("/api/orgs/invites/accept", {
    method: "POST",
    body: JSON.stringify({ token }),
  });
  setSession(s);
  return s;
}

/** New user: the invite doubles as signup (account starts email-verified). */
export async function acceptInviteSignup(
  token: string,
  fullName: string,
  password: string
): Promise<Session> {
  const s = await api<Session>("/api/orgs/invites/accept-signup", {
    method: "POST",
    body: JSON.stringify({ token, full_name: fullName, password }),
  });
  setSession(s);
  return s;
}

// Multi-org membership & the org switcher.
export interface MyOrg {
  organization_id: string;
  organization_name: string;
  role: Role;
  is_active_org: boolean;
}

export const myOrganizations = () => api<MyOrg[]>("/api/orgs/mine");

export async function switchOrganization(organizationId: string): Promise<Session> {
  const s = await api<Session>("/api/orgs/switch", {
    method: "POST",
    body: JSON.stringify({ organization_id: organizationId }),
  });
  setSession(s);
  return s;
}

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

// --- Outreach (Instagram DM automation) ---

export interface IgAccount {
  id: string;
  client_id: string;
  ig_user_id: string;
  username: string | null;
  name: string | null;
  status: "active" | "disconnected";
  error_detail: string | null;
  daily_send_cap: number;
  automation_paused: boolean;
  connected_at: string | null;
}

export type OutreachTriggerType =
  | "dm"
  | "story_reply"
  | "comment"
  | "live_comment"
  | "mention"
  | "story_mention";

export interface OutreachRule {
  id: string;
  client_id: string;
  account_id: string;
  name: string;
  enabled: boolean;
  trigger_type: OutreachTriggerType;
  keywords: string[];
  media_ids: string[];
  filters: { min_followers?: number; max_followers?: number; verified_only?: boolean };
  reply_text: string | null;
  create_contact: boolean;
  tag_names: string[];
  enroll_sequence_id: string | null;
  capture_prospect: boolean;
  once_per_user: boolean;
}

export interface OutreachStep {
  id?: string;
  position?: number;
  kind: "message" | "wait" | "condition";
  text_a?: string | null;
  text_b?: string | null;
  promoted_variant?: string | null;
  wait_hours?: number | null;
  condition?: string | null;
  on_true?: string | null;
  on_false?: string | null;
}

export interface OutreachSequence {
  id: string;
  client_id: string;
  account_id: string;
  name: string;
  description: string | null;
  status: "draft" | "active" | "paused";
  review_first_day: boolean;
  exit_on_reply: boolean;
  settings: Record<string, unknown>;
  activated_at: string | null;
  steps?: OutreachStep[];
}

export interface OutreachConvo {
  id: string;
  client_id: string;
  account_id: string;
  ig_user_id: string;
  peer: { username?: string; name?: string; follower_count?: number };
  contact_id: string | null;
  contact_name: string | null;
  window_open: boolean;
  human_agent_available: boolean;
  last_user_message_at: string | null;
  last_message_at: string | null;
  last_message_preview: string | null;
  unread_count: number;
  enrollments: {
    id: string;
    sequence_name: string;
    status: string;
    exit_reason: string | null;
  }[];
  deal_value_cents: number | null;
  qualified: boolean;
}

export interface OutreachMsg {
  id: string;
  direction: "in" | "out";
  text: string | null;
  status: string;
  kind: string | null;
  variant: string | null;
  event_type: string | null;
  message_tag: string | null;
  error_detail: string | null;
  sent_at: string | null;
  created_at: string;
}

export interface OutreachProspect {
  id: string;
  client_id: string;
  username: string;
  ig_user_id: string | null;
  source: string;
  status: string;
  vertical: string | null;
  enrichment: Record<string, unknown>;
  contact_id: string | null;
  conversation_id: string | null;
  sequence_id: string | null;
  engaged_at: string | null;
  created_at: string;
}

export interface OutreachAnalytics {
  headline: {
    sent: number;
    received: number;
    reply_rate: number;
    active_enrollments: number;
    avg_reply_seconds: number | null;
  };
  sequences: {
    sequence_id: string;
    name: string;
    status: string;
    enrolled: number;
    sent: number;
    replied: number;
    booked: number;
    closed: number;
    reply_rate: number;
    variants: {
      step_position: number;
      promoted: string | null;
      a: { sent: number; replies: number };
      b: { sent: number; replies: number };
    }[];
  }[];
  rules: {
    rule_id: string;
    name: string;
    trigger_type: string;
    fired: number;
    sent: number;
    replies: number;
  }[];
  verticals: { vertical: string; prospects: number; engaged: number }[];
}

const q = (params: Record<string, string | undefined>) => {
  const usp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v) usp.set(k, v);
  const s = usp.toString();
  return s ? `?${s}` : "";
};

export const listIgAccounts = (clientId?: string) =>
  api<IgAccount[]>(`/api/outreach/accounts${q({ client_id: clientId })}`);
export const igConnectStart = (clientId: string) =>
  api<{ url: string }>(`/api/outreach/accounts/connect/start?client_id=${clientId}`);
export const updateIgAccount = (
  id: string,
  body: { daily_send_cap?: number; automation_paused?: boolean }
) =>
  api<IgAccount>(`/api/outreach/accounts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
export const disconnectIgAccount = (id: string) =>
  api(`/api/outreach/accounts/${id}`, { method: "DELETE" });

export const listOutreachRules = (clientId?: string) =>
  api<OutreachRule[]>(`/api/outreach/rules${q({ client_id: clientId })}`);
export const createOutreachRule = (body: Partial<OutreachRule>) =>
  api<OutreachRule>("/api/outreach/rules", {
    method: "POST",
    body: JSON.stringify(body),
  });
export const updateOutreachRule = (id: string, body: Partial<OutreachRule>) =>
  api<OutreachRule>(`/api/outreach/rules/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
export const deleteOutreachRule = (id: string) =>
  api(`/api/outreach/rules/${id}`, { method: "DELETE" });

export const listOutreachSequences = (clientId?: string) =>
  api<OutreachSequence[]>(`/api/outreach/sequences${q({ client_id: clientId })}`);
export const getOutreachSequence = (id: string) =>
  api<OutreachSequence>(`/api/outreach/sequences/${id}`);
export const createOutreachSequence = (body: {
  account_id: string;
  name: string;
  description?: string;
  review_first_day?: boolean;
  exit_on_reply?: boolean;
}) =>
  api<OutreachSequence>("/api/outreach/sequences", {
    method: "POST",
    body: JSON.stringify(body),
  });
export const saveOutreachSteps = (id: string, steps: OutreachStep[]) =>
  api<OutreachSequence>(`/api/outreach/sequences/${id}/steps`, {
    method: "PUT",
    body: JSON.stringify(steps),
  });
export const activateOutreachSequence = (id: string) =>
  api<OutreachSequence>(`/api/outreach/sequences/${id}/activate`, { method: "POST" });
export const pauseOutreachSequence = (id: string) =>
  api<OutreachSequence>(`/api/outreach/sequences/${id}/pause`, { method: "POST" });

export const outreachInbox = (clientId?: string, search?: string) =>
  api<OutreachConvo[]>(`/api/outreach/inbox${q({ client_id: clientId, q: search })}`);
export const outreachMessages = (conversationId: string) =>
  api<OutreachMsg[]>(`/api/outreach/conversations/${conversationId}/messages`);
export const outreachReply = (
  conversationId: string,
  body: { text: string; use_human_agent?: boolean }
) =>
  api<{ status: string }>(`/api/outreach/conversations/${conversationId}/reply`, {
    method: "POST",
    body: JSON.stringify(body),
  });
export const outreachMarkRead = (conversationId: string) =>
  api(`/api/outreach/conversations/${conversationId}/read`, { method: "POST" });
export const outreachEnroll = (body: { sequence_id: string; conversation_id: string }) =>
  api<{ id: string }>(`/api/outreach/enrollments`, {
    method: "POST",
    body: JSON.stringify(body),
  });
export const outreachUnenroll = (id: string) =>
  api(`/api/outreach/enrollments/${id}`, { method: "DELETE" });

export const outreachPendingMessages = (clientId?: string) =>
  api<{ id: string; conversation_id: string; text: string; created_at: string }[]>(
    `/api/outreach/messages/pending${q({ client_id: clientId })}`
  );
export const outreachApproveMessage = (id: string) =>
  api<{ status: string }>(`/api/outreach/messages/${id}/approve`, { method: "POST" });
export const outreachDiscardMessage = (id: string) =>
  api(`/api/outreach/messages/${id}/discard`, { method: "POST" });

export const listOutreachProspects = (clientId?: string) =>
  api<OutreachProspect[]>(`/api/outreach/prospects${q({ client_id: clientId })}`);
export const importOutreachProspects = (body: {
  client_id: string;
  handles: string[];
  vertical?: string;
  sequence_id?: string;
  account_id?: string;
}) =>
  api<{ created: number; skipped: number }>("/api/outreach/prospects/import", {
    method: "POST",
    body: JSON.stringify(body),
  });
export const enrichOutreachProspect = (id: string) =>
  api<{ status: string }>(`/api/outreach/prospects/${id}/enrich`, { method: "POST" });
export const deleteOutreachProspect = (id: string) =>
  api(`/api/outreach/prospects/${id}`, { method: "DELETE" });

export const outreachAnalytics = (clientId?: string, days = 30) =>
  api<OutreachAnalytics>(
    `/api/outreach/analytics${q({ client_id: clientId, days: String(days) })}`
  );
export const outreachAuditExportUrl = (clientId?: string) =>
  `/api/outreach/audit/export${q({ client_id: clientId })}`;

/** Download an authenticated CSV export (api() is JSON-only). */
export async function downloadCsv(path: string, filename: string) {
  const session = getSession();
  const resp = await fetch(`${BASE}${path}`, {
    headers: session ? { Authorization: `Bearer ${session.access_token}` } : {},
  });
  if (!resp.ok) throw new Error(`Export failed (HTTP ${resp.status})`);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
