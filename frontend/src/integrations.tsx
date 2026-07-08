import { useEffect, useState } from "react";
import {
  deleteIntegration,
  listIntegrations,
  setGoogleCreds,
  setMetaCreds,
  type IntegrationStatus,
} from "./api";

const STATUS_LABEL: Record<IntegrationStatus["source"], string> = {
  organization: "Connected",
  global: "Using shared app",
  none: "Not configured",
};

const STATUS_CLASS: Record<IntegrationStatus["source"], string> = {
  organization: "active",
  global: "warn",
  none: "none",
};

export function Integrations() {
  const [statuses, setStatuses] = useState<IntegrationStatus[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    listIntegrations().then(setStatuses).catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  const meta = statuses.find((s) => s.provider === "meta");
  const google = statuses.find((s) => s.provider === "google");

  return (
    <div>
      <div className="page-head">
        <div>
          <h2>Integrations</h2>
          <p className="page-sub">
            Connect your own Meta and Google Ads apps so you can link your
            clients' ad accounts.
          </p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}

      <MetaCard status={meta} onChange={load} />
      <GoogleCard status={google} onChange={load} />
    </div>
  );
}

function StatusBadge({ status }: { status?: IntegrationStatus }) {
  if (!status) return null;
  return (
    <span className={`badge ${STATUS_CLASS[status.source]}`}>
      {STATUS_LABEL[status.source]}
    </span>
  );
}

function ProviderShell({
  title,
  desc,
  status,
  children,
  onRemove,
}: {
  title: string;
  desc: string;
  status?: IntegrationStatus;
  children: React.ReactNode;
  onRemove: () => void;
}) {
  return (
    <div className="integration-card">
      <div className="integration-head">
        <div>
          <strong>{title}</strong>
          <p className="page-sub">{desc}</p>
        </div>
        <StatusBadge status={status} />
      </div>
      {children}
      {status?.source === "organization" && (
        <div className="integration-foot">
          <span className="page-sub">
            App ID: <code>{status.public_id}</code>
          </span>
          <button className="danger" onClick={onRemove}>
            Remove
          </button>
        </div>
      )}
    </div>
  );
}

function MetaCard({
  status,
  onChange,
}: {
  status?: IntegrationStatus;
  onChange: () => void;
}) {
  const [appId, setAppId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await setMetaCreds({ app_id: appId.trim(), app_secret: appSecret.trim() });
      setAppId("");
      setAppSecret("");
      setSaved(true);
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <ProviderShell
      title="Meta (Facebook / Instagram Ads)"
      desc="From your Meta app: App ID and App Secret."
      status={status}
      onRemove={() => deleteIntegration("meta").then(onChange)}
    >
      <div className="form-grid">
        <label className="field">
          <span>App ID</span>
          <input value={appId} onChange={(e) => setAppId(e.target.value)} placeholder="e.g. 1234567890" />
        </label>
        <label className="field">
          <span>App Secret</span>
          <input
            type="password"
            value={appSecret}
            onChange={(e) => setAppSecret(e.target.value)}
            placeholder="••••••••"
          />
        </label>
        {error && <p className="error">{error}</p>}
        {saved && <p className="notice">Saved.</p>}
        <div>
          <button
            className="primary"
            disabled={saving || !appId.trim() || !appSecret.trim()}
            onClick={save}
          >
            {saving ? "Saving…" : status?.source === "organization" ? "Update" : "Save"}
          </button>
        </div>
      </div>
    </ProviderShell>
  );
}

function GoogleCard({
  status,
  onChange,
}: {
  status?: IntegrationStatus;
  onChange: () => void;
}) {
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [devToken, setDevToken] = useState("");
  const [loginCustomerId, setLoginCustomerId] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await setGoogleCreds({
        client_id: clientId.trim(),
        client_secret: clientSecret.trim(),
        developer_token: devToken.trim(),
        login_customer_id: loginCustomerId.trim() || undefined,
      });
      setClientId("");
      setClientSecret("");
      setDevToken("");
      setLoginCustomerId("");
      setSaved(true);
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const ready = clientId.trim() && clientSecret.trim() && devToken.trim();

  return (
    <ProviderShell
      title="Google Ads"
      desc="OAuth Client ID + Secret and your Google Ads developer token."
      status={status}
      onRemove={() => deleteIntegration("google").then(onChange)}
    >
      <div className="form-grid">
        <label className="field">
          <span>OAuth Client ID</span>
          <input value={clientId} onChange={(e) => setClientId(e.target.value)} />
        </label>
        <label className="field">
          <span>OAuth Client Secret</span>
          <input type="password" value={clientSecret} onChange={(e) => setClientSecret(e.target.value)} placeholder="••••••••" />
        </label>
        <label className="field">
          <span>Developer token</span>
          <input type="password" value={devToken} onChange={(e) => setDevToken(e.target.value)} placeholder="••••••••" />
        </label>
        <label className="field">
          <span>
            Login customer ID <em className="opt">optional (MCC)</em>
          </span>
          <input value={loginCustomerId} onChange={(e) => setLoginCustomerId(e.target.value)} placeholder="1234567890" />
        </label>
        {error && <p className="error">{error}</p>}
        {saved && <p className="notice">Saved.</p>}
        <div>
          <button className="primary" disabled={saving || !ready} onClick={save}>
            {saving ? "Saving…" : status?.source === "organization" ? "Update" : "Save"}
          </button>
        </div>
      </div>
    </ProviderShell>
  );
}
