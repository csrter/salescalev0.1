/**
 * Branding settings (Admin/Owner): white-label product name, logo, colors,
 * branded email sender, and the custom-domain claim/verify lifecycle. Saving
 * re-runs theme.ts refreshBranding() so the running app re-themes without a
 * reload — this page is the UI for the Stage-0 runtime theming.
 *
 * Security notes: the backend validates color keys/hex and restricts
 * logo/favicon URLs to http(s) (BrandingIn), and the client re-guards via
 * safeBrandUrl() before rendering any preview. Domain verification is
 * DNS-TXT-based server-side — nothing here can skip it.
 */

import { useEffect, useState, type CSSProperties } from "react";
import {
  clearCustomDomain,
  clearOrgBranding,
  getOrgBranding,
  setCustomDomain,
  setOrgBranding,
  verifyCustomDomain,
  type BrandingConfig,
  type OrgBranding,
} from "./api";
import { useToast } from "./components/Toast";
import { Alert, Badge, Button, Field, SkeletonText } from "./components/ui";
import { refreshBranding, safeBrandUrl } from "./theme";
import "./styles/views/settings.css";

/** Color keys the backend accepts (services/branding.py BRAND_COLOR_KEYS),
 * with the neutral defaults shown when a tenant hasn't overridden them.
 * These hexes are DATA (grep-gate allowlisted), also seeded into the live
 * preview so an un-staged palette still renders coherently. */
const COLOR_FIELDS: { key: string; label: string; fallback: string }[] = [
  { key: "primary", label: "Primary", fallback: "#2b62e0" },
  { key: "primary_strong", label: "Primary (hover)", fallback: "#2050c2" },
  { key: "primary_soft", label: "Primary (tint)", fallback: "#e2eafc" },
  { key: "header_start", label: "Sidebar top", fallback: "#10152e" },
  { key: "header_end", label: "Sidebar bottom", fallback: "#0b0f21" },
];

const fallbackFor = (key: string) =>
  COLOR_FIELDS.find((f) => f.key === key)?.fallback ?? "#2b62e0";

export function BrandingSettings() {
  const toast = useToast();
  const [config, setConfig] = useState<BrandingConfig | null>(null);
  const [form, setForm] = useState<OrgBranding | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    getOrgBranding()
      .then((c) => {
        setConfig(c);
        setForm(c.branding);
      })
      .catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  if (error && !config)
    return (
      <div className="branding-settings">
        <div className="set-page-head">
          <div>
            <h2>Branding</h2>
          </div>
        </div>
        <Alert tone="danger">{error}</Alert>
      </div>
    );
  if (!config || !form)
    return (
      <div className="branding-settings">
        <div className="set-page-head">
          <div>
            <h2>Branding</h2>
          </div>
        </div>
        <SkeletonText lines={5} />
      </div>
    );

  const set = (patch: Partial<OrgBranding>) => setForm({ ...form, ...patch });
  const setColor = (key: string, value: string | null) => {
    const colors = { ...form.colors };
    if (value === null) delete colors[key];
    else colors[key] = value;
    set({ colors });
  };

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const body: Partial<OrgBranding> = {
        logo_url: form.logo_url || null,
        favicon_url: form.favicon_url || null,
        colors: form.colors,
        email_from_name: form.email_from_name || null,
        email_from_address: form.email_from_address || null,
        apply_to_team: form.apply_to_team,
      };
      if (form.product_name.trim()) body.product_name = form.product_name.trim();
      await setOrgBranding(body);
      await refreshBranding();
      toast("Branding saved", "ok");
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    setBusy(true);
    setError(null);
    try {
      await clearOrgBranding();
      await refreshBranding();
      toast("Branding reset to defaults", "ok");
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const logoPreview = safeBrandUrl(form.logo_url);

  // Staged palette → inline vars on the preview container ONLY (never <html>).
  // Mirrors theme.ts BRAND_VAR_MAP: primary → --accent/--brand-blue,
  // header_* → --header-*. --accent-strong/-soft are only pinned when the
  // tenant explicitly staged them (matching save); otherwise settings.css
  // re-derives the whole accent family from --accent so the preview re-keys.
  const c = form.colors;
  const previewVars = {
    "--accent": c.primary ?? fallbackFor("primary"),
    "--brand-blue": c.primary ?? fallbackFor("primary"),
    "--header-start": c.header_start ?? fallbackFor("header_start"),
    "--header-end": c.header_end ?? fallbackFor("header_end"),
    ...(c.primary_strong ? { "--accent-strong": c.primary_strong } : {}),
    ...(c.primary_soft ? { "--accent-soft": c.primary_soft } : {}),
  } as CSSProperties;

  return (
    <div className="branding-settings">
      <div className="set-page-head">
        <div>
          <h2>Branding</h2>
          <p className="set-page-sub">
            What your clients see: your name, logo and colors anywhere they
            interact with the platform.
          </p>
        </div>
      </div>

      {!config.white_labeling_available && (
        <Alert tone="warn">
          White-labeling isn't included in your current plan.
        </Alert>
      )}

      <section className="set-section card">
        <h3>Identity</h3>
        <div className="set-form">
          <Field label="Product name">
            <input
              value={form.product_name}
              maxLength={100}
              onChange={(e) => set({ product_name: e.target.value })}
            />
          </Field>
          <Field label="Logo URL" optional>
            <input
              placeholder="https://…/logo.png"
              value={form.logo_url ?? ""}
              onChange={(e) => set({ logo_url: e.target.value || null })}
            />
          </Field>
          {logoPreview && (
            <img
              className="set-logo-preview"
              src={logoPreview}
              alt="Logo preview"
              height={30}
            />
          )}
          <Field label="Favicon URL" optional>
            <input
              placeholder="https://…/favicon.png"
              value={form.favicon_url ?? ""}
              onChange={(e) => set({ favicon_url: e.target.value || null })}
            />
          </Field>
          <label className="set-check">
            <input
              type="checkbox"
              checked={form.apply_to_team}
              onChange={(e) => set({ apply_to_team: e.target.checked })}
            />
            Also apply this branding to our own team's screens
          </label>
        </div>
      </section>

      <section className="set-section card">
        <h3>Colors</h3>
        <div className="set-colors">
          {COLOR_FIELDS.map(({ key, label, fallback }) => (
            <div key={key} className="set-color">
              <input
                id={`brand-color-${key}`}
                type="color"
                value={form.colors[key] ?? fallback}
                onChange={(e) => setColor(key, e.target.value)}
              />
              <label htmlFor={`brand-color-${key}`} className="set-color-name">
                {label}
              </label>
              {form.colors[key] ? (
                <Button variant="link" onClick={() => setColor(key, null)}>
                  reset
                </Button>
              ) : (
                <span className="set-color-flag">default</span>
              )}
            </div>
          ))}
        </div>
      </section>

      <div className="set-section">
        <h3>Live preview</h3>
        <p className="set-note">
          Exactly how these colors render across the app — updates as you edit,
          before you save. Watch that button and accent text stay legible.
        </p>
        <div
          className="card set-preview"
          style={previewVars}
          role="img"
          aria-label="Live preview of your branding applied to the app chrome"
        >
          <div className="set-preview-chrome" aria-hidden="true">
            <aside className="set-preview-side">
              <span className="set-preview-logo">
                {(form.product_name || "Salescale").trim()}
              </span>
              <span className="set-preview-nav set-preview-nav--active">
                Dashboard
              </span>
              <span className="set-preview-nav">Clients</span>
              <span className="set-preview-nav">Reports</span>
            </aside>
            <div className="set-preview-main">
              <div className="kpi">
                <div className="kpi-label">Blended spend</div>
                <div className="kpi-value">$48.2K</div>
                <span className="kpi-delta kpi-delta--good">+12.4% vs prev 30d</span>
              </div>
              <div className="set-preview-table">
                <div className="set-preview-tr">Paganelli HVAC</div>
                <div className="set-preview-tr set-preview-tr--sel">
                  Northside Plumbing
                </div>
              </div>
              <div className="set-preview-controls">
                <span className="btn btn--primary">Save changes</span>
                <span className="badge badge--accent">Qualified</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <section className="set-section card">
        <h3>Branded email</h3>
        <div className="set-form">
          <Field label="From name" optional>
            <input
              placeholder="Atlas Reach Reports"
              value={form.email_from_name ?? ""}
              onChange={(e) => set({ email_from_name: e.target.value || null })}
            />
          </Field>
          <Field label="From address" optional>
            <input
              type="email"
              placeholder="reports@youragency.com"
              value={form.email_from_address ?? ""}
              onChange={(e) =>
                set({ email_from_address: e.target.value || null })
              }
            />
          </Field>
          <p className="set-footnote">
            Client-facing email uses this sender once the domain is verified
            with the email provider; otherwise it falls back to the default.
          </p>
        </div>
      </section>

      {error && <Alert tone="danger">{error}</Alert>}
      <div className="set-actions">
        <Button variant="primary" busy={busy} disabled={busy} onClick={save}>
          Save branding
        </Button>
        <Button variant="ghost" disabled={busy} onClick={reset}>
          Reset to defaults
        </Button>
      </div>

      <CustomDomainPanel
        state={config.custom_domain}
        available={config.white_labeling_available}
        onChanged={load}
      />
    </div>
  );
}

function CustomDomainPanel({
  state,
  available,
  onChanged,
}: {
  state: BrandingConfig["custom_domain"];
  available: boolean;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [domain, setDomain] = useState(state.domain ?? "");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Re-sync the input if the server normalized/cleared the domain on reload.
  useEffect(() => {
    setDomain(state.domain ?? "");
  }, [state.domain]);

  const run = async (fn: () => Promise<string | null>) => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const n = await fn();
      if (n) setNote(n);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="set-section card">
      <h3>
        Custom domain{" "}
        {state.domain &&
          (state.verified ? (
            <Badge tone="ok">verified</Badge>
          ) : (
            <Badge tone="info">pending verification</Badge>
          ))}
      </h3>
      <p className="set-note">
        Serve the platform from your own domain (e.g.{" "}
        <code>ads.youragency.com</code>) so clients log in under your brand.
      </p>
      <div className="set-domain">
        <Field label="Domain" optional>
          <input
            placeholder="ads.youragency.com"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
          />
        </Field>
        <Button
          disabled={busy || !domain || !available}
          onClick={() =>
            run(async () => {
              await setCustomDomain(domain);
              return "Domain claimed — add the DNS records below, then verify.";
            })
          }
        >
          {state.domain ? "Change domain" : "Claim domain"}
        </Button>
        {state.domain && (
          <>
            <Button
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const r = await verifyCustomDomain();
                  if (r.verified) {
                    toast("Domain verified", "ok");
                    return null;
                  }
                  return r.detail ?? "Not verified yet.";
                })
              }
            >
              Verify
            </Button>
            <Button
              variant="link"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  await clearCustomDomain();
                  setDomain("");
                  return "Custom domain removed.";
                })
              }
            >
              Remove
            </Button>
          </>
        )}
      </div>
      {state.domain && !state.verified && state.verification_token && (
        <p className="set-footnote">
          Create a DNS TXT record at <code>{state.txt_record_name}</code>{" "}
          containing <code>{state.verification_token}</code>, point the domain
          at your deployment, then click Verify.
        </p>
      )}
      {note && (
        <Alert tone="info" className="set-mt">
          {note}
        </Alert>
      )}
      {error && (
        <Alert tone="danger" className="set-mt">
          {error}
        </Alert>
      )}
    </section>
  );
}
