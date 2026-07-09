import { useCallback, useEffect, useState } from "react";
import {
  ADMIN_ROLES,
  TEAM_ROLES,
  api,
  createClient,
  getPlatforms,
  getSession,
  login,
  setSession,
  signup,
  type AdAccount,
  type AdGroup,
  type AdRow,
  type Campaign,
  type Client,
  type Connection,
  type Platform,
  type Session,
} from "./api";
import { CreativesPanel } from "./creatives";
import { AssetGroupsPanel, KeywordsPanel, SearchTermsPanel } from "./google";
import {
  AuditLogView,
  ManageProvider,
  PendingChangesPanel,
  useManage,
} from "./manage";
import { Dashboard } from "./dashboard";
import { CrmView } from "./crm";
import { Logo } from "./logo";
import { SuperAdmin, TeamAdmin } from "./admin";
import { Billing, ResetPassword, VerifyEmail } from "./account";
import { Integrations } from "./integrations";
import {
  forgotPassword,
  oauthStart,
  openAuthUrl,
  resendVerification,
  sessionFromToken,
} from "./api";
import "./App.css";

type IconName =
  | "clients"
  | "changes"
  | "audit"
  | "team"
  | "billing"
  | "integrations"
  | "admin"
  | "logout"
  | "plus"
  | "chevron";

const ICON_PATHS: Record<IconName, string> = {
  clients:
    "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M23 21v-2a4 4 0 0 0-3-3.87 M16 3.13a4 4 0 0 1 0 7.75",
  changes: "M6 3v12 M18 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6 M6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6 M15 6a9 9 0 0 0-9 9",
  audit: "M9 11l3 3L22 4 M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11",
  team: "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M23 21v-2a4 4 0 0 0-3-3.87 M16 3.13a4 4 0 0 1 0 7.75",
  admin: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z",
  billing: "M2 7a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2z M2 10h20",
  integrations: "M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1 M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4 M16 17l5-5-5-5 M21 12H9",
  plus: "M12 5v14 M5 12h14",
  chevron: "M9 18l6-6-6-6",
};

function Icon({ name }: { name: IconName }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICON_PATHS[name].split(" M").map((seg, i) => (
        <path key={i} d={i === 0 ? seg : "M" + seg} />
      ))}
    </svg>
  );
}

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
}

type Tab =
  | "clients"
  | "changes"
  | "audit"
  | "team"
  | "integrations"
  | "billing"
  | "admin";

const PAGE_TITLES: Record<Tab, string> = {
  clients: "Clients",
  changes: "Pending changes",
  audit: "Audit log",
  team: "Team",
  integrations: "Integrations",
  billing: "Billing",
  admin: "Platform admin",
};

function clearAuthQuery() {
  window.history.replaceState({}, "", window.location.pathname);
}

export default function App() {
  const [session, setSess] = useState<Session | null>(getSession());
  const [tab, setTab] = useState<Tab>("clients");
  const [authRoute, setAuthRoute] = useState<{ kind: "verify" | "reset"; token: string } | null>(
    () => {
      const p = new URLSearchParams(window.location.search);
      const verify = p.get("verify");
      const reset = p.get("reset");
      if (verify) return { kind: "verify", token: verify };
      if (reset) return { kind: "reset", token: reset };
      return null;
    }
  );
  // Social login returns with the token in the URL fragment (#access_token=…).
  const [oauthBusy, setOauthBusy] = useState(() =>
    window.location.hash.includes("access_token=")
  );

  useEffect(() => {
    const m = window.location.hash.match(/access_token=([^&]+)/);
    if (!m) return;
    sessionFromToken(m[1])
      .then(setSess)
      .catch(() => setSession(null))
      .finally(() => {
        window.history.replaceState({}, "", window.location.pathname);
        setOauthBusy(false);
      });
  }, []);

  if (oauthBusy)
    return (
      <div className="auth-center">
        <div className="auth-card">
          <Logo auth />
          <p className="auth-sub">Signing you in…</p>
        </div>
      </div>
    );

  // Links from verification / reset emails land here before any session.
  if (authRoute?.kind === "verify")
    return (
      <VerifyEmail
        token={authRoute.token}
        onDone={() => {
          clearAuthQuery();
          setAuthRoute(null);
        }}
      />
    );
  if (authRoute?.kind === "reset")
    return (
      <ResetPassword
        token={authRoute.token}
        onDone={() => {
          clearAuthQuery();
          setAuthRoute(null);
        }}
      />
    );

  if (!session) return <Login onLogin={setSess} />;
  const isTeam = TEAM_ROLES.includes(session.role);
  const isAdmin = ADMIN_ROLES.includes(session.role);
  const isOwner = session.role === "owner";
  const nav: { key: Tab; label: string; icon: IconName; show: boolean }[] = [
    { key: "clients", label: "Clients", icon: "clients", show: true },
    { key: "changes", label: "Pending changes", icon: "changes", show: isTeam },
    { key: "audit", label: "Audit log", icon: "audit", show: true },
    { key: "team", label: "Team", icon: "team", show: isAdmin },
    { key: "integrations", label: "Integrations", icon: "integrations", show: isAdmin },
    { key: "billing", label: "Billing", icon: "billing", show: isOwner },
    { key: "admin", label: "Admin", icon: "admin", show: !!session.is_superadmin },
  ];
  return (
    <ManageProvider>
      <div className="app">
        <aside className="sidebar">
          <Logo />
          <nav className="side-nav">
            {nav
              .filter((n) => n.show)
              .map((n) => (
                <button
                  key={n.key}
                  className={`nav-item ${tab === n.key ? "active" : ""}`}
                  onClick={() => setTab(n.key)}
                >
                  <Icon name={n.icon} />
                  <span>{n.label}</span>
                </button>
              ))}
          </nav>
          <div className="side-foot">
            <div className="user-chip">
              <div className="avatar">{initials(session.full_name)}</div>
              <div className="user-meta">
                <strong>{session.full_name}</strong>
                <span>
                  {session.role}
                  {session.is_superadmin ? " · platform" : ""}
                </span>
              </div>
            </div>
            <button
              className="logout"
              onClick={() => {
                setSession(null);
                setSess(null);
              }}
            >
              <Icon name="logout" />
              <span>Log out</span>
            </button>
          </div>
        </aside>
        <div className="main">
          <header className="topbar">
            <div className="workspace">
              <span className="workspace-avatar">
                {initials(session.organization_name)}
              </span>
              <div className="workspace-meta">
                <span className="workspace-label">Workspace</span>
                <strong>{session.organization_name}</strong>
              </div>
            </div>
            <span className="breadcrumb">{PAGE_TITLES[tab]}</span>
          </header>
          <div className="content">
            {session.email_verified === false && <VerifyBanner />}
            {tab === "clients" && <Clients session={session} />}
            {tab === "changes" && <PendingChangesPanel />}
            {tab === "audit" && <AuditLogView />}
            {tab === "team" && isAdmin && <TeamAdmin session={session} />}
            {tab === "integrations" && isAdmin && <Integrations />}
            {tab === "billing" && isOwner && <Billing session={session} />}
            {tab === "admin" && session.is_superadmin && <SuperAdmin />}
          </div>
        </div>
      </div>
    </ManageProvider>
  );
}

function VerifyBanner() {
  const [sent, setSent] = useState(false);
  return (
    <div className="verify-banner">
      <span>Please verify your email address to secure your account.</span>
      <button
        className="ghost"
        disabled={sent}
        onClick={async () => {
          try {
            await resendVerification();
            setSent(true);
          } catch {
            /* ignore — best effort */
          }
        }}
      >
        {sent ? "Sent ✓" : "Resend email"}
      </button>
    </div>
  );
}

function Login({ onLogin }: { onLogin: (s: Session) => void }) {
  const [mode, setMode] = useState<"login" | "signup" | "forgot">("login");
  const [orgName, setOrgName] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [resetSent, setResetSent] = useState(false);
  const oauth = async (provider: "google" | "meta") => {
    setError(null);
    try {
      const { url } = await oauthStart(provider);
      openAuthUrl(url);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <div className="auth-shell">
      <div className="auth-aside">
        <Logo auth />
        <h2 className="auth-headline">
          Every client's ads &amp; CRM, in one place.
        </h2>
        <p className="auth-tag">
          Meta, Google and more — blended cross-platform metrics, server-side
          conversions and a native CRM, built for modern agencies.
        </p>
        <ul className="auth-points">
          <li>Manage every client's ad accounts from one login</li>
          <li>Blended CAC / ROAS the platforms can't show you</li>
          <li>Leads flow straight into the built-in CRM</li>
        </ul>
      </div>
      <form
        className="auth-card"
        onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          try {
            if (mode === "forgot") {
              await forgotPassword(email);
              setResetSent(true);
            } else {
              onLogin(
                mode === "login"
                  ? await login(email, password)
                  : await signup(orgName, email, password, fullName)
              );
            }
          } catch (err) {
            setError((err as Error).message);
          }
        }}
      >
        <h1>
          {mode === "login"
            ? "Welcome back"
            : mode === "signup"
            ? "Create your organization"
            : "Reset your password"}
        </h1>
        <p className="auth-sub">
          {mode === "login"
            ? "Log in to your Salescale workspace."
            : mode === "signup"
            ? "Start managing your agency's clients in minutes."
            : "We'll email you a link to set a new password."}
        </p>
        {mode === "forgot" && resetSent ? (
          <>
            <p className="notice">
              If an account exists for {email}, a reset link is on its way.
            </p>
            <button
              type="button"
              className="link auth-toggle"
              onClick={() => {
                setMode("login");
                setResetSent(false);
              }}
            >
              Back to login
            </button>
          </>
        ) : (
          <>
            {mode === "signup" && (
              <>
                <label className="field">
                  <span>Agency / organization name</span>
                  <input
                    placeholder="Atlas Reach"
                    value={orgName}
                    onChange={(e) => setOrgName(e.target.value)}
                  />
                </label>
                <label className="field">
                  <span>Your name</span>
                  <input
                    placeholder="Jane Doe"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                  />
                </label>
              </>
            )}
            <label className="field">
              <span>Email</span>
              <input
                type="email"
                placeholder="you@agency.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </label>
            {mode !== "forgot" && (
              <label className="field">
                <span>Password</span>
                <input
                  placeholder="••••••••"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
            )}
            <button type="submit" className="primary block">
              {mode === "login"
                ? "Log in"
                : mode === "signup"
                ? "Create organization"
                : "Send reset link"}
            </button>
            {error && <p className="error">{error}</p>}
            {mode !== "forgot" && (
              <>
                <div className="oauth-divider">
                  <span>or</span>
                </div>
                <button type="button" className="oauth-btn" onClick={() => oauth("google")}>
                  Continue with Google
                </button>
                <button type="button" className="oauth-btn" onClick={() => oauth("meta")}>
                  Continue with Meta
                </button>
              </>
            )}
            {mode === "login" && (
              <button
                type="button"
                className="link"
                onClick={() => {
                  setError(null);
                  setMode("forgot");
                }}
              >
                Forgot password?
              </button>
            )}
            <button
              type="button"
              className="link auth-toggle"
              onClick={() => {
                setError(null);
                setMode(mode === "login" ? "signup" : "login");
              }}
            >
              {mode === "login"
                ? "New agency? Sign up"
                : mode === "signup"
                ? "Already have an account? Log in"
                : "Back to login"}
            </button>
          </>
        )}
      </form>
    </div>
  );
}

function Clients({ session }: { session: Session }) {
  const [clients, setClients] = useState<Client[]>([]);
  const [selected, setSelected] = useState<Client | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const isAdmin = ADMIN_ROLES.includes(session.role);

  const load = () =>
    api<Client[]>("/api/clients")
      .then(setClients)
      .catch((e) => setError(e.message))
      .finally(() => setLoaded(true));
  useEffect(() => {
    load();
  }, []);

  if (selected)
    return (
      <ClientDetail
        client={selected}
        session={session}
        onBack={() => {
          setSelected(null);
          load();
        }}
      />
    );

  return (
    <div>
      <div className="page-head">
        <div>
          <h2>Clients</h2>
          <p className="page-sub">
            {clients.length} {clients.length === 1 ? "client" : "clients"} in{" "}
            {session.organization_name}
          </p>
        </div>
        {isAdmin && clients.length > 0 && (
          <button className="primary" onClick={() => setAdding(true)}>
            <Icon name="plus" /> Add client
          </button>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      {loaded && clients.length === 0 ? (
        <div className="empty-state">
          <div className="empty-icon">
            <Icon name="clients" />
          </div>
          <h3>No clients yet</h3>
          <p>
            {isAdmin
              ? "Add your first client to start connecting ad accounts and tracking performance."
              : "No clients have been added to this organization yet."}
          </p>
          {isAdmin && (
            <button className="primary" onClick={() => setAdding(true)}>
              <Icon name="plus" /> Add your first client
            </button>
          )}
        </div>
      ) : (
        <ul className="client-grid">
          {clients.map((c) => (
            <li
              key={c.id}
              className="client-card"
              onClick={() => setSelected(c)}
            >
              <div className="client-avatar">{initials(c.name)}</div>
              <div className="client-info">
                <strong>{c.name}</strong>
                <span className={`badge ${c.status}`}>{c.status}</span>
              </div>
              <Icon name="chevron" />
            </li>
          ))}
        </ul>
      )}
      {adding && (
        <AddClientModal
          onClose={() => setAdding(false)}
          onCreated={(c) => {
            setAdding(false);
            setClients((prev) => [...prev, c]);
          }}
        />
      )}
    </div>
  );
}

function AddClientModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (c: Client) => void;
}) {
  const [name, setName] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>Add a client</h3>
        <p className="modal-sub">
          Create a client to connect their ad accounts and track performance.
        </p>
        <form
          className="form-grid"
          onSubmit={async (e) => {
            e.preventDefault();
            setSaving(true);
            setError(null);
            try {
              const c = await createClient({
                name: name.trim(),
                internal_notes: notes.trim() || undefined,
              });
              onCreated(c);
            } catch (err) {
              setError((err as Error).message);
              setSaving(false);
            }
          }}
        >
          <label className="field">
            <span>Client name</span>
            <input
              autoFocus
              placeholder="e.g. Paganelli HVAC"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </label>
          <label className="field">
            <span>
              Internal notes <em className="opt">optional</em>
            </span>
            <textarea
              rows={3}
              placeholder="Only your team sees this — never the client."
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </label>
          {error && <p className="error">{error}</p>}
          <div className="modal-actions">
            <button type="button" className="ghost" onClick={onClose}>
              Cancel
            </button>
            <button
              type="submit"
              className="primary"
              disabled={saving || !name.trim()}
            >
              {saving ? "Adding…" : "Add client"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function ClientDetail({
  client,
  session,
  onBack,
}: {
  client: Client;
  session: Session;
  onBack: () => void;
}) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [platformFilter, setPlatformFilter] = useState<string>("all");
  const [view, setView] = useState<"dashboard" | "crm">("dashboard");
  const [error, setError] = useState<string | null>(null);
  // Connecting platforms is Admin/Owner surface — mirrors the API gate.
  const isAdmin = ADMIN_ROLES.includes(session.role);
  const isTeam = TEAM_ROLES.includes(session.role);

  const loadConnections = useCallback(() => {
    api<Connection[]>(`/api/clients/${client.id}/connections`)
      .then(setConnections)
      .catch((e) => setError(e.message));
  }, [client.id]);

  useEffect(() => {
    loadConnections();
  }, [loadConnections]);

  // Desktop OAuth completes in the system browser; refresh connections when
  // the app window regains focus so a just-connected platform shows up without
  // a manual reload. (Harmless on web.)
  useEffect(() => {
    window.addEventListener("focus", loadConnections);
    return () => window.removeEventListener("focus", loadConnections);
  }, [loadConnections]);

  // Platform catalog drives the connect list and filter — see /api/platforms.
  useEffect(() => {
    getPlatforms().then(setPlatforms).catch((e) => setError(e.message));
  }, []);

  const connect = async (platform: string) => {
    const { url } = await api<{ url: string }>(
      `/api/connect/${platform}/start?client_id=${client.id}`
    );
    openAuthUrl(url);
  };

  return (
    <div>
      <div className="client-head">
        <button className="link" onClick={onBack}>
          ← All clients
        </button>
        <h2>{client.name}</h2>
        <nav className="toggle">
          <button
            className={view === "dashboard" ? "active" : ""}
            onClick={() => setView("dashboard")}
          >
            Dashboard
          </button>
          <button
            className={view === "crm" ? "active" : ""}
            onClick={() => setView("crm")}
          >
            CRM
          </button>
        </nav>
        {/* One filter governs every widget and the account tree below —
            no reload, no separate views. */}
        {view === "dashboard" && (
          <nav className="toggle platform-toggle">
            <button
              className={platformFilter === "all" ? "active" : ""}
              onClick={() => setPlatformFilter("all")}
            >
              Blended
            </button>
            {platforms
              .filter((p) => p.connectable)
              .map((p) => (
                <button
                  key={p.id}
                  className={platformFilter === p.id ? "active" : ""}
                  onClick={() => setPlatformFilter(p.id)}
                >
                  {p.name} only
                </button>
              ))}
          </nav>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      {view === "crm" && <CrmView clientId={client.id} session={session} />}
      {view === "dashboard" && (
        <Dashboard
          clientId={client.id}
          session={session}
          platforms={platformFilter}
        />
      )}
      {view === "dashboard" && (
      <section>
        <h3>Platform connections</h3>
        {platforms.map((platform) => {
          const conn = connections.find((c) => c.platform === platform.id);
          return (
            <div key={platform.id} className="connection">
              <strong>{platform.name}</strong>
              {platform.coming_soon ? (
                <span className="badge none">coming soon</span>
              ) : conn ? (
                <span className={`badge ${conn.status}`}>
                  {conn.status}
                  {conn.error_detail ? ` — ${conn.error_detail}` : ""}
                </span>
              ) : (
                <span className="badge none">not connected</span>
              )}
              {isAdmin && platform.connectable && (
                <button onClick={() => connect(platform.id)}>
                  {conn ? "Reconnect" : "Connect"}
                </button>
              )}
            </div>
          );
        })}
      </section>
      )}
      {view === "dashboard" && (
      <section>
        <h3>Accounts &amp; campaigns</h3>
        <AccountTree
          clientId={client.id}
          platformFilter={platformFilter}
          canManage={isTeam}
        />
      </section>
      )}
    </div>
  );
}

function AccountTree({
  clientId,
  platformFilter,
  canManage,
}: {
  clientId: string;
  platformFilter: string;
  canManage: boolean;
}) {
  const [accounts, setAccounts] = useState<AdAccount[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<AdAccount[]>(`/api/ad-accounts?client_id=${clientId}`)
      .then(setAccounts)
      .catch((e) => setError(e.message));
  }, [clientId]);

  const visible = accounts.filter(
    (a) => platformFilter === "all" || a.platform === platformFilter
  );

  if (error) return <p className="error">{error}</p>;
  if (!visible.length) return <p className="muted">No ad accounts yet.</p>;
  return (
    <ul className="tree">
      {visible.map((a) => (
        <AccountNode key={a.id} account={a} canManage={canManage} />
      ))}
    </ul>
  );
}

/**
 * Children are pulled live from the platform (refresh=true). If the live
 * pull fails (platform outage, connection error), fall back to the local
 * cache with a visible warning instead of a blank tree.
 */
function useLazyChildren<T>(basePath: string | null) {
  const [items, setItems] = useState<T[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [warning, setWarning] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!basePath) return;
    setLoading(true);
    setWarning(null);
    setError(null);
    try {
      setItems(await api<T[]>(`${basePath}?refresh=true`));
    } catch (liveErr) {
      try {
        setItems(await api<T[]>(`${basePath}?refresh=false`));
        setWarning(
          `Live refresh failed (${(liveErr as Error).message}) — showing cached data`
        );
      } catch (cacheErr) {
        setError((cacheErr as Error).message);
      }
    } finally {
      setLoading(false);
    }
  }, [basePath]);

  useEffect(() => {
    load();
  }, [load]);
  return { items, loading, warning, error, reload: load };
}

function PlatformBadge({ platform }: { platform: "meta" | "google" }) {
  return <span className={`platform ${platform}`}>{platform}</span>;
}

function NewCampaignForm({
  account,
  onExecuted,
}: {
  account: AdAccount;
  onExecuted: () => void;
}) {
  const { stage } = useManage();
  const [name, setName] = useState("");
  const [budget, setBudget] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="inline-form">
      <input
        placeholder="Campaign name"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <input
        placeholder="Daily budget $"
        type="number"
        min="1"
        value={budget}
        onChange={(e) => setBudget(e.target.value)}
      />
      <button
        disabled={!name || !budget}
        onClick={() =>
          stage(
            {
              ad_account_id: account.id,
              entity_type: "campaign",
              action: "create",
              entity_name: name,
              payload: {
                name,
                daily_budget_micros: Math.round(Number(budget) * 1_000_000),
                status: "PAUSED",
                ...(account.platform === "meta"
                  ? { objective: "OUTCOME_LEADS" }
                  : {}),
              },
            },
            onExecuted
          ).catch((e) => setError((e as Error).message))
        }
      >
        Stage create
      </button>
      <span className="muted">created paused; enable it when ready</span>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function EditForm({
  label,
  initialName,
  initialBudgetMicros,
  onStage,
}: {
  label: string;
  initialName: string;
  initialBudgetMicros?: number | null;
  onStage: (payload: Record<string, unknown>) => void;
}) {
  const [name, setName] = useState(initialName);
  const [budget, setBudget] = useState(
    initialBudgetMicros != null ? String(initialBudgetMicros / 1_000_000) : ""
  );
  return (
    <div className="inline-form">
      <input value={name} onChange={(e) => setName(e.target.value)} />
      <input
        placeholder="Daily budget $"
        type="number"
        min="1"
        value={budget}
        onChange={(e) => setBudget(e.target.value)}
      />
      <button
        onClick={() => {
          const payload: Record<string, unknown> = {};
          if (name !== initialName) payload.name = name;
          if (budget !== "")
            payload.daily_budget_micros = Math.round(Number(budget) * 1_000_000);
          onStage(payload);
        }}
      >
        Stage {label}
      </button>
    </div>
  );
}

function AccountNode({
  account,
  canManage,
}: {
  account: AdAccount;
  canManage: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [showCreatives, setShowCreatives] = useState(false);
  const { items, loading, warning, error, reload } = useLazyChildren<Campaign>(
    open ? `/api/ad-accounts/${account.id}/campaigns` : null
  );
  return (
    <li>
      <div className="node" onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} <PlatformBadge platform={account.platform} />
        <strong>{account.name}</strong>
        <span className="muted">{account.external_id}</span>
      </div>
      {open && (
        <ul>
          {loading && <li className="muted">Loading live from API…</li>}
          {warning && <li className="warning">{warning}</li>}
          {error && <li className="error">{error}</li>}
          {items?.map((c) => (
            <CampaignNode
              key={c.id}
              campaign={c}
              account={account}
              canManage={canManage}
              onChanged={reload}
            />
          ))}
          {items?.length === 0 && <li className="muted">No campaigns</li>}
          {canManage && (
            <li className="node-actions">
              <button className="link" onClick={() => setShowNew(!showNew)}>
                {showNew ? "Cancel" : "+ New campaign"}
              </button>
              {account.platform === "meta" && (
                <button
                  className="link"
                  onClick={() => setShowCreatives(!showCreatives)}
                >
                  {showCreatives ? "Hide creatives" : "Creatives"}
                </button>
              )}
              {showNew && (
                <NewCampaignForm
                  account={account}
                  onExecuted={() => {
                    setShowNew(false);
                    reload();
                  }}
                />
              )}
              {showCreatives && <CreativesPanel adAccountId={account.id} />}
            </li>
          )}
        </ul>
      )}
    </li>
  );
}

function CampaignNode({
  campaign,
  account,
  canManage,
  onChanged,
}: {
  campaign: Campaign;
  account: AdAccount;
  canManage: boolean;
  onChanged: () => void;
}) {
  const { stage } = useManage();
  const [open, setOpen] = useState(false);
  const [panel, setPanel] = useState<
    "none" | "edit" | "terms" | "assets"
  >("none");
  const { items, loading, warning, error, reload } = useLazyChildren<AdGroup>(
    open ? `/api/campaigns/${campaign.id}/ad-groups` : null
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const budget =
    campaign.daily_budget_micros != null
      ? `$${(campaign.daily_budget_micros / 1_000_000).toFixed(2)}/day`
      : null;
  const paused = campaign.status?.toUpperCase() === "PAUSED";

  const stageAction = (action: string, payload: Record<string, unknown> = {}) =>
    stage(
      {
        ad_account_id: account.id,
        entity_type: "campaign",
        action,
        entity_id: campaign.id,
        payload,
      },
      onChanged
    ).catch((e) => setActionError((e as Error).message));

  return (
    <li>
      <div className="node" onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} {campaign.name}
        <span className={`badge ${campaign.status?.toLowerCase()}`}>
          {campaign.status}
        </span>
        {budget && <span className="muted">{budget}</span>}
        {canManage && (
          <span className="row-actions" onClick={(e) => e.stopPropagation()}>
            <button
              className="link"
              onClick={() => stageAction(paused ? "resume" : "pause")}
            >
              {paused ? "Resume" : "Pause"}
            </button>
            <button
              className="link"
              onClick={() => setPanel(panel === "edit" ? "none" : "edit")}
            >
              Edit
            </button>
            {account.platform === "google" && (
              <>
                <button
                  className="link"
                  onClick={() => setPanel(panel === "terms" ? "none" : "terms")}
                >
                  Search terms
                </button>
                <button
                  className="link"
                  onClick={() => setPanel(panel === "assets" ? "none" : "assets")}
                >
                  Asset groups
                </button>
              </>
            )}
          </span>
        )}
      </div>
      {actionError && <p className="error">{actionError}</p>}
      {panel === "edit" && (
        <EditForm
          label="update"
          initialName={campaign.name}
          initialBudgetMicros={campaign.daily_budget_micros}
          onStage={(payload) => {
            setPanel("none");
            stageAction("update", payload);
          }}
        />
      )}
      {panel === "terms" && (
        <SearchTermsPanel campaignId={campaign.id} adAccountId={account.id} />
      )}
      {panel === "assets" && (
        <AssetGroupsPanel campaignId={campaign.id} adAccountId={account.id} />
      )}
      {open && (
        <ul>
          {loading && <li className="muted">Loading…</li>}
          {warning && <li className="warning">{warning}</li>}
          {error && <li className="error">{error}</li>}
          {items?.map((g) => (
            <AdGroupNode
              key={g.id}
              adGroup={g}
              account={account}
              canManage={canManage}
              onChanged={reload}
            />
          ))}
          {items?.length === 0 && (
            <li className="muted">No ad sets / ad groups</li>
          )}
        </ul>
      )}
    </li>
  );
}

function AdGroupNode({
  adGroup,
  account,
  canManage,
  onChanged,
}: {
  adGroup: AdGroup;
  account: AdAccount;
  canManage: boolean;
  onChanged: () => void;
}) {
  const { stage } = useManage();
  const [open, setOpen] = useState(false);
  const [showKeywords, setShowKeywords] = useState(false);
  const { items, loading, warning, error, reload } = useLazyChildren<AdRow>(
    open ? `/api/ad-groups/${adGroup.id}/ads` : null
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const paused = adGroup.status?.toUpperCase() === "PAUSED";

  const stageAction = (action: string) =>
    stage(
      {
        ad_account_id: account.id,
        entity_type: "ad_group",
        action,
        entity_id: adGroup.id,
      },
      onChanged
    ).catch((e) => setActionError((e as Error).message));

  return (
    <li>
      <div className="node" onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} {adGroup.name}
        <span className={`badge ${adGroup.status?.toLowerCase()}`}>
          {adGroup.status}
        </span>
        {canManage && (
          <span className="row-actions" onClick={(e) => e.stopPropagation()}>
            <button
              className="link"
              onClick={() => stageAction(paused ? "resume" : "pause")}
            >
              {paused ? "Resume" : "Pause"}
            </button>
            {account.platform === "google" && (
              <button
                className="link"
                onClick={() => setShowKeywords(!showKeywords)}
              >
                Keywords
              </button>
            )}
          </span>
        )}
      </div>
      {actionError && <p className="error">{actionError}</p>}
      {showKeywords && (
        <KeywordsPanel adGroupId={adGroup.id} adAccountId={account.id} />
      )}
      {open && (
        <ul>
          {loading && <li className="muted">Loading…</li>}
          {warning && <li className="warning">{warning}</li>}
          {error && <li className="error">{error}</li>}
          {items?.map((ad) => (
            <AdLeaf
              key={ad.id}
              ad={ad}
              account={account}
              canManage={canManage}
              onChanged={reload}
            />
          ))}
          {items?.length === 0 && <li className="muted">No ads</li>}
        </ul>
      )}
    </li>
  );
}

function AdLeaf({
  ad,
  account,
  canManage,
  onChanged,
}: {
  ad: AdRow;
  account: AdAccount;
  canManage: boolean;
  onChanged: () => void;
}) {
  const { stage } = useManage();
  const [actionError, setActionError] = useState<string | null>(null);
  const paused = ad.status?.toUpperCase() === "PAUSED";
  return (
    <li>
      {ad.name}{" "}
      <span className={`badge ${ad.status?.toLowerCase()}`}>{ad.status}</span>
      {canManage && (
        <span className="row-actions">
          <button
            className="link"
            onClick={() =>
              stage(
                {
                  ad_account_id: account.id,
                  entity_type: "ad",
                  action: paused ? "resume" : "pause",
                  entity_id: ad.id,
                },
                onChanged
              ).catch((e) => setActionError((e as Error).message))
            }
          >
            {paused ? "Resume" : "Pause"}
          </button>
        </span>
      )}
      {actionError && <p className="error">{actionError}</p>}
    </li>
  );
}
