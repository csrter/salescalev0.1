/**
 * Org-level "bring your own app" credential page. The set of platforms is
 * driven by the backend registry (GET /api/platforms) rather than hardcoded:
 * Meta and Google support per-org app credentials today; every other
 * registered platform renders as a neutral "coming soon" card until its
 * adapter ships. Platform identity is a neutral monogram chip — never a brand
 * color (DESIGN.md §4.8, §7).
 */

import { useCallback, useEffect, useState } from "react";
import {
  deleteIntegration,
  deleteLeadProviderKey,
  getAiProviderStatus,
  getPlatforms,
  getRedirectUris,
  listIntegrations,
  setGoogleCreds,
  setLeadProviderKey,
  setMetaCreds,
  type AiProviderStatus,
  type IntegrationStatus,
  type Platform,
  type RedirectUri,
} from "./api";
import { ConfirmDialog } from "./components/Dialog";
import { useToast } from "./components/Toast";
import {
  Alert,
  Badge,
  type BadgeTone,
  Button,
  Field,
  PlatformChip,
  SkeletonText,
} from "./components/ui";
import "./styles/views/manage.css";

const STATUS: Record<
  IntegrationStatus["source"],
  { label: string; tone: BadgeTone }
> = {
  organization: { label: "Connected", tone: "ok" },
  global: { label: "Using shared app", tone: "info" },
  none: { label: "Not configured", tone: "neutral" },
};

export function Integrations({ isOwner }: { isOwner: boolean }) {
  const [statuses, setStatuses] = useState<IntegrationStatus[]>([]);
  const [platforms, setPlatforms] = useState<Platform[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadStatuses = useCallback(() => {
    listIntegrations()
      .then(setStatuses)
      .catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    loadStatuses();
  }, [loadStatuses]);
  useEffect(() => {
    getPlatforms()
      .then(setPlatforms)
      .catch((e) => setError(e.message));
  }, []);

  const statusFor = (id: string) => statuses.find((s) => s.provider === id);

  return (
    <div className="mg-view">
      <header className="mg-head">
        <h2>Integrations</h2>
        <p className="mg-sub">
          Connect your own Meta and Google Ads apps so you can link your
          clients' ad accounts. More platforms are on the way.
        </p>
      </header>
      {error && <Alert tone="danger">{error}</Alert>}
      <RedirectUrisCard />
      <AiProviderKeysCard isOwner={isOwner} />
      {platforms === null ? (
        <SkeletonText lines={4} />
      ) : (
        <div className="mg-integrations">
          {platforms.map((p) =>
            p.id === "meta" ? (
              <MetaCard
                key={p.id}
                platform={p}
                status={statusFor("meta")}
                onChange={loadStatuses}
              />
            ) : p.id === "google" ? (
              <GoogleCard
                key={p.id}
                platform={p}
                status={statusFor("google")}
                onChange={loadStatuses}
              />
            ) : (
              <ComingSoonCard key={p.id} platform={p} />
            )
          )}
        </div>
      )}
    </div>
  );
}

const AI_PROVIDER_LABELS: Record<string, string> = {
  anthropic: "Anthropic (Claude)",
  openai: "OpenAI",
  gemini: "Google Gemini",
};

/** BYO AI-provider key: powers AI insights, {{ai_snippet}} personalization and
 * AI research fields. The active provider is operator-selected; the org's own
 * key (when set) is used before the platform's. Owner-only writes —
 * server-enforced, the UI mirrors it. */
function AiProviderKeysCard({ isOwner }: { isOwner: boolean }) {
  const [status, setStatus] = useState<AiProviderStatus | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const toast = useToast();

  const refresh = useCallback(
    () => getAiProviderStatus().then(setStatus).catch(() => setStatus(null)),
    []
  );
  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (status === null) return null;
  const active = status.providers.find((p) => p.provider === status.active);

  const save = async () => {
    if (!editing) return;
    setBusy(true);
    try {
      await setLeadProviderKey(editing, key.trim());
      toast(`${AI_PROVIDER_LABELS[editing]} key saved`, "ok");
      setEditing(null);
      setKey("");
      await refresh();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const clear = async (provider: string) => {
    setBusy(true);
    try {
      await deleteLeadProviderKey(provider);
      toast(`${AI_PROVIDER_LABELS[provider]} key removed`, "info");
      await refresh();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card mg-integration">
      <div className="mg-integration-head">
        <div className="mg-integration-title">
          <h3 className="mg-redirects-title">AI provider</h3>
          <p className="mg-sub">
            Powers AI insights, email/SMS personalization and AI research
            fields. Active provider: {AI_PROVIDER_LABELS[status.active]} (
            {status.model}) — your own key is used when set, otherwise the
            platform's. Keys are stored encrypted and never shown again.
          </p>
        </div>
        {active?.source === "organization" ? (
          <Badge tone="ok">Your key</Badge>
        ) : active?.configured ? (
          <Badge tone="info">Platform key</Badge>
        ) : (
          <Badge tone="warn">No key — AI features off</Badge>
        )}
      </div>
      {!isOwner && (
        <p className="mg-sub">
          Only the organization owner can add or remove AI provider keys.
        </p>
      )}
      <ul className="mg-ai-provider-list">
        {status.providers.map((p) => (
          <li key={p.provider} className="mg-ai-provider-row">
            <span className="mg-ai-provider-name">
              {AI_PROVIDER_LABELS[p.provider]}
              {p.provider === status.active && <Badge tone="info">active</Badge>}
              {p.source === "organization" ? (
                <Badge tone="ok">your key</Badge>
              ) : p.configured ? (
                <Badge tone="neutral">platform key</Badge>
              ) : (
                <Badge tone="neutral">not configured</Badge>
              )}
            </span>
            {isOwner &&
              (editing === p.provider ? (
                <form
                  className="mg-ai-provider-edit"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void save();
                  }}
                >
                  <input
                    type="password"
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    placeholder={`${AI_PROVIDER_LABELS[p.provider]} API key`}
                    aria-label={`${AI_PROVIDER_LABELS[p.provider]} API key`}
                    autoFocus
                  />
                  <Button
                    type="submit"
                    size="sm"
                    busy={busy}
                    disabled={key.trim().length < 8}
                  >
                    Save
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() => {
                      setEditing(null);
                      setKey("");
                    }}
                  >
                    Cancel
                  </Button>
                </form>
              ) : (
                <div className="mg-ai-provider-actions">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setEditing(p.provider);
                      setKey("");
                    }}
                  >
                    {p.source === "organization" ? "Replace key" : "Add key"}
                  </Button>
                  {p.source === "organization" && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      onClick={() => void clear(p.provider)}
                    >
                      Remove
                    </Button>
                  )}
                </div>
              ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

const URI_PURPOSE: Record<RedirectUri["purpose"], string> = {
  connect: "Ad-account connect",
  signin: "Sign in with",
};

/** The exact OAuth redirect URIs this deployment sends. Registering them
 * verbatim on the Meta/Google app is what prevents the classic
 * redirect_uri_mismatch — connect and sign-in use DIFFERENT callback paths
 * on the same OAuth app, and each must be listed. */
function RedirectUrisCard() {
  const [uris, setUris] = useState<RedirectUri[] | null>(null);
  useEffect(() => {
    getRedirectUris()
      .then(setUris)
      .catch(() => setUris([])); // informational card — never blocks the page
  }, []);
  if (!uris || uris.length === 0) return null;
  return (
    <div className="card mg-integration mg-redirects">
      <div className="mg-integration-head">
        <div className="mg-integration-title">
          <h3 className="mg-redirects-title">OAuth redirect URIs</h3>
          <p className="mg-sub">
            Add each of these to your app's authorized redirect URIs —
            Google Cloud Console for Google, App Dashboard for Meta. A missing
            entry is what causes &ldquo;Error 400: redirect_uri_mismatch&rdquo;
            on sign-in or connect.
          </p>
        </div>
      </div>
      <ul className="mg-redirect-list">
        {uris.map((u) => (
          <li key={`${u.provider}-${u.purpose}`} className="mg-redirect-row">
            <span className="mg-redirect-label">
              {URI_PURPOSE[u.purpose]}{" "}
              {u.provider === "google" ? "Google" : "Meta"}
            </span>
            <code className="mg-redirect-uri">{u.uri}</code>
            <UriCopy text={u.uri} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function UriCopy({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size="sm"
      variant="ghost"
      onClick={() => {
        void navigator.clipboard.writeText(text);
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }}
    >
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

function StatusBadge({ status }: { status?: IntegrationStatus }) {
  if (!status) return null;
  const s = STATUS[status.source];
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

function ComingSoonCard({ platform }: { platform: Platform }) {
  return (
    <div className="card mg-integration">
      <div className="mg-integration-head">
        <div className="mg-integration-title">
          <PlatformChip name={platform.name} />
        </div>
        <Badge tone="info">Coming soon</Badge>
      </div>
      <p className="mg-sub">
        Bring-your-own-app credentials for {platform.name} unlock when its
        adapter ships.
      </p>
    </div>
  );
}

function ProviderShell({
  platform,
  provider,
  desc,
  status,
  children,
  onChange,
}: {
  platform: Platform;
  provider: "meta" | "google";
  desc: string;
  status?: IntegrationStatus;
  children: React.ReactNode;
  onChange: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [removing, setRemoving] = useState(false);
  const toast = useToast();

  const remove = async () => {
    setRemoving(true);
    try {
      await deleteIntegration(provider);
      toast(`${platform.name} disconnected`, "info");
      onChange();
      setConfirming(false);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setRemoving(false);
    }
  };

  return (
    <div className="card mg-integration">
      <div className="mg-integration-head">
        <div className="mg-integration-title">
          <PlatformChip name={platform.name} />
          <p className="mg-sub">{desc}</p>
        </div>
        <StatusBadge status={status} />
      </div>
      {children}
      {status?.source === "organization" && (
        <div className="mg-integration-foot">
          <span className="mg-appid">
            App ID: <code>{status.public_id}</code>
          </span>
          <Button
            variant="danger-outline"
            size="sm"
            onClick={() => setConfirming(true)}
          >
            Remove
          </Button>
        </div>
      )}
      <ConfirmDialog
        open={confirming}
        tone="danger"
        title={`Remove ${platform.name} integration`}
        confirmLabel="Remove integration"
        cancelLabel="Keep connected"
        busy={removing}
        rows={[
          {
            field: "API credentials",
            platform: platform.name,
            oldValue: "Configured",
            newValue: "Removed",
            delta: null,
          },
        ]}
        onCancel={() => {
          if (!removing) setConfirming(false);
        }}
        onConfirm={remove}
      >
        <p className="mg-sub">
          Client ad accounts linked through this app stop syncing until you
          reconnect. Nothing is deleted inside {platform.name}.
        </p>
      </ConfirmDialog>
    </div>
  );
}

function MetaCard({
  platform,
  status,
  onChange,
}: {
  platform: Platform;
  status?: IntegrationStatus;
  onChange: () => void;
}) {
  const [appId, setAppId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const ready = Boolean(appId.trim() && appSecret.trim());

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await setMetaCreds({ app_id: appId.trim(), app_secret: appSecret.trim() });
      setAppId("");
      setAppSecret("");
      toast("Meta credentials saved", "ok");
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <ProviderShell
      platform={platform}
      provider="meta"
      desc="From your Meta app: App ID and App Secret."
      status={status}
      onChange={onChange}
    >
      <form
        className="mg-form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) save();
        }}
      >
        <Field label="App ID">
          <input
            value={appId}
            onChange={(e) => setAppId(e.target.value)}
            placeholder="e.g. 1234567890"
          />
        </Field>
        <Field label="App Secret">
          <input
            type="password"
            value={appSecret}
            onChange={(e) => setAppSecret(e.target.value)}
            placeholder="••••••••"
          />
        </Field>
        {error && <Alert tone="danger">{error}</Alert>}
        <div className="mg-form-actions">
          <Button type="submit" variant="primary" busy={saving} disabled={saving || !ready}>
            {status?.source === "organization" ? "Update" : "Save"}
          </Button>
        </div>
      </form>
    </ProviderShell>
  );
}

function GoogleCard({
  platform,
  status,
  onChange,
}: {
  platform: Platform;
  status?: IntegrationStatus;
  onChange: () => void;
}) {
  const [clientId, setClientId] = useState("");
  const [clientSecret, setClientSecret] = useState("");
  const [devToken, setDevToken] = useState("");
  const [loginCustomerId, setLoginCustomerId] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const ready = Boolean(clientId.trim() && clientSecret.trim() && devToken.trim());

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
      toast("Google Ads credentials saved", "ok");
      onChange();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <ProviderShell
      platform={platform}
      provider="google"
      desc="OAuth Client ID + Secret and your Google Ads developer token."
      status={status}
      onChange={onChange}
    >
      <form
        className="mg-form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          if (ready) save();
        }}
      >
        <Field label="OAuth Client ID">
          <input value={clientId} onChange={(e) => setClientId(e.target.value)} />
        </Field>
        <Field label="OAuth Client Secret">
          <input
            type="password"
            value={clientSecret}
            onChange={(e) => setClientSecret(e.target.value)}
            placeholder="••••••••"
          />
        </Field>
        <Field label="Developer token">
          <input
            type="password"
            value={devToken}
            onChange={(e) => setDevToken(e.target.value)}
            placeholder="••••••••"
          />
        </Field>
        <Field label="Login customer ID" optional description="MCC accounts only">
          <input
            value={loginCustomerId}
            onChange={(e) => setLoginCustomerId(e.target.value)}
            placeholder="1234567890"
          />
        </Field>
        {error && <Alert tone="danger">{error}</Alert>}
        <div className="mg-form-actions">
          <Button type="submit" variant="primary" busy={saving} disabled={saving || !ready}>
            {status?.source === "organization" ? "Update" : "Save"}
          </Button>
        </div>
      </form>
    </ProviderShell>
  );
}
