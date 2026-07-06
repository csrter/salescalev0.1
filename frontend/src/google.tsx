/**
 * Google-only management surface: keywords (with match types and negatives),
 * search terms review, and Performance Max asset groups. These concepts have
 * no Meta equivalent, so they render as their own panels instead of being
 * forced into the shared campaign tree shapes.
 */

import { useCallback, useEffect, useState } from "react";
import { api, type AssetGroup, type Keyword, type SearchTerm } from "./api";
import { useManage } from "./manage";

const MATCH_TYPES = ["EXACT", "PHRASE", "BROAD"] as const;

export function KeywordsPanel({
  adGroupId,
  adAccountId,
}: {
  adGroupId: string;
  adAccountId: string;
}) {
  const { stage } = useManage();
  const [keywords, setKeywords] = useState<Keyword[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [matchType, setMatchType] = useState<string>("PHRASE");
  const [negative, setNegative] = useState(false);

  const load = useCallback(() => {
    api<Keyword[]>(`/api/ad-groups/${adGroupId}/keywords`)
      .then(setKeywords)
      .catch((e) => setError(e.message));
  }, [adGroupId]);
  useEffect(load, [load]);

  const addKeyword = () =>
    stage(
      {
        ad_account_id: adAccountId,
        entity_type: "keyword",
        action: "add",
        entity_name: text,
        payload: { ad_group_id: adGroupId, text, match_type: matchType, negative },
      },
      () => {
        setText("");
        load();
      }
    ).catch((e) => setError((e as Error).message));

  const removeKeyword = (kw: Keyword) =>
    stage(
      {
        ad_account_id: adAccountId,
        entity_type: "keyword",
        action: "remove",
        entity_name: kw.text,
        payload: { ad_group_id: adGroupId, criterion_id: kw.criterion_id, text: kw.text },
      },
      load
    ).catch((e) => setError((e as Error).message));

  if (error) return <p className="error">{error}</p>;
  if (keywords === null) return <p className="muted">Loading keywords…</p>;
  return (
    <div className="subpanel">
      <table className="compact">
        <thead>
          <tr>
            <th>Keyword</th>
            <th>Match</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {keywords.map((k) => (
            <tr key={k.criterion_id} className={k.negative ? "negative" : ""}>
              <td>
                {k.negative ? "− " : ""}
                {k.text}
              </td>
              <td>{k.match_type}</td>
              <td>{k.status ?? "—"}</td>
              <td>
                <button className="link" onClick={() => removeKeyword(k)}>
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="inline-form">
        <input
          placeholder="New keyword"
          value={text}
          onChange={(e) => setText(e.target.value)}
        />
        <select value={matchType} onChange={(e) => setMatchType(e.target.value)}>
          {MATCH_TYPES.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
        <label>
          <input
            type="checkbox"
            checked={negative}
            onChange={(e) => setNegative(e.target.checked)}
          />
          negative
        </label>
        <button disabled={!text} onClick={addKeyword}>
          Stage add
        </button>
      </div>
    </div>
  );
}

export function SearchTermsPanel({
  campaignId,
  adAccountId,
}: {
  campaignId: string;
  adAccountId: string;
}) {
  const { stage } = useManage();
  const [terms, setTerms] = useState<SearchTerm[] | null>(null);
  const [days, setDays] = useState(30);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<SearchTerm[]>(`/api/campaigns/${campaignId}/search-terms?days=${days}`)
      .then(setTerms)
      .catch((e) => setError(e.message));
  }, [campaignId, days]);

  const addNegative = (term: string) =>
    stage({
      ad_account_id: adAccountId,
      entity_type: "campaign_negative",
      action: "add",
      entity_name: term,
      payload: { campaign_id: campaignId, text: term, match_type: "EXACT" },
    }).catch((e) => setError((e as Error).message));

  if (error) return <p className="error">{error}</p>;
  if (terms === null) return <p className="muted">Loading search terms…</p>;
  return (
    <div className="subpanel">
      <div className="toggle">
        {[7, 14, 30].map((d) => (
          <button
            key={d}
            className={days === d ? "active" : ""}
            onClick={() => setDays(d)}
          >
            Last {d} days
          </button>
        ))}
      </div>
      <table className="compact">
        <thead>
          <tr>
            <th>Search term</th>
            <th>Impr.</th>
            <th>Clicks</th>
            <th>Cost</th>
            <th>Conv.</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {terms.map((t) => (
            <tr key={`${t.ad_group_external_id}-${t.search_term}`}>
              <td>{t.search_term}</td>
              <td>{t.impressions}</td>
              <td>{t.clicks}</td>
              <td>${(t.cost_micros / 1_000_000).toFixed(2)}</td>
              <td>{t.conversions}</td>
              <td>
                <button className="link" onClick={() => addNegative(t.search_term)}>
                  Add as negative
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {terms.length === 0 && <p className="muted">No search terms in range.</p>}
    </div>
  );
}

export function AssetGroupsPanel({
  campaignId,
  adAccountId,
}: {
  campaignId: string;
  adAccountId: string;
}) {
  const { stage } = useManage();
  const [groups, setGroups] = useState<AssetGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api<AssetGroup[]>(`/api/campaigns/${campaignId}/asset-groups`)
      .then(setGroups)
      .catch((e) => setError(e.message));
  }, [campaignId]);
  useEffect(load, [load]);

  const toggle = (g: AssetGroup) =>
    stage(
      {
        ad_account_id: adAccountId,
        entity_type: "asset_group",
        action: g.status === "PAUSED" ? "resume" : "pause",
        entity_external_id: g.external_id,
        entity_name: g.name,
      },
      load
    ).catch((e) => setError((e as Error).message));

  if (error) return <p className="error">{error}</p>;
  if (groups === null) return <p className="muted">Loading asset groups…</p>;
  return (
    <div className="subpanel">
      {groups.length === 0 && (
        <p className="muted">No asset groups (not a Performance Max campaign?).</p>
      )}
      <table className="compact">
        <tbody>
          {groups.map((g) => (
            <tr key={g.external_id}>
              <td>{g.name}</td>
              <td>
                <span className={`badge ${g.status.toLowerCase()}`}>{g.status}</span>
              </td>
              <td className="muted">{g.ad_strength ?? ""}</td>
              <td>
                <button className="link" onClick={() => toggle(g)}>
                  {g.status === "PAUSED" ? "Resume" : "Pause"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
