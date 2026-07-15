/**
 * Google-only management surface: keywords (with match types and negatives),
 * search terms review, and Performance Max asset groups. These concepts have
 * no Meta equivalent, so they render as their own panels instead of being
 * forced into the shared campaign tree shapes.
 */

import { useCallback, useEffect, useState } from "react";
import { api, type AssetGroup, type Keyword, type SearchTerm } from "./api";
import { useManage } from "./manage";
import { DataTable } from "./components/DataTable";
import { Alert, Badge, Button, Field, Segmented, toneForStatus } from "./components/ui";
import "./styles/views/manage.css";

const MATCH_TYPES = ["EXACT", "PHRASE", "BROAD"] as const;

function dollarsFromMicros(micros?: number | null): string {
  return micros == null ? "" : (micros / 1_000_000).toFixed(2);
}

function microsFromDollars(dollars: string): number | undefined {
  const n = parseFloat(dollars);
  return Number.isFinite(n) && n >= 0 ? Math.round(n * 1_000_000) : undefined;
}

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
  const [bid, setBid] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editMatch, setEditMatch] = useState<string>("PHRASE");
  const [editBid, setEditBid] = useState("");

  const load = useCallback(() => {
    api<Keyword[]>(`/api/ad-groups/${adGroupId}/keywords`)
      .then(setKeywords)
      .catch((e) => setError(e.message));
  }, [adGroupId]);
  useEffect(load, [load]);

  const addKeyword = () => {
    if (!text) return;
    stage(
      {
        ad_account_id: adAccountId,
        entity_type: "keyword",
        action: "add",
        entity_name: text,
        payload: {
          ad_group_id: adGroupId,
          text,
          match_type: matchType,
          negative,
          bid_micros: negative ? undefined : microsFromDollars(bid),
        },
      },
      () => {
        setText("");
        setBid("");
        load();
      }
    ).catch((e) => setError((e as Error).message));
  };

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

  const startEdit = (kw: Keyword) => {
    setEditingId(kw.criterion_id);
    setEditMatch(kw.match_type);
    setEditBid(dollarsFromMicros(kw.cpc_bid_micros));
  };

  const saveEdit = (kw: Keyword) => {
    const payload: Record<string, unknown> = { ad_group_id: adGroupId };
    if (editMatch !== kw.match_type) payload.match_type = editMatch;
    const bidMicros = microsFromDollars(editBid);
    if (bidMicros !== undefined && bidMicros !== kw.cpc_bid_micros) {
      payload.bid_micros = bidMicros;
    }
    if (Object.keys(payload).length === 1) {
      setEditingId(null);
      return;
    }
    stage(
      {
        ad_account_id: adAccountId,
        entity_type: "keyword",
        action: "update",
        entity_external_id: kw.criterion_id,
        entity_name: kw.text,
        payload,
      },
      () => {
        setEditingId(null);
        load();
      }
    ).catch((e) => setError((e as Error).message));
  };

  const toggleStatus = (kw: Keyword) =>
    stage(
      {
        ad_account_id: adAccountId,
        entity_type: "keyword",
        action: kw.status === "PAUSED" ? "resume" : "pause",
        entity_external_id: kw.criterion_id,
        entity_name: kw.text,
        payload: { ad_group_id: adGroupId },
      },
      load
    ).catch((e) => setError((e as Error).message));

  if (error) return <Alert tone="danger">{error}</Alert>;
  return (
    <div className="mg-panel">
      <DataTable<Keyword>
        loading={keywords === null}
        rows={keywords ?? []}
        rowKey={(k) => k.criterion_id}
        emptyMessage="No keywords in this ad group yet."
        columns={[
          {
            key: "text",
            header: "Keyword",
            render: (k) => (
              <span className="mg-cell-inline">
                {k.negative && <Badge tone="neutral">Negative</Badge>}
                {k.text}
              </span>
            ),
            sortValue: (k) => k.text,
          },
          {
            key: "match",
            header: "Match",
            render: (k) =>
              editingId === k.criterion_id ? (
                <Segmented<string>
                  ariaLabel="Match type"
                  options={MATCH_TYPES.map((m) => ({ value: m, label: m }))}
                  value={editMatch}
                  onChange={setEditMatch}
                />
              ) : (
                k.match_type
              ),
            sortValue: (k) => k.match_type,
          },
          {
            key: "bid",
            header: "Bid",
            align: "right",
            render: (k) =>
              editingId === k.criterion_id ? (
                <input
                  className="input mg-bid-input"
                  type="number"
                  min="0"
                  step="0.01"
                  placeholder="Ad group default"
                  value={editBid}
                  onChange={(e) => setEditBid(e.target.value)}
                />
              ) : k.cpc_bid_micros ? (
                `$${dollarsFromMicros(k.cpc_bid_micros)}`
              ) : (
                "Ad group default"
              ),
            sortValue: (k) => k.cpc_bid_micros ?? 0,
          },
          {
            key: "status",
            header: "Status",
            render: (k) =>
              k.status ? <Badge tone={toneForStatus(k.status)}>{k.status}</Badge> : "—",
            sortValue: (k) => k.status ?? "",
          },
          {
            key: "actions",
            header: "",
            render: (k) =>
              editingId === k.criterion_id ? (
                <span className="mg-row-actions">
                  <Button variant="primary" size="sm" onClick={() => saveEdit(k)}>
                    Save
                  </Button>
                  <Button variant="ghost" size="sm" onClick={() => setEditingId(null)}>
                    Cancel
                  </Button>
                </span>
              ) : (
                <span className="mg-row-actions">
                  {k.status !== "REMOVED" && (
                    <>
                      <Button variant="ghost" size="sm" onClick={() => startEdit(k)}>
                        Edit
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => toggleStatus(k)}>
                        {k.status === "PAUSED" ? "Resume" : "Pause"}
                      </Button>
                    </>
                  )}
                  <Button variant="danger-outline" size="sm" onClick={() => removeKeyword(k)}>
                    Remove
                  </Button>
                </span>
              ),
          },
        ]}
      />
      <form
        className="mg-form"
        onSubmit={(e) => {
          e.preventDefault();
          addKeyword();
        }}
      >
        <div className="mg-form-cell">
          <Field label="New keyword">
            <input
              placeholder="e.g. emergency furnace repair"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
          </Field>
        </div>
        <Field label="Match type">
          <Segmented<string>
            ariaLabel="Match type"
            options={MATCH_TYPES.map((m) => ({ value: m, label: m }))}
            value={matchType}
            onChange={setMatchType}
          />
        </Field>
        {!negative && (
          <div className="mg-form-cell">
            <Field label="Bid (optional)">
              <input
                type="number"
                min="0"
                step="0.01"
                placeholder="Ad group default"
                value={bid}
                onChange={(e) => setBid(e.target.value)}
              />
            </Field>
          </div>
        )}
        <label className="mg-check">
          <input
            type="checkbox"
            checked={negative}
            onChange={(e) => setNegative(e.target.checked)}
          />
          Negative
        </label>
        <div className="mg-form-actions">
          <Button type="submit" variant="primary" disabled={!text}>
            Stage add
          </Button>
        </div>
      </form>
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
  const [days, setDays] = useState("30");
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

  if (error) return <Alert tone="danger">{error}</Alert>;
  return (
    <div className="mg-panel">
      <Segmented<string>
        ariaLabel="Search-term date range"
        options={[
          { value: "7", label: "Last 7 days" },
          { value: "14", label: "Last 14 days" },
          { value: "30", label: "Last 30 days" },
        ]}
        value={days}
        onChange={setDays}
      />
      <DataTable<SearchTerm>
        loading={terms === null}
        rows={terms ?? []}
        rowKey={(t) => `${t.ad_group_external_id}-${t.search_term}`}
        emptyMessage="No search terms in range."
        initialSort="-cost"
        columns={[
          {
            key: "term",
            header: "Search term",
            render: (t) => t.search_term,
            sortValue: (t) => t.search_term,
          },
          {
            key: "impressions",
            header: "Impr.",
            align: "right",
            render: (t) => t.impressions,
            sortValue: (t) => t.impressions,
          },
          {
            key: "clicks",
            header: "Clicks",
            align: "right",
            render: (t) => t.clicks,
            sortValue: (t) => t.clicks,
          },
          {
            key: "cost",
            header: "Cost",
            align: "right",
            render: (t) => `$${(t.cost_micros / 1_000_000).toFixed(2)}`,
            sortValue: (t) => t.cost_micros,
          },
          {
            key: "conversions",
            header: "Conv.",
            align: "right",
            render: (t) => t.conversions,
            sortValue: (t) => t.conversions,
          },
          {
            key: "actions",
            header: "",
            render: (t) => (
              <span className="mg-row-actions">
                <Button variant="ghost" size="sm" onClick={() => addNegative(t.search_term)}>
                  Add as negative
                </Button>
              </span>
            ),
          },
        ]}
      />
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

  if (error) return <Alert tone="danger">{error}</Alert>;
  return (
    <div className="mg-panel">
      <DataTable<AssetGroup>
        loading={groups === null}
        rows={groups ?? []}
        rowKey={(g) => g.external_id}
        emptyMessage="No asset groups (not a Performance Max campaign?)."
        columns={[
          {
            key: "name",
            header: "Asset group",
            render: (g) => g.name,
            sortValue: (g) => g.name,
          },
          {
            key: "status",
            header: "Status",
            render: (g) => <Badge tone={toneForStatus(g.status)}>{g.status}</Badge>,
            sortValue: (g) => g.status,
          },
          {
            key: "strength",
            header: "Ad strength",
            render: (g) => g.ad_strength ?? "—",
            sortValue: (g) => g.ad_strength ?? "",
          },
          {
            key: "actions",
            header: "",
            render: (g) => (
              <span className="mg-row-actions">
                <Button variant="ghost" size="sm" onClick={() => toggle(g)}>
                  {g.status === "PAUSED" ? "Resume" : "Pause"}
                </Button>
              </span>
            ),
          },
        ]}
      />
    </div>
  );
}
