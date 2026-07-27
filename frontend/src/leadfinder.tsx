/**
 * Lead Finder (Phase 12) — search businesses by vertical + geography via
 * Google Places, see what's already in the CRM, and import the rest as
 * house-CRM leads. Imported leads run the enrich→verify pipeline in the
 * background and land with a verification badge in the CRM view.
 *
 * Team-only (App.tsx gates the tab; the API gates the endpoints).
 */
import { useEffect, useMemo, useState } from "react";
import {
  deleteLeadProviderKey,
  getHouseClient,
  getLeadFinderUsage,
  importLeads,
  listLeadProviders,
  pollApifySearch,
  searchLeads,
  setLeadProviderKey,
  startApifySearch,
  type LeadFinderPlace,
  type LeadFinderUsage,
  type LeadProviderStatus,
} from "./api";
import { DataTable, type Column } from "./components/DataTable";
import { useToast } from "./components/Toast";
import {
  Alert,
  Badge,
  Button,
  EmptyState,
  Kpi,
  KpiGrid,
  KpiSkeleton,
  Segmented,
} from "./components/ui";
import { Compass, Search } from "./components/icons";
import "./styles/views/leadfinder.css";

function fmtLimit(n: number | null | undefined): string {
  return n == null ? "∞" : String(n);
}

// Lead-data providers an org can key up (the AI-provider keys stored through
// the same endpoint are managed from their own surfaces, not here).
const DATA_PROVIDERS: { id: string; label: string; blurb: string }[] = [
  {
    id: "google_places",
    label: "Google Places",
    blurb: "Business search — used for the Lead Finder search itself.",
  },
  {
    id: "apollo",
    label: "Apollo.io",
    blurb:
      "Owner name & direct/mobile line, work email, company description, estimated revenue and headcount. Your own Apollo API key; lookups spend your Apollo credits.",
  },
  {
    id: "apify",
    label: "Apify (Google Maps scraper)",
    blurb:
      "Alternate search source: scrapes Google Maps on your own Apify account (API token from console.apify.com). Runs spend your Apify credits, not your monthly search quota.",
  },
  {
    id: "hunter",
    label: "Hunter",
    blurb: "Extra work-email candidates found for the business's domain.",
  },
  {
    id: "zerobounce",
    label: "ZeroBounce",
    blurb: "Email verification verdicts (valid / risky / invalid).",
  },
];

function ProviderKeysCard() {
  const toast = useToast();
  const [statuses, setStatuses] = useState<LeadProviderStatus[] | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = () =>
    listLeadProviders()
      .then(setStatuses)
      .catch(() => setStatuses(null));

  useEffect(() => {
    void refresh();
  }, []);

  if (statuses === null) return null;
  const statusFor = (id: string) => statuses.find((s) => s.provider === id);

  const save = async () => {
    if (!editing || key.trim().length < 8 || busy) return;
    setBusy(true);
    try {
      await setLeadProviderKey(editing, key.trim());
      toast("Provider key saved", "ok");
      setEditing(null);
      setKey("");
      await refresh();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const clear = async (id: string) => {
    setBusy(true);
    try {
      await deleteLeadProviderKey(id);
      toast("Provider key removed", "ok");
      await refresh();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="lf-providers">
      <h2 className="lf-providers-title">Data providers</h2>
      <p className="lf-sub">
        Enrichment beyond the business's own website — owner contact info,
        revenue and headcount — comes from providers you connect with your
        organization's own API keys. Keys are stored encrypted and never shown
        again.
      </p>
      <ul className="lf-provider-list">
        {DATA_PROVIDERS.map((p) => {
          const s = statusFor(p.id);
          return (
            <li key={p.id} className="lf-provider-row">
              <div className="lf-provider-info">
                <span className="lf-provider-name">
                  {p.label}{" "}
                  {s?.configured ? (
                    <Badge tone="ok">
                      {s.source === "organization" ? "connected" : "platform key"}
                    </Badge>
                  ) : (
                    <Badge tone="neutral">not connected</Badge>
                  )}
                </span>
                <span className="lf-provider-blurb">{p.blurb}</span>
              </div>
              {editing === p.id ? (
                <form
                  className="lf-provider-edit"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void save();
                  }}
                >
                  <input
                    type="password"
                    value={key}
                    onChange={(e) => setKey(e.target.value)}
                    placeholder={`${p.label} API key`}
                    aria-label={`${p.label} API key`}
                    autoFocus
                  />
                  <Button type="submit" size="sm" busy={busy} disabled={key.trim().length < 8}>
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
                <div className="lf-provider-actions">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setEditing(p.id);
                      setKey("");
                    }}
                  >
                    {s?.source === "organization" ? "Replace key" : "Add key"}
                  </Button>
                  {s?.source === "organization" && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      onClick={() => void clear(p.id)}
                    >
                      Remove
                    </Button>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function LeadFinderView({ isAdmin = false }: { isAdmin?: boolean }) {
  const toast = useToast();
  // Day-one setup cue: with no Places key resolvable (org OR platform),
  // every search 503s — surface the fix up front instead of a dead end.
  const [placesReady, setPlacesReady] = useState<boolean | null>(null);
  const [query, setQuery] = useState("");
  const [location, setLocation] = useState("");
  const [maxResults, setMaxResults] = useState(20);
  const [minRating, setMinRating] = useState(0); // 0 = any (Places-side)
  // Search source: the metered Places API, or the org's own Apify scraper
  // (BYO token; async runs, results take a minute or two).
  const [source, setSource] = useState<"places" | "apify">("places");
  const [apifyMax, setApifyMax] = useState(60);
  const [searching, setSearching] = useState(false);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const [searchNote, setSearchNote] = useState<string | null>(null);
  // Apify-run progress line (its own state so it never renders under the
  // quota-clamp "Partial results" alert).
  const [scrapeNote, setScrapeNote] = useState<string | null>(null);
  const [searchId, setSearchId] = useState<string | null>(null);
  const [results, setResults] = useState<LeadFinderPlace[] | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);
  const [usage, setUsage] = useState<LeadFinderUsage | null>(null);
  // Result filters — Lead Finder's own, applied client-side to this search.
  const [fltCategory, setFltCategory] = useState("");
  const [fltHasPhone, setFltHasPhone] = useState(false);
  const [fltHasWebsite, setFltHasWebsite] = useState(false);
  const [fltHideInCrm, setFltHideInCrm] = useState(false);
  const [fltMinRating, setFltMinRating] = useState(0);

  useEffect(() => {
    let alive = true;
    getLeadFinderUsage()
      .then((u) => alive && setUsage(u))
      .catch(() => undefined); // the strip is informative, never blocking
    listLeadProviders()
      .then((ps) => {
        if (!alive) return;
        const gp = ps.find((p) => p.provider === "google_places");
        setPlacesReady(gp ? gp.configured : null);
      })
      .catch(() => undefined); // banner is informative, never blocking
    return () => {
      alive = false;
    };
  }, []);

  // Client-side filters over the current search's results.
  const filtered = useMemo(() => {
    let rows = results ?? [];
    if (fltCategory) rows = rows.filter((r) => r.types.includes(fltCategory));
    if (fltHasPhone) rows = rows.filter((r) => !!r.phone);
    if (fltHasWebsite) rows = rows.filter((r) => !!r.website);
    if (fltHideInCrm) rows = rows.filter((r) => !r.in_crm);
    if (fltMinRating > 0)
      rows = rows.filter((r) => (r.rating ?? 0) >= fltMinRating);
    return rows;
  }, [results, fltCategory, fltHasPhone, fltHasWebsite, fltHideInCrm, fltMinRating]);

  const categories = useMemo(() => {
    const seen = new Set<string>();
    for (const r of results ?? []) for (const t of r.types) seen.add(t);
    return [...seen].sort();
  }, [results]);

  const filtersActive =
    !!fltCategory || fltHasPhone || fltHasWebsite || fltHideInCrm || fltMinRating > 0;

  const clearFilters = () => {
    setFltCategory("");
    setFltHasPhone(false);
    setFltHasWebsite(false);
    setFltHideInCrm(false);
    setFltMinRating(0);
  };

  const importable = useMemo(
    () => filtered.filter((r) => !r.in_crm),
    [filtered]
  );

  const applyResults = (searchIdIn: string, rows: LeadFinderPlace[]) => {
    setSearchId(searchIdIn);
    setResults(rows);
    clearFilters();
    setChecked(new Set(rows.filter((p) => !p.in_crm).map((p) => p.place_id)));
  };

  // Apify runs are async on Apify's side — poll every 5s, cap at 10 minutes.
  const APIFY_POLL_MS = 5000;
  const APIFY_POLL_MAX = 120;
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

  const runApifySearch = async () => {
    const r = await startApifySearch(
      query.trim(),
      location.trim() || undefined,
      apifyMax
    );
    setScrapeNote(
      "Scraping Google Maps on your Apify account — this usually takes a minute or two…"
    );
    try {
      for (let i = 0; i < APIFY_POLL_MAX; i++) {
        await sleep(APIFY_POLL_MS);
        const p = await pollApifySearch(r.search_id, r.run_id);
        if (p.status === "SUCCEEDED" && p.results) {
          applyResults(p.search_id, p.results);
          return;
        }
      }
      throw new Error(
        "The Apify run is taking longer than 10 minutes — check it at console.apify.com and try again."
      );
    } finally {
      setScrapeNote(null);
    }
  };

  const runSearch = async () => {
    if (query.trim().length < 2 || searching) return;
    setSearching(true);
    setSearchErr(null);
    setSearchNote(null);
    try {
      if (source === "apify") {
        await runApifySearch();
        return;
      }
      const r = await searchLeads(query.trim(), location.trim() || undefined, {
        maxResults,
        minRating: minRating > 0 ? minRating : undefined,
      });
      applyResults(r.search_id, r.results);
      setUsage((u) => (u ? { ...u, searches: r.usage } : u));
      if (r.quota_clamped)
        setSearchNote(
          "Your monthly search quota didn't cover the full request — showing what it allowed."
        );
    } catch (e) {
      setSearchErr((e as Error).message);
      setSearchNote(null);
    } finally {
      setSearching(false);
    }
  };

  const runImport = async () => {
    if (!searchId || !results || checked.size === 0 || importing) return;
    setImporting(true);
    try {
      const selected = results.filter((p) => checked.has(p.place_id) && !p.in_crm);
      const houseId = (await getHouseClient()).client_id;
      const r = await importLeads(searchId, houseId, selected);
      toast(
        r.created > 0
          ? `Imported ${r.created} lead${r.created === 1 ? "" : "s"} into the CRM — verifying emails in the background`
          : "Nothing new to import — those businesses are already in your CRM",
        r.created > 0 ? "ok" : "info"
      );
      // Reflect the import inline instead of forcing a re-search.
      setResults(
        results.map((p) =>
          checked.has(p.place_id) ? { ...p, in_crm: true } : p
        )
      );
      setChecked(new Set());
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setImporting(false);
    }
  };

  const toggle = (placeId: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(placeId)) next.delete(placeId);
      else next.add(placeId);
      return next;
    });
  };

  const allChecked = importable.length > 0 && importable.every((p) => checked.has(p.place_id));

  const columns: Column<LeadFinderPlace>[] = [
    {
      key: "pick",
      header: (
        <input
          type="checkbox"
          aria-label="Select all importable results"
          checked={allChecked}
          onChange={() =>
            setChecked(
              allChecked ? new Set() : new Set(importable.map((p) => p.place_id))
            )
          }
        />
      ),
      render: (p) =>
        p.in_crm ? (
          <Badge tone="info">in CRM</Badge>
        ) : (
          <input
            type="checkbox"
            aria-label={`Select ${p.name}`}
            checked={checked.has(p.place_id)}
            onChange={() => toggle(p.place_id)}
            onClick={(e) => e.stopPropagation()}
          />
        ),
    },
    {
      key: "name",
      header: "Business",
      render: (p) => (
        <div className="lf-biz">
          <span className="lf-biz-name">{p.name}</span>
          {p.address && <span className="lf-biz-addr">{p.address}</span>}
        </div>
      ),
      sortValue: (p) => p.name.toLowerCase(),
    },
    {
      key: "category",
      header: "Category",
      render: (p) =>
        p.types[0] ? (
          <Badge tone="neutral">{p.types[0].replace(/_/g, " ")}</Badge>
        ) : (
          "—"
        ),
    },
    { key: "phone", header: "Phone", render: (p) => p.phone ?? "—" },
    {
      key: "website",
      header: "Website",
      render: (p) =>
        p.website ? (
          <a
            href={p.website}
            target="_blank"
            rel="noreferrer"
            onClick={(e) => e.stopPropagation()}
          >
            {p.website.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")}
          </a>
        ) : (
          "—"
        ),
    },
    {
      key: "rating",
      header: "Rating",
      align: "right",
      render: (p) => (p.rating != null ? p.rating.toFixed(1) : "—"),
      sortValue: (p) => p.rating ?? -1,
    },
  ];

  return (
    <section className="lf">
      <div className="lf-head">
        <p className="lf-sub">
          Find businesses by vertical and geography, then import them as leads
          in your CRM. Imported leads are enriched automatically: the owner is
          identified from the business's own website (or your connected data
          provider) so the lead is a person — with the business name kept
          alongside — and emails are verified.
        </p>
      </div>

      {placesReady === false && source === "places" && (
        <Alert tone="warn" title="Lead Finder needs a Google Places key">
          No Google Places API key is configured, so searches can't run yet.
          {isAdmin ? (
            <>
              {" "}Add your key under <strong>Data providers</strong> at the
              bottom of this page (Google Cloud Console → enable "Places API
              (New)" → create an API key) — or connect an Apify token and
              switch the search source to the Apify scraper.
            </>
          ) : (
            <> Ask an org admin to add one under Data providers.</>
          )}
        </Alert>
      )}

      {usage ? (
        <KpiGrid>
          <Kpi
            label="Searches this month"
            value={`${usage.searches.used} / ${fmtLimit(usage.searches.limit)}`}
          />
          <Kpi
            label="Email verifications this month"
            value={`${usage.verifications.used} / ${fmtLimit(usage.verifications.limit)}`}
          />
        </KpiGrid>
      ) : (
        <KpiGrid>
          <KpiSkeleton />
          <KpiSkeleton />
        </KpiGrid>
      )}

      <div className="lf-source-row">
        <Segmented<"places" | "apify">
          options={[
            { value: "places", label: "Google Places" },
            { value: "apify", label: "Apify scraper" },
          ]}
          value={source}
          onChange={setSource}
          ariaLabel="Search source"
        />
        {source === "apify" && (
          <span className="lf-source-hint">
            Runs on your own Apify account (token under Data providers) —
            results take a minute or two and don’t use your monthly search
            quota.
          </span>
        )}
      </div>

      <form
        className="lf-search"
        onSubmit={(e) => {
          e.preventDefault();
          void runSearch();
        }}
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Business type or keyword — e.g. HVAC contractors"
          aria-label="Business type or keyword"
        />
        <input
          value={location}
          onChange={(e) => setLocation(e.target.value)}
          placeholder="City / area — e.g. Scottsdale AZ"
          aria-label="Location"
        />
        {source === "places" ? (
          <select
            value={maxResults}
            onChange={(e) => setMaxResults(Number(e.target.value))}
            aria-label="Results per search"
            title="Each page of 20 results is one search against your monthly quota"
          >
            <option value={20}>20 results · 1 search</option>
            <option value={40}>40 results · 2 searches</option>
            <option value={60}>60 results · 3 searches</option>
          </select>
        ) : (
          <select
            value={apifyMax}
            onChange={(e) => setApifyMax(Number(e.target.value))}
            aria-label="Places to scrape"
            title="Scraped on your own Apify account — spends Apify credits, not your monthly quota"
          >
            <option value={20}>Up to 20 places</option>
            <option value={60}>Up to 60 places</option>
            <option value={100}>Up to 100 places</option>
            <option value={200}>Up to 200 places</option>
          </select>
        )}
        {source === "places" && (
          <select
            value={minRating}
            onChange={(e) => setMinRating(Number(e.target.value))}
            aria-label="Minimum Google rating"
          >
            <option value={0}>Any rating</option>
            <option value={3.5}>3.5+ stars</option>
            <option value={4}>4.0+ stars</option>
            <option value={4.5}>4.5+ stars</option>
          </select>
        )}
        <Button type="submit" busy={searching} disabled={query.trim().length < 2}>
          <Search size={16} aria-hidden="true" /> Search
        </Button>
      </form>

      {searchErr && (
        <Alert tone="danger" title="Search failed">
          {searchErr}
        </Alert>
      )}
      {searchNote && (
        <Alert tone="warn" title="Partial results">
          {searchNote}
        </Alert>
      )}
      {scrapeNote && (
        <Alert tone="info" title="Scrape in progress">
          {scrapeNote}
        </Alert>
      )}

      {results !== null && (
        <>
          <div className="lf-filterbar" role="group" aria-label="Filter results">
            <select
              value={fltCategory}
              onChange={(e) => setFltCategory(e.target.value)}
              aria-label="Filter by category"
            >
              <option value="">All categories</option>
              {categories.map((t) => (
                <option key={t} value={t}>
                  {t.replace(/_/g, " ")}
                </option>
              ))}
            </select>
            <select
              value={fltMinRating}
              onChange={(e) => setFltMinRating(Number(e.target.value))}
              aria-label="Filter by rating"
            >
              <option value={0}>Any rating</option>
              <option value={3.5}>3.5+ stars</option>
              <option value={4}>4.0+ stars</option>
              <option value={4.5}>4.5+ stars</option>
            </select>
            <label className="lf-flt-check">
              <input
                type="checkbox"
                checked={fltHasPhone}
                onChange={(e) => setFltHasPhone(e.target.checked)}
              />
              Has phone
            </label>
            <label className="lf-flt-check">
              <input
                type="checkbox"
                checked={fltHasWebsite}
                onChange={(e) => setFltHasWebsite(e.target.checked)}
              />
              Has website
            </label>
            <label className="lf-flt-check">
              <input
                type="checkbox"
                checked={fltHideInCrm}
                onChange={(e) => setFltHideInCrm(e.target.checked)}
              />
              Hide already in CRM
            </label>
            {filtersActive && (
              <Button variant="ghost" size="sm" onClick={clearFilters}>
                Clear filters
              </Button>
            )}
          </div>
          <div className="lf-actions">
            <span className="lf-count">
              {filtered.length} result{filtered.length === 1 ? "" : "s"}
              {filtersActive && ` (of ${results.length})`}
              {checked.size > 0 && ` · ${checked.size} selected`}
            </span>
            <Button
              onClick={() => void runImport()}
              busy={importing}
              disabled={checked.size === 0}
            >
              Import selected into CRM
            </Button>
          </div>
          <DataTable<LeadFinderPlace>
            columns={columns}
            rows={filtered}
            rowKey={(p) => p.place_id}
            caption="Lead Finder search results"
            emptyMessage={
              filtersActive
                ? "No results match the current filters."
                : "No businesses matched that search."
            }
          />
        </>
      )}

      {results === null && !searchErr && (
        <EmptyState
          hero
          icon={<Compass size={28} aria-hidden="true" />}
          title="Find your next clients"
        >
          Search a vertical and a location — results come from the licensed
          Google Places API, or from the Apify Google Maps scraper running on
          your own Apify account. Businesses already in your CRM are flagged
          so you don't import them twice.
        </EmptyState>
      )}

      {isAdmin && <ProviderKeysCard />}
    </section>
  );
}
