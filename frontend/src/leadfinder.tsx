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
  getHouseClient,
  getLeadFinderUsage,
  importLeads,
  searchLeads,
  type LeadFinderPlace,
  type LeadFinderUsage,
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
} from "./components/ui";
import { Compass, Search } from "./components/icons";
import "./styles/views/leadfinder.css";

function fmtLimit(n: number | null | undefined): string {
  return n == null ? "∞" : String(n);
}

export function LeadFinderView() {
  const toast = useToast();
  const [query, setQuery] = useState("");
  const [location, setLocation] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchErr, setSearchErr] = useState<string | null>(null);
  const [searchId, setSearchId] = useState<string | null>(null);
  const [results, setResults] = useState<LeadFinderPlace[] | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [importing, setImporting] = useState(false);
  const [usage, setUsage] = useState<LeadFinderUsage | null>(null);

  useEffect(() => {
    let alive = true;
    getLeadFinderUsage()
      .then((u) => alive && setUsage(u))
      .catch(() => undefined); // the strip is informative, never blocking
    return () => {
      alive = false;
    };
  }, []);

  const importable = useMemo(
    () => (results ?? []).filter((r) => !r.in_crm),
    [results]
  );

  const runSearch = async () => {
    if (query.trim().length < 2 || searching) return;
    setSearching(true);
    setSearchErr(null);
    try {
      const r = await searchLeads(query.trim(), location.trim() || undefined);
      setSearchId(r.search_id);
      setResults(r.results);
      setChecked(new Set(r.results.filter((p) => !p.in_crm).map((p) => p.place_id)));
      setUsage((u) => (u ? { ...u, searches: r.usage } : u));
    } catch (e) {
      setSearchErr((e as Error).message);
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
          in your CRM. Imported leads are enriched from the business's own
          website and email-verified automatically.
        </p>
      </div>

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
        <Button type="submit" busy={searching} disabled={query.trim().length < 2}>
          <Search size={16} aria-hidden="true" /> Search
        </Button>
      </form>

      {searchErr && (
        <Alert tone="danger" title="Search failed">
          {searchErr}
        </Alert>
      )}

      {results !== null && (
        <>
          <div className="lf-actions">
            <span className="lf-count">
              {results.length} result{results.length === 1 ? "" : "s"}
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
            rows={results}
            rowKey={(p) => p.place_id}
            caption="Lead Finder search results"
            emptyMessage="No businesses matched that search."
          />
        </>
      )}

      {results === null && !searchErr && (
        <EmptyState
          hero
          icon={<Compass size={28} aria-hidden="true" />}
          title="Find your next clients"
        >
          Search a vertical and a location — results come from Google Places,
          never scraped. Businesses already in your CRM are flagged so you
          don't import them twice.
        </EmptyState>
      )}
    </section>
  );
}
