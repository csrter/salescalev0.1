import {
  createContext,
  lazy,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import {
  ADMIN_ROLES,
  TEAM_ROLES,
  api,
  createClient,
  getHouseClient,
  getPlatforms,
  getSession,
  isMfaChallenge,
  login,
  loginMfa,
  myOrganizations,
  refreshSession,
  setSession,
  signup,
  switchOrganization,
  type LoginChallenge,
  type MyOrg,
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
import { Logo } from "./logo";
import { AcceptInvite, Billing, ResetPassword, VerifyEmail } from "./account";
import { AccountPickerDialog } from "./components/AccountPicker";

// Code-split the heavy views (each becomes its own chunk; the login/shell
// stays in the entry bundle). All are named exports, hence the .then() maps.
const Dashboard = lazy(() =>
  import("./dashboard").then((m) => ({ default: m.Dashboard })),
);
const CrmView = lazy(() =>
  import("./crm").then((m) => ({ default: m.CrmView })),
);
const SuperAdmin = lazy(() =>
  import("./admin").then((m) => ({ default: m.SuperAdmin })),
);
const TeamAdmin = lazy(() =>
  import("./admin").then((m) => ({ default: m.TeamAdmin })),
);
const Integrations = lazy(() =>
  import("./integrations").then((m) => ({ default: m.Integrations })),
);
const TwoFactorSettings = lazy(() =>
  import("./security").then((m) => ({ default: m.TwoFactorSettings })),
);
const BrandingSettings = lazy(() =>
  import("./branding").then((m) => ({ default: m.BrandingSettings })),
);
const LeadFinderView = lazy(() =>
  import("./leadfinder").then((m) => ({ default: m.LeadFinderView })),
);
const OutreachView = lazy(() =>
  import("./outreach").then((m) => ({ default: m.OutreachView })),
);
const EmailOutreachView = lazy(() =>
  import("./email_outreach").then((m) => ({ default: m.EmailOutreachView })),
);
const SmsOutreachView = lazy(() =>
  import("./sms_outreach").then((m) => ({ default: m.SmsOutreachView })),
);
import { CommandPalette, type Command } from "./components/CommandPalette";
import { ToastProvider, useToast } from "./components/Toast";
import {
  Alert,
  Badge,
  Button,
  EmptyState,
  Field,
  Kbd,
  PlatformChip,
  Segmented,
  Skeleton,
  SkeletonText,
  toneForStatus,
} from "./components/ui";
import { Dialog } from "./components/Dialog";
import {
  Building2,
  Check,
  ChevronLeft,
  ChevronRight,
  Compass,
  CreditCard,
  Eye,
  GitBranch,
  Link2,
  LogOut,
  Mail,
  Menu,
  MessageSquare,
  Moon,
  Palette,
  Plus,
  Search,
  Send,
  Settings,
  Shield,
  Sun,
  Table2,
  Users,
  type LucideIcon,
} from "./components/icons";
import {
  applyDensity,
  setThemePref,
  useBranding,
  useDensity,
  useTheme,
} from "./theme";
import {
  forgotPassword,
  oauthStart,
  openAuthUrl,
  resendVerification,
  sessionFromToken,
} from "./api";
import "./styles/shell.css";
import "./styles/auth.css";
import "./styles/views/clients.css";
import "./legacy.css";

function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");
}

/** The live-status tick channel: any successful write pings this. */
function pingSave(phase: "saving" | "saved" | "error") {
  window.dispatchEvent(new CustomEvent("save-tick", { detail: { phase } }));
}

type Tab =
  | "clients"
  | "crm"
  | "leads"
  | "outreach"
  | "email"
  | "sms"
  | "changes"
  | "audit"
  | "team"
  | "integrations"
  | "billing"
  | "branding"
  | "security"
  | "admin";

const PAGE_TITLES: Record<Tab, string> = {
  clients: "Clients",
  crm: "CRM",
  leads: "Lead Finder",
  outreach: "Outreach",
  email: "Email",
  sms: "SMS",
  changes: "Pending changes",
  audit: "Audit log",
  team: "Team",
  integrations: "Integrations",
  billing: "Billing",
  branding: "Branding",
  security: "Security",
  admin: "Platform admin",
};

interface NavItem {
  key: Tab;
  label: string;
  icon: LucideIcon;
  section: string;
  show: boolean;
}

/** Breadcrumb segment. Ancestors carry onClick (set view state back). */
interface Crumb {
  label: string;
  onClick?: () => void;
}

/** ClientDetail extends the breadcrumb (client name › campaign) through
 * this — the topbar renders base crumbs + the registered trail. */
const TrailCtx = createContext<(trail: Crumb[]) => void>(() => {});

function clearAuthQuery() {
  window.history.replaceState({}, "", window.location.pathname);
}

/** Suspense fallback while a lazy view chunk loads — existing skeleton
 * primitives, shaped like a page header + body. */
function ViewFallback() {
  return (
    <div className="view-fallback" aria-hidden="true">
      <Skeleton height="1.6em" width="240px" />
      <SkeletonText lines={4} />
    </div>
  );
}

export default function App() {
  const [session, setSess] = useState<Session | null>(getSession());
  const [tab, setTab] = useState<Tab>("clients");
  // Lifted so the breadcrumb (topbar) and the command palette can both
  // set/clear the open client.
  const [selectedClient, setSelectedClient] = useState<Client | null>(null);
  const [trail, setTrail] = useState<Crumb[]>([]);
  const [sideCollapsed, setSideCollapsed] = useState(
    () => localStorage.getItem("sidebar-collapsed") === "1"
  );
  // Off-canvas nav drawer on narrow screens (≤760px). Closed by navigating,
  // tapping the scrim, or leaving the breakpoint. The desktop collapse state
  // is suppressed while narrow so the drawer always shows the full labels.
  const [mobileNav, setMobileNav] = useState(false);
  const [isNarrow, setIsNarrow] = useState(
    () => typeof window !== "undefined" && window.matchMedia("(max-width: 760px)").matches
  );
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 760px)");
    const onChange = () => {
      setIsNarrow(mq.matches);
      if (!mq.matches) setMobileNav(false); // returning to desktop closes the drawer
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  const [closedSections, setClosedSections] = useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem("sidebar-closed") ?? "[]");
    } catch {
      return [];
    }
  });
  // Tabs the user has actually opened. The heavy workspace views (clients, crm,
  // email, sms, leads) stay mounted once visited and toggle visibility with
  // `hidden` instead of unmount/remount — so switching tabs no longer refetches
  // everything. Memory stays bounded: a view only mounts after its first visit,
  // never eagerly. Polling inside a hidden view is gated by the `active` prop.
  const [visited, setVisited] = useState<Set<Tab>>(
    () => new Set<Tab>(["clients"]),
  );

  // Jump-to-client commands for the palette, fetched fresh each time it
  // opens. The API returns only this org's clients (tenant-scoped
  // server-side). Declared before the early returns — hooks rules.
  const loadClientCommands = useCallback(
    () =>
      api<Client[]>("/api/clients").then((clients) =>
        clients.map((c) => ({
          id: `client:${c.id}`,
          title: c.name,
          section: "Clients",
          keywords: "client account",
          run: () => {
            setTab("clients");
            setSelectedClient(c);
          },
        }))
      ),
    []
  );
  const [authRoute, setAuthRoute] = useState<{
    kind: "verify" | "reset" | "invite";
    token: string;
  } | null>(() => {
    const p = new URLSearchParams(window.location.search);
    const verify = p.get("verify");
    const reset = p.get("reset");
    const invite = p.get("invite");
    if (verify) return { kind: "verify", token: verify };
    if (reset) return { kind: "reset", token: reset };
    if (invite) return { kind: "invite", token: invite };
    return null;
  });
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

  // On load, refresh the session from /me so server-side changes (org 2FA
  // policy, role) and token validity are reflected. A revoked/expired token
  // 401s inside api(), which clears the session and reloads.
  useEffect(() => {
    if (!getSession() || window.location.hash.includes("access_token=")) return;
    refreshSession()
      .then(setSess)
      .catch(() => {});
  }, []);

  // Density is a team-session preference; Client-role sessions never get the
  // attribute (cleared on their login, and on logout).
  const isTeamRole = !!session && TEAM_ROLES.includes(session.role);
  useEffect(() => {
    applyDensity(isTeamRole);
  }, [isTeamRole]);

  // Record every tab the user opens (covers navigate(), palette jumps, and any
  // other setTab path) so the keep-mounted views know they've been visited.
  useEffect(() => {
    setVisited((cur) => (cur.has(tab) ? cur : new Set(cur).add(tab)));
  }, [tab]);

  // The org's "house" CRM (the agency's own prospect pipeline) mounts CrmView
  // against a hidden client the server gets-or-creates. Resolve its id lazily —
  // only the first time a team user opens the CRM tab, never for the Client
  // role. Bump retries on error. (Declared before the early returns — hooks.)
  const [houseId, setHouseId] = useState<string | null>(null);
  const [houseErr, setHouseErr] = useState<string | null>(null);
  const [houseBump, setHouseBump] = useState(0);
  useEffect(() => {
    if (tab !== "crm" || !isTeamRole || houseId) return;
    let alive = true;
    setHouseErr(null);
    getHouseClient()
      .then((r) => alive && setHouseId(r.client_id))
      .catch((e) => alive && setHouseErr((e as Error).message));
    return () => {
      alive = false;
    };
  }, [tab, isTeamRole, houseId, houseBump]);

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
  // Team invite: works logged-out (login/signup inline) and logged-in
  // (one-click accept, which also switches the active org).
  if (authRoute?.kind === "invite")
    return (
      <AcceptInvite
        token={authRoute.token}
        session={session}
        onJoined={(s) => {
          setSess(s);
          clearAuthQuery();
          setAuthRoute(null);
        }}
        onDone={() => {
          clearAuthQuery();
          setAuthRoute(null);
        }}
      />
    );

  if (!session) return <Login onLogin={setSess} />;

  // Org policy: this user must enable 2FA before using the app.
  if (session.mfa_setup_required) {
    return (
      <MfaGate
        session={session}
        onContinue={async () => {
          const s = await refreshSession();
          setSess(s);
        }}
        onLogout={() => {
          setSession(null);
          setSess(null);
        }}
      />
    );
  }
  const isTeam = TEAM_ROLES.includes(session.role);
  const isAdmin = ADMIN_ROLES.includes(session.role);
  const isOwner = session.role === "owner";
  // Role gating for what a user can reach lives HERE (and in the render
  // guards below + the API itself) — the sidebar and the command palette
  // both consume this same filtered list.
  const nav: NavItem[] = [
    { key: "clients", label: "Clients", icon: Building2, section: "Workspace", show: true },
    { key: "crm", label: "CRM", icon: Table2, section: "Workspace", show: isTeam },
    { key: "leads", label: "Lead Finder", icon: Compass, section: "Workspace", show: isTeam },
    { key: "outreach", label: "Outreach", icon: Send, section: "Outreach", show: isTeam },
    { key: "email", label: "Email", icon: Mail, section: "Outreach", show: isTeam },
    { key: "sms", label: "SMS", icon: MessageSquare, section: "Outreach", show: isTeam },
    { key: "changes", label: "Pending changes", icon: GitBranch, section: "Activity", show: isTeam },
    { key: "audit", label: "Audit log", icon: Eye, section: "Activity", show: true },
    { key: "team", label: "Team", icon: Users, section: "Settings", show: isAdmin },
    { key: "integrations", label: "Integrations", icon: Link2, section: "Settings", show: isAdmin },
    { key: "billing", label: "Billing", icon: CreditCard, section: "Settings", show: isOwner },
    { key: "branding", label: "Branding", icon: Palette, section: "Settings", show: isAdmin },
    { key: "security", label: "Security", icon: Shield, section: "Settings", show: true },
    { key: "admin", label: "Admin", icon: Settings, section: "Platform", show: !!session.is_superadmin },
  ];
  const visibleNav = nav.filter((n) => n.show);

  const navigate = (k: Tab) => {
    setSelectedClient(null);
    setTab(k);
    setMobileNav(false);
  };

  const logout = () => {
    setSession(null);
    setSess(null);
  };

  const paletteCommands: Command[] = [
    ...visibleNav.map((n) => ({
      id: `nav:${n.key}`,
      title: n.label,
      section: "Go to",
      keywords: n.section,
      run: () => navigate(n.key),
    })),
    {
      id: "theme:light",
      title: "Theme: light",
      section: "Preferences",
      keywords: "appearance mode",
      run: () => setThemePref("light"),
    },
    {
      id: "theme:dark",
      title: "Theme: dark",
      section: "Preferences",
      keywords: "appearance mode",
      run: () => setThemePref("dark"),
    },
    {
      id: "theme:system",
      title: "Theme: match system",
      section: "Preferences",
      keywords: "appearance mode auto",
      run: () => setThemePref("system"),
    },
    {
      id: "logout",
      title: "Log out",
      section: "Account",
      keywords: "sign out",
      run: logout,
    },
  ];

  // Breadcrumb: the current view, then any deeper trail (client › campaign).
  // The org name is intentionally omitted — it already lives in the sidebar
  // footer, so repeating it here was pure duplication.
  const crumbs: Crumb[] = [
    {
      label: PAGE_TITLES[tab],
      onClick:
        tab === "clients" && selectedClient
          ? () => setSelectedClient(null)
          : undefined,
    },
    ...(tab === "clients" ? trail : []),
  ];

  return (
    <ToastProvider>
      <ManageProvider>
        <TrailCtx.Provider value={setTrail}>
          <div
            className={`app ${sideCollapsed && !isNarrow ? "side-collapsed" : ""} ${
              mobileNav ? "mobile-nav-open" : ""
            }`.trim()}
          >
            <Sidebar
              session={session}
              nav={visibleNav}
              tab={tab}
              onNavigate={navigate}
              collapsed={sideCollapsed}
              onToggleCollapsed={() =>
                setSideCollapsed((v) => {
                  localStorage.setItem("sidebar-collapsed", v ? "" : "1");
                  return !v;
                })
              }
              closedSections={closedSections}
              onToggleSection={(section) =>
                setClosedSections((cur) => {
                  const next = cur.includes(section)
                    ? cur.filter((s) => s !== section)
                    : [...cur, section];
                  localStorage.setItem("sidebar-closed", JSON.stringify(next));
                  return next;
                })
              }
              onLogout={logout}
              showDensity={isTeam}
            />
            {mobileNav && (
              <div
                className="nav-scrim"
                onClick={() => setMobileNav(false)}
                aria-hidden="true"
              />
            )}
            <div className="main">
              <Topbar crumbs={crumbs} onOpenNav={() => setMobileNav(true)} />
              <div className="content">
                {session.email_verified === false && <VerifyBanner />}
                {/* Workspace views stay mounted once visited (state, scroll,
                    fetched data survive tab switches); visibility toggles via
                    the hidden attribute. Polling views receive `active` so a
                    hidden view never polls. Each host carries the entrance
                    transition (.view-host @starting-style — fires on mount AND
                    on display:none → visible). */}
                {visited.has("clients") && (
                  <div className="view-host" hidden={tab !== "clients"}>
                    <Suspense fallback={<ViewFallback />}>
                      <Clients
                        session={session}
                        selected={selectedClient}
                        onSelect={setSelectedClient}
                        active={tab === "clients"}
                      />
                    </Suspense>
                  </div>
                )}
                {isTeam && visited.has("crm") && houseId && (
                  <div className="view-host" hidden={tab !== "crm"}>
                    <Suspense fallback={<ViewFallback />}>
                      <CrmView
                        clientId={houseId}
                        session={session}
                        active={tab === "crm"}
                      />
                    </Suspense>
                  </div>
                )}
                {tab === "crm" &&
                  isTeam &&
                  !houseId &&
                  (houseErr ? (
                    <section className="crm view-host">
                      <Alert tone="danger" title="Couldn't load the CRM">
                        <div className="crm-alert-body">
                          <span>{houseErr}</span>
                          <Button
                            size="sm"
                            onClick={() => setHouseBump((b) => b + 1)}
                          >
                            Retry
                          </Button>
                        </div>
                      </Alert>
                    </section>
                  ) : (
                    <section className="crm view-host">
                      <div className="crm-board" aria-hidden="true">
                        {Array.from({ length: 4 }, (_, i) => (
                          <div key={i} className="kanban-lane">
                            <Skeleton height="0.85em" width="55%" />
                            <Skeleton height="3.4em" />
                            <Skeleton height="3.4em" />
                          </div>
                        ))}
                      </div>
                    </section>
                  ))}
                {isTeam && visited.has("leads") && (
                  <div className="view-host" hidden={tab !== "leads"}>
                    <Suspense fallback={<ViewFallback />}>
                      <LeadFinderView isAdmin={isAdmin} />
                    </Suspense>
                  </div>
                )}
                {isTeam && visited.has("outreach") && (
                  <div className="view-host" hidden={tab !== "outreach"}>
                    <Suspense fallback={<ViewFallback />}>
                      <OutreachView
                        isAdmin={isAdmin}
                        active={tab === "outreach"}
                      />
                    </Suspense>
                  </div>
                )}
                {isTeam && visited.has("email") && (
                  <div className="view-host" hidden={tab !== "email"}>
                    <Suspense fallback={<ViewFallback />}>
                      <EmailOutreachView
                        isAdmin={isAdmin}
                        active={tab === "email"}
                      />
                    </Suspense>
                  </div>
                )}
                {isTeam && visited.has("sms") && (
                  <div className="view-host" hidden={tab !== "sms"}>
                    <Suspense fallback={<ViewFallback />}>
                      <SmsOutreachView
                        isAdmin={isAdmin}
                        isOwner={isOwner}
                        active={tab === "sms"}
                      />
                    </Suspense>
                  </div>
                )}
                {/* Lighter/settings views keep the mount-on-visit behavior —
                    each fresh mount also plays the entrance via .view-host. */}
                {tab === "changes" && (
                  <div className="view-host">
                    <PendingChangesPanel />
                  </div>
                )}
                {tab === "audit" && (
                  <div className="view-host">
                    <AuditLogView />
                  </div>
                )}
                {tab === "team" && isAdmin && (
                  <div className="view-host">
                    <Suspense fallback={<ViewFallback />}>
                      <TeamAdmin
                        session={session}
                        onGoToBilling={() => navigate("billing")}
                      />
                    </Suspense>
                  </div>
                )}
                {tab === "integrations" && isAdmin && (
                  <div className="view-host">
                    <Suspense fallback={<ViewFallback />}>
                      <Integrations isOwner={isOwner} />
                    </Suspense>
                  </div>
                )}
                {tab === "billing" && isOwner && (
                  <div className="view-host">
                    <Billing session={session} />
                  </div>
                )}
                {tab === "branding" && isAdmin && (
                  <div className="view-host">
                    <Suspense fallback={<ViewFallback />}>
                      <BrandingSettings />
                    </Suspense>
                  </div>
                )}
                {tab === "security" && (
                  <div className="view-host">
                    <Suspense fallback={<ViewFallback />}>
                      <TwoFactorSettings session={session} />
                    </Suspense>
                  </div>
                )}
                {tab === "admin" && session.is_superadmin && (
                  <div className="view-host">
                    <Suspense fallback={<ViewFallback />}>
                      <SuperAdmin />
                    </Suspense>
                  </div>
                )}
              </div>
            </div>
            <CommandPalette
              commands={paletteCommands}
              loadDynamic={loadClientCommands}
            />
          </div>
        </TrailCtx.Provider>
      </ManageProvider>
    </ToastProvider>
  );
}

// --- shell chrome ----------------------------------------------------------

function Sidebar({
  session,
  nav,
  tab,
  onNavigate,
  collapsed,
  onToggleCollapsed,
  closedSections,
  onToggleSection,
  onLogout,
  showDensity,
}: {
  session: Session;
  nav: NavItem[];
  tab: Tab;
  onNavigate: (t: Tab) => void;
  collapsed: boolean;
  onToggleCollapsed: () => void;
  closedSections: string[];
  onToggleSection: (section: string) => void;
  onLogout: () => void;
  showDensity: boolean;
}) {
  const sections = [...new Set(nav.map((n) => n.section))];
  const navRef = useRef<HTMLElement>(null);
  // THE sliding pill: one absolutely-positioned, transform-animated element.
  const [pill, setPill] = useState<{ top: number; height: number } | null>(null);
  // Suppress the slide on the very first placement so the pill appears in
  // position instead of animating from the top of the rail on mount.
  const pillPlaced = useRef(false);
  const measurePill = useCallback(() => {
    const el = navRef.current?.querySelector<HTMLElement>('[aria-current="page"]');
    setPill((prev) => {
      if (!el || el.offsetHeight === 0) return null;
      const next = { top: el.offsetTop, height: el.offsetHeight };
      return prev && prev.top === next.top && prev.height === next.height
        ? prev
        : next;
    });
  }, []);
  useLayoutEffect(() => {
    measurePill();
    // Re-measure when rail geometry settles/changes (late stylesheet apply,
    // font metrics, window resize) — the pill must always track the item.
    const el = navRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measurePill);
    ro.observe(el);
    el.querySelectorAll(".side-section").forEach((s) => ro.observe(s));
    return () => ro.disconnect();
  }, [measurePill, tab, closedSections, collapsed, nav.length]);
  // After the first placement paints, allow the slide animation for later moves.
  useEffect(() => {
    if (pill && !pillPlaced.current) {
      const id = requestAnimationFrame(() => {
        pillPlaced.current = true;
      });
      return () => cancelAnimationFrame(id);
    }
  }, [pill]);

  return (
    <aside className="sidebar">
      <div className="side-top">
        <Logo />
        <button
          type="button"
          className="side-icon-btn side-collapse"
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          onClick={onToggleCollapsed}
        >
          <ChevronLeft size={16} aria-hidden="true" />
        </button>
      </div>
      <button
        type="button"
        className="side-search"
        aria-label="Search (⌘K)"
        onClick={() => window.dispatchEvent(new Event("cmdk:open"))}
      >
        <Search size={15} aria-hidden="true" />
        <span>Search…</span>
        <Kbd onField>⌘K</Kbd>
      </button>
      <nav className="side-nav" ref={navRef} aria-label="Primary">
        {pill && (
          <span
            className="nav-pill"
            aria-hidden="true"
            style={{
              height: pill.height,
              transform: `translateY(${pill.top}px)`,
              transition: pillPlaced.current ? undefined : "none",
            }}
          />
        )}
        {sections.map((section) => (
          <div key={section} className="side-section">
            <button
              type="button"
              className="side-section-head"
              aria-expanded={!closedSections.includes(section)}
              onClick={() => onToggleSection(section)}
            >
              <span>{section}</span>
              <span
                className={`section-chevron ${
                  closedSections.includes(section) ? "" : "open"
                }`}
                aria-hidden="true"
              >
                <ChevronRight size={12} />
              </span>
            </button>
            {!closedSections.includes(section) &&
              nav
                .filter((n) => n.section === section)
                .map((n) => {
                  const IconCmp = n.icon;
                  return (
                    <button
                      key={n.key}
                      type="button"
                      className="nav-item"
                      title={n.label}
                      aria-label={n.label}
                      aria-current={tab === n.key ? "page" : undefined}
                      onClick={() => onNavigate(n.key)}
                    >
                      <IconCmp aria-hidden="true" />
                      <span>{n.label}</span>
                    </button>
                  );
                })}
          </div>
        ))}
      </nav>
      <div className="side-foot">
        <OrgSwitcher session={session} />
        <div className="user-chip">
          <div className="avatar" aria-hidden="true">
            {initials(session.full_name)}
          </div>
          <div className="user-meta">
            <strong>{session.full_name}</strong>
            <span>
              {session.role}
              {session.is_superadmin ? " · platform" : ""}
            </span>
          </div>
          <ThemeToggle />
        </div>
        {showDensity && <DensityControl />}
        <button type="button" className="logout" onClick={onLogout} aria-label="Log out">
          <LogOut size={16} aria-hidden="true" />
          <span>Log out</span>
        </button>
      </div>
    </aside>
  );
}

/** Row-density preference (Comfortable/Dense). Lives in the sidebar footer
 * with the other display prefs (theme) rather than taking prime top-bar space
 * on every view. Hidden on the collapsed rail. */
function DensityControl() {
  const { pref, setPref } = useDensity();
  return (
    <div className="side-density">
      <Segmented
        ariaLabel="Row density"
        value={pref}
        onChange={setPref}
        options={[
          { value: "comfortable", label: "Comfortable" },
          { value: "dense", label: "Dense" },
        ]}
      />
    </div>
  );
}

/** Multi-org accounts get a workspace picker; single-org accounts just see
 * their org name. Switching repoints the account's active org server-side,
 * then reloads — every piece of tenant-scoped state must reset. */
function OrgSwitcher({ session }: { session: Session }) {
  const [orgs, setOrgs] = useState<MyOrg[] | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    myOrganizations()
      .then(setOrgs)
      .catch(() => setOrgs(null));
  }, []);

  if (!orgs || orgs.length < 2)
    return (
      <div className="org-switcher org-switcher--single" title={session.organization_name}>
        <Building2 size={14} aria-hidden="true" />
        <span>{session.organization_name}</span>
      </div>
    );

  return (
    <div className="org-switcher">
      <Building2 size={14} aria-hidden="true" />
      <select
        className="select org-switcher-select"
        aria-label="Switch organization"
        value={session.organization_id}
        disabled={busy}
        onChange={async (e) => {
          setBusy(true);
          try {
            await switchOrganization(e.target.value);
            window.location.reload();
          } catch {
            setBusy(false);
          }
        }}
      >
        {orgs.map((o) => (
          <option key={o.organization_id} value={o.organization_id}>
            {o.organization_name} · {o.role}
          </option>
        ))}
      </select>
    </div>
  );
}

function Topbar({ crumbs, onOpenNav }: { crumbs: Crumb[]; onOpenNav: () => void }) {
  // Transparent at scrollTop 0; glass once content scrolls under
  // (IntersectionObserver on a 1px sentinel; degrades to always-stuck).
  const [stuck, setStuck] = useState(false);
  const sentinelRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setStuck(true);
      return;
    }
    const io = new IntersectionObserver(([entry]) =>
      setStuck(!entry.isIntersecting)
    );
    io.observe(el);
    return () => io.disconnect();
  }, []);

  return (
    <>
      <div ref={sentinelRef} className="topbar-sentinel" aria-hidden="true" />
      <header className={`topbar ${stuck ? "topbar--stuck" : ""}`.trim()}>
        <button
          type="button"
          className="topbar-menu-btn"
          aria-label="Open navigation"
          onClick={onOpenNav}
        >
          <Menu size={18} aria-hidden="true" />
        </button>
        <nav className="crumb" aria-label="Breadcrumb">
          <ol>
            {crumbs.map((c, i) => {
              const last = i === crumbs.length - 1;
              return (
                <li key={`${i}-${c.label}`}>
                  {i > 0 && (
                    <span className="crumb-sep" aria-hidden="true">
                      ›
                    </span>
                  )}
                  {!last && c.onClick ? (
                    <button type="button" onClick={c.onClick}>
                      {c.label}
                    </button>
                  ) : (
                    <span
                      className="crumb-here"
                      aria-current={last ? "page" : undefined}
                    >
                      {c.label}
                    </span>
                  )}
                </li>
              );
            })}
          </ol>
        </nav>
        <div className="topbar-tools">
          <SaveTick />
        </div>
      </header>
    </>
  );
}

/** "Saving… → Saved HH:MM:SS" — one aria-live region doubling as the
 * app-wide SR write-feedback channel. Any write dispatches the window
 * CustomEvent "save-tick" with detail.phase saving|saved|error. */
function SaveTick() {
  const [text, setText] = useState("");
  useEffect(() => {
    const onTick = (e: Event) => {
      const phase = (e as CustomEvent<{ phase?: string }>).detail?.phase;
      if (phase === "saving") setText("Saving…");
      else if (phase === "error") setText("");
      else {
        const d = new Date();
        const p = (n: number) => String(n).padStart(2, "0");
        setText(`Saved ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`);
      }
    };
    window.addEventListener("save-tick", onTick);
    return () => window.removeEventListener("save-tick", onTick);
  }, []);
  return (
    <span className="save-tick" role="status" aria-live="polite">
      {text}
    </span>
  );
}

function ThemeToggle() {
  const { pref, setPref } = useTheme();
  const resolvedDark =
    pref === "system"
      ? window.matchMedia("(prefers-color-scheme: dark)").matches
      : pref === "dark";
  return (
    <button
      type="button"
      className="side-icon-btn theme-toggle"
      title={`Theme: ${pref} — switch to ${resolvedDark ? "light" : "dark"}`}
      aria-label={`Switch to ${resolvedDark ? "light" : "dark"} theme`}
      onClick={() => setPref(resolvedDark ? "light" : "dark")}
    >
      {resolvedDark ? (
        <Sun size={16} aria-hidden="true" />
      ) : (
        <Moon size={16} aria-hidden="true" />
      )}
    </button>
  );
}

function VerifyBanner() {
  const [sent, setSent] = useState(false);
  return (
    <Alert tone="warn" className="verify-banner">
      <span>Please verify your email address to secure your account.</span>
      <Button
        variant="ghost"
        size="sm"
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
      </Button>
    </Alert>
  );
}

function MfaGate({
  session,
  onContinue,
  onLogout,
}: {
  session: Session;
  onContinue: () => void;
  onLogout: () => void;
}) {
  return (
    <div className="mfa-gate">
      <div className="mfa-gate-inner">
        <Logo />
        <h1>Two-factor authentication required</h1>
        <p className="muted">
          Your organization requires 2FA. Set it up below, then continue.
        </p>
        <TwoFactorSettings session={session} />
        <div className="gate-actions">
          <Button variant="primary" onClick={onContinue}>
            I've set it up — continue
          </Button>
          <Button variant="link" onClick={onLogout}>
            Log out
          </Button>
        </div>
      </div>
    </div>
  );
}

// --- auth (login / signup / forgot / MFA challenge) -------------------------
// Presentation only: the submit handlers, query/hash parsing and session
// logic are behavior-identical to the pre-revamp screens.

function Login({ onLogin }: { onLogin: (s: Session) => void }) {
  const branding = useBranding();
  const [mode, setMode] = useState<"login" | "signup" | "forgot">("login");
  const [orgName, setOrgName] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  // A failed social login redirects back with ?login_error=<reason> — show it
  // once and strip it from the URL.
  const [error, setError] = useState<string | null>(() => {
    const msg = new URLSearchParams(window.location.search).get("login_error");
    if (msg) window.history.replaceState({}, "", window.location.pathname);
    return msg;
  });
  const [resetSent, setResetSent] = useState(false);
  const [challenge, setChallenge] = useState<LoginChallenge | null>(null);
  const [code, setCode] = useState("");
  const [rememberDevice, setRememberDevice] = useState(false);
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
        <h2 className="auth-headline">Every campaign. Every client. One&nbsp;place.</h2>
        <p className="auth-tag">
          Meta, Google and more — blended cross-platform metrics, server-side
          conversions and a native CRM, built for modern agencies.
        </p>
        <ul className="auth-points">
          <li>
            <Check size={16} aria-hidden="true" />
            <span>Manage every client's ad accounts from one login</span>
          </li>
          <li>
            <Check size={16} aria-hidden="true" />
            <span>Blended CAC / ROAS the platforms can't show you</span>
          </li>
          <li>
            <Check size={16} aria-hidden="true" />
            <span>Leads flow straight into the built-in CRM</span>
          </li>
        </ul>
      </div>
      <div className="auth-panel-wrap">
        {challenge ? (
          <form
            className="auth-card"
            onSubmit={async (e) => {
              e.preventDefault();
              setError(null);
              try {
                onLogin(await loginMfa(challenge.challenge_token, code, rememberDevice));
              } catch (err) {
                setError((err as Error).message);
              }
            }}
          >
            <h1>Two-step verification</h1>
            <p className="auth-sub">
              {challenge.method === "totp"
                ? "Enter the 6-digit code from your authenticator app."
                : challenge.method === "email"
                ? "Enter the code we just emailed you."
                : "Enter the code we just texted you."}
            </p>
            <Field label="Verification code">
              <input
                value={code}
                onChange={(e) => setCode(e.target.value)}
                inputMode="numeric"
                autoComplete="one-time-code"
                autoFocus
                placeholder="123456"
              />
            </Field>
            <label className="auth-remember-device">
              <input
                type="checkbox"
                checked={rememberDevice}
                onChange={(e) => setRememberDevice(e.target.checked)}
              />
              <span>Remember this device for 30 days</span>
            </label>
            <Button type="submit" variant="primary" size="lg" block>
              Verify
            </Button>
            {error && <Alert tone="danger">{error}</Alert>}
            <p className="auth-sub">You can also enter one of your backup codes.</p>
            <Button
              variant="link"
              className="auth-toggle"
              onClick={() => {
                setChallenge(null);
                setCode("");
                setRememberDevice(false);
                setError(null);
              }}
            >
              ← Back to login
            </Button>
          </form>
        ) : (
          <form
            key={mode}
            className="auth-card"
            onSubmit={async (e) => {
              e.preventDefault();
              setError(null);
              try {
                if (mode === "forgot") {
                  await forgotPassword(email);
                  setResetSent(true);
                } else if (mode === "login") {
                  const r = await login(email, password);
                  if (isMfaChallenge(r)) setChallenge(r);
                  else onLogin(r);
                } else {
                  onLogin(await signup(orgName, email, password, fullName));
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
                ? `Log in to your ${branding.product_name} workspace.`
                : mode === "signup"
                ? "Start managing your agency's clients in minutes."
                : "We'll email you a link to set a new password."}
            </p>
            {mode === "forgot" && resetSent ? (
              <>
                <Alert tone="ok">
                  If an account exists for {email}, a reset link is on its way.
                </Alert>
                <Button
                  variant="link"
                  className="auth-toggle"
                  onClick={() => {
                    setMode("login");
                    setResetSent(false);
                  }}
                >
                  Back to login
                </Button>
              </>
            ) : (
              <>
                {mode === "signup" && (
                  <>
                    <Field label="Agency / organization name">
                      <input
                        placeholder="Atlas Reach"
                        value={orgName}
                        onChange={(e) => setOrgName(e.target.value)}
                      />
                    </Field>
                    <Field label="Your name">
                      <input
                        placeholder="Jane Doe"
                        value={fullName}
                        onChange={(e) => setFullName(e.target.value)}
                      />
                    </Field>
                  </>
                )}
                <Field label="Email">
                  <input
                    type="email"
                    autoFocus={mode !== "signup"}
                    autoComplete="email"
                    placeholder="you@agency.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </Field>
                {mode !== "forgot" && (
                  <Field label="Password">
                    <input
                      type="password"
                      autoComplete={
                        mode === "signup" ? "new-password" : "current-password"
                      }
                      placeholder="••••••••"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                    />
                  </Field>
                )}
                <Button type="submit" variant="primary" size="lg" block>
                  {mode === "login"
                    ? "Log in"
                    : mode === "signup"
                    ? "Create organization"
                    : "Send reset link"}
                </Button>
                {error && <Alert tone="danger">{error}</Alert>}
                {/* Social sign-in needs a web origin for the OAuth callback
                    to land on — the desktop app is file:// + a localhost
                    backend, so the redirect can never complete there. Hide
                    the buttons rather than dead-end the user mid-flow. */}
                {mode !== "forgot" && !window.salescale?.isDesktop && (
                  <>
                    <div className="oauth-divider">
                      <span>or</span>
                    </div>
                    <Button block onClick={() => oauth("google")}>
                      Continue with Google
                    </Button>
                    <Button block onClick={() => oauth("meta")}>
                      Continue with Meta
                    </Button>
                  </>
                )}
                {mode === "login" && (
                  <Button
                    variant="link"
                    onClick={() => {
                      setError(null);
                      setMode("forgot");
                    }}
                  >
                    Forgot password?
                  </Button>
                )}
                <Button
                  variant="link"
                  className="auth-toggle"
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
                </Button>
              </>
            )}
          </form>
        )}
      </div>
    </div>
  );
}

// --- clients grid ------------------------------------------------------------

function Clients({
  session,
  selected,
  onSelect,
  active = true,
}: {
  session: Session;
  selected: Client | null;
  onSelect: (c: Client | null) => void;
  /** False while the view is kept mounted but hidden (tab switched away). */
  active?: boolean;
}) {
  const [clients, setClients] = useState<Client[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const isAdmin = ADMIN_ROLES.includes(session.role);
  const toast = useToast();

  // Fetched once on first mount; the view stays mounted across tab switches
  // so this no longer re-runs per navigation. Explicit invalidations (back
  // from a detail where status may have changed, create) call load() again.
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
        active={active}
        onBack={() => {
          onSelect(null);
          load();
        }}
      />
    );

  return (
    <div>
      <div className="cl-page-head">
        <div>
          <h2>Clients</h2>
          <p className="cl-page-sub">
            {clients.length} {clients.length === 1 ? "client" : "clients"} in{" "}
            {session.organization_name}
          </p>
        </div>
        {isAdmin && clients.length > 0 && (
          <Button variant="primary" onClick={() => setAdding(true)}>
            <Plus size={16} aria-hidden="true" /> Add client
          </Button>
        )}
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      {!loaded ? (
        <ul className="cl-grid" aria-hidden="true">
          {Array.from({ length: 6 }, (_, i) => (
            <li key={i}>
              <div className="card cl-card cl-card--skel">
                <Skeleton width={44} height={44} />
                <div className="cl-info">
                  <Skeleton width="60%" height="0.9em" />
                  <Skeleton width="35%" height="0.7em" />
                </div>
              </div>
            </li>
          ))}
        </ul>
      ) : clients.length === 0 ? (
        <EmptyState
          hero
          icon={<Building2 aria-hidden="true" />}
          title="No clients yet"
          action={
            isAdmin ? (
              <Button variant="primary" onClick={() => setAdding(true)}>
                <Plus size={16} aria-hidden="true" /> Add your first client
              </Button>
            ) : undefined
          }
        >
          {isAdmin
            ? "Add your first client to start connecting ad accounts and tracking performance."
            : "No clients have been added to this organization yet."}
        </EmptyState>
      ) : (
        <ul className="cl-grid">
          {clients.map((c) => (
            <li key={c.id}>
              <button
                type="button"
                className="card card--interactive cl-card"
                onClick={() => onSelect(c)}
              >
                <span className="cl-avatar" aria-hidden="true">
                  {initials(c.name)}
                </span>
                <span className="cl-info">
                  <strong className="cl-name">{c.name}</strong>
                  <Badge tone={toneForStatus(c.status)}>{c.status}</Badge>
                </span>
                <ChevronRight className="cl-go" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {adding && (
        <AddClientDialog
          onClose={() => setAdding(false)}
          onCreated={(c) => {
            setAdding(false);
            setClients((prev) => [...prev, c]);
            toast(`Client "${c.name}" added`, "ok");
          }}
        />
      )}
    </div>
  );
}

function AddClientDialog({
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
  const nameRef = useRef<HTMLInputElement>(null);
  const formId = useId();

  return (
    <Dialog
      open
      onClose={onClose}
      title="Add a client"
      size="sm"
      initialFocus={nameRef}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            form={formId}
            variant="primary"
            busy={saving}
            disabled={saving || !name.trim()}
          >
            Add client
          </Button>
        </>
      }
    >
      <p className="cl-dialog-sub">
        Create a client to connect their ad accounts and track performance.
      </p>
      <form
        id={formId}
        className="cl-form"
        onSubmit={async (e) => {
          e.preventDefault();
          setSaving(true);
          setError(null);
          pingSave("saving");
          try {
            const c = await createClient({
              name: name.trim(),
              internal_notes: notes.trim() || undefined,
            });
            pingSave("saved");
            onCreated(c);
          } catch (err) {
            pingSave("error");
            setError((err as Error).message);
            setSaving(false);
          }
        }}
      >
        <Field label="Client name">
          <input
            ref={nameRef}
            placeholder="e.g. Paganelli HVAC"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
        </Field>
        <Field
          label="Internal notes"
          optional
          description="Only your team sees this — never the client."
        >
          <textarea
            rows={3}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </Field>
        {error && <Alert tone="danger">{error}</Alert>}
      </form>
    </Dialog>
  );
}

// --- client detail ------------------------------------------------------------

/** Tree selection payload (breadcrumb shows campaign selections). */
interface TreeSel {
  id: string;
  name: string;
  type: string;
}

function ClientDetail({
  client,
  session,
  onBack,
  active = true,
}: {
  client: Client;
  session: Session;
  onBack: () => void;
  /** False while the parent view is kept mounted but hidden. */
  active?: boolean;
}) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [platforms, setPlatforms] = useState<Platform[]>([]);
  const [platformFilter, setPlatformFilter] = useState<string>("all");
  const [view, setView] = useState<"dashboard" | "crm">("dashboard");
  const [error, setError] = useState<string | null>(null);
  const [treeSel, setTreeSel] = useState<TreeSel | null>(null);
  // Which platform's account picker is open (agency logins see many ad
  // accounts; the picker assigns them to the right client), and a bump key
  // that remounts the account tree after an attach/move.
  const [pickerPlatform, setPickerPlatform] = useState<string | null>(null);
  const [treeKey, setTreeKey] = useState(0);
  // Connecting platforms is Admin/Owner surface — mirrors the API gate.
  const isAdmin = ADMIN_ROLES.includes(session.role);
  const isTeam = TEAM_ROLES.includes(session.role);

  // Extend the topbar breadcrumb: client name › selected campaign.
  const setTrail = useContext(TrailCtx);
  useEffect(() => {
    const campaign = treeSel && treeSel.type === "campaign" ? treeSel : null;
    setTrail([
      {
        label: client.name,
        onClick: campaign ? () => setTreeSel(null) : undefined,
      },
      ...(campaign ? [{ label: campaign.name }] : []),
    ]);
    return () => setTrail([]);
  }, [client.name, treeSel, setTrail]);

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
  // a manual reload. (Harmless on web.) Gated so alt-tabbing doesn't hammer
  // the API: refetch on focus only when a connect flow is plausibly pending
  // (the user just opened the OAuth window), or at most once per 30s — and
  // never while the view is hidden behind another tab.
  const oauthPending = useRef(false);
  const lastFocusRefetch = useRef(0);
  useEffect(() => {
    const onFocus = () => {
      if (!active) return;
      const now = Date.now();
      if (!oauthPending.current && now - lastFocusRefetch.current < 30_000)
        return;
      oauthPending.current = false;
      lastFocusRefetch.current = now;
      loadConnections();
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [loadConnections, active]);

  // Platform catalog drives the connect list and filter — see /api/platforms.
  useEffect(() => {
    getPlatforms().then(setPlatforms).catch((e) => setError(e.message));
  }, []);

  const platformName = useCallback(
    (id: string) => platforms.find((p) => p.id === id)?.name ?? id,
    [platforms]
  );

  const connect = async (platform: string) => {
    const { url } = await api<{ url: string }>(
      `/api/connect/${platform}/start?client_id=${client.id}`
    );
    openAuthUrl(url);
    // Arm the focus-refetch above: the next window focus (back from the
    // OAuth flow) should reload connections immediately.
    oauthPending.current = true;
  };

  return (
    <div className="cl-detail">
      <header className="card card--hero cl-hero">
        <div className="cl-hero-top">
          <Button variant="ghost" size="sm" onClick={onBack}>
            <ChevronLeft size={16} aria-hidden="true" /> All clients
          </Button>
          <Segmented
            ariaLabel="Client view"
            value={view}
            onChange={setView}
            options={[
              { value: "dashboard", label: "Dashboard" },
              { value: "crm", label: "CRM" },
            ]}
          />
        </div>
        <div className="cl-hero-main">
          <h2 className="cl-hero-name">{client.name}</h2>
          <Badge tone={toneForStatus(client.status)}>{client.status}</Badge>
        </div>
        <div className="cl-hero-chips">
          {connections.length > 0 ? (
            connections.map((c) => (
              <PlatformChip key={c.id} name={platformName(c.platform)} />
            ))
          ) : (
            <span className="cl-hero-none">No platforms connected yet</span>
          )}
        </div>
      </header>
      {/* One filter governs every widget and the account tree below —
          no reload, no separate views. */}
      {view === "dashboard" && platforms.length > 0 && (
        <div className="cl-toolbar">
          <Segmented
            ariaLabel="Platform filter"
            value={platformFilter}
            onChange={setPlatformFilter}
            options={[
              { value: "all", label: "Blended" },
              ...platforms
                .filter((p) => p.connectable)
                .map((p) => ({ value: p.id, label: `${p.name} only` })),
            ]}
          />
        </div>
      )}
      {error && <Alert tone="danger">{error}</Alert>}
      {view === "crm" && (
        <CrmView clientId={client.id} session={session} active={active} />
      )}
      {view === "dashboard" && (
        <Dashboard
          clientId={client.id}
          session={session}
          platforms={platformFilter}
        />
      )}
      {view === "dashboard" && (
        <section className="card cl-section">
          <div className="card-header">
            <h3 className="card-title">Platform connections</h3>
          </div>
          <div className="cl-connections">
            {platforms.map((platform) => {
              const conn = connections.find((c) => c.platform === platform.id);
              return (
                <div key={platform.id} className="cl-connection">
                  <PlatformChip name={platform.name} />
                  {platform.coming_soon ? (
                    <Badge tone="info">coming soon</Badge>
                  ) : conn ? (
                    <Badge tone={toneForStatus(conn.status)}>{conn.status}</Badge>
                  ) : (
                    <Badge tone="neutral">not connected</Badge>
                  )}
                  {conn?.error_detail && (
                    <span className="cl-conn-err" title={conn.error_detail}>
                      {conn.error_detail}
                    </span>
                  )}
                  {isAdmin && platform.connectable && (
                    <span className="cl-conn-actions">
                      {conn?.status === "active" &&
                        ["meta", "google"].includes(platform.id) && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setPickerPlatform(platform.id)}
                          >
                            Manage accounts
                          </Button>
                        )}
                      <Button size="sm" onClick={() => connect(platform.id)}>
                        {conn ? "Reconnect" : "Connect"}
                      </Button>
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}
      {view === "dashboard" && (
        <section className="card cl-section">
          <div className="card-header">
            <h3 className="card-title">Accounts &amp; campaigns</h3>
          </div>
          <AccountTree
            key={treeKey}
            clientId={client.id}
            platformFilter={platformFilter}
            canManage={isTeam}
            platformName={platformName}
            selection={treeSel}
            onSelect={setTreeSel}
          />
        </section>
      )}
      {pickerPlatform && (
        <AccountPickerDialog
          open
          onClose={() => setPickerPlatform(null)}
          platform={pickerPlatform}
          platformLabel={platformName(pickerPlatform)}
          clientId={client.id}
          clientName={client.name}
          onChanged={() => setTreeKey((k) => k + 1)}
        />
      )}
    </div>
  );
}

// --- account tree (§4.14: role=tree, full keyboard model) ---------------------

interface TreeMeta {
  id: string;
  parentId: string | null;
  level: number;
  canExpand: boolean;
  name: string;
  type: string;
}

interface TreeCtxVal {
  openIds: Set<string>;
  setOpen: (id: string, open: boolean) => void;
  focusId: string | null;
  setFocusId: (id: string) => void;
  firstId: string | null;
  selectedId: string | null;
  select: (sel: TreeSel) => void;
  register: (meta: TreeMeta) => () => void;
  rowKeyDown: (e: ReactKeyboardEvent<HTMLDivElement>, meta: TreeMeta) => void;
}

const TreeCtx = createContext<TreeCtxVal | null>(null);

function AccountTree({
  clientId,
  platformFilter,
  canManage,
  platformName,
  selection,
  onSelect,
}: {
  clientId: string;
  platformFilter: string;
  canManage: boolean;
  platformName: (id: string) => string;
  selection: TreeSel | null;
  onSelect: (sel: TreeSel | null) => void;
}) {
  const [accounts, setAccounts] = useState<AdAccount[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());
  const [focusId, setFocusId] = useState<string | null>(null);
  const registry = useRef(new Map<string, TreeMeta>());
  const treeRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    api<AdAccount[]>(`/api/ad-accounts?client_id=${clientId}`)
      .then(setAccounts)
      .catch((e) => setError(e.message));
  }, [clientId]);

  // If the tab-stop row unmounts (ancestor collapsed, filter changed), fall
  // back to the first visible row.
  useEffect(() => {
    if (focusId && !registry.current.has(focusId)) setFocusId(null);
  });

  const setOpen = useCallback((id: string, open: boolean) => {
    setOpenIds((prev) => {
      const next = new Set(prev);
      if (open) next.add(id);
      else next.delete(id);
      return next;
    });
  }, []);

  const register = useCallback((meta: TreeMeta) => {
    registry.current.set(meta.id, meta);
    return () => {
      registry.current.delete(meta.id);
    };
  }, []);

  const visible = (accounts ?? []).filter(
    (a) => platformFilter === "all" || a.platform === platformFilter
  );
  const firstId = visible[0]?.id ?? null;

  const rowKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>, meta: TreeMeta) => {
    const rows = Array.from(
      treeRef.current?.querySelectorAll<HTMLElement>('[role="treeitem"]') ?? []
    );
    const idx = rows.findIndex((el) => el.dataset.treeId === meta.id);
    const focusRow = (el: HTMLElement | undefined) => {
      if (!el?.dataset.treeId) return;
      setFocusId(el.dataset.treeId);
      el.focus();
    };
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        focusRow(rows[idx + 1]);
        break;
      case "ArrowUp":
        e.preventDefault();
        focusRow(rows[idx - 1]);
        break;
      case "Home":
        e.preventDefault();
        focusRow(rows[0]);
        break;
      case "End":
        e.preventDefault();
        focusRow(rows[rows.length - 1]);
        break;
      case "ArrowRight":
        e.preventDefault();
        if (meta.canExpand && !openIds.has(meta.id)) setOpen(meta.id, true);
        else if (meta.canExpand && openIds.has(meta.id)) focusRow(rows[idx + 1]);
        break;
      case "ArrowLeft":
        e.preventDefault();
        if (meta.canExpand && openIds.has(meta.id)) setOpen(meta.id, false);
        else {
          for (let i = idx - 1; i >= 0; i--) {
            if (Number(rows[i].dataset.level) === meta.level - 1) {
              focusRow(rows[i]);
              break;
            }
          }
        }
        break;
      case "Enter":
        e.preventDefault();
        onSelect({ id: meta.id, name: meta.name, type: meta.type });
        break;
      case "*":
        e.preventDefault();
        registry.current.forEach((m) => {
          if (m.parentId === meta.parentId && m.canExpand) setOpen(m.id, true);
        });
        break;
    }
  };

  if (error) return <Alert tone="danger">{error}</Alert>;
  if (accounts === null)
    return (
      <ul className="tree cl-tree" aria-hidden="true">
        <TreeSkeletonRows level={1} />
      </ul>
    );
  if (!visible.length)
    return (
      <EmptyState title="No ad accounts yet">
        {platformFilter === "all"
          ? "Connect a platform above to pull this client's ad accounts in."
          : "No ad accounts on this platform yet."}
      </EmptyState>
    );

  const ctx: TreeCtxVal = {
    openIds,
    setOpen,
    focusId,
    setFocusId,
    firstId,
    selectedId: selection?.id ?? null,
    select: (sel) => onSelect(sel),
    register,
    rowKeyDown,
  };

  return (
    <TreeCtx.Provider value={ctx}>
      <ul
        className="tree cl-tree"
        role="tree"
        aria-label="Accounts and campaigns"
        ref={treeRef}
      >
        {visible.map((a) => (
          <AccountNode
            key={a.id}
            account={a}
            canManage={canManage}
            platformName={platformName}
          />
        ))}
      </ul>
    </TreeCtx.Provider>
  );
}

/** One 36px tree row (§4.14): indent rails, rotating chevron, type tag. */
function TreeRow({
  id,
  parentId,
  level,
  canExpand,
  name,
  entityType,
  typeTag,
  children,
}: {
  id: string;
  parentId: string | null;
  level: number;
  canExpand: boolean;
  name: string;
  entityType: string;
  typeTag: string;
  children: ReactNode;
}) {
  const ctx = useContext(TreeCtx)!;
  const open = ctx.openIds.has(id);
  const selected = ctx.selectedId === id;
  const tabbable = (ctx.focusId ?? ctx.firstId) === id;

  useEffect(
    () => ctx.register({ id, parentId, level, canExpand, name, type: entityType }),
    // ctx.register is stable (useCallback in AccountTree).
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [id, parentId, level, canExpand, name, entityType]
  );

  const meta: TreeMeta = { id, parentId, level, canExpand, name, type: entityType };

  return (
    <div
      role="treeitem"
      className="tree-row"
      data-tree-id={id}
      data-level={level}
      aria-level={level}
      aria-expanded={canExpand ? open : undefined}
      aria-selected={selected}
      tabIndex={tabbable ? 0 : -1}
      onClick={() => {
        ctx.setFocusId(id);
        ctx.select({ id, name, type: entityType });
        if (canExpand) ctx.setOpen(id, !open);
      }}
      onKeyDown={(e) => ctx.rowKeyDown(e, meta)}
    >
      {Array.from({ length: level - 1 }, (_, i) => (
        <span key={i} className="tree-indent" aria-hidden="true" />
      ))}
      {canExpand ? (
        <span className="tree-chevron" aria-hidden="true">
          <ChevronRight size={16} />
        </span>
      ) : (
        <span className="tree-chevron tree-chevron--leaf" aria-hidden="true" />
      )}
      <span className="tree-type">{typeTag}</span>
      {children}
    </div>
  );
}

/** 2 inline skeleton rows while async children load (§4.14). */
function TreeSkeletonRows({ level }: { level: number }) {
  return (
    <>
      {[0, 1].map((i) => (
        <li role="none" key={i}>
          <div className="tree-row" aria-hidden="true">
            {Array.from({ length: level - 1 }, (_, j) => (
              <span key={j} className="tree-indent" />
            ))}
            <Skeleton width={i === 0 ? "52%" : "38%"} height="0.8em" />
          </div>
        </li>
      ))}
    </>
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
    <div className="cl-inline-form">
      <Field label="Campaign name">
        <input
          placeholder="Campaign name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <Field label="Daily budget ($)">
        <input
          placeholder="e.g. 50"
          type="number"
          min="1"
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
        />
      </Field>
      <Button
        variant="primary"
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
      </Button>
      <span className="cl-inline-hint">created paused; enable it when ready</span>
      {error && <Alert tone="danger">{error}</Alert>}
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
    <div className="cl-inline-form">
      <Field label="Name">
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Daily budget ($)">
        <input
          placeholder="e.g. 50"
          type="number"
          min="1"
          value={budget}
          onChange={(e) => setBudget(e.target.value)}
        />
      </Field>
      <Button
        variant="primary"
        onClick={() => {
          const payload: Record<string, unknown> = {};
          if (name !== initialName) payload.name = name;
          if (budget !== "")
            payload.daily_budget_micros = Math.round(Number(budget) * 1_000_000);
          onStage(payload);
        }}
      >
        Stage {label}
      </Button>
    </div>
  );
}

function AccountNode({
  account,
  canManage,
  platformName,
}: {
  account: AdAccount;
  canManage: boolean;
  platformName: (id: string) => string;
}) {
  const ctx = useContext(TreeCtx)!;
  const open = ctx.openIds.has(account.id);
  const [showNew, setShowNew] = useState(false);
  const [showCreatives, setShowCreatives] = useState(false);
  const { items, loading, warning, error, reload } = useLazyChildren<Campaign>(
    open ? `/api/ad-accounts/${account.id}/campaigns` : null
  );
  return (
    <li role="none">
      <TreeRow
        id={account.id}
        parentId={null}
        level={1}
        canExpand
        name={account.name}
        entityType="account"
        typeTag="ACCT"
      >
        <PlatformChip name={platformName(account.platform)} />
        <strong className="tree-name">{account.name}</strong>
        <span className="tree-ext">{account.external_id}</span>
      </TreeRow>
      {open && (
        <ul role="group" className="cl-tree-group">
          {loading && <TreeSkeletonRows level={2} />}
          {warning && (
            <li role="none" className="cl-tree-note cl-tree-note--warn">
              {warning}
            </li>
          )}
          {error && (
            <li role="none" className="cl-tree-note cl-tree-note--err">
              {error}
            </li>
          )}
          {items?.map((c) => (
            <CampaignNode
              key={c.id}
              campaign={c}
              account={account}
              canManage={canManage}
              onChanged={reload}
            />
          ))}
          {items?.length === 0 && (
            <li role="none" className="cl-tree-note">
              No campaigns
            </li>
          )}
          {canManage && (
            <li role="none" className="cl-tree-actions">
              <span className="cl-tree-actions-row">
                <Button variant="link" onClick={() => setShowNew(!showNew)}>
                  {showNew ? "Cancel" : "+ New campaign"}
                </Button>
                {account.platform === "meta" && (
                  <Button
                    variant="link"
                    onClick={() => setShowCreatives(!showCreatives)}
                  >
                    {showCreatives ? "Hide creatives" : "Creatives"}
                  </Button>
                )}
              </span>
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
  const ctx = useContext(TreeCtx)!;
  const open = ctx.openIds.has(campaign.id);
  const [panel, setPanel] = useState<"none" | "edit" | "terms" | "assets">(
    "none"
  );
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
    <li role="none">
      <TreeRow
        id={campaign.id}
        parentId={account.id}
        level={2}
        canExpand
        name={campaign.name}
        entityType="campaign"
        typeTag="CMP"
      >
        <span className="tree-name">{campaign.name}</span>
        {campaign.status && (
          <Badge tone={toneForStatus(campaign.status)}>{campaign.status}</Badge>
        )}
        {budget && <span className="tree-spend">{budget}</span>}
        {canManage && (
          <span className="tree-actions" onClick={(e) => e.stopPropagation()}>
            <Button
              variant="link"
              onClick={() => stageAction(paused ? "resume" : "pause")}
            >
              {paused ? "Resume" : "Pause"}
            </Button>
            <Button
              variant="link"
              onClick={() => setPanel(panel === "edit" ? "none" : "edit")}
            >
              Edit
            </Button>
            {account.platform === "google" && (
              <>
                <Button
                  variant="link"
                  onClick={() => setPanel(panel === "terms" ? "none" : "terms")}
                >
                  Search terms
                </Button>
                <Button
                  variant="link"
                  onClick={() => setPanel(panel === "assets" ? "none" : "assets")}
                >
                  Asset groups
                </Button>
              </>
            )}
          </span>
        )}
      </TreeRow>
      {actionError && <Alert tone="danger">{actionError}</Alert>}
      {panel === "edit" && (
        <div className="cl-tree-panel">
          <EditForm
            label="update"
            initialName={campaign.name}
            initialBudgetMicros={campaign.daily_budget_micros}
            onStage={(payload) => {
              setPanel("none");
              stageAction("update", payload);
            }}
          />
        </div>
      )}
      {panel === "terms" && (
        <div className="cl-tree-panel">
          <SearchTermsPanel campaignId={campaign.id} adAccountId={account.id} />
        </div>
      )}
      {panel === "assets" && (
        <div className="cl-tree-panel">
          <AssetGroupsPanel campaignId={campaign.id} adAccountId={account.id} />
        </div>
      )}
      {open && (
        <ul role="group" className="cl-tree-group">
          {loading && <TreeSkeletonRows level={3} />}
          {warning && (
            <li role="none" className="cl-tree-note cl-tree-note--warn">
              {warning}
            </li>
          )}
          {error && (
            <li role="none" className="cl-tree-note cl-tree-note--err">
              {error}
            </li>
          )}
          {items?.map((g) => (
            <AdGroupNode
              key={g.id}
              adGroup={g}
              campaign={campaign}
              account={account}
              canManage={canManage}
              onChanged={reload}
            />
          ))}
          {items?.length === 0 && (
            <li role="none" className="cl-tree-note">
              No ad sets / ad groups
            </li>
          )}
        </ul>
      )}
    </li>
  );
}

function AdGroupNode({
  adGroup,
  campaign,
  account,
  canManage,
  onChanged,
}: {
  adGroup: AdGroup;
  campaign: Campaign;
  account: AdAccount;
  canManage: boolean;
  onChanged: () => void;
}) {
  const { stage } = useManage();
  const ctx = useContext(TreeCtx)!;
  const open = ctx.openIds.has(adGroup.id);
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
    <li role="none">
      <TreeRow
        id={adGroup.id}
        parentId={campaign.id}
        level={3}
        canExpand
        name={adGroup.name}
        entityType="ad_group"
        typeTag="ADSET"
      >
        <span className="tree-name">{adGroup.name}</span>
        {adGroup.status && (
          <Badge tone={toneForStatus(adGroup.status)}>{adGroup.status}</Badge>
        )}
        {canManage && (
          <span className="tree-actions" onClick={(e) => e.stopPropagation()}>
            <Button
              variant="link"
              onClick={() => stageAction(paused ? "resume" : "pause")}
            >
              {paused ? "Resume" : "Pause"}
            </Button>
            {account.platform === "google" && (
              <Button
                variant="link"
                onClick={() => setShowKeywords(!showKeywords)}
              >
                Keywords
              </Button>
            )}
          </span>
        )}
      </TreeRow>
      {actionError && <Alert tone="danger">{actionError}</Alert>}
      {showKeywords && (
        <div className="cl-tree-panel">
          <KeywordsPanel adGroupId={adGroup.id} adAccountId={account.id} />
        </div>
      )}
      {open && (
        <ul role="group" className="cl-tree-group">
          {loading && <TreeSkeletonRows level={4} />}
          {warning && (
            <li role="none" className="cl-tree-note cl-tree-note--warn">
              {warning}
            </li>
          )}
          {error && (
            <li role="none" className="cl-tree-note cl-tree-note--err">
              {error}
            </li>
          )}
          {items?.map((ad) => (
            <AdLeaf
              key={ad.id}
              ad={ad}
              adGroup={adGroup}
              account={account}
              canManage={canManage}
              onChanged={reload}
            />
          ))}
          {items?.length === 0 && (
            <li role="none" className="cl-tree-note">
              No ads
            </li>
          )}
        </ul>
      )}
    </li>
  );
}

function AdLeaf({
  ad,
  adGroup,
  account,
  canManage,
  onChanged,
}: {
  ad: AdRow;
  adGroup: AdGroup;
  account: AdAccount;
  canManage: boolean;
  onChanged: () => void;
}) {
  const { stage } = useManage();
  const [actionError, setActionError] = useState<string | null>(null);
  const paused = ad.status?.toUpperCase() === "PAUSED";
  return (
    <li role="none">
      <TreeRow
        id={ad.id}
        parentId={adGroup.id}
        level={4}
        canExpand={false}
        name={ad.name}
        entityType="ad"
        typeTag="AD"
      >
        <span className="tree-name">{ad.name}</span>
        {ad.status && <Badge tone={toneForStatus(ad.status)}>{ad.status}</Badge>}
        {canManage && (
          <span className="tree-actions" onClick={(e) => e.stopPropagation()}>
            <Button
              variant="link"
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
            </Button>
          </span>
        )}
      </TreeRow>
      {actionError && <Alert tone="danger">{actionError}</Alert>}
    </li>
  );
}
