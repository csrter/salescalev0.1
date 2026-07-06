import { useCallback, useEffect, useState } from "react";
import {
  ADMIN_ROLES,
  TEAM_ROLES,
  api,
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
import "./App.css";

type Tab = "clients" | "changes" | "audit";

export default function App() {
  const [session, setSess] = useState<Session | null>(getSession());
  const [tab, setTab] = useState<Tab>("clients");
  if (!session) return <Login onLogin={setSess} />;
  const isTeam = TEAM_ROLES.includes(session.role);
  return (
    <ManageProvider>
      <div className="shell">
        <header>
          <h1>{session.organization_name}</h1>
          <nav className="toggle">
            <button
              className={tab === "clients" ? "active" : ""}
              onClick={() => setTab("clients")}
            >
              Clients
            </button>
            {isTeam && (
              <button
                className={tab === "changes" ? "active" : ""}
                onClick={() => setTab("changes")}
              >
                Pending changes
              </button>
            )}
            <button
              className={tab === "audit" ? "active" : ""}
              onClick={() => setTab("audit")}
            >
              Audit log
            </button>
          </nav>
          <span>
            {session.full_name} ({session.role})
          </span>
          <button
            onClick={() => {
              setSession(null);
              setSess(null);
            }}
          >
            Log out
          </button>
        </header>
        {tab === "clients" && <Clients session={session} />}
        {tab === "changes" && <PendingChangesPanel />}
        {tab === "audit" && <AuditLogView />}
      </div>
    </ManageProvider>
  );
}

function Login({ onLogin }: { onLogin: (s: Session) => void }) {
  const [mode, setMode] = useState<"login" | "signup">("login");
  const [orgName, setOrgName] = useState("");
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <form
      className="login"
      onSubmit={async (e) => {
        e.preventDefault();
        try {
          onLogin(
            mode === "login"
              ? await login(email, password)
              : await signup(orgName, email, password, fullName)
          );
        } catch (err) {
          setError((err as Error).message);
        }
      }}
    >
      <h1>Salescale</h1>
      {mode === "signup" && (
        <>
          <input
            placeholder="Agency / organization name"
            value={orgName}
            onChange={(e) => setOrgName(e.target.value)}
          />
          <input
            placeholder="Your name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
          />
        </>
      )}
      <input
        placeholder="Email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
      />
      <input
        placeholder="Password"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      <button type="submit">
        {mode === "login" ? "Log in" : "Create organization"}
      </button>
      <button
        type="button"
        className="link"
        onClick={() => {
          setError(null);
          setMode(mode === "login" ? "signup" : "login");
        }}
      >
        {mode === "login"
          ? "New agency? Sign up"
          : "Already have an account? Log in"}
      </button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

function Clients({ session }: { session: Session }) {
  const [clients, setClients] = useState<Client[]>([]);
  const [selected, setSelected] = useState<Client | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Client[]>("/api/clients").then(setClients).catch((e) => setError(e.message));
  }, []);

  if (selected)
    return (
      <ClientDetail
        client={selected}
        session={session}
        onBack={() => setSelected(null)}
      />
    );

  return (
    <div>
      <h2>Clients</h2>
      {error && <p className="error">{error}</p>}
      <ul className="cards">
        {clients.map((c) => (
          <li key={c.id} onClick={() => setSelected(c)}>
            <strong>{c.name}</strong>
            <span className={`badge ${c.status}`}>{c.status}</span>
          </li>
        ))}
      </ul>
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
  const [platformFilter, setPlatformFilter] = useState<"all" | "meta" | "google">(
    "all"
  );
  const [error, setError] = useState<string | null>(null);
  // Connecting platforms is Admin/Owner surface — mirrors the API gate.
  const isAdmin = ADMIN_ROLES.includes(session.role);
  const isTeam = TEAM_ROLES.includes(session.role);

  useEffect(() => {
    api<Connection[]>(`/api/clients/${client.id}/connections`)
      .then(setConnections)
      .catch((e) => setError(e.message));
  }, [client.id]);

  const connect = async (platform: "meta" | "google") => {
    const { url } = await api<{ url: string }>(
      `/api/connect/${platform}/start?client_id=${client.id}`
    );
    window.location.href = url;
  };

  return (
    <div>
      <div className="client-head">
        <button className="link" onClick={onBack}>
          ← All clients
        </button>
        <h2>{client.name}</h2>
        {/* One filter governs every widget and the account tree below —
            no reload, no separate views. */}
        <nav className="toggle platform-toggle">
          {(["all", "meta", "google"] as const).map((p) => (
            <button
              key={p}
              className={platformFilter === p ? "active" : ""}
              onClick={() => setPlatformFilter(p)}
            >
              {p === "all" ? "Blended" : p === "meta" ? "Meta only" : "Google only"}
            </button>
          ))}
        </nav>
      </div>
      {error && <p className="error">{error}</p>}
      <Dashboard
        clientId={client.id}
        session={session}
        platforms={platformFilter}
      />
      <section>
        <h3>Platform connections</h3>
        {(["meta", "google"] as const).map((platform) => {
          const conn = connections.find((c) => c.platform === platform);
          return (
            <div key={platform} className="connection">
              <strong>{platform === "meta" ? "Meta" : "Google Ads"}</strong>
              {conn ? (
                <span className={`badge ${conn.status}`}>
                  {conn.status}
                  {conn.error_detail ? ` — ${conn.error_detail}` : ""}
                </span>
              ) : (
                <span className="badge none">not connected</span>
              )}
              {isAdmin && (
                <button onClick={() => connect(platform)}>
                  {conn ? "Reconnect" : "Connect"}
                </button>
              )}
            </div>
          );
        })}
      </section>
      <section>
        <h3>Accounts &amp; campaigns</h3>
        <AccountTree
          clientId={client.id}
          platformFilter={platformFilter}
          canManage={isTeam}
        />
      </section>
    </div>
  );
}

function AccountTree({
  clientId,
  platformFilter,
  canManage,
}: {
  clientId: string;
  platformFilter: "all" | "meta" | "google";
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
