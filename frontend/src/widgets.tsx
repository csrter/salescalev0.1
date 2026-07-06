/**
 * Phase 4 widget components. Each widget receives the client, the session,
 * the active platform filter, and a refresh counter (bumped by the
 * dashboard's "Sync insights" button) — and is responsible for respecting
 * the filter: blended totals refetch with ?platforms=, per-platform tables
 * filter client-side, and platform-specific widgets (fatigue, quality
 * score) say so when the filter excludes their platform.
 */

import { useEffect, useState } from "react";
import {
  ADMIN_ROLES,
  api,
  TEAM_ROLES,
  type Campaign,
  type Session,
} from "./api";
import { useManage } from "./manage";

export type PlatformFilter = "all" | "meta" | "google";

export interface WidgetProps {
  clientId: string;
  session: Session;
  platforms: PlatformFilter;
  refresh: number;
}

const $ = (micros?: number | null) =>
  micros == null ? "—" : `$${(micros / 1_000_000).toFixed(2)}`;
const money = (v?: number | null) => (v == null ? "—" : `$${v.toFixed(2)}`);
const pct = (v?: number | null) =>
  v == null ? "—" : `${(v * 100).toFixed(0)}%`;

const filterParam = (platforms: PlatformFilter) =>
  platforms === "all" ? "" : `&platforms=${platforms}`;

const keepPlatform = (platforms: PlatformFilter, p: string) =>
  platforms === "all" || platforms === p;

function useWidgetData<T>(path: string | null, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!path) return;
    let stale = false;
    setLoading(true);
    setError(null);
    api<T>(path)
      .then((d) => !stale && setData(d))
      .catch((e) => !stale && setError((e as Error).message))
      .finally(() => !stale && setLoading(false));
    return () => {
      stale = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading };
}

function WidgetBody({
  error,
  loading,
  empty,
  children,
}: {
  error: string | null;
  loading: boolean;
  empty?: string | null;
  children: React.ReactNode;
}) {
  if (error) return <p className="error">{error}</p>;
  if (loading) return <p className="muted">Loading…</p>;
  if (empty) return <p className="muted">{empty}</p>;
  return <>{children}</>;
}

/** Note shown when the platform filter excludes a single-platform widget. */
function FilteredOut({ widget }: { widget: string }) {
  return (
    <p className="muted filtered-note">
      {widget} is a single-platform widget — hidden by the current platform
      filter.
    </p>
  );
}

// --- Blended overview ---

export function OverviewWidget({ clientId, platforms, refresh }: WidgetProps) {
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/blended?client_id=${clientId}${filterParam(platforms)}`,
    [clientId, platforms, refresh]
  );
  const lqa = useWidgetData<any>(
    `/api/metrics/lead-quality-adjusted-cpl?client_id=${clientId}${filterParam(platforms)}`,
    [clientId, platforms, refresh]
  );
  return (
    <WidgetBody error={error} loading={loading} empty={null}>
      {data && (
        <div className="metric-cards">
          <div className="metric-card">
            <span className="metric-label">Spend (30d)</span>
            <strong>{$(data.total_spend_micros)}</strong>
            <span className="muted">{data.total_tracked_leads} tracked leads</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">Blended CPL</span>
            <strong>{money(data.blended_cpl)}</strong>
            <span className="muted">
              {data.unattributed_leads > 0
                ? `incl. ${data.unattributed_leads} unattributed`
                : "all leads attributed"}
            </span>
          </div>
          <div className="metric-card">
            <span className="metric-label">Blended CAC</span>
            <strong>{money(data.blended_cac)}</strong>
            <span className="muted">{data.won_deals_from_paid} won from paid</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">Blended ROAS</span>
            <strong>{data.blended_roas ?? "—"}</strong>
            <span className="muted">won-deal revenue / spend</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">LQA-CPL</span>
            <strong>
              {money(lqa.data?.blended_lead_quality_adjusted_cpl)}
            </strong>
            <span className="muted">
              {lqa.data?.total_qualified_leads ?? "—"} qualified
            </span>
          </div>
        </div>
      )}
    </WidgetBody>
  );
}

// --- Channel mix table ---

export function ChannelMixWidget({ clientId, platforms, refresh }: WidgetProps) {
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/blended?client_id=${clientId}${filterParam(platforms)}`,
    [clientId, platforms, refresh]
  );
  const lqa = useWidgetData<any>(
    `/api/metrics/lead-quality-adjusted-cpl?client_id=${clientId}${filterParam(platforms)}`,
    [clientId, platforms, refresh]
  );
  const rows = Object.entries(data?.per_platform ?? {});
  return (
    <WidgetBody
      error={error}
      loading={loading}
      empty={data && rows.length === 0 ? "No spend recorded yet — sync insights." : null}
    >
      <table className="compact">
        <thead>
          <tr>
            <th>Platform</th>
            <th>Spend</th>
            <th>Share</th>
            <th>Leads</th>
            <th>Tracked CPL</th>
            <th>Platform CPL*</th>
            <th>LQA-CPL</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([platform, v]: [string, any]) => (
            <tr key={platform}>
              <td>
                <span className={`platform ${platform}`}>{platform}</span>
              </td>
              <td>{$(v.spend_micros)}</td>
              <td>{pct(v.spend_share)}</td>
              <td>{v.tracked_leads}</td>
              <td>{money(v.tracked_cpl)}</td>
              <td>{money(v.platform_cpl)}</td>
              <td>
                {money(
                  lqa.data?.per_platform?.[platform]?.lead_quality_adjusted_cpl
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted footnote">
        *Platform CPL uses the platform's own conversion claim; Tracked CPL
        uses UTM/click-id-attributed Salescale leads.
      </p>
    </WidgetBody>
  );
}

// --- Spend / pacing chart ---

const PLATFORM_COLORS: Record<string, string> = {
  meta: "var(--cobalt)",
  google: "var(--amber)",
};

export function SpendPacingWidget({ clientId, platforms, refresh }: WidgetProps) {
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/spend-daily?client_id=${clientId}${filterParam(platforms)}`,
    [clientId, platforms, refresh]
  );
  const series = Object.entries(data?.per_platform ?? {}) as [string, any][];
  const days: string[] = data?.days ?? [];
  const max = Math.max(
    1,
    ...series.flatMap(([, s]) => s.daily_spend_micros as number[])
  );
  const W = 600;
  const H = 180;
  const PAD = 6;
  const x = (i: number) =>
    days.length > 1 ? PAD + (i * (W - 2 * PAD)) / (days.length - 1) : W / 2;
  const y = (v: number) => H - PAD - (v / max) * (H - 2 * PAD);

  return (
    <WidgetBody
      error={error}
      loading={loading}
      empty={data && series.length === 0 ? "No spend recorded yet — sync insights." : null}
    >
      <div className="chart-legend">
        {series.map(([platform, s]) => {
          const daily = s.daily_spend_micros as number[];
          const last7 = daily.slice(-7);
          const avg = last7.reduce((a, b) => a + b, 0) / Math.max(last7.length, 1);
          return (
            <span key={platform} className="legend-item">
              <i style={{ background: PLATFORM_COLORS[platform] ?? "#888" }} />
              {platform} · {$(s.total_spend_micros)} total · {$(Math.round(avg))}
              /day (7d)
            </span>
          );
        })}
      </div>
      <svg
        className="pacing-chart"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label="Daily spend by platform"
      >
        {[0.25, 0.5, 0.75].map((f) => (
          <line
            key={f}
            x1={PAD}
            x2={W - PAD}
            y1={y(max * f)}
            y2={y(max * f)}
            className="gridline"
          />
        ))}
        {series.map(([platform, s]) => {
          const daily = s.daily_spend_micros as number[];
          const pts = daily.map((v, i) => `${x(i)},${y(v)}`).join(" ");
          return (
            <g key={platform}>
              <polygon
                points={`${x(0)},${y(0)} ${pts} ${x(daily.length - 1)},${y(0)}`}
                fill={PLATFORM_COLORS[platform] ?? "#888"}
                opacity="0.12"
              />
              <polyline
                points={pts}
                fill="none"
                stroke={PLATFORM_COLORS[platform] ?? "#888"}
                strokeWidth="2"
                vectorEffect="non-scaling-stroke"
              />
            </g>
          );
        })}
      </svg>
      <div className="chart-axis">
        <span>{days[0]}</span>
        <span className="muted">daily spend, max {$(max)}</span>
        <span>{days[days.length - 1]}</span>
      </div>
    </WidgetBody>
  );
}

// --- Funnel tiers ---

export function FunnelTiersWidget({ clientId, platforms, refresh }: WidgetProps) {
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/funnel-tiers?client_id=${clientId}`,
    [clientId, refresh]
  );
  const rows = Object.entries(data ?? {})
    .filter(([platform]) => keepPlatform(platforms, platform))
    .flatMap(([platform, tiers]: [string, any]) =>
      Object.entries(tiers).map(([tier, v]) => ({ platform, tier, ...(v as any) }))
    );
  return (
    <WidgetBody
      error={error}
      loading={loading}
      empty={data && rows.length === 0 ? "No tiered spend in range." : null}
    >
      <table className="compact">
        <thead>
          <tr>
            <th>Platform</th>
            <th>Tier</th>
            <th>Spend</th>
            <th>Conv.</th>
            <th>CPL</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.platform}-${r.tier}`}>
              <td>
                <span className={`platform ${r.platform}`}>{r.platform}</span>
              </td>
              <td>{r.tier.replace("_", " ")}</td>
              <td>{$(r.spend_micros)}</td>
              <td>{r.conversions}</td>
              <td>{money(r.cpl)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </WidgetBody>
  );
}

// --- Creative fatigue (Meta) ---

export function FatigueWidget({ clientId, platforms, refresh }: WidgetProps) {
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/creative-fatigue?client_id=${clientId}`,
    [clientId, refresh]
  );
  if (!keepPlatform(platforms, "meta"))
    return <FilteredOut widget="Creative fatigue (Meta)" />;
  const flagged = data?.flagged ?? [];
  return (
    <WidgetBody
      error={error}
      loading={loading}
      empty={
        data && flagged.length === 0
          ? "No fatigued creatives — every ad's recent CTR is within 30% of its own baseline."
          : null
      }
    >
      <ul className="alert-list">
        {flagged.map((a: any) => (
          <li key={a.ad_external_id}>
            <span className="badge warn">fatigue {a.fatigue_score}</span>
            <strong>{a.ad_name}</strong>
            <span className="muted">
              CTR {(a.recent_ctr * 100).toFixed(2)}% vs baseline{" "}
              {(a.baseline_ctr * 100).toFixed(2)}%
            </span>
          </li>
        ))}
      </ul>
    </WidgetBody>
  );
}

// --- Quality Score / ad strength (Google) ---

export function QualityWidget({ clientId, platforms, refresh }: WidgetProps) {
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/quality-trends?client_id=${clientId}`,
    [clientId, refresh]
  );
  if (!keepPlatform(platforms, "google"))
    return <FilteredOut widget="Quality Score alerts (Google)" />;
  const flagged = data?.flagged ?? [];
  return (
    <WidgetBody
      error={error}
      loading={loading}
      empty={
        data && flagged.length === 0
          ? "No Quality Score or ad-strength drops in the last 30 days."
          : null
      }
    >
      <ul className="alert-list">
        {flagged.map((e: any) => (
          <li key={`${e.metric}-${e.entity_external_id}`}>
            <span className="badge warn">
              {e.metric === "quality_score" ? "QS" : "ad strength"} {e.delta}
            </span>
            <strong>{e.entity_name}</strong>
            <span className="muted">
              {e.first} → {e.latest}
              {e.latest_label ? ` (${e.latest_label})` : ""} since {e.first_date}
            </span>
          </li>
        ))}
      </ul>
    </WidgetBody>
  );
}

// --- Guarantee / goal tracker ---

export function GuaranteeWidget({
  clientId,
  session,
  platforms,
  refresh,
}: WidgetProps) {
  const [bump, setBump] = useState(0);
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/guarantee?client_id=${clientId}${filterParam(platforms)}`,
    [clientId, platforms, refresh, bump]
  );
  const isAdmin = ADMIN_ROLES.includes(session.role);
  const [editing, setEditing] = useState(false);

  if (data && !data.configured && !isAdmin)
    return (
      <p className="muted">No performance guarantee configured for this client.</p>
    );

  return (
    <WidgetBody error={error} loading={loading} empty={null}>
      {data?.configured && !editing && (
        <div className="guarantee">
          <div className="guarantee-head">
            <strong>{data.name}</strong>
            <span className={`badge ${data.met ? "ok" : data.on_pace ? "ok" : "warn"}`}>
              {data.met ? "met" : data.on_pace ? "on pace" : "behind pace"}
            </span>
            {isAdmin && (
              <button className="link" onClick={() => setEditing(true)}>
                Edit
              </button>
            )}
          </div>
          <div className="progress-track">
            <div
              className={`progress-fill ${data.met || data.on_pace ? "" : "behind"}`}
              style={{ width: `${Math.min(data.pct_of_target * 100, 100)}%` }}
            />
            <div
              className="progress-expected"
              style={{
                left: `${Math.min((data.expected_by_now / data.target) * 100, 100)}%`,
              }}
              title={`straight-line pace: ${data.expected_by_now} by today`}
            />
          </div>
          <div className="guarantee-numbers">
            <strong>
              {data.progress} / {data.target}
            </strong>{" "}
            {data.metric.replace(/_/g, " ")}
            <span className="muted">
              {" · "}
              {data.window.days_remaining} day
              {data.window.days_remaining === 1 ? "" : "s"} left of{" "}
              {data.window.days_total}
            </span>
          </div>
          <div className="guarantee-platforms">
            {Object.entries(data.per_platform).map(([p, n]) => (
              <span key={p} className="legend-item">
                <i
                  style={{ background: PLATFORM_COLORS[p] ?? "var(--ink-faint)" }}
                />
                {p}: {n as number}
              </span>
            ))}
            {Object.keys(data.per_platform).length === 0 && (
              <span className="muted">no contributions yet</span>
            )}
          </div>
        </div>
      )}
      {(editing || (data && !data.configured)) && isAdmin && (
        <GuaranteeConfigForm
          clientId={clientId}
          existing={data?.configured ? data : null}
          onSaved={() => {
            setEditing(false);
            setBump((b) => b + 1);
          }}
          onCancel={data?.configured ? () => setEditing(false) : undefined}
        />
      )}
    </WidgetBody>
  );
}

function GuaranteeConfigForm({
  clientId,
  existing,
  onSaved,
  onCancel,
}: {
  clientId: string;
  existing: any | null;
  onSaved: () => void;
  onCancel?: () => void;
}) {
  const [name, setName] = useState(existing?.name ?? "");
  const [metric, setMetric] = useState(existing?.metric ?? "qualified_leads");
  const [target, setTarget] = useState(existing ? String(existing.target) : "");
  const [windowDays, setWindowDays] = useState(
    existing ? String(existing.window.days_total) : "30"
  );
  const [startDate, setStartDate] = useState(existing?.window?.start ?? "");
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="inline-form column">
      <span className="muted">
        {existing ? "Edit guarantee terms" : "Set up a performance guarantee"}{" "}
        (Organization-configured — e.g. a trial-sprint lead promise)
      </span>
      <input
        placeholder="Guarantee name (e.g. 14-Day Trial Sprint)"
        value={name}
        onChange={(e) => setName(e.target.value)}
      />
      <div className="inline-form">
        <select value={metric} onChange={(e) => setMetric(e.target.value)}>
          <option value="qualified_leads">qualified leads</option>
          <option value="tracked_leads">tracked leads</option>
          <option value="won_deals">won deals</option>
        </select>
        <input
          placeholder="Target #"
          type="number"
          min="1"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
        />
        <input
          placeholder="Window (days)"
          type="number"
          min="1"
          value={windowDays}
          onChange={(e) => setWindowDays(e.target.value)}
        />
        <input
          type="date"
          title="Start date (blank = rolling window)"
          value={startDate}
          onChange={(e) => setStartDate(e.target.value)}
        />
      </div>
      <div className="inline-form">
        <button
          disabled={!name || !target || !windowDays}
          onClick={() =>
            api(`/api/clients/${clientId}/guarantee`, {
              method: "PUT",
              body: JSON.stringify({
                name,
                metric,
                target: Number(target),
                window_days: Number(windowDays),
                start_date: startDate || null,
              }),
            })
              .then(onSaved)
              .catch((e) => setError((e as Error).message))
          }
        >
          Save guarantee
        </button>
        {onCancel && (
          <button className="link" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

// --- Attribution discrepancy alerts ---

export function ReconciliationWidget({
  clientId,
  platforms,
  refresh,
}: WidgetProps) {
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/reconciliation?client_id=${clientId}`,
    [clientId, refresh]
  );
  const rows = Object.entries(data?.per_platform ?? {}).filter(([p]) =>
    keepPlatform(platforms, p)
  );
  const flags = (data?.flags ?? []).filter(
    (f: any) => f.platform === null || keepPlatform(platforms, f.platform)
  );
  return (
    <WidgetBody
      error={error}
      loading={loading}
      empty={data && rows.length === 0 ? "No platform-reported conversions in range." : null}
    >
      <table className="compact">
        <thead>
          <tr>
            <th>Platform</th>
            <th>Reported</th>
            <th>UTM-confirmed</th>
            <th>Δ</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([platform, v]: [string, any]) => (
            <tr key={platform} className={v.flagged ? "negative" : ""}>
              <td>
                <span className={`platform ${platform}`}>{platform}</span>
              </td>
              <td>{v.platform_reported}</td>
              <td>{v.utm_confirmed}</td>
              <td>
                {v.discrepancy > 0 ? "+" : ""}
                {v.discrepancy} {v.flagged ? "⚑" : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {flags.map((f: any, i: number) => (
        <p key={i} className="warning">
          ⚑ {f.detail}
        </p>
      ))}
    </WidgetBody>
  );
}

// --- Raw campaign table (power-user editing) ---

export function CampaignTableWidget({
  clientId,
  session,
  platforms,
  refresh,
}: WidgetProps) {
  const { stage } = useManage();
  const [bump, setBump] = useState(0);
  const { data, error, loading } = useWidgetData<Campaign[]>(
    `/api/campaigns?client_id=${clientId}`,
    [clientId, refresh, bump]
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const canManage = TEAM_ROLES.includes(session.role);
  const rows = (data ?? []).filter((c) => keepPlatform(platforms, c.platform));

  const stageAction = (c: any, action: string) =>
    stage(
      {
        ad_account_id: c.ad_account_id,
        entity_type: "campaign",
        action,
        entity_id: c.id,
      },
      () => setBump((b) => b + 1)
    ).catch((e) => setActionError((e as Error).message));

  return (
    <WidgetBody
      error={error}
      loading={loading}
      empty={
        data && rows.length === 0
          ? "No cached campaigns — open the account tree below to pull live."
          : null
      }
    >
      {actionError && <p className="error">{actionError}</p>}
      <table className="compact">
        <thead>
          <tr>
            <th>Campaign</th>
            <th>Platform</th>
            <th>Status</th>
            <th>Daily budget</th>
            <th>Objective</th>
            {canManage && <th />}
          </tr>
        </thead>
        <tbody>
          {rows.map((c: any) => {
            const paused = c.status?.toUpperCase() === "PAUSED";
            return (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td>
                  <span className={`platform ${c.platform}`}>{c.platform}</span>
                </td>
                <td>
                  <span className={`badge ${c.status?.toLowerCase()}`}>
                    {c.status ?? "—"}
                  </span>
                </td>
                <td>{$(c.daily_budget_micros)}</td>
                <td className="muted">{c.objective ?? "—"}</td>
                {canManage && (
                  <td>
                    <button
                      className="link"
                      onClick={() => stageAction(c, paused ? "resume" : "pause")}
                    >
                      {paused ? "Resume" : "Pause"}
                    </button>
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
      {canManage && (
        <p className="muted footnote">
          Pause/resume stages a change for confirmation — nothing touches the
          live account until you confirm it.
        </p>
      )}
    </WidgetBody>
  );
}

// --- Vertical benchmark (team-only) ---

export function BenchmarkWidget({ clientId, refresh }: WidgetProps) {
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/benchmark?client_id=${clientId}`,
    [clientId, refresh]
  );
  return (
    <WidgetBody
      error={error}
      loading={loading}
      empty={data && !data.vertical ? "Client has no vertical set." : null}
    >
      {data?.vertical && (
        <div className="metric-cards">
          <div className="metric-card">
            <span className="metric-label">
              vs. {data.vertical} book ({data.peers} clients)
            </span>
            <strong>
              {data.vs_median_pct == null
                ? "—"
                : `${data.vs_median_pct > 0 ? "+" : ""}${data.vs_median_pct}% CPL`}
            </strong>
            <span className="muted">
              median {money(data.vertical_median_blended_cpl)} · this client{" "}
              {money(data.client_blended_cpl)}
            </span>
          </div>
        </div>
      )}
    </WidgetBody>
  );
}

// --- UTM builder (team tool) ---

export function UtmBuilderWidget({ clientId }: WidgetProps) {
  const [platform, setPlatform] = useState("meta");
  const [campaign, setCampaign] = useState("");
  const [content, setContent] = useState("");
  const [landing, setLanding] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [violations, setViolations] = useState<any | null>(null);
  const [error, setError] = useState<string | null>(null);

  return (
    <div>
      <div className="inline-form">
        <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
          <option value="meta">Meta</option>
          <option value="google">Google</option>
        </select>
        <input
          placeholder="Campaign name"
          value={campaign}
          onChange={(e) => setCampaign(e.target.value)}
        />
        <input
          placeholder="Ad / content (optional)"
          value={content}
          onChange={(e) => setContent(e.target.value)}
        />
      </div>
      <div className="inline-form">
        <input
          placeholder="Landing page URL (optional)"
          value={landing}
          onChange={(e) => setLanding(e.target.value)}
        />
        <button
          disabled={!campaign}
          onClick={() =>
            api<{ query_string: string }>(
              `/api/utm/build?client_id=${clientId}&platform=${platform}` +
                `&campaign_name=${encodeURIComponent(campaign)}` +
                (content ? `&content=${encodeURIComponent(content)}` : "")
            )
              .then((r) => {
                setError(null);
                setResult(
                  landing
                    ? `${landing}${landing.includes("?") ? "&" : "?"}${r.query_string}`
                    : `?${r.query_string}`
                );
              })
              .catch((e) => setError((e as Error).message))
          }
        >
          Build URL
        </button>
        <button
          className="link"
          onClick={() =>
            api<any>(`/api/utm/violations?client_id=${clientId}`)
              .then(setViolations)
              .catch((e) => setError((e as Error).message))
          }
        >
          Check convention violations
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {result && (
        <code
          className="utm-result"
          title="Click to copy"
          onClick={() => navigator.clipboard?.writeText(result)}
        >
          {result}
        </code>
      )}
      {violations && (
        <p className={violations.violations.length ? "warning" : "muted"}>
          {violations.violations.length} violation(s) in {violations.checked}{" "}
          recent landing events
          {violations.violations
            .slice(0, 5)
            .map((v: any) => ` · ${v.problems[0]}`)}
        </p>
      )}
    </div>
  );
}
