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

import { useEffect, useState } from "react";
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
import { Badge, Field, SkeletonText } from "./components/ui";
import { refreshBranding, safeBrandUrl } from "./theme";

/** Color keys the backend accepts (services/branding.py BRAND_COLOR_KEYS),
 * with the neutral defaults shown when a tenant hasn't overridden them. */
const COLOR_FIELDS: { key: string; label: string; fallback: string }[] = [
  { key: "primary", label: "Primary", fallback: "#4f46e5" },
  { key: "primary_strong", label: "Primary (hover)", fallback: "#4338ca" },
  { key: "primary_soft", label: "Primary (tint)", fallback: "#e7e8fb" },
  { key: "header_start", label: "Sidebar top", fallback: "#10152e" },
  { key: "header_end", label: "Sidebar bottom", fallback: "#0b0f21" },
];

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

  if (error) return <p className="error">{error}</p>;
  if (!config || !form)
    return (
      <div>
        <div className="page-head">
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

  return (
    <div className="branding-settings">
      <div className="page-head">
        <div>
          <h2>Branding</h2>
          <p className="page-sub">
            What your clients see: your name, logo and colors anywhere they
            interact with the platform.
          </p>
        </div>
      </div>

      {!config.white_labeling_available && (
        <p className="notice">
          White-labeling isn't included in your current plan.
        </p>
      )}

      <section>
        <h3>Identity</h3>
        <div className="form-grid branding-form">
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
              className="brand-logo branding-preview"
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
          <label className="crm-check">
            <input
              type="checkbox"
              checked={form.apply_to_team}
              onChange={(e) => set({ apply_to_team: e.target.checked })}
            />
            Also apply this branding to our own team's screens
          </label>
        </div>
      </section>

      <section>
        <h3>Colors</h3>
        <div className="branding-colors">
          {COLOR_FIELDS.map(({ key, label, fallback }) => (
            <div key={key} className="branding-color">
              <input
                type="color"
                value={form.colors[key] ?? fallback}
                onChange={(e) => setColor(key, e.target.value)}
                title={label}
              />
              <span>{label}</span>
              {form.colors[key] ? (
                <button className="link" onClick={() => setColor(key, null)}>
                  reset
                </button>
              ) : (
                <span className="muted">default</span>
              )}
            </div>
          ))}
        </div>
      </section>

      <section>
        <h3>Branded email</h3>
        <div className="form-grid branding-form">
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
          <p className="muted footnote">
            Client-facing email uses this sender once the domain is verified
            with the email provider; otherwise it falls back to the default.
          </p>
        </div>
      </section>

      {error && <p className="error">{error}</p>}
      <div className="inline-form">
        <button className="primary" disabled={busy} onClick={save}>
          {busy ? "Saving…" : "Save branding"}
        </button>
        <button className="ghost" disabled={busy} onClick={reset}>
          Reset to defaults
        </button>
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
    <section>
      <h3>
        Custom domain{" "}
        {state.domain &&
          (state.verified ? (
            <Badge tone="ok">verified</Badge>
          ) : (
            <Badge tone="pending">pending verification</Badge>
          ))}
      </h3>
      <p className="muted">
        Serve the platform from your own domain (e.g.{" "}
        <code>ads.youragency.com</code>) so clients log in under your brand.
      </p>
      <div className="inline-form">
        <input
          placeholder="ads.youragency.com"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
        />
        <button
          disabled={busy || !domain || !available}
          onClick={() =>
            run(async () => {
              await setCustomDomain(domain);
              return "Domain claimed — add the DNS records below, then verify.";
            })
          }
        >
          {state.domain ? "Change domain" : "Claim domain"}
        </button>
        {state.domain && (
          <>
            <button
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
            </button>
            <button
              className="link danger"
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
            </button>
          </>
        )}
      </div>
      {state.domain && !state.verified && state.verification_token && (
        <p className="muted footnote">
          Create a DNS TXT record at <code>{state.txt_record_name}</code>{" "}
          containing <code>{state.verification_token}</code>, point the domain
          at your deployment, then click Verify.
        </p>
      )}
      {note && <p className="muted">{note}</p>}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
