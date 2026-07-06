import { useEffect, useState } from "react";
import {
  ADMIN_ROLES,
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
import "./App.css";

export default function App() {
  const [session, setSess] = useState<Session | null>(getSession());
  if (!session) return <Login onLogin={setSess} />;
  return (
    <div className="shell">
      <header>
        <h1>{session.organization_name}</h1>
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
      <Clients session={session} />
    </div>
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
      <button onClick={onBack}>← All clients</button>
      <h2>{client.name}</h2>
      {error && <p className="error">{error}</p>}
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
        <div className="toggle">
          {(["all", "meta", "google"] as const).map((p) => (
            <button
              key={p}
              className={platformFilter === p ? "active" : ""}
              onClick={() => setPlatformFilter(p)}
            >
              {p === "all" ? "All platforms" : p === "meta" ? "Meta" : "Google"}
            </button>
          ))}
        </div>
        <AccountTree clientId={client.id} platformFilter={platformFilter} />
      </section>
    </div>
  );
}

function AccountTree({
  clientId,
  platformFilter,
}: {
  clientId: string;
  platformFilter: "all" | "meta" | "google";
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
        <AccountNode key={a.id} account={a} />
      ))}
    </ul>
  );
}

function useLazyChildren<T>(path: string | null) {
  const [items, setItems] = useState<T[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    setLoading(true);
    api<T[]>(path)
      .then(setItems)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [path]);
  return { items, loading, error };
}

function PlatformBadge({ platform }: { platform: "meta" | "google" }) {
  return <span className={`platform ${platform}`}>{platform}</span>;
}

function AccountNode({ account }: { account: AdAccount }) {
  const [open, setOpen] = useState(false);
  const { items, loading, error } = useLazyChildren<Campaign>(
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
          {error && <li className="error">{error}</li>}
          {items?.map((c) => (
            <CampaignNode key={c.id} campaign={c} />
          ))}
          {items?.length === 0 && <li className="muted">No campaigns</li>}
        </ul>
      )}
    </li>
  );
}

function CampaignNode({ campaign }: { campaign: Campaign }) {
  const [open, setOpen] = useState(false);
  const { items, loading, error } = useLazyChildren<AdGroup>(
    open ? `/api/campaigns/${campaign.id}/ad-groups` : null
  );
  const budget =
    campaign.daily_budget_micros != null
      ? `$${(campaign.daily_budget_micros / 1_000_000).toFixed(2)}/day`
      : null;
  return (
    <li>
      <div className="node" onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} {campaign.name}
        <span className={`badge ${campaign.status?.toLowerCase()}`}>
          {campaign.status}
        </span>
        {budget && <span className="muted">{budget}</span>}
      </div>
      {open && (
        <ul>
          {loading && <li className="muted">Loading…</li>}
          {error && <li className="error">{error}</li>}
          {items?.map((g) => (
            <AdGroupNode key={g.id} adGroup={g} />
          ))}
          {items?.length === 0 && (
            <li className="muted">No ad sets / ad groups</li>
          )}
        </ul>
      )}
    </li>
  );
}

function AdGroupNode({ adGroup }: { adGroup: AdGroup }) {
  const [open, setOpen] = useState(false);
  const { items, loading, error } = useLazyChildren<AdRow>(
    open ? `/api/ad-groups/${adGroup.id}/ads` : null
  );
  return (
    <li>
      <div className="node" onClick={() => setOpen(!open)}>
        {open ? "▾" : "▸"} {adGroup.name}
        <span className={`badge ${adGroup.status?.toLowerCase()}`}>
          {adGroup.status}
        </span>
      </div>
      {open && (
        <ul>
          {loading && <li className="muted">Loading…</li>}
          {error && <li className="error">{error}</li>}
          {items?.map((ad) => (
            <li key={ad.id}>
              {ad.name}{" "}
              <span className={`badge ${ad.status?.toLowerCase()}`}>
                {ad.status}
              </span>
            </li>
          ))}
          {items?.length === 0 && <li className="muted">No ads</li>}
        </ul>
      )}
    </li>
  );
}
