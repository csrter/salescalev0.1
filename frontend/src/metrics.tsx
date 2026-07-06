/**
 * Phase 3 metrics panel — one plain section per metric family. Phase 4 owns
 * the customizable dashboard; this is the readable reference rendering.
 * Formula definitions live with the computation in
 * backend/app/services/metrics.py.
 */

import { useEffect, useState } from "react";
import { api, TEAM_ROLES, type Session } from "./api";

const $ = (micros?: number | null) =>
  micros == null ? "—" : `$${(micros / 1_000_000).toFixed(2)}`;
const num = (v?: number | null) => (v == null ? "—" : String(v));
const money = (v?: number | null) => (v == null ? "—" : `$${v.toFixed(2)}`);

function useMetric<T>(path: string | null, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!path) return;
    setData(null);
    api<T>(path).then(setData).catch((e) => setError(e.message));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, ...deps]);
  return { data, error };
}

export function MetricsPanel({
  clientId,
  session,
}: {
  clientId: string;
  session: Session;
}) {
  const isTeam = TEAM_ROLES.includes(session.role);
  const [syncing, setSyncing] = useState(false);
  const [syncNote, setSyncNote] = useState<string | null>(null);
  const [bump, setBump] = useState(0);

  const blended = useMetric<any>(
    `/api/metrics/blended?client_id=${clientId}`, [bump]
  );
  const tiers = useMetric<any>(
    `/api/metrics/funnel-tiers?client_id=${clientId}`, [bump]
  );
  const fatigue = useMetric<any>(
    `/api/metrics/creative-fatigue?client_id=${clientId}`, [bump]
  );
  const quality = useMetric<any>(
    `/api/metrics/quality-trends?client_id=${clientId}`, [bump]
  );
  const lqa = useMetric<any>(
    `/api/metrics/lead-quality-adjusted-cpl?client_id=${clientId}`, [bump]
  );
  const recon = useMetric<any>(
    `/api/metrics/reconciliation?client_id=${clientId}`, [bump]
  );
  const bench = useMetric<any>(
    isTeam ? `/api/metrics/benchmark?client_id=${clientId}` : null, [bump]
  );

  const sync = async () => {
    setSyncing(true);
    setSyncNote(null);
    try {
      const resp = await api<{ results: any[] }>(
        `/api/insights/sync?client_id=${clientId}`,
        { method: "POST" }
      );
      const failures = resp.results.filter((r) => !r.ok);
      setSyncNote(
        failures.length
          ? `Synced with issues: ${failures
              .map((f) => `${f.platform}: ${f.error}`)
              .join("; ")}`
          : `Synced ${resp.results.length} account(s)`
      );
      setBump((b) => b + 1);
    } catch (e) {
      setSyncNote((e as Error).message);
    } finally {
      setSyncing(false);
    }
  };

  const b = blended.data;
  return (
    <section>
      <h3>
        Metrics (last 30 days){" "}
        {isTeam && (
          <button onClick={sync} disabled={syncing}>
            {syncing ? "Syncing…" : "Sync insights"}
          </button>
        )}
      </h3>
      {syncNote && <p className="muted">{syncNote}</p>}
      {blended.error && <p className="error">{blended.error}</p>}

      {b && (
        <div className="metric-cards">
          <div className="metric-card">
            <span className="metric-label">Blended CPL</span>
            <strong>{money(b.blended_cpl)}</strong>
            <span className="muted">
              {$(b.total_spend_micros)} / {b.total_tracked_leads} leads
            </span>
          </div>
          <div className="metric-card">
            <span className="metric-label">Blended CAC</span>
            <strong>{money(b.blended_cac)}</strong>
            <span className="muted">{b.won_deals_from_paid} won from paid</span>
          </div>
          <div className="metric-card">
            <span className="metric-label">Blended ROAS</span>
            <strong>{num(b.blended_roas)}</strong>
            <span className="muted">Salescale won-deal revenue / spend</span>
          </div>
          {lqa.data && (
            <div className="metric-card">
              <span className="metric-label">LQA-CPL (blended)</span>
              <strong>{money(lqa.data.blended_lead_quality_adjusted_cpl)}</strong>
              <span className="muted">
                {lqa.data.total_qualified_leads} qualified ·{" "}
                {lqa.data.source} source
              </span>
            </div>
          )}
          {isTeam && bench.data?.vertical && (
            <div className="metric-card">
              <span className="metric-label">
                Vs. {bench.data.vertical} book ({bench.data.peers} clients)
              </span>
              <strong>
                {bench.data.vs_median_pct == null
                  ? "—"
                  : `${bench.data.vs_median_pct > 0 ? "+" : ""}${bench.data.vs_median_pct}% CPL`}
              </strong>
              <span className="muted">
                median {money(bench.data.vertical_median_blended_cpl)}
              </span>
            </div>
          )}
        </div>
      )}

      {b && (
        <>
          <h4>Channel mix</h4>
          <table className="compact">
            <thead>
              <tr>
                <th>Platform</th>
                <th>Spend</th>
                <th>Spend share</th>
                <th>Tracked leads</th>
                <th>Lead share</th>
                <th>Tracked CPL</th>
                <th>Platform CPL*</th>
                <th>LQA-CPL</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(b.per_platform).map(([platform, v]: [string, any]) => (
                <tr key={platform}>
                  <td>{platform}</td>
                  <td>{$(v.spend_micros)}</td>
                  <td>{v.spend_share == null ? "—" : `${(v.spend_share * 100).toFixed(0)}%`}</td>
                  <td>{v.tracked_leads}</td>
                  <td>{v.lead_share == null ? "—" : `${(v.lead_share * 100).toFixed(0)}%`}</td>
                  <td>{money(v.tracked_cpl)}</td>
                  <td>{money(v.platform_cpl)}</td>
                  <td>
                    {money(
                      lqa.data?.per_platform?.[platform]
                        ?.lead_quality_adjusted_cpl
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted">
            *Platform CPL uses the platform's own conversion claim; Tracked CPL
            uses UTM/click-id-attributed Salescale leads.
          </p>
        </>
      )}

      {tiers.data && Object.keys(tiers.data).length > 0 && (
        <>
          <h4>Funnel tiers</h4>
          <table className="compact">
            <thead>
              <tr>
                <th>Platform</th>
                <th>Tier</th>
                <th>Spend</th>
                <th>Conversions</th>
                <th>CPL</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(tiers.data).flatMap(([platform, t]: [string, any]) =>
                Object.entries(t).map(([tier, v]: [string, any]) => (
                  <tr key={`${platform}-${tier}`}>
                    <td>{platform}</td>
                    <td>{tier}</td>
                    <td>{$(v.spend_micros)}</td>
                    <td>{v.conversions}</td>
                    <td>{money(v.cpl)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </>
      )}

      {(fatigue.data?.flagged?.length ?? 0) > 0 && (
        <>
          <h4>Creative fatigue flags (Meta)</h4>
          <ul>
            {fatigue.data.flagged.map((a: any) => (
              <li key={a.ad_external_id} className="warning">
                {a.ad_name}: CTR {(a.recent_ctr * 100).toFixed(2)}% vs baseline{" "}
                {(a.baseline_ctr * 100).toFixed(2)}% — fatigue score{" "}
                {a.fatigue_score}
              </li>
            ))}
          </ul>
        </>
      )}

      {(quality.data?.flagged?.length ?? 0) > 0 && (
        <>
          <h4>Quality drops (Google)</h4>
          <ul>
            {quality.data.flagged.map((e: any) => (
              <li key={`${e.metric}-${e.entity_external_id}`} className="warning">
                {e.metric === "quality_score" ? "QS" : "Ad strength"}{" "}
                {e.entity_name}: {e.first} → {e.latest}
                {e.latest_label ? ` (${e.latest_label})` : ""} since{" "}
                {e.first_date}
              </li>
            ))}
          </ul>
        </>
      )}

      {recon.data && (
        <>
          <h4>Attribution reconciliation</h4>
          <table className="compact">
            <thead>
              <tr>
                <th>Platform</th>
                <th>Platform-reported</th>
                <th>UTM-confirmed</th>
                <th>Discrepancy</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(recon.data.per_platform).map(
                ([platform, v]: [string, any]) => (
                  <tr key={platform} className={v.flagged ? "negative" : ""}>
                    <td>{platform}</td>
                    <td>{v.platform_reported}</td>
                    <td>{v.utm_confirmed}</td>
                    <td>
                      {v.discrepancy > 0 ? "+" : ""}
                      {v.discrepancy} {v.flagged ? "⚑" : ""}
                    </td>
                  </tr>
                )
              )}
            </tbody>
          </table>
          {recon.data.flags.map((f: any, i: number) => (
            <p key={i} className="warning">
              ⚑ {f.detail}
            </p>
          ))}
        </>
      )}

      {isTeam && <UtmBuilder clientId={clientId} />}
    </section>
  );
}

function UtmBuilder({ clientId }: { clientId: string }) {
  const [platform, setPlatform] = useState("meta");
  const [campaign, setCampaign] = useState("");
  const [content, setContent] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [violations, setViolations] = useState<any | null>(null);

  return (
    <>
      <h4>UTM builder</h4>
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
        <button
          disabled={!campaign}
          onClick={() =>
            api<{ query_string: string }>(
              `/api/utm/build?client_id=${clientId}&platform=${platform}` +
                `&campaign_name=${encodeURIComponent(campaign)}` +
                (content ? `&content=${encodeURIComponent(content)}` : "")
            ).then((r) => setResult(r.query_string))
          }
        >
          Build
        </button>
        <button
          className="link"
          onClick={() =>
            api<any>(`/api/utm/violations?client_id=${clientId}`).then(
              setViolations
            )
          }
        >
          Check convention violations
        </button>
      </div>
      {result && <code className="utm-result">?{result}</code>}
      {violations && (
        <p className={violations.violations.length ? "warning" : "muted"}>
          {violations.violations.length} violation(s) in {violations.checked}{" "}
          recent landing events
          {violations.violations
            .slice(0, 5)
            .map((v: any) => ` · ${v.problems[0]}`)}
        </p>
      )}
    </>
  );
}
