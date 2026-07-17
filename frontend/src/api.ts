const BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";
/** Absolute API origin — exported so views can build full URLs (e.g. webhook
 * endpoints a user pastes into a third-party provider's config). */
export const API_BASE = BASE;

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

// "Remember this device" for 2FA — deliberately stored under its OWN key,
// separate from "session". A plain sign-out clears the session but must NOT
// clear this: the entire point is that logging back in on the same browser
// skips the 2FA challenge. It's only cleared by revoking it explicitly, by
// "log out everywhere" (which revokes it server-side too), or by expiring.
function getDeviceToken(): string | null {
  return localStorage.getItem("device_token");
}
function setDeviceToken(t: string | null) {
  if (t) localStorage.setItem("device_token", t);
  else localStorage.removeItem("device_token");
}

// Bound every request so a stalled backend/proxy surfaces as a readable
// timeout instead of the browser's opaque "NetworkError" long after the
// user gave up. Generous: live platform refreshes can legitimately take
// tens of seconds (the backend caps its own platform reads at 45s).
const REQUEST_TIMEOUT_MS = 75_000;

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const session = getSession();
  let resp: Response;
  try {
    resp = await fetch(`${BASE}${path}`, {
      ...init,
      signal: init?.signal ?? AbortSignal.timeout(REQUEST_TIMEOUT_MS),
      headers: {
        "Content-Type": "application/json",
        ...(session ? { Authorization: `Bearer ${session.access_token}` } : {}),
        ...init?.headers,
      },
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "TimeoutError") {
      throw new Error("the server took too long to respond");
    }
    throw e;
  }
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
  // If this browser holds a "remember this device" grant, send it along — a
  // valid one lets the backend skip straight past the 2FA challenge.
  const deviceToken = getDeviceToken();
  const r = await api<LoginResult>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
    headers: deviceToken ? { "X-Device-Token": deviceToken } : undefined,
  });
  if (!isMfaChallenge(r)) setSession(r);
  return r;
}

interface LoginMfaResponse extends Session {
  device_token?: string | null;
}

/** Second step of a 2FA login: exchange the challenge + code for a session.
 * Pass rememberDevice=true to also get a "remember this device" grant back
 * (stored locally so future logins on this browser skip the challenge). */
export async function loginMfa(
  challenge_token: string,
  code: string,
  rememberDevice = false,
): Promise<Session> {
  const s = await api<LoginMfaResponse>("/api/auth/login/mfa", {
    method: "POST",
    body: JSON.stringify({ challenge_token, code, remember_device: rememberDevice }),
  });
  if (s.device_token) setDeviceToken(s.device_token);
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
export const logoutEverywhere = async () => {
  const r = await api<{ ok: boolean }>("/api/auth/logout-all", { method: "POST" });
  // The server just revoked every trusted device too — drop the local copy.
  setDeviceToken(null);
  return r;
};

// --- Remembered 2FA devices ---

export interface TrustedDeviceInfo {
  id: string;
  user_agent: string | null;
  ip: string | null;
  created_at: string;
  last_used_at: string;
  expires_at: string;
}
export const getTrustedDevices = () =>
  api<TrustedDeviceInfo[]>("/api/auth/trusted-devices");
export const revokeTrustedDevice = (id: string) =>
  api<{ ok: boolean }>(`/api/auth/trusted-devices/${id}`, { method: "DELETE" });

// --- Organization (security policy) ---

export interface Org {
  id: string;
  name: string;
  require_mfa: boolean;
  allow_remember_device: boolean;
  sms_opt_in_default: boolean;
  created_at: string;
}
export const getMyOrg = () => api<Org>("/api/orgs/me");
export const setRequireMfa = (require_mfa: boolean) =>
  api<Org>("/api/orgs/me/require-mfa", { method: "PUT", body: JSON.stringify({ require_mfa }) });
export const setAllowRememberDevice = (allow_remember_device: boolean) =>
  api<Org>("/api/orgs/me/allow-remember-device", {
    method: "PUT",
    body: JSON.stringify({ allow_remember_device }),
  });
/** Stamps every NEW contact with a standing SMS-consent attestation
 * (source "org_default:pre_opted_funnel") — for agencies whose intake
 * funnels collect SMS consent before leads reach Salescale. STOP/suppression
 * still always wins at send time; this only affects contact creation. */
export const setOrgSmsOptInDefault = (sms_opt_in_default: boolean) =>
  api<Org>("/api/orgs/me/sms-opt-in-default", {
    method: "PUT",
    body: JSON.stringify({ sms_opt_in_default }),
  });

// --- Lead SMS notifications (text-the-team alerts on new leads) ---

export interface LeadNotificationsConfig {
  enabled: boolean;
  phones: string[];
}

/** Org-level config additionally carries the shared message template (used
 * for both org-wide AND per-client recipients — see ClientLeadNotifications
 * in crm.tsx). null message_template means "use default_template". */
export interface OrgLeadNotificationsConfig extends LeadNotificationsConfig {
  message_template: string | null;
  default_template: string;
}

export const getLeadNotifications = () =>
  api<OrgLeadNotificationsConfig>("/api/orgs/me/lead-notifications");

export const setLeadNotifications = (
  body: LeadNotificationsConfig & { message_template?: string | null }
) =>
  api<OrgLeadNotificationsConfig>("/api/orgs/me/lead-notifications", {
    method: "PUT",
    body: JSON.stringify(body),
  });

/** Per-client counterpart — e.g. the client's own business owner, texted
 * alongside the agency's own ops numbers above. */
export const getClientLeadNotifications = (clientId: string) =>
  api<LeadNotificationsConfig>(`/api/clients/${clientId}/lead-notifications`);

export const setClientLeadNotifications = (
  clientId: string,
  body: LeadNotificationsConfig
) =>
  api<LeadNotificationsConfig>(`/api/clients/${clientId}/lead-notifications`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

// --- Org outreach context (grounds the AI snippet + AI research prompts) ---

export interface OrgOutreachContext {
  company_description: string | null;
  icp: string | null;
  offer: string | null;
  tone_guide: string | null;
}

export const getOrgOutreachContext = () =>
  api<OrgOutreachContext>("/api/orgs/me/outreach-context");

export const setOrgOutreachContext = (body: Partial<OrgOutreachContext>) =>
  api<OrgOutreachContext>("/api/orgs/me/outreach-context", {
    method: "PUT",
    body: JSON.stringify(body),
  });

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
  /** Postal address for the cold-email CAN-SPAM footer (required to send). */
  mailing_address: string | null;
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

// --- Account picker (agency MCC / Business Manager connects) ---
// An agency login sees many ad accounts; the callback attaches nothing when
// the choice is ambiguous, and these endpoints let an Admin assign each
// account to the right client (or move one that landed on the wrong client).

export interface ConnectableAccount {
  external_id: string;
  name: string;
  currency?: string | null;
  timezone?: string | null;
  status?: string | null;
  /** false when another organization already holds this account. */
  available: boolean;
  attached?: {
    account_id: string;
    client_id: string;
    client_name: string;
  } | null;
}

export const listConnectableAccounts = (platform: string, clientId: string) =>
  api<ConnectableAccount[]>(
    `/api/connect/${platform}/accounts?client_id=${clientId}`
  );

export const attachAccounts = (
  platform: string,
  clientId: string,
  externalIds: string[]
) =>
  api<{ attached: number; skipped: string[] }>(
    `/api/connect/${platform}/accounts`,
    {
      method: "POST",
      body: JSON.stringify({ client_id: clientId, external_ids: externalIds }),
    }
  );

export const reassignAdAccount = (accountId: string, clientId: string) =>
  api<{ moved: boolean }>(`/api/ad-accounts/${accountId}`, {
    method: "PATCH",
    body: JSON.stringify({ client_id: clientId }),
  });

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
  cpc_bid_micros?: number | null;
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
  /** Only on the send/resend response when email delivery isn't configured —
   * the admin shares it out-of-band. Never present in list responses. */
  invite_link?: string | null;
}

// The exact OAuth redirect URIs this deployment sends — an operator registers
// them verbatim on their Google/Meta app (connect and sign-in use different
// callback paths; both must be registered or the provider shows
// redirect_uri_mismatch / "URL blocked").
export interface RedirectUri {
  provider: "google" | "meta";
  purpose: "connect" | "signin";
  uri: string;
}

export const getRedirectUris = () =>
  api<RedirectUri[]>("/api/integrations/redirect-uris");

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

// --- Phase 12: Lead Finder & email verification ---

export type VerificationStatus =
  | "unverified"
  | "valid"
  | "risky"
  | "invalid"
  | "unknown";

export interface LeadFinderPlace {
  place_id: string;
  name: string;
  address: string | null;
  phone: string | null;
  website: string | null;
  rating: number | null;
  types: string[];
  in_crm: boolean;
}

export interface MeteredUsage {
  used: number;
  limit: number | null; // null = unlimited
}

export interface LeadFinderUsage {
  searches: MeteredUsage;
  verifications: MeteredUsage;
  plan: string;
}

export interface LeadSearchOptions {
  maxResults?: number; // 20 / 40 / 60 — each page of 20 costs 1 search
  minRating?: number; // Places-side filter, 0.5 steps
  openNow?: boolean;
}

export const searchLeads = (
  query: string,
  location?: string,
  opts: LeadSearchOptions = {}
) =>
  api<{
    search_id: string;
    results: LeadFinderPlace[];
    pages_fetched: number;
    quota_clamped: boolean;
    usage: MeteredUsage;
  }>("/api/lead-finder/search", {
    method: "POST",
    body: JSON.stringify({
      query,
      location: location || null,
      max_results: opts.maxResults ?? 20,
      min_rating: opts.minRating ?? null,
      open_now: opts.openNow ?? false,
    }),
  });

export const importLeads = (
  searchId: string,
  clientId: string,
  places: LeadFinderPlace[]
) =>
  api<{ created: number; contact_ids: string[]; skipped: { place_id: string; reason: string }[] }>(
    "/api/lead-finder/import",
    {
      method: "POST",
      body: JSON.stringify({ search_id: searchId, client_id: clientId, places }),
    }
  );

export const getLeadFinderUsage = () =>
  api<LeadFinderUsage>("/api/lead-finder/usage");

export interface LeadProviderStatus {
  provider: string;
  configured: boolean;
  source: "organization" | "global" | "none";
}

export const listLeadProviders = () =>
  api<LeadProviderStatus[]>("/api/lead-finder/providers");
export const setLeadProviderKey = (provider: string, apiKey: string) =>
  api<LeadProviderStatus>(`/api/lead-finder/providers/${provider}`, {
    method: "PUT",
    body: JSON.stringify({ api_key: apiKey }),
  });
export const deleteLeadProviderKey = (provider: string) =>
  api<LeadProviderStatus>(`/api/lead-finder/providers/${provider}`, {
    method: "DELETE",
  });

/** Active AI provider (operator-selected) + this org's BYO-key status for
 * each. Key writes go through setLeadProviderKey/deleteLeadProviderKey and
 * are owner-only for the AI providers (server-enforced). */
export type AiProvider = "anthropic" | "openai" | "gemini";

export interface AiProviderStatus {
  active: AiProvider;
  model: string;
  /** True when this org has explicitly picked a provider (vs the operator default). */
  org_selected: boolean;
  /** Selectable models per provider — first entry is the recommended default. */
  available: Record<AiProvider, string[]>;
  providers: LeadProviderStatus[];
}

export const getAiProviderStatus = () =>
  api<AiProviderStatus>("/api/integrations/ai-provider");

/** Owner-only: set this org's active AI provider + model (model omitted →
 * the provider's default). Returns the refreshed status. */
export const setAiProvider = (provider: AiProvider, model: string | null) =>
  api<AiProviderStatus>("/api/integrations/ai-provider", {
    method: "PUT",
    body: JSON.stringify({ provider, model }),
  });

/** Re-run enrichment (owner name/title/mobile via the org's profile
 * provider, site discovery, verification) on existing leads — backfills
 * contacts imported before the Apollo key was connected. */
export const enrichContacts = (contactIds: string[]) =>
  api<{ queued: number }>("/api/crm/contacts/enrich", {
    method: "POST",
    body: JSON.stringify({ contact_ids: contactIds }),
  });

/** One enrichment run's progress record (the CRM status card). `status`
 * "interrupted" is server-derived: a running job whose heartbeat went
 * quiet (backend restarted mid-run). */
export interface EnrichmentJob {
  id: string;
  status: "running" | "completed" | "failed" | "interrupted";
  phase: "enriching" | "verifying" | "done";
  total: number;
  processed: number;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  elapsed_seconds: number;
  eta_seconds: number | null;
}

export const getEnrichmentJobs = () =>
  api<{ jobs: EnrichmentJob[]; processing: boolean }>("/api/crm/enrich/jobs");

export const verifyContacts = (contactIds: string[]) =>
  api<{
    verified: Record<string, { verification_status: VerificationStatus; verified_at: string | null }>;
    skipped_no_email: string[];
    usage: MeteredUsage;
  }>("/api/crm/contacts/verify", {
    method: "POST",
    body: JSON.stringify({ contact_ids: contactIds }),
  });

// --- CRM contact edit / delete ---
// Editable identity fields on a contact. All partial; sending null clears the
// field (company_name null/empty clears the Company link). Changing email
// resets the verification verdict server-side.
export interface ContactEditBody {
  first_name?: string | null;
  last_name?: string | null;
  email?: string | null;
  phone?: string | null;
  mobile_phone?: string | null;
  job_title?: string | null;
  city?: string | null;
  state?: string | null;
  zip?: string | null;
  company_name?: string | null;
  /** True records a manual opt-in; false revokes it. */
  sms_opt_in?: boolean | null;
  /** Only the keys present are changed; a key set to null clears that value. */
  custom_fields?: Record<string, unknown> | null;
}

export const updateContact = (id: string, body: ContactEditBody) =>
  api(`/api/crm/contacts/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const deleteContact = (id: string) =>
  api<void>(`/api/crm/contacts/${id}`, { method: "DELETE" });

export const bulkDeleteContacts = (contactIds: string[]) =>
  api<{ deleted: number }>("/api/crm/contacts/bulk-delete", {
    method: "POST",
    body: JSON.stringify({ contact_ids: contactIds }),
  });

/** Apply ONE field (or one custom_fields key) across many contacts at once —
 * the bulk-selection bar's "Edit" action. Identity fields (name/email/phone)
 * are deliberately not exposed in that UI even though this shape accepts
 * them; only city/state/company_name/job_title/sms_opt_in/custom_fields are
 * offered. Cross-org or unknown ids are silently skipped. */
export const bulkUpdateContacts = (contactIds: string[], fields: ContactEditBody) =>
  api<{ updated: number; skipped: number }>("/api/crm/contacts/bulk-update", {
    method: "POST",
    body: JSON.stringify({ contact_ids: contactIds, fields }),
  });

// --- CRM contact lists ---
// Named, client-scoped audiences — like Tags but managed + used directly as
// outreach audiences (enroll-by-list, below).

export interface ContactList {
  id: string;
  name: string;
  client_id: string;
  member_count: number;
}

export const listContactLists = (clientId: string) =>
  api<ContactList[]>(`/api/crm/lists?client_id=${clientId}`);

export const createContactList = (clientId: string, name: string) =>
  api<ContactList>("/api/crm/lists", {
    method: "POST",
    body: JSON.stringify({ client_id: clientId, name }),
  });

export const renameContactList = (id: string, name: string) =>
  api<ContactList>(`/api/crm/lists/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ name }),
  });

export const deleteContactList = (id: string) =>
  api<void>(`/api/crm/lists/${id}`, { method: "DELETE" });

export const addContactsToList = (id: string, contactIds: string[]) =>
  api<{ added: number; skipped: number }>(`/api/crm/lists/${id}/contacts`, {
    method: "POST",
    body: JSON.stringify({ contact_ids: contactIds }),
  });

export const removeContactsFromList = (id: string, contactIds: string[]) =>
  api<{ removed: number }>(`/api/crm/lists/${id}/contacts/remove`, {
    method: "POST",
    body: JSON.stringify({ contact_ids: contactIds }),
  });

// --- AI research fields ("Claygent-lite") ---
// Org-defined research questions answered per-contact by the AI provider,
// grounded ONLY in the contact/company's own CRM+enrichment facts and text
// fetched from their own website (same polite-crawler posture as
// enrichment.py) — never Meta surfaces, never free-generated. Values render
// via the {{research.<key>}} template token alongside custom fields.

export interface ResearchFieldDef {
  id: string;
  key: string;
  label: string;
  prompt: string;
  max_words: number;
  archived: boolean;
}

export const listResearchFields = () =>
  api<ResearchFieldDef[]>("/api/crm/research-fields");

export const createResearchField = (body: {
  key?: string;
  label: string;
  prompt: string;
  max_words?: number;
}) =>
  api<ResearchFieldDef>("/api/crm/research-fields", {
    method: "POST",
    body: JSON.stringify(body),
  });

export const updateResearchField = (
  id: string,
  body: { label?: string; prompt?: string; max_words?: number; archived?: boolean }
) =>
  api<ResearchFieldDef>(`/api/crm/research-fields/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });

export const deleteResearchField = (id: string) =>
  api<void>(`/api/crm/research-fields/${id}`, { method: "DELETE" });

/** Queues background research for up to 200 contacts at once — returns
 * immediately with a receipt; results land on each contact's research
 * fields as the background task completes. */
export const runResearch = (body: {
  contact_ids: string[];
  field_keys?: string[];
  force?: boolean;
}) =>
  api<{ queued: number }>("/api/crm/research/run", {
    method: "POST",
    body: JSON.stringify(body),
  });

// ==========================================================================
// Cold-email outreach module (base /api/email-outreach).
// A single mailbox → campaigns (multi-step sequences) → enrollments, plus a
// unified inbox, suppression list, and analytics. Every send routes through
// the Phase-12 verification gate server-side (risky warned, invalid excluded).
// ==========================================================================

export type EmailSmtpSecurity = "ssl" | "starttls";
export type EmailAccountStatus = "active" | "error";

export interface EmailAccount {
  id: string;
  name: string;
  from_name: string;
  from_email: string;
  smtp_host: string;
  smtp_port: number;
  smtp_security: EmailSmtpSecurity;
  imap_host: string;
  imap_port: number;
  imap_security: EmailSmtpSecurity;
  smtp_username: string;
  imap_username: string;
  status: EmailAccountStatus;
  error_detail: string | null;
  daily_send_cap: number;
  warmup_enabled: boolean;
  warmup_started_at: string | null;
  warmup_target_daily: number;
  /** IANA zone the warmup window/weekends follow; null = UTC. */
  warmup_timezone: string | null;
  warmup_stage: string | null;
  /** Deterministic ramp maturity 0–100 (100 = fully warmed, maintenance). */
  warmup_progress: number;
  /** Measured reputation 0–100, null until enough warmup data exists. */
  warmup_health: number | null;
  /** Today's planned synthetic warmup volume (0 on weekends / warmup off). */
  warmup_volume_today: number;
  /** Warmup sends already made today. */
  warmup_sends_today: number;
  /** Lifetime warmup engagement counters. */
  warmup_totals: { sent: number; delivered: number; junk: number };
  /** Day 10+ of the ramp — low-volume real sends should begin. */
  warmup_blended_ready: boolean;
  effective_daily_cap: number;
  sends_today: number;
  last_synced_at: string | null;
  signature: string | null;
}

export interface EmailAccountBody {
  name?: string;
  from_name?: string;
  from_email?: string;
  smtp_host?: string;
  smtp_port?: number;
  smtp_security?: EmailSmtpSecurity;
  imap_host?: string;
  imap_port?: number;
  imap_security?: EmailSmtpSecurity;
  smtp_username?: string;
  /** Only sent to rotate the SMTP password (write-only, never returned). */
  smtp_password?: string;
  imap_username?: string;
  /** Only sent to rotate the IMAP password (write-only, never returned). */
  imap_password?: string;
  daily_send_cap?: number;
  warmup_enabled?: boolean;
  warmup_target_daily?: number;
  warmup_timezone?: string | null;
  signature?: string | null;
}

export type EmailCampaignStatus = "draft" | "active" | "paused" | "archived";

export interface EmailCampaign {
  id: string;
  name: string;
  status: EmailCampaignStatus;
  account_id: string;
  /** Sending pool, rotation order. Each contact is assigned one mailbox at
   * first send and keeps it for the whole sequence (thread continuity). */
  account_ids: string[];
  steps_count: number;
  enrolled: number;
  active_enrollments: number;
  sent: number;
  delivery_rate: number | null;
  open_rate: number | null;
  reply_rate: number | null;
  bounce_rate: number | null;
  unsubscribe_rate: number | null;
  /** When on, an enrollment's next step is held (deferred, not skipped) until
   * a team member approves it on the Review tab. */
  require_approval: boolean;
  created_at: string;
}

export interface EmailStep {
  id?: string;
  position: number;
  wait_days: number;
  subject: string | null;
  body: string;
  ai_instructions: string | null;
}

export interface EmailCampaignDetail extends EmailCampaign {
  timezone: string;
  send_window_start: number;
  send_window_end: number;
  send_days: number[];
  daily_cap: number | null;
  open_tracking: boolean;
  steps: EmailStep[];
  /** Short style/voice note for the AI snippet ("warm, plainspoken, no hype"). */
  ai_tone: string | null;
  /** Few-shot example email the AI snippet should match the voice of. */
  ai_example: string | null;
}

export interface EmailCampaignBody {
  name?: string;
  account_id?: string;
  account_ids?: string[];
  timezone?: string;
  send_window_start?: number;
  send_window_end?: number;
  send_days?: number[];
  daily_cap?: number | null;
  open_tracking?: boolean;
  require_approval?: boolean;
  ai_tone?: string | null;
  ai_example?: string | null;
}

export interface EmailEnrollment {
  id: string;
  contact: {
    id: string;
    first_name: string | null;
    last_name: string | null;
    email: string | null;
    company_name: string | null;
  };
  status: string;
  exit_reason: string | null;
  current_position: number;
  next_run_at: string | null;
  replied_at: string | null;
  created_at: string;
}

export interface EnrollReceipt {
  enrolled: number;
  risky: { contact_id: string; email: string }[];
  skipped: {
    contact_id: string;
    reason: "invalid_email" | "suppressed" | "no_email" | "already_enrolled";
  }[];
}

export type EmailMessageDirection = "in" | "out";

export interface EmailThread {
  id: string;
  account_id: string;
  contact: {
    id: string;
    first_name: string | null;
    last_name: string | null;
    email: string | null;
  } | null;
  subject: string;
  snippet: string;
  last_message_at: string | null;
  /** When the prospect last replied — null means sent, still awaiting a reply. */
  last_inbound_at: string | null;
  unread: boolean;
  message_count: number;
}

/** Inbox scope: all conversations, sent-and-awaiting-reply, or replied. */
export type EmailInboxFilter = "all" | "awaiting" | "replied";

export interface EmailMessage {
  id: string;
  direction: EmailMessageDirection;
  status: string;
  kind: string;
  subject: string | null;
  body_text: string;
  sent_at: string | null;
  received_at: string | null;
  opened_at: string | null;
  open_count: number;
}

export interface EmailSuppression {
  id: string;
  email: string;
  reason: string;
  created_at: string;
}

export interface EmailRateBlock {
  sent: number;
  delivered: number;
  bounced: number;
  opened: number;
  replied: number;
  unsubscribed: number;
  delivery_rate: number | null;
  open_rate: number | null;
  reply_rate: number | null;
  bounce_rate: number | null;
  unsubscribe_rate: number | null;
}

export interface EmailAnalytics {
  /** False when no AI key resolves for the org — {{ai_snippet}} renders empty. */
  ai_configured?: boolean;
  totals: EmailRateBlock;
  by_day: {
    date: string;
    sent: number;
    opened: number;
    replied: number;
    bounced: number;
  }[];
  by_campaign: {
    campaign_id: string;
    name: string;
    sent: number;
    delivery_rate: number | null;
    open_rate: number | null;
    reply_rate: number | null;
  }[];
  by_step?: { position: number; sent: number; opened: number; replied: number }[];
  accounts: {
    account_id: string;
    from_email: string;
    status: EmailAccountStatus;
    sends_today: number;
    effective_daily_cap: number;
    warmup_stage: string | null;
    warmup_progress: number;
    warmup_health: number | null;
    bounce_rate_7d: number | null;
  }[];
}

export interface EmailUsage {
  sends: { used: number; limit: number | null };
  plan: string;
}

const EO = "/api/email-outreach";

// --- accounts ---
export const listEmailAccounts = () => api<EmailAccount[]>(`${EO}/accounts`);
export const createEmailAccount = (
  body: EmailAccountBody & { smtp_password: string; imap_password: string }
) => api<EmailAccount>(`${EO}/accounts`, { method: "POST", body: JSON.stringify(body) });
export const updateEmailAccount = (id: string, body: EmailAccountBody) =>
  api<EmailAccount>(`${EO}/accounts/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteEmailAccount = (id: string) =>
  api(`${EO}/accounts/${id}`, { method: "DELETE" });
export const testEmailAccount = (id: string) =>
  api<{ smtp_ok: boolean; imap_ok: boolean; detail: string | null }>(
    `${EO}/accounts/${id}/test`,
    { method: "POST" },
  );

// --- campaigns ---
export const listEmailCampaigns = () => api<EmailCampaign[]>(`${EO}/campaigns`);
export const getEmailCampaign = (id: string) =>
  api<EmailCampaignDetail>(`${EO}/campaigns/${id}`);
export const createEmailCampaign = (body: EmailCampaignBody & { name: string; account_ids: string[] }) =>
  api<EmailCampaignDetail>(`${EO}/campaigns`, { method: "POST", body: JSON.stringify(body) });
export const updateEmailCampaign = (id: string, body: EmailCampaignBody) =>
  api<EmailCampaignDetail>(`${EO}/campaigns/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
export const saveEmailSteps = (id: string, steps: EmailStep[]) =>
  api<EmailCampaignDetail>(`${EO}/campaigns/${id}/steps`, {
    method: "PUT",
    body: JSON.stringify({ steps }),
  });
export const activateEmailCampaign = (id: string) =>
  api<EmailCampaignDetail>(`${EO}/campaigns/${id}/activate`, { method: "POST" });
export const pauseEmailCampaign = (id: string) =>
  api<EmailCampaignDetail>(`${EO}/campaigns/${id}/pause`, { method: "POST" });
export const archiveEmailCampaign = (id: string) =>
  api<EmailCampaignDetail>(`${EO}/campaigns/${id}/archive`, { method: "POST" });
export const enrollEmailContacts = (
  id: string,
  body: { contact_ids: string[] } | { list_id: string },
) =>
  api<EnrollReceipt>(`${EO}/campaigns/${id}/enroll`, {
    method: "POST",
    body: JSON.stringify(body),
  });
export const listEmailEnrollments = (id: string) =>
  api<EmailEnrollment[]>(`${EO}/campaigns/${id}/enrollments`);
export const unenrollEmail = (campaignId: string, enrollmentId: string) =>
  api(`${EO}/campaigns/${campaignId}/enrollments/${enrollmentId}`, { method: "DELETE" });
export const previewEmailStep = (id: string, contactId: string, position: number) =>
  api<{ subject: string; body: string }>(`${EO}/campaigns/${id}/preview`, {
    method: "POST",
    body: JSON.stringify({ contact_id: contactId, position }),
  });

// --- QA / audience preview (Review tab) ---
// Renders a whole step across every enrolled contact so a human can approve,
// hand-edit, or exclude before anything sends — same render/AI-snippet spend
// as the real send, just earlier and cached on the enrollment.

export type EmailQaStatus = "approved" | null;

export interface EmailPreviewRow {
  enrollment_id: string;
  contact: {
    id: string;
    first_name: string | null;
    last_name: string | null;
    email: string | null;
    company_name: string | null;
  };
  subject: string;
  body: string;
  overridden: boolean;
  qa_status: EmailQaStatus;
  issues: string[];
}

export interface EmailPreviewBatch {
  total: number;
  rows: EmailPreviewRow[];
}

export const previewEmailBatch = (
  campaignId: string,
  params: { position?: number; limit?: number; offset?: number } = {}
) =>
  api<EmailPreviewBatch>(`${EO}/campaigns/${campaignId}/preview-batch`, {
    method: "POST",
    body: JSON.stringify({
      position: params.position ?? 1,
      limit: params.limit ?? 25,
      offset: params.offset ?? 0,
    }),
  });

export const setEmailOverride = (
  enrollmentId: string,
  body: { position: number; subject?: string | null; body: string }
) =>
  api<{ ok: boolean }>(`${EO}/enrollments/${enrollmentId}/override`, {
    method: "PUT",
    body: JSON.stringify(body),
  });

export const clearEmailOverride = (enrollmentId: string, position: number) =>
  api<{ ok: boolean }>(
    `${EO}/enrollments/${enrollmentId}/override?position=${position}`,
    { method: "DELETE" }
  );

export type EmailQaAction = "approve" | "unapprove" | "exclude";

export const campaignQa = (
  campaignId: string,
  body: { enrollment_ids: string[]; action: EmailQaAction }
) =>
  api<{ updated: number }>(`${EO}/campaigns/${campaignId}/qa`, {
    method: "POST",
    body: JSON.stringify(body),
  });

// --- inbox ---
export const listEmailThreads = (
  accountId?: string,
  unread?: boolean,
  filter?: EmailInboxFilter,
) =>
  api<EmailThread[]>(
    `${EO}/inbox${q({
      account_id: accountId,
      unread: unread ? "1" : undefined,
      filter: filter && filter !== "all" ? filter : undefined,
    })}`,
  );
export const listEmailThreadMessages = (threadId: string) =>
  // The endpoint returns { thread, messages } — unwrap to the array the
  // inbox pane renders. (Returning the object made messages.map crash the
  // whole view to a blank background when a real thread was first opened.)
  api<{ messages: EmailMessage[] }>(
    `${EO}/threads/${threadId}/messages`,
  ).then((r) => r.messages);
export const replyEmailThread = (threadId: string, body: string) =>
  api<{ status: string }>(`${EO}/threads/${threadId}/reply`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });
export const markEmailThreadRead = (threadId: string) =>
  api(`${EO}/threads/${threadId}/mark-read`, { method: "POST" });
export const composeEmail = (body: {
  account_id: string;
  contact_id: string;
  subject: string;
  body: string;
}) => api<{ status: string }>(`${EO}/compose`, { method: "POST", body: JSON.stringify(body) });

// --- suppression ---
export const listEmailSuppression = () => api<EmailSuppression[]>(`${EO}/suppression`);
export const addEmailSuppression = (emails: string[]) =>
  api<{ added: number }>(`${EO}/suppression`, {
    method: "POST",
    body: JSON.stringify({ emails }),
  });
export const deleteEmailSuppression = (id: string) =>
  api(`${EO}/suppression/${id}`, { method: "DELETE" });

// --- analytics & usage ---
export const emailAnalytics = (campaignId?: string, days = 30) =>
  api<EmailAnalytics>(
    `${EO}/analytics${q({ campaign_id: campaignId, days: String(days) })}`,
  );
export const emailUsage = () => api<EmailUsage>(`${EO}/usage`);

/** Contacts for the enroll / compose pickers — the house CRM (or any client). */
export interface EmailPickContact {
  id: string;
  first_name: string | null;
  last_name: string | null;
  email: string | null;
  phone: string | null;
  company_name: string | null;
  verification_status?: VerificationStatus | null;
}
export const listCrmContactsForClient = (clientId: string) =>
  api<EmailPickContact[]>(`/api/crm/contacts?client_id=${clientId}`);

/** Same picker contract as EmailPickContact, plus the SMS opt-in flag the
 * gate keys off (services/sms_consent.py — only opted-in contacts are
 * textable, regardless of what number is on file). */
export interface SmsPickContact extends EmailPickContact {
  sms_opt_in?: boolean;
}
export const listSmsCrmContactsForClient = (clientId: string) =>
  api<SmsPickContact[]>(`/api/crm/contacts?client_id=${clientId}`);

// ==========================================================================
// SMS outreach module (base /api/sms).
// Mirrors the cold-email module's shapes (see EmailAccount/EmailCampaign
// above) minus email-only concepts: no threads/subjects/open-tracking/
// ai_snippet/unsubscribe. Every send routes server-side through
// services/sms_consent.sendable() — only contacts with a recorded SMS
// opt-in are textable; the enroll receipt surfaces the skip buckets
// (no_number/no_consent/suppressed/already) rather than silently dropping
// anyone.
// ==========================================================================

export type SmsAccountStatus = "active" | "error";

export type SmsProvider = "twilio" | "sendblue" | "bluebubbles";

/** Recent-send-sampled health signal (last 25 outbound messages). See
 * services/sms_send.channel_health on the backend. */
export interface SmsChannelHealth {
  status: "healthy" | "degraded" | "blocked";
  sent: number;
  delivered: number;
  failed: number;
  downgraded: number;
  sampled: number;
  detail: string;
}

export interface SmsAccount {
  id: string;
  name: string;
  provider: SmsProvider;
  account_sid: string;
  from_number: string | null;
  messaging_service_sid: string | null;
  /** BlueBubbles VPS relay base URL (self-hosted, dev/prototype path). */
  relay_url: string | null;
  /** Minimum seconds enforced between outbound sends on this account, any provider. */
  min_send_spacing_seconds: number | null;
  /** Upper bound of the pacing range — with min, a uniform-random gap in [min, max] is used. */
  max_send_spacing_seconds: number | null;
  status: SmsAccountStatus;
  error_detail: string | null;
  daily_send_cap: number;
  sends_today: number;
  /** URL secret for providers without request signing (Sendblue, BlueBubbles). */
  webhook_token: string | null;
  channel_health: SmsChannelHealth | null;
  created_at: string;
}

export interface SmsAccountBody {
  name?: string;
  provider?: SmsProvider;
  account_sid?: string | null;
  /** Only sent to rotate the auth token / secret (write-only, never returned). */
  auth_token?: string;
  from_number?: string | null;
  messaging_service_sid?: string | null;
  relay_url?: string | null;
  min_send_spacing_seconds?: number | null;
  max_send_spacing_seconds?: number | null;
  daily_send_cap?: number;
}

export type SmsCampaignStatus = "draft" | "active" | "paused" | "archived";

export interface SmsCampaign {
  id: string;
  name: string;
  status: SmsCampaignStatus;
  account_id: string;
  steps_count: number;
  enrolled: number;
  active_enrollments: number;
  sent: number;
  delivery_rate: number | null;
  reply_rate: number | null;
  opt_out_rate: number | null;
  created_at: string;
}

export interface SmsStep {
  id?: string;
  position: number;
  wait_days: number;
  body: string;
  ai_instructions: string | null;
}

export interface SmsCampaignDetail extends SmsCampaign {
  timezone: string;
  send_window_start: number;
  send_window_end: number;
  send_days: number[];
  daily_cap: number | null;
  include_compliance_footer: boolean;
  steps: SmsStep[];
}

export interface SmsCampaignBody {
  name?: string;
  account_id?: string;
  timezone?: string;
  send_window_start?: number;
  send_window_end?: number;
  send_days?: number[];
  daily_cap?: number | null;
  include_compliance_footer?: boolean;
}

export interface SmsEnrollment {
  id: string;
  contact: {
    id: string;
    first_name: string | null;
    last_name: string | null;
    email: string | null;
    company_name: string | null;
  };
  status: string;
  exit_reason: string | null;
  current_position: number;
  next_run_at: string | null;
  created_at: string;
}

export interface SmsEnrollReceipt {
  enrolled: number;
  skipped: {
    contact_id: string;
    reason: "no_number" | "no_consent" | "suppressed" | "already";
  }[];
}

export interface SmsMessage {
  id: string;
  account_id: string;
  direction: EmailMessageDirection;
  status: string;
  kind: string;
  body: string;
  contact: {
    id: string;
    first_name: string | null;
    last_name: string | null;
    phone: string | null;
  } | null;
  sent_at: string | null;
  received_at: string | null;
  read_at: string | null;
}

export interface SmsSuppression {
  id: string;
  phone_e164: string;
  reason: string;
  detail: string | null;
  created_at: string;
}

export interface SmsRateBlock {
  sent: number;
  delivered: number;
  failed: number;
  replied: number;
  opted_out: number;
  delivery_rate: number | null;
  reply_rate: number | null;
  opt_out_rate: number | null;
}

export interface SmsAnalytics {
  /** False when no AI key resolves for the org — {{ai_snippet}} renders empty. */
  ai_configured?: boolean;
  totals: SmsRateBlock;
  by_day: {
    date: string;
    sent: number;
    delivered: number;
    replied: number;
    failed: number;
  }[];
  by_campaign: {
    campaign_id: string;
    name: string;
    sent: number;
    delivery_rate: number | null;
    reply_rate: number | null;
  }[];
  accounts: {
    account_id: string;
    from_number: string | null;
    status: SmsAccountStatus;
    sends_today: number;
    daily_send_cap: number;
  }[];
}

export interface SmsUsage {
  sends: { used: number; limit: number | null };
  plan: string;
}

const SO = "/api/sms";

// --- accounts ---
export const listSmsAccounts = () => api<SmsAccount[]>(`${SO}/accounts`);
export const createSmsAccount = (
  body: SmsAccountBody & {
    name: string;
    /** Optional/null for bluebubbles — the backend fills a placeholder SID. */
    account_sid?: string | null;
    auth_token: string;
  },
) => api<SmsAccount>(`${SO}/accounts`, { method: "POST", body: JSON.stringify(body) });
export const updateSmsAccount = (id: string, body: SmsAccountBody) =>
  api<SmsAccount>(`${SO}/accounts/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const deleteSmsAccount = (id: string) =>
  api(`${SO}/accounts/${id}`, { method: "DELETE" });
export const testSmsAccount = (id: string) =>
  api<{ ok: boolean; detail: string | null }>(`${SO}/accounts/${id}/test`, {
    method: "POST",
  });

// --- campaigns ---
export const listSmsCampaigns = () => api<SmsCampaign[]>(`${SO}/campaigns`);
export const getSmsCampaign = (id: string) =>
  api<SmsCampaignDetail>(`${SO}/campaigns/${id}`);
export const createSmsCampaign = (body: SmsCampaignBody & { name: string; account_id: string }) =>
  api<SmsCampaignDetail>(`${SO}/campaigns`, { method: "POST", body: JSON.stringify(body) });
export const updateSmsCampaign = (id: string, body: SmsCampaignBody) =>
  api<SmsCampaignDetail>(`${SO}/campaigns/${id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
export const saveSmsSteps = (id: string, steps: SmsStep[]) =>
  api<SmsCampaignDetail>(`${SO}/campaigns/${id}/steps`, {
    method: "PUT",
    body: JSON.stringify({ steps }),
  });
export const activateSmsCampaign = (id: string) =>
  api<SmsCampaignDetail>(`${SO}/campaigns/${id}/activate`, { method: "POST" });
export const pauseSmsCampaign = (id: string) =>
  api<SmsCampaignDetail>(`${SO}/campaigns/${id}/pause`, { method: "POST" });
export const archiveSmsCampaign = (id: string) =>
  api<SmsCampaignDetail>(`${SO}/campaigns/${id}/archive`, { method: "POST" });
export const enrollSmsContacts = (
  id: string,
  body: { contact_ids?: string[]; client_id?: string; list_id?: string },
) =>
  api<SmsEnrollReceipt>(`${SO}/campaigns/${id}/enroll`, {
    method: "POST",
    body: JSON.stringify(body),
  });
export const listSmsEnrollments = (id: string) =>
  api<SmsEnrollment[]>(`${SO}/campaigns/${id}/enrollments`);
export const unenrollSms = (campaignId: string, enrollmentId: string) =>
  api(`${SO}/campaigns/${campaignId}/enrollments/${enrollmentId}`, { method: "DELETE" });
export const previewSmsStep = (id: string, contactId: string, position: number) =>
  api<{ body: string }>(`${SO}/campaigns/${id}/preview`, {
    method: "POST",
    body: JSON.stringify({ contact_id: contactId, position }),
  });

// --- messages (conversation list — SMS has no threads) ---
export const listSmsMessages = (contactId?: string) =>
  api<SmsMessage[]>(`${SO}/messages${q({ contact_id: contactId })}`);

export const composeSms = (body: {
  account_id: string;
  contact_id: string;
  body: string;
}) => api<{ status: string; message_id: string | null }>(`${SO}/compose`, {
  method: "POST",
  body: JSON.stringify(body),
});

export const markSmsRead = (contactId: string) =>
  api<{ marked: number }>(`${SO}/messages/mark-read`, {
    method: "POST",
    body: JSON.stringify({ contact_id: contactId }),
  });

// --- suppression ---
export const listSmsSuppression = () => api<SmsSuppression[]>(`${SO}/suppression`);
export const addSmsSuppression = (phone: string, detail?: string) =>
  api<{ ok: boolean; phone_e164: string }>(`${SO}/suppression`, {
    method: "POST",
    body: JSON.stringify({ phone, detail }),
  });
export const deleteSmsSuppression = (id: string) =>
  api(`${SO}/suppression/${id}`, { method: "DELETE" });

// --- analytics & usage ---
export const smsAnalytics = (campaignId?: string, days = 30) =>
  api<SmsAnalytics>(`${SO}/analytics${q({ campaign_id: campaignId, days: String(days) })}`);
export const smsUsage = () => api<SmsUsage>(`${SO}/usage`);

/** The two per-account Twilio webhook URLs the user must paste into their
 * Twilio number / Messaging Service config. Built client-side from API_BASE —
 * inbound is REQUIRED for STOP/HELP keyword handling to ever reach us. */
export function smsWebhookUrls(account: {
  id: string;
  provider: SmsProvider;
  webhook_token: string | null;
}): { inbound: string; status: string } {
  if (account.provider === "bluebubbles") {
    // BlueBubbles posts both new-message and updated-message events to the
    // SAME inbound URL; auth is a shared secret (header or ?secret=), not a
    // second status endpoint.
    return {
      inbound: `${API_BASE}/api/webhooks/imessage/bluebubbles/${account.id}`,
      status: `${API_BASE}/api/webhooks/imessage/bluebubbles/${account.id}`,
    };
  }
  if (account.provider === "sendblue") {
    // Sendblue webhooks carry no documented signature header, so the
    // per-account token in the URL path is the authenticity check.
    const t = account.webhook_token ?? "";
    return {
      inbound: `${API_BASE}/api/sms/webhooks/sendblue/inbound/${account.id}/${t}`,
      status: `${API_BASE}/api/sms/webhooks/sendblue/status/${account.id}/${t}`,
    };
  }
  return {
    inbound: `${API_BASE}/api/sms/webhooks/inbound/${account.id}`,
    status: `${API_BASE}/api/sms/webhooks/status/${account.id}`,
  };
}
