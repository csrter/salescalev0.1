/**
 * Phase 4 widget components (UI-revamp). Each widget receives the client, the
 * session, the active platform filter, and a refresh counter (bumped by the
 * dashboard's "Sync insights" button) — and is responsible for respecting the
 * filter: blended totals refetch with ?platforms=, per-platform tables filter
 * client-side, and platform-specific widgets (fatigue, quality score) say so
 * when the filter excludes their platform.
 *
 * Everything renders on the shared primitives (§4): KPIs on .kpi tiles, tables
 * on DataTable, the spend chart on charts.tsx LineChart, forms on Field, status
 * on Badge tones, platform identity on the neutral PlatformChip. No per-platform
 * brand/chart colors and no literal colors — platform identity is the label.
 */

import { useEffect, useState } from "react";
import {
  ADMIN_ROLES,
  api,
  getPlatforms,
  TEAM_ROLES,
  type Campaign,
  type Platform,
  type Session,
} from "./api";
import { useManage } from "./manage";
import {
  Alert,
  Badge,
  Button,
  Field,
  Kpi,
  KpiGrid,
  PlatformChip,
  Segmented,
  SkeletonText,
  toneForStatus,
} from "./components/ui";
import { DataTable, type Column } from "./components/DataTable";
import { LineChart, type ChartSeries } from "./components/charts";

// "all" (blended) or a specific platform id from the registry
// (GET /api/platforms). Not a fixed union so new platforms need no edit here.
export type PlatformFilter = string;

export interface WidgetProps {
  clientId: string;
  session: Session;
  platforms: PlatformFilter;
  refresh: number;
  /** Dashboard timeframe (YYYY-MM-DD, inclusive), owned by the page like
   * `platforms`. Every widget whose backend endpoint accepts since/until
   * honors it; point-in-time widgets (guarantee, fatigue, quality) ignore it. */
  since: string;
  until: string;
  /** Human label for the active timeframe (e.g. "Today", "Last 7 days",
   * "Jul 1 – Jul 15") — for widgets that show the range in a heading. */
  rangeLabel: string;
  /** Dashboard spend-selection filter: specific ad accounts/campaigns
   * (external ids). Empty = every connected account. Only the
   * spend/blended-metrics widgets (Overview, Channel mix, Spend & pacing)
   * honor this — the backend filter exists on /metrics/blended and
   * /metrics/spend-daily only. */
  accountIds: string[];
  campaignIds: string[];
}

// --- formatting -----------------------------------------------------------

const usd = (v: number) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(
    v,
  );
const usdCompact = (v: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(v);

/** Spend/CPL values arrive in micros. */
const $ = (micros?: number | null) =>
  micros == null ? "—" : usd(micros / 1_000_000);
const $compact = (micros?: number | null) =>
  micros == null ? "—" : usdCompact(micros / 1_000_000);
const money = (v?: number | null) => (v == null ? "—" : usd(v));
const pct = (v?: number | null) =>
  v == null ? "—" : `${(v * 100).toFixed(0)}%`;

const filterParam = (platforms: PlatformFilter) =>
  platforms === "all" ? "" : `&platforms=${platforms}`;

const keepPlatform = (platforms: PlatformFilter, p: string) =>
  platforms === "all" || platforms === p;

const rangeParam = (since: string, until: string) =>
  `&since=${since}&until=${until}`;

const entityParam = (accountIds: string[], campaignIds: string[]) =>
  (accountIds.length ? `&account_ids=${accountIds.join(",")}` : "") +
  (campaignIds.length ? `&campaign_ids=${campaignIds.join(",")}` : "");

const clicksFmt = (v?: number | null) => (v == null ? "—" : v.toLocaleString());
const ctrFmt = (v?: number | null) => (v == null ? "—" : `${(v * 100).toFixed(2)}%`);

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
  if (error) return <Alert tone="danger">{error}</Alert>;
  if (loading) return <SkeletonText lines={3} />;
  if (empty) return <p className="dash-muted">{empty}</p>;
  return <>{children}</>;
}

/** Note shown when the platform filter excludes a single-platform widget. */
function FilteredOut({ widget }: { widget: string }) {
  return (
    <p className="dash-muted">
      {widget} is a single-platform widget — hidden by the current platform
      filter.
    </p>
  );
}

// --- Blended overview -----------------------------------------------------

export function OverviewWidget({
  clientId,
  platforms,
  refresh,
  since,
  until,
  rangeLabel,
  accountIds,
  campaignIds,
}: WidgetProps) {
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/blended?client_id=${clientId}${filterParam(platforms)}${rangeParam(since, until)}${entityParam(accountIds, campaignIds)}`,
    [clientId, platforms, refresh, since, until, accountIds, campaignIds],
  );
  const lqa = useWidgetData<any>(
    `/api/metrics/lead-quality-adjusted-cpl?client_id=${clientId}${filterParam(platforms)}${rangeParam(since, until)}`,
    [clientId, platforms, refresh, since, until],
  );
  return (
    <WidgetBody error={error} loading={loading} empty={null}>
      {data && (
        <KpiGrid>
          {/* The one hero tile per view: blended spend. Spend up is not "good"
              on its own, so upIsGood={false} (deltas are informational-only —
              the metrics endpoints expose no prior period yet). */}
          <Kpi
            hero
            label={`Spend (${rangeLabel})`}
            value={$compact(data.total_spend_micros)}
            upIsGood={false}
          />
          <Kpi label="Blended CPL" value={money(data.blended_cpl)} upIsGood={false} />
          <Kpi label="Blended CAC" value={money(data.blended_cac)} upIsGood={false} />
          <Kpi label="Blended ROAS" value={data.blended_roas ?? "—"} />
          <Kpi
            label="LQA-CPL"
            value={money(lqa.data?.blended_lead_quality_adjusted_cpl)}
            upIsGood={false}
          />
          <Kpi label="Impressions" value={clicksFmt(data.total_impressions)} />
          <Kpi label="Clicks" value={clicksFmt(data.total_clicks)} />
          <Kpi label="CTR" value={ctrFmt(data.blended_ctr)} />
          <Kpi label="CPC" value={money(data.blended_cpc)} upIsGood={false} />
        </KpiGrid>
      )}
    </WidgetBody>
  );
}

// --- Channel mix table ----------------------------------------------------

interface ChannelRow {
  platform: string;
  spend_micros: number;
  spend_share: number;
  tracked_leads: number;
  tracked_cpl: number | null;
  platform_cpl: number | null;
  lqa_cpl: number | null;
  clicks: number;
  impressions: number;
  ctr: number | null;
  cpc: number | null;
}

export function ChannelMixWidget({
  clientId,
  platforms,
  refresh,
  since,
  until,
  accountIds,
  campaignIds,
}: WidgetProps) {
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/blended?client_id=${clientId}${filterParam(platforms)}${rangeParam(since, until)}${entityParam(accountIds, campaignIds)}`,
    [clientId, platforms, refresh, since, until, accountIds, campaignIds],
  );
  const lqa = useWidgetData<any>(
    `/api/metrics/lead-quality-adjusted-cpl?client_id=${clientId}${filterParam(platforms)}${rangeParam(since, until)}`,
    [clientId, platforms, refresh, since, until],
  );
  const rows: ChannelRow[] = Object.entries(data?.per_platform ?? {}).map(
    ([platform, v]: [string, any]) => ({
      platform,
      spend_micros: v.spend_micros,
      spend_share: v.spend_share,
      tracked_leads: v.tracked_leads,
      tracked_cpl: v.tracked_cpl,
      platform_cpl: v.platform_cpl,
      lqa_cpl: lqa.data?.per_platform?.[platform]?.lead_quality_adjusted_cpl ?? null,
      clicks: v.clicks,
      impressions: v.impressions,
      ctr: v.ctr,
      cpc: v.cpc,
    }),
  );
  const columns: Column<ChannelRow>[] = [
    {
      key: "platform",
      header: "Platform",
      render: (r) => <PlatformChip name={r.platform} />,
      sortValue: (r) => r.platform,
    },
    { key: "spend", header: "Spend", align: "right", render: (r) => $(r.spend_micros), sortValue: (r) => r.spend_micros },
    { key: "share", header: "Share", align: "right", render: (r) => pct(r.spend_share), sortValue: (r) => r.spend_share },
    { key: "impressions", header: "Impr.", align: "right", render: (r) => clicksFmt(r.impressions), sortValue: (r) => r.impressions },
    { key: "clicks", header: "Clicks", align: "right", render: (r) => clicksFmt(r.clicks), sortValue: (r) => r.clicks },
    { key: "ctr", header: "CTR", align: "right", render: (r) => ctrFmt(r.ctr), sortValue: (r) => r.ctr ?? -1 },
    { key: "cpc", header: "CPC", align: "right", render: (r) => money(r.cpc) },
    { key: "leads", header: "Leads", align: "right", render: (r) => r.tracked_leads, sortValue: (r) => r.tracked_leads },
    { key: "tcpl", header: "Tracked CPL", align: "right", render: (r) => money(r.tracked_cpl) },
    { key: "pcpl", header: "Platform CPL*", align: "right", render: (r) => money(r.platform_cpl) },
    { key: "lqacpl", header: "LQA-CPL", align: "right", render: (r) => money(r.lqa_cpl) },
  ];
  return (
    <WidgetBody
      error={error}
      loading={loading}
      empty={data && rows.length === 0 ? "No spend recorded yet — sync insights." : null}
    >
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.platform}
        caption="Channel mix by platform"
        initialSort="-spend"
      />
      <p className="dash-footnote">
        *Platform CPL uses the platform's own conversion claim; Tracked CPL uses
        UTM/click-id-attributed Salescale leads.
      </p>
    </WidgetBody>
  );
}

// --- Spend / pacing chart -------------------------------------------------

interface SpendDayRow {
  day: string;
  [platform: string]: string | number;
}

export function SpendPacingWidget({
  clientId,
  platforms,
  refresh,
  since,
  until,
  accountIds,
  campaignIds,
}: WidgetProps) {
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/spend-daily?client_id=${clientId}${filterParam(platforms)}${rangeParam(since, until)}${entityParam(accountIds, campaignIds)}`,
    [clientId, platforms, refresh, since, until, accountIds, campaignIds],
  );
  const [mode, setMode] = useState<"chart" | "table">("chart");

  const perPlatform = Object.entries(data?.per_platform ?? {}) as [string, any][];
  const days: string[] = data?.days ?? [];

  // Dollars (not micros) so the chart's y-axis and tooltip read in currency.
  const series: ChartSeries[] = perPlatform.map(([platform, s]) => ({
    name: platform,
    data: (s.daily_spend_micros as number[]).map((v) => v / 1_000_000),
  }));

  return (
    <WidgetBody
      error={error}
      loading={loading}
      empty={data && perPlatform.length === 0 ? "No spend recorded yet — sync insights." : null}
    >
      <div className="dash-chart-toolbar">
        <Segmented
          ariaLabel="Spend view"
          value={mode}
          onChange={setMode}
          options={[
            { value: "chart", label: "Chart" },
            { value: "table", label: "Table" },
          ]}
        />
      </div>
      {mode === "chart" ? (
        <LineChart
          labels={days}
          series={series}
          height={190}
          area
          formatValue={(v) => usdCompact(v)}
          ariaLabel={`Daily spend by platform over ${days.length} days`}
        />
      ) : (
        <SpendTable days={days} perPlatform={perPlatform} />
      )}
    </WidgetBody>
  );
}

/** Screen-reader / "view as table" twin of the spend chart. */
function SpendTable({
  days,
  perPlatform,
}: {
  days: string[];
  perPlatform: [string, any][];
}) {
  const rows: SpendDayRow[] = days.map((day, i) => {
    const row: SpendDayRow = { day };
    for (const [platform, s] of perPlatform) {
      row[platform] = (s.daily_spend_micros as number[])[i] ?? 0;
    }
    return row;
  });
  const columns: Column<SpendDayRow>[] = [
    { key: "day", header: "Day", render: (r) => r.day, sortValue: (r) => r.day },
    ...perPlatform.map(
      ([platform]): Column<SpendDayRow> => ({
        key: platform,
        header: platform,
        align: "right",
        render: (r) => $(r[platform] as number),
        sortValue: (r) => r[platform] as number,
      }),
    ),
  ];
  return (
    <DataTable
      columns={columns}
      rows={rows}
      rowKey={(r) => r.day}
      caption="Daily spend by platform"
    />
  );
}

// --- Funnel tiers ---------------------------------------------------------

interface FunnelRow {
  platform: string;
  tier: string;
  spend_micros: number;
  conversions: number;
  cpl: number | null;
}

export function FunnelTiersWidget({
  clientId,
  platforms,
  refresh,
  since,
  until,
}: WidgetProps) {
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/funnel-tiers?client_id=${clientId}${rangeParam(since, until)}`,
    [clientId, refresh, since, until],
  );
  const rows: FunnelRow[] = Object.entries(data ?? {})
    .filter(([platform]) => keepPlatform(platforms, platform))
    .flatMap(([platform, tiers]: [string, any]) =>
      Object.entries(tiers).map(([tier, v]: [string, any]) => ({
        platform,
        tier,
        spend_micros: v.spend_micros,
        conversions: v.conversions,
        cpl: v.cpl,
      })),
    );
  const columns: Column<FunnelRow>[] = [
    { key: "platform", header: "Platform", render: (r) => <PlatformChip name={r.platform} />, sortValue: (r) => r.platform },
    { key: "tier", header: "Tier", render: (r) => r.tier.replace(/_/g, " "), sortValue: (r) => r.tier },
    { key: "spend", header: "Spend", align: "right", render: (r) => $(r.spend_micros), sortValue: (r) => r.spend_micros },
    { key: "conv", header: "Conv.", align: "right", render: (r) => r.conversions, sortValue: (r) => r.conversions },
    { key: "cpl", header: "CPL", align: "right", render: (r) => money(r.cpl) },
  ];
  return (
    <WidgetBody
      error={error}
      loading={loading}
      empty={data && rows.length === 0 ? "No tiered spend in range." : null}
    >
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => `${r.platform}-${r.tier}`}
        caption="Funnel tier performance"
        initialSort="-spend"
      />
    </WidgetBody>
  );
}

// --- Creative fatigue (Meta) ----------------------------------------------

export function FatigueWidget({ clientId, platforms, refresh }: WidgetProps) {
  const active = keepPlatform(platforms, "meta");
  const { data, error, loading } = useWidgetData<any>(
    active ? `/api/metrics/creative-fatigue?client_id=${clientId}` : null,
    [clientId, refresh, active],
  );
  if (!active) return <FilteredOut widget="Creative fatigue (Meta)" />;
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
      <ul className="dash-flags">
        {flagged.map((a: any) => (
          <li key={a.ad_external_id} className="dash-flag">
            <Badge tone="warn">fatigue {a.fatigue_score}</Badge>
            <strong>{a.ad_name}</strong>
            <span className="dash-flag-sub">
              CTR {(a.recent_ctr * 100).toFixed(2)}% vs baseline{" "}
              {(a.baseline_ctr * 100).toFixed(2)}%
            </span>
          </li>
        ))}
      </ul>
    </WidgetBody>
  );
}

// --- Quality Score / ad strength (Google) ---------------------------------

export function QualityWidget({ clientId, platforms, refresh }: WidgetProps) {
  const active = keepPlatform(platforms, "google");
  const { data, error, loading } = useWidgetData<any>(
    active ? `/api/metrics/quality-trends?client_id=${clientId}` : null,
    [clientId, refresh, active],
  );
  if (!active) return <FilteredOut widget="Quality Score alerts (Google)" />;
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
      <ul className="dash-flags">
        {flagged.map((e: any) => (
          <li key={`${e.metric}-${e.entity_external_id}`} className="dash-flag">
            <Badge tone="warn">
              {e.metric === "quality_score" ? "QS" : "ad strength"} {e.delta}
            </Badge>
            <strong>{e.entity_name}</strong>
            <span className="dash-flag-sub">
              {e.first} → {e.latest}
              {e.latest_label ? ` (${e.latest_label})` : ""} since {e.first_date}
            </span>
          </li>
        ))}
      </ul>
    </WidgetBody>
  );
}

// --- Guarantee / goal tracker ---------------------------------------------

export function GuaranteeWidget({
  clientId,
  session,
  platforms,
  refresh,
}: WidgetProps) {
  const [bump, setBump] = useState(0);
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/guarantee?client_id=${clientId}${filterParam(platforms)}`,
    [clientId, platforms, refresh, bump],
  );
  const isAdmin = ADMIN_ROLES.includes(session.role);
  const [editing, setEditing] = useState(false);

  if (data && !data.configured && !isAdmin)
    return (
      <p className="dash-muted">
        No performance guarantee configured for this client.
      </p>
    );

  return (
    <WidgetBody error={error} loading={loading} empty={null}>
      {data?.configured && !editing && (
        <div className="dash-guarantee">
          <div className="dash-guarantee-head">
            <span className="dash-guarantee-name">{data.name}</span>
            <Badge tone={data.met || data.on_pace ? "ok" : "warn"}>
              {data.met ? "met" : data.on_pace ? "on pace" : "behind pace"}
            </Badge>
            {isAdmin && (
              <Button variant="link" size="sm" onClick={() => setEditing(true)}>
                Edit
              </Button>
            )}
          </div>
          <div
            className="dash-progress"
            role="progressbar"
            aria-valuenow={Math.round((data.pct_of_target ?? 0) * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`${data.progress} of ${data.target} ${String(
              data.metric,
            ).replace(/_/g, " ")}`}
          >
            <div
              className={`dash-progress-fill ${
                data.met || data.on_pace ? "" : "dash-progress-fill--behind"
              }`.trim()}
              style={{ width: `${Math.min(data.pct_of_target * 100, 100)}%` }}
            />
            <div
              className="dash-progress-pace"
              style={{
                left: `${Math.min((data.expected_by_now / data.target) * 100, 100)}%`,
              }}
              title={`straight-line pace: ${data.expected_by_now} by today`}
            />
          </div>
          <div className="dash-guarantee-numbers">
            <strong>
              {data.progress} / {data.target}
            </strong>{" "}
            {String(data.metric).replace(/_/g, " ")}
            <span className="dash-muted">
              {" · "}
              {data.window.days_remaining} day
              {data.window.days_remaining === 1 ? "" : "s"} left of{" "}
              {data.window.days_total}
            </span>
          </div>
          <div className="dash-legend">
            {Object.entries(data.per_platform).map(([p, n]) => (
              <PlatformChip key={p} name={`${p}: ${n as number}`} />
            ))}
            {Object.keys(data.per_platform).length === 0 && (
              <span className="dash-muted">no contributions yet</span>
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
    existing ? String(existing.window.days_total) : "30",
  );
  const [startDate, setStartDate] = useState(existing?.window?.start ?? "");
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="dash-form">
      <p className="dash-note">
        {existing ? "Edit guarantee terms" : "Set up a performance guarantee"} —
        Organization-configured (e.g. a trial-sprint lead promise).
      </p>
      <Field label="Guarantee name">
        <input
          className="input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g. 14-Day Trial Sprint"
        />
      </Field>
      <div className="dash-form-row">
        <Field label="Metric">
          <select
            className="select"
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
          >
            <option value="qualified_leads">qualified leads</option>
            <option value="tracked_leads">tracked leads</option>
            <option value="won_deals">won deals</option>
          </select>
        </Field>
        <Field label="Target #">
          <input
            className="input"
            type="number"
            min="1"
            value={target}
            onChange={(e) => setTarget(e.target.value)}
          />
        </Field>
        <Field label="Window (days)">
          <input
            className="input"
            type="number"
            min="1"
            value={windowDays}
            onChange={(e) => setWindowDays(e.target.value)}
          />
        </Field>
        <Field label="Start date" optional description="Blank = rolling window">
          <input
            className="input"
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
          />
        </Field>
      </div>
      <div className="dash-form-row">
        <Button
          variant="primary"
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
        </Button>
        {onCancel && (
          <Button variant="ghost" onClick={onCancel}>
            Cancel
          </Button>
        )}
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
    </div>
  );
}

// --- Attribution discrepancy alerts ---------------------------------------

interface ReconRow {
  platform: string;
  platform_reported: number;
  utm_confirmed: number;
  discrepancy: number;
  flagged: boolean;
}

export function ReconciliationWidget({
  clientId,
  platforms,
  refresh,
  since,
  until,
}: WidgetProps) {
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/reconciliation?client_id=${clientId}${rangeParam(since, until)}`,
    [clientId, refresh, since, until],
  );
  const rows: ReconRow[] = Object.entries(data?.per_platform ?? {})
    .filter(([p]) => keepPlatform(platforms, p))
    .map(([platform, v]: [string, any]) => ({
      platform,
      platform_reported: v.platform_reported,
      utm_confirmed: v.utm_confirmed,
      discrepancy: v.discrepancy,
      flagged: v.flagged,
    }));
  const flags = (data?.flags ?? []).filter(
    (f: any) => f.platform === null || keepPlatform(platforms, f.platform),
  );
  const columns: Column<ReconRow>[] = [
    { key: "platform", header: "Platform", render: (r) => <PlatformChip name={r.platform} />, sortValue: (r) => r.platform },
    { key: "reported", header: "Reported", align: "right", render: (r) => r.platform_reported, sortValue: (r) => r.platform_reported },
    { key: "confirmed", header: "UTM-confirmed", align: "right", render: (r) => r.utm_confirmed, sortValue: (r) => r.utm_confirmed },
    {
      key: "delta",
      header: "Δ",
      align: "right",
      render: (r) => (
        <span className={r.flagged ? "dash-flag-sub" : undefined}>
          {r.discrepancy > 0 ? "+" : ""}
          {r.discrepancy}
        </span>
      ),
      sortValue: (r) => r.discrepancy,
    },
  ];
  return (
    <WidgetBody
      error={error}
      loading={loading}
      empty={data && rows.length === 0 ? "No platform-reported conversions in range." : null}
    >
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.platform}
        caption="Attribution reconciliation by platform"
      />
      {flags.map((f: any, i: number) => (
        <Alert key={i} tone="warn">
          {f.detail}
        </Alert>
      ))}
    </WidgetBody>
  );
}

// --- Raw campaign table (power-user editing) ------------------------------

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
    [clientId, refresh, bump],
  );
  const [actionError, setActionError] = useState<string | null>(null);
  const canManage = TEAM_ROLES.includes(session.role);
  const rows = (data ?? []).filter((c) => keepPlatform(platforms, c.platform));

  const stageAction = (c: Campaign, action: string) =>
    stage(
      {
        ad_account_id: (c as any).ad_account_id,
        entity_type: "campaign",
        action,
        entity_id: c.id,
      },
      () => setBump((b) => b + 1),
    ).catch((e) => setActionError((e as Error).message));

  const columns: Column<Campaign>[] = [
    { key: "name", header: "Campaign", render: (c) => c.name, sortValue: (c) => c.name },
    { key: "platform", header: "Platform", render: (c) => <PlatformChip name={c.platform} />, sortValue: (c) => c.platform },
    {
      key: "status",
      header: "Status",
      render: (c) => <Badge tone={toneForStatus(c.status ?? "unknown")}>{c.status ?? "—"}</Badge>,
      sortValue: (c) => c.status ?? "",
    },
    { key: "budget", header: "Daily budget", align: "right", render: (c) => $(c.daily_budget_micros), sortValue: (c) => c.daily_budget_micros ?? -1 },
    { key: "objective", header: "Objective", render: (c) => c.objective ?? "—" },
    ...(canManage
      ? [
          {
            key: "actions",
            header: "",
            render: (c: Campaign) => {
              const paused = c.status?.toUpperCase() === "PAUSED";
              return (
                <Button
                  variant="link"
                  size="sm"
                  onClick={() => stageAction(c, paused ? "resume" : "pause")}
                >
                  {paused ? "Resume" : "Pause"}
                </Button>
              );
            },
          } satisfies Column<Campaign>,
        ]
      : []),
  ];

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
      {actionError && <Alert tone="danger">{actionError}</Alert>}
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(c) => c.id}
        caption="Campaigns across all platforms"
        initialSort="name"
      />
      {canManage && (
        <p className="dash-footnote">
          Pause/resume stages a change for confirmation — nothing touches the
          live account until you confirm it.
        </p>
      )}
    </WidgetBody>
  );
}

// --- Vertical benchmark (team-only) ---------------------------------------

export function BenchmarkWidget({ clientId, refresh, since, until }: WidgetProps) {
  const { data, error, loading } = useWidgetData<any>(
    `/api/metrics/benchmark?client_id=${clientId}${rangeParam(since, until)}`,
    [clientId, refresh, since, until],
  );
  return (
    <WidgetBody
      error={error}
      loading={loading}
      empty={data && !data.vertical ? "Client has no vertical set." : null}
    >
      {data?.vertical && (
        <KpiGrid>
          <Kpi
            label={`vs. ${data.vertical} book (${data.peers} clients)`}
            value={
              data.vs_median_pct == null
                ? "—"
                : `${data.vs_median_pct > 0 ? "+" : ""}${data.vs_median_pct}% CPL`
            }
            delta={data.vs_median_pct ?? null}
            deltaLabel="vs vertical median"
            upIsGood={false}
          />
        </KpiGrid>
      )}
      {data?.vertical && (
        <p className="dash-footnote">
          Median {money(data.vertical_median_blended_cpl)} · this client{" "}
          {money(data.client_blended_cpl)}
        </p>
      )}
    </WidgetBody>
  );
}

// --- UTM builder (team tool) ----------------------------------------------

export function UtmBuilderWidget({ clientId }: WidgetProps) {
  const [catalog, setCatalog] = useState<Platform[]>([]);
  const [platform, setPlatform] = useState("meta");
  const [campaign, setCampaign] = useState("");
  const [content, setContent] = useState("");
  const [landing, setLanding] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [violations, setViolations] = useState<any | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Registry-driven platform list — a newly-registered platform appears with
  // no edit here (mirrors the shell's GET /api/platforms usage).
  useEffect(() => {
    getPlatforms()
      .then((ps) => {
        const connectable = ps.filter((p) => p.connectable);
        setCatalog(connectable.length ? connectable : ps);
        if (connectable.length && !connectable.some((p) => p.id === "meta"))
          setPlatform(connectable[0].id);
      })
      .catch(() => {});
  }, []);

  const copy = () => {
    if (!result) return;
    navigator.clipboard?.writeText(result);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="dash-form">
      <div className="dash-form-row">
        <Field label="Platform">
          <select
            className="select"
            value={platform}
            onChange={(e) => setPlatform(e.target.value)}
          >
            {catalog.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Campaign name">
          <input
            className="input"
            value={campaign}
            onChange={(e) => setCampaign(e.target.value)}
          />
        </Field>
        <Field label="Ad / content" optional>
          <input
            className="input"
            value={content}
            onChange={(e) => setContent(e.target.value)}
          />
        </Field>
      </div>
      <div className="dash-form-row">
        <Field label="Landing page URL" optional>
          <input
            className="input"
            value={landing}
            onChange={(e) => setLanding(e.target.value)}
          />
        </Field>
        <Button
          variant="primary"
          disabled={!campaign}
          onClick={() =>
            api<{ query_string: string }>(
              `/api/utm/build?client_id=${clientId}&platform=${platform}` +
                `&campaign_name=${encodeURIComponent(campaign)}` +
                (content ? `&content=${encodeURIComponent(content)}` : ""),
            )
              .then((r) => {
                setError(null);
                setResult(
                  landing
                    ? `${landing}${landing.includes("?") ? "&" : "?"}${r.query_string}`
                    : `?${r.query_string}`,
                );
              })
              .catch((e) => setError((e as Error).message))
          }
        >
          Build URL
        </Button>
        <Button
          variant="ghost"
          onClick={() =>
            api<any>(`/api/utm/violations?client_id=${clientId}`)
              .then(setViolations)
              .catch((e) => setError((e as Error).message))
          }
        >
          Check convention violations
        </Button>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      {result && (
        <div className="dash-result">
          <code>{result}</code>
          <Button variant="default" size="sm" onClick={copy}>
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      )}
      {violations && (
        <Alert tone={violations.violations.length ? "warn" : "info"}>
          {violations.violations.length} violation(s) in {violations.checked}{" "}
          recent landing events
          {violations.violations
            .slice(0, 5)
            .map((v: any) => ` · ${v.problems[0]}`)}
        </Alert>
      )}
    </div>
  );
}

// --- Phase 5: server-side conversion tracking health (team-only) ----------
// Event Match Quality per event for the client's Meta dataset, plus the
// dispatch log for every server-side send. Admins configure each platform's
// destination (Meta dataset / Google conversion action) inline.

interface LogRow {
  id: string;
  when: string;
  event_name: string;
  is_test: boolean;
  platform: string;
  status: string;
  detail: string | null;
  match_keys: string[];
}

export function ConversionHealthWidget({
  clientId,
  session,
  platforms,
  refresh,
}: WidgetProps) {
  const [bump, setBump] = useState(0);
  const isAdmin = ADMIN_ROLES.includes(session.role);
  const [configuring, setConfiguring] = useState(false);
  const configs = useWidgetData<any[]>(
    `/api/clients/${clientId}/conversion-configs`,
    [clientId, refresh, bump],
  );
  const log = useWidgetData<any[]>(
    `/api/conversions/log?client_id=${clientId}&limit=20`,
    [clientId, refresh, bump],
  );
  const metaConfigured = (configs.data ?? []).some(
    (c) => c.platform === "meta" && c.enabled,
  );
  const emq = useWidgetData<any>(
    metaConfigured && keepPlatform(platforms, "meta")
      ? `/api/conversions/emq?client_id=${clientId}`
      : null,
    [clientId, metaConfigured, platforms, refresh, bump],
  );
  const logRows: LogRow[] = (log.data ?? [])
    .filter((e) => keepPlatform(platforms, e.dispatch.platform))
    .map((e) => ({
      id: e.dispatch.id,
      when: new Date(e.dispatch.attempted_at).toLocaleString(),
      event_name: e.event_name + (e.dispatch.is_test ? " (test)" : ""),
      is_test: e.dispatch.is_test,
      platform: e.dispatch.platform,
      status: e.dispatch.status,
      detail: e.dispatch.detail ?? null,
      match_keys: e.dispatch.match_keys ?? [],
    }));

  const columns: Column<LogRow>[] = [
    { key: "when", header: "When", render: (r) => r.when, sortValue: (r) => r.when },
    { key: "event", header: "Event", render: (r) => r.event_name },
    { key: "platform", header: "Platform", render: (r) => <PlatformChip name={r.platform} /> },
    {
      key: "status",
      header: "Status",
      render: (r) => <Badge tone={toneForStatus(r.status)}>{r.status}</Badge>,
      sortValue: (r) => r.status,
    },
    { key: "matched", header: "Matched on", render: (r) => r.match_keys.join(", ") || "—" },
  ];

  return (
    <WidgetBody error={configs.error} loading={configs.loading} empty={null}>
      {configs.data && configs.data.length === 0 && !configuring && (
        <p className="dash-muted">
          Server-side conversion tracking isn't set up for this client.
          {isAdmin && (
            <>
              {" "}
              <Button variant="link" size="sm" onClick={() => setConfiguring(true)}>
                Configure
              </Button>
            </>
          )}
        </p>
      )}
      {configs.data && configs.data.length > 0 && (
        <>
          {metaConfigured && keepPlatform(platforms, "meta") && (
            <div className="dash-emq">
              <span className="dash-emq-label">Meta Event Match Quality</span>
              {emq.loading && <span className="dash-muted">loading…</span>}
              {emq.error && <Badge tone="warn">unavailable</Badge>}
              {emq.data &&
                (emq.data.events.length === 0 ? (
                  <span className="dash-muted">no scored events yet</span>
                ) : (
                  emq.data.events.map((e: any) => (
                    <Badge
                      key={e.event_name}
                      tone={
                        e.composite_score == null
                          ? "neutral"
                          : e.composite_score >= 6
                            ? "ok"
                            : "warn"
                      }
                    >
                      {e.event_name}: {e.composite_score ?? "—"}/10
                    </Badge>
                  ))
                ))}
            </div>
          )}
          <DataTable
            columns={columns}
            rows={logRows}
            rowKey={(r) => r.id}
            caption="Server-side conversion dispatch log"
            emptyMessage="No server-side sends yet."
          />
          {isAdmin && !configuring && (
            <p className="dash-footnote">
              <Button variant="link" size="sm" onClick={() => setConfiguring(true)}>
                Tracking settings
              </Button>
            </p>
          )}
        </>
      )}
      {configuring && isAdmin && (
        <ConversionConfigForm
          clientId={clientId}
          configs={configs.data ?? []}
          onDone={() => {
            setConfiguring(false);
            setBump((b) => b + 1);
          }}
        />
      )}
    </WidgetBody>
  );
}

function ConversionConfigForm({
  clientId,
  configs,
  onDone,
}: {
  clientId: string;
  configs: any[];
  onDone: () => void;
}) {
  const meta = configs.find((c) => c.platform === "meta");
  const google = configs.find((c) => c.platform === "google");
  const [datasetId, setDatasetId] = useState(meta?.settings?.dataset_id ?? "");
  const [testCode, setTestCode] = useState(meta?.settings?.test_event_code ?? "");
  const [customerId, setCustomerId] = useState(google?.settings?.customer_id ?? "");
  const [actionId, setActionId] = useState(
    google?.settings?.conversion_action_id ?? "",
  );
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setError(null);
    try {
      if (datasetId) {
        await api(`/api/clients/${clientId}/conversion-configs/meta`, {
          method: "PUT",
          body: JSON.stringify({
            enabled: true,
            settings: {
              dataset_id: datasetId,
              ...(testCode ? { test_event_code: testCode } : {}),
              event_name: "Lead",
            },
          }),
        });
      }
      if (customerId && actionId) {
        await api(`/api/clients/${clientId}/conversion-configs/google`, {
          method: "PUT",
          body: JSON.stringify({
            enabled: true,
            settings: {
              customer_id: customerId,
              conversion_action_id: actionId,
              ad_user_data_consent: "GRANTED",
            },
          }),
        });
      }
      onDone();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const testSend = async (platform: string) => {
    setStatus(`sending ${platform} test…`);
    try {
      const resp = await api<any>(`/api/conversions/test-send`, {
        method: "POST",
        body: JSON.stringify({
          client_id: clientId,
          platform,
          email: "test@example.com",
          first_name: "Test",
        }),
      });
      const r = resp.results[0];
      setStatus(
        r
          ? `${platform}: ${r.status}${r.detail ? ` — ${r.detail}` : ""}` +
              (platform === "meta" && r.status === "sent"
                ? " (check Events Manager → Test Events)"
                : "")
          : `${platform}: no result`,
      );
    } catch (e) {
      setStatus((e as Error).message);
    }
  };

  return (
    <div className="dash-form">
      <p className="dash-note">
        Per-client destinations — each client sends to its own Meta dataset and
        Google conversion action.
      </p>
      <div className="dash-form-row">
        <Field label="Meta dataset / pixel ID">
          <input
            className="input"
            value={datasetId}
            onChange={(e) => setDatasetId(e.target.value)}
          />
        </Field>
        <Field label="Test event code" description="Events Manager">
          <input
            className="input"
            value={testCode}
            onChange={(e) => setTestCode(e.target.value)}
          />
        </Field>
        {meta && (
          <Button
            variant="ghost"
            disabled={!testCode}
            title={
              testCode
                ? "Send a test event, visible in Meta's Test Events tool"
                : "Set a test event code first"
            }
            onClick={() => testSend("meta")}
          >
            Send Meta test
          </Button>
        )}
      </div>
      <div className="dash-form-row">
        <Field label="Google Ads customer ID">
          <input
            className="input"
            value={customerId}
            onChange={(e) => setCustomerId(e.target.value)}
          />
        </Field>
        <Field label="Conversion action ID" description="Type UPLOAD_CLICKS">
          <input
            className="input"
            value={actionId}
            onChange={(e) => setActionId(e.target.value)}
          />
        </Field>
        {google && (
          <Button variant="ghost" onClick={() => testSend("google")}>
            Send Google test
          </Button>
        )}
      </div>
      <div className="dash-form-row">
        <Button
          variant="primary"
          disabled={!datasetId && !(customerId && actionId)}
          onClick={save}
        >
          Save tracking settings
        </Button>
        <Button variant="ghost" onClick={onDone}>
          Close
        </Button>
      </div>
      {status && <p className="dash-note">{status}</p>}
      {error && <Alert tone="danger">{error}</Alert>}
    </div>
  );
}
