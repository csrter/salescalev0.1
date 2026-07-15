/**
 * Phase 4 customizable dashboard (UI-revamp).
 *
 * A real widget system, not a fixed grid: widgets are addable, removable,
 * resizable (drag the corner handle, in 12-col × row units), and rearrangeable
 * (drag the 6-dot grip in the widget header). The resulting layout is saved per
 * user per client view via /api/dashboard/layout. No saved layout means the
 * role default below.
 *
 * PERSISTENCE INVARIANT (do not change): the round-trip shape is
 * `{ widgets: WidgetSlot[] }` where WidgetSlot = { type, w, h }. Drag reorder
 * splices the slot array; pointer resize snaps to 12-col × 120px-row units read
 * off grid.clientWidth; keyboard Arrange mode mutates the same array and PUTs
 * the identical shape. COLS/ROW_PX/GAP_PX below are shared by the resize math
 * and the CSS grid (.dash-grid) — keep them in lockstep.
 *
 * The platform filter is owned by the page (one toggle governs every widget)
 * and passed down; see widgets.tsx for how each widget honors it.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  TEAM_ROLES,
  type AdAccount,
  type Campaign,
  type Session,
} from "./api";
import {
  Button,
  EmptyState,
  PlatformChip,
  Segmented,
  Skeleton,
  SkeletonText,
} from "./components/ui";
import { GripVertical, Plus, RefreshCw, X } from "./components/icons";
import {
  BenchmarkWidget,
  CampaignTableWidget,
  ChannelMixWidget,
  ConversionHealthWidget,
  FatigueWidget,
  FunnelTiersWidget,
  GuaranteeWidget,
  OverviewWidget,
  QualityWidget,
  ReconciliationWidget,
  SpendPacingWidget,
  UtmBuilderWidget,
  type PlatformFilter,
  type WidgetProps,
} from "./widgets";
import "./styles/views/dashboard.css";

interface WidgetSlot {
  type: string;
  w: number; // grid columns, 1–12
  h: number; // grid rows (ROW_PX each), 1–6
}

interface WidgetDef {
  title: string;
  component: (props: WidgetProps) => React.ReactNode;
  teamOnly?: boolean;
  minW: number;
  minH: number;
}

export const WIDGET_REGISTRY: Record<string, WidgetDef> = {
  overview: { title: "Blended performance", component: OverviewWidget, minW: 6, minH: 1 },
  channel_mix: { title: "Channel mix", component: ChannelMixWidget, minW: 5, minH: 2 },
  spend_pacing: { title: "Spend & pacing", component: SpendPacingWidget, minW: 4, minH: 2 },
  funnel_tiers: { title: "Funnel tiers", component: FunnelTiersWidget, minW: 4, minH: 2 },
  guarantee: { title: "Guarantee tracker", component: GuaranteeWidget, minW: 4, minH: 2 },
  fatigue: { title: "Creative fatigue (Meta)", component: FatigueWidget, minW: 4, minH: 1 },
  quality: { title: "Quality alerts (Google)", component: QualityWidget, minW: 4, minH: 1 },
  reconciliation: { title: "Attribution discrepancies", component: ReconciliationWidget, minW: 4, minH: 2 },
  campaigns: { title: "Campaigns (all platforms)", component: CampaignTableWidget, minW: 6, minH: 2 },
  benchmark: { title: "Vertical benchmark", component: BenchmarkWidget, teamOnly: true, minW: 4, minH: 1 },
  conversion_health: { title: "Conversion tracking (server-side)", component: ConversionHealthWidget, teamOnly: true, minW: 6, minH: 2 },
  utm_builder: { title: "UTM builder", component: UtmBuilderWidget, teamOnly: true, minW: 6, minH: 2 },
};

const TEAM_DEFAULT: WidgetSlot[] = [
  { type: "overview", w: 12, h: 1 },
  { type: "guarantee", w: 5, h: 2 },
  { type: "spend_pacing", w: 7, h: 2 },
  { type: "channel_mix", w: 7, h: 2 },
  { type: "funnel_tiers", w: 5, h: 2 },
  { type: "fatigue", w: 6, h: 2 },
  { type: "quality", w: 6, h: 2 },
  { type: "reconciliation", w: 6, h: 2 },
  { type: "benchmark", w: 6, h: 2 },
  { type: "conversion_health", w: 12, h: 2 },
  { type: "campaigns", w: 12, h: 2 },
  { type: "utm_builder", w: 12, h: 2 },
];

const CLIENT_DEFAULT: WidgetSlot[] = [
  { type: "overview", w: 12, h: 1 },
  { type: "guarantee", w: 5, h: 2 },
  { type: "spend_pacing", w: 7, h: 2 },
  { type: "channel_mix", w: 7, h: 2 },
  { type: "funnel_tiers", w: 5, h: 2 },
  { type: "reconciliation", w: 12, h: 2 },
];

const COLS = 12;
const ROW_PX = 120;
const GAP_PX = 12;
const MAX_H = 6;

const clamp = (v: number, lo: number, hi: number) =>
  Math.min(Math.max(v, lo), hi);

// --- Timeframe + account/campaign spend filter -----------------------------
// Owned by the page (one control set governs every widget), same pattern as
// the platform toggle — persisted alongside the widget layout via
// /api/dashboard/filters (see PersistedFilters below).

type TimeframePreset = "today" | "7d" | "30d" | "90d" | "custom";

// Field names match DashboardFiltersIn on the wire (backend/app/api/
// dashboard.py) since this object round-trips through PUT/GET verbatim —
// same convention as StageChangeBody in api.ts.
interface DashFilters {
  preset: TimeframePreset;
  since: string | null; // only meaningful (and only persisted) for "custom"
  until: string | null;
  account_ids: string[];
  campaign_ids: string[];
}

const DEFAULT_FILTERS: DashFilters = {
  preset: "30d",
  since: null,
  until: null,
  account_ids: [],
  campaign_ids: [],
};

const isoDate = (d: Date) => d.toISOString().slice(0, 10);

const PRESET_LABEL: Record<TimeframePreset, string> = {
  today: "Today",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  "90d": "Last 90 days",
  custom: "Custom",
};

/** Resolve a preset to concrete since/until dates, recomputed from "today"
 * on every load so a saved "7d" preset never shows a stale window. */
function resolveRange(f: DashFilters): { since: string; until: string; label: string } {
  const today = new Date();
  const until = isoDate(today);
  if (f.preset === "custom") {
    const since = f.since ?? isoDate(new Date(today.getTime() - 29 * 86_400_000));
    const u = f.until ?? until;
    return { since, until: u, label: since === u ? since : `${since} – ${u}` };
  }
  const daysBack = { today: 0, "7d": 6, "30d": 29, "90d": 89 }[f.preset];
  const sinceDate = new Date(today);
  sinceDate.setDate(sinceDate.getDate() - daysBack);
  return { since: isoDate(sinceDate), until, label: PRESET_LABEL[f.preset] };
}

export function Dashboard({
  clientId,
  session,
  platforms,
}: {
  clientId: string;
  session: Session;
  platforms: PlatformFilter;
}) {
  const isTeam = TEAM_ROLES.includes(session.role);
  const allowed = Object.entries(WIDGET_REGISTRY).filter(
    ([, def]) => isTeam || !def.teamOnly,
  );
  const [widgets, setWidgets] = useState<WidgetSlot[] | null>(null);
  const [saveState, setSaveState] = useState<"saved" | "saving" | "error">("saved");
  const [refresh, setRefresh] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [syncNote, setSyncNote] = useState<string | null>(null);
  const [arrange, setArrange] = useState(false);
  const [announce, setAnnounce] = useState("");
  const [filters, setFilters] = useState<DashFilters>(DEFAULT_FILTERS);
  // Which widget is mid-drag; live-updated as it passes over siblings so the
  // reorder animates in place (standard sortable pattern).
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const filtersSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setWidgets(null);
    api<{ widgets: WidgetSlot[] | null }>(
      `/api/dashboard/layout?client_id=${clientId}`,
    )
      .then((r) =>
        setWidgets(
          (r.widgets ?? (isTeam ? TEAM_DEFAULT : CLIENT_DEFAULT)).filter(
            (w) => w.type in WIDGET_REGISTRY,
          ),
        ),
      )
      .catch(() => setWidgets(isTeam ? TEAM_DEFAULT : CLIENT_DEFAULT));
  }, [clientId, isTeam]);

  useEffect(() => {
    setFilters(DEFAULT_FILTERS);
    api<{ filters: Partial<DashFilters> | null }>(
      `/api/dashboard/filters?client_id=${clientId}`,
    )
      .then((r) => {
        if (r.filters) setFilters({ ...DEFAULT_FILTERS, ...r.filters });
      })
      .catch(() => {});
  }, [clientId]);

  const persist = useCallback(
    (next: WidgetSlot[]) => {
      setSaveState("saving");
      if (saveTimer.current) clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(() => {
        api(`/api/dashboard/layout?client_id=${clientId}`, {
          method: "PUT",
          body: JSON.stringify({ widgets: next }),
        })
          .then(() => setSaveState("saved"))
          .catch(() => setSaveState("error"));
      }, 500);
    },
    [clientId],
  );

  const update = (next: WidgetSlot[]) => {
    setWidgets(next);
    persist(next);
  };

  const updateFilters = (next: DashFilters) => {
    setFilters(next);
    if (filtersSaveTimer.current) clearTimeout(filtersSaveTimer.current);
    filtersSaveTimer.current = setTimeout(() => {
      api(`/api/dashboard/filters?client_id=${clientId}`, {
        method: "PUT",
        body: JSON.stringify(next),
      }).catch(() => {});
    }, 500);
  };

  const range = resolveRange(filters);

  const sync = async () => {
    setSyncing(true);
    setSyncNote(null);
    try {
      const resp = await api<{ results: any[] }>(
        `/api/insights/sync?client_id=${clientId}`,
        { method: "POST" },
      );
      const failures = resp.results.filter((r) => !r.ok);
      setSyncNote(
        failures.length
          ? `Synced with issues: ${failures
              .map((f) => `${f.platform}: ${f.error}`)
              .join("; ")}`
          : `Synced ${resp.results.length} account(s)`,
      );
      setRefresh((b) => b + 1);
    } catch (e) {
      setSyncNote((e as Error).message);
    } finally {
      setSyncing(false);
    }
  };

  // Keyboard Arrange mode: mutate the SAME slot array and persist the identical
  // shape. Left/Up move a widget earlier, Right/Down later; Shift+←/→ resize
  // width, Shift+↑/↓ resize height.
  const arrangeKey = (e: React.KeyboardEvent, i: number) => {
    if (!widgets) return;
    if (e.key === "Escape") {
      setArrange(false);
      return;
    }
    const def = WIDGET_REGISTRY[widgets[i].type];
    const arrows = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"];
    if (!arrows.includes(e.key)) return;
    e.preventDefault();
    if (e.shiftKey) {
      const slot = widgets[i];
      let { w, h } = slot;
      if (e.key === "ArrowLeft") w = clamp(w - 1, def.minW, COLS);
      if (e.key === "ArrowRight") w = clamp(w + 1, def.minW, COLS);
      if (e.key === "ArrowUp") h = clamp(h - 1, def.minH, MAX_H);
      if (e.key === "ArrowDown") h = clamp(h + 1, def.minH, MAX_H);
      const next = widgets.map((s, j) => (j === i ? { ...s, w, h } : s));
      update(next);
      setAnnounce(`${def.title} resized to ${w} by ${h}`);
    } else {
      const delta =
        e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 1;
      const j = clamp(i + delta, 0, widgets.length - 1);
      if (j === i) return;
      const next = [...widgets];
      const [moved] = next.splice(i, 1);
      next.splice(j, 0, moved);
      update(next);
      setAnnounce(`${def.title} moved to position ${j + 1} of ${next.length}`);
    }
  };

  if (widgets === null)
    return (
      <section className="dash">
        <div className="dash-grid">
          {[12, 5, 7, 7, 5].map((w, i) => (
            <div
              key={i}
              className="card dash-widget"
              style={{ gridColumn: `span ${w}`, gridRow: "span 2" }}
            >
              <div className="dash-widget-body">
                <Skeleton height="0.8em" width="40%" />
                <Skeleton height="2.2em" />
              </div>
            </div>
          ))}
        </div>
      </section>
    );

  const present = new Set(widgets.map((w) => w.type));
  const addable = allowed.filter(([type]) => !present.has(type));

  return (
    <section className="dash">
      <div className="dash-toolbar">
        <h3>Dashboard</h3>
        <span className={`dash-status ${saveState === "error" ? "dash-status--error" : ""}`.trim()} aria-live="polite">
          {saveState === "saving"
            ? "saving layout…"
            : saveState === "error"
              ? "layout save failed"
              : "layout saved"}
        </span>
        {syncNote && <span className="dash-note">{syncNote}</span>}
        <span className="dash-actions">
          <TimeframeControl
            filters={filters}
            onChange={updateFilters}
          />
          <AccountCampaignFilter
            clientId={clientId}
            accountIds={filters.account_ids}
            campaignIds={filters.campaign_ids}
            onChange={(accountIds, campaignIds) =>
              updateFilters({
                ...filters,
                account_ids: accountIds,
                campaign_ids: campaignIds,
              })
            }
          />
          {isTeam && (
            <Button variant="ghost" size="sm" onClick={sync} busy={syncing}>
              <RefreshCw size={16} aria-hidden="true" /> Sync insights
            </Button>
          )}
          <Button
            variant={arrange ? "primary" : "default"}
            size="sm"
            aria-pressed={arrange}
            onClick={() => {
              setArrange((a) => !a);
              setAnnounce(
                arrange ? "Arrange mode off" : "Arrange mode on — use arrows to move, Shift+arrows to resize",
              );
            }}
          >
            {arrange ? "Done" : "Arrange"}
          </Button>
          <AddWidgetMenu
            addable={addable}
            onAdd={(type) => {
              const def = WIDGET_REGISTRY[type];
              update([
                ...widgets,
                { type, w: Math.max(def.minW, 6), h: Math.max(def.minH, 2) },
              ]);
            }}
          />
        </span>
      </div>
      {arrange && (
        <p className="dash-arrange-hint">
          Arrange mode: focus a widget, then arrows to reorder, Shift+arrows to
          resize, Esc to exit.
        </p>
      )}
      <div
        className={`dash-grid ${arrange ? "dash-grid--arrange" : ""}`.trim()}
        ref={gridRef}
      >
        {widgets.map((slot, i) => {
          const Body = WIDGET_REGISTRY[slot.type].component;
          return (
            <WidgetCard
              key={slot.type}
              slot={slot}
              index={i}
              count={widgets.length}
              arrange={arrange}
              dragging={dragIndex === i}
              gridRef={gridRef}
              onArrangeKey={(e) => arrangeKey(e, i)}
              onDragStart={() => setDragIndex(i)}
              onDragEnter={() => {
                if (dragIndex === null || dragIndex === i) return;
                const next = [...widgets];
                const [moved] = next.splice(dragIndex, 1);
                next.splice(i, 0, moved);
                setWidgets(next);
                setDragIndex(i);
              }}
              onDragEnd={() => {
                setDragIndex(null);
                persist(widgets);
              }}
              onResize={(w, h, done) => {
                const next = widgets.map((s, j) => (j === i ? { ...s, w, h } : s));
                setWidgets(next);
                if (done) persist(next);
              }}
              onRemove={() => update(widgets.filter((_, j) => j !== i))}
            >
              <Body
                clientId={clientId}
                session={session}
                platforms={platforms}
                refresh={refresh}
                since={range.since}
                until={range.until}
                rangeLabel={range.label}
                accountIds={filters.account_ids}
                campaignIds={filters.campaign_ids}
              />
            </WidgetCard>
          );
        })}
      </div>
      {widgets.length === 0 && (
        <EmptyState title="An empty canvas" hero>
          Use “Add widget” above to build this view — every widget is
          drag-to-rearrange and resizable, and the layout saves per user.
        </EmptyState>
      )}
      <div className="visually-hidden" aria-live="polite">
        {announce}
      </div>
    </section>
  );
}

function AddWidgetMenu({
  addable,
  onAdd,
}: {
  addable: [string, WidgetDef][];
  onAdd: (type: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span className="dash-add" ref={ref}>
      <Button
        variant="default"
        size="sm"
        disabled={!addable.length}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <Plus size={16} aria-hidden="true" /> Add widget
      </Button>
      {open && (
        <div className="dash-add-menu" role="menu">
          {addable.map(([type, def]) => (
            <button
              key={type}
              type="button"
              role="menuitem"
              className="dash-add-item"
              onClick={() => {
                setOpen(false);
                onAdd(type);
              }}
            >
              {def.title}
            </button>
          ))}
        </div>
      )}
    </span>
  );
}

function TimeframeControl({
  filters,
  onChange,
}: {
  filters: DashFilters;
  onChange: (next: DashFilters) => void;
}) {
  const today = isoDate(new Date());
  const setPreset = (preset: TimeframePreset) => {
    if (preset === "custom") {
      const r = resolveRange({ ...filters, preset: "custom" });
      onChange({ ...filters, preset, since: filters.since ?? r.since, until: filters.until ?? r.until });
    } else {
      onChange({ ...filters, preset, since: null, until: null });
    }
  };
  return (
    <span className="dash-timeframe">
      <Segmented<TimeframePreset>
        ariaLabel="Timeframe"
        value={filters.preset}
        onChange={setPreset}
        options={[
          { value: "today", label: "Today" },
          { value: "7d", label: "7d" },
          { value: "30d", label: "30d" },
          { value: "90d", label: "90d" },
          { value: "custom", label: "Custom" },
        ]}
      />
      {filters.preset === "custom" && (
        <span className="dash-timeframe-custom">
          <input
            className="input dash-date-input"
            type="date"
            aria-label="Since"
            value={filters.since ?? ""}
            max={filters.until ?? today}
            onChange={(e) => onChange({ ...filters, since: e.target.value })}
          />
          <span aria-hidden="true">–</span>
          <input
            className="input dash-date-input"
            type="date"
            aria-label="Until"
            value={filters.until ?? ""}
            min={filters.since ?? undefined}
            max={today}
            onChange={(e) => onChange({ ...filters, until: e.target.value })}
          />
        </span>
      )}
    </span>
  );
}

function AccountCampaignFilter({
  clientId,
  accountIds,
  campaignIds,
  onChange,
}: {
  clientId: string;
  accountIds: string[];
  campaignIds: string[];
  onChange: (accountIds: string[], campaignIds: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [accounts, setAccounts] = useState<AdAccount[] | null>(null);
  const [campaignsByAccount, setCampaignsByAccount] = useState<
    Record<string, Campaign[]>
  >({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open || accounts !== null) return;
    api<AdAccount[]>(`/api/ad-accounts?client_id=${clientId}`)
      .then(setAccounts)
      .catch(() => setAccounts([]));
  }, [open, accounts, clientId]);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const loadCampaigns = (account: AdAccount) => {
    if (campaignsByAccount[account.id]) return;
    api<Campaign[]>(`/api/ad-accounts/${account.id}/campaigns`)
      .then((cs) =>
        setCampaignsByAccount((prev) => ({ ...prev, [account.id]: cs })),
      )
      .catch(() =>
        setCampaignsByAccount((prev) => ({ ...prev, [account.id]: [] })),
      );
  };

  const toggleExpand = (account: AdAccount) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(account.id)) {
        next.delete(account.id);
      } else {
        next.add(account.id);
        loadCampaigns(account);
      }
      return next;
    });
  };

  const toggleAccount = (a: AdAccount) => {
    const has = accountIds.includes(a.external_id);
    const nextAccounts = has
      ? accountIds.filter((id) => id !== a.external_id)
      : [...accountIds, a.external_id];
    // Selecting a whole account supersedes any of its own campaign-level
    // picks; deselecting it should drop them too, so the two never conflict.
    const ownCampaignIds = new Set(
      (campaignsByAccount[a.id] ?? []).map((c) => c.external_id),
    );
    const nextCampaigns = campaignIds.filter((id) => !ownCampaignIds.has(id));
    onChange(nextAccounts, nextCampaigns);
  };

  const toggleCampaign = (c: Campaign) => {
    const has = campaignIds.includes(c.external_id);
    onChange(
      accountIds,
      has ? campaignIds.filter((id) => id !== c.external_id) : [...campaignIds, c.external_id],
    );
  };

  const count = accountIds.length + campaignIds.length;

  return (
    <span className="dash-add" ref={ref}>
      <Button
        variant="default"
        size="sm"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {count === 0 ? "All accounts" : `${count} selected`}
      </Button>
      {open && (
        <div className="dash-add-menu dash-filter-menu" role="menu">
          {accounts === null && <SkeletonText lines={3} />}
          {accounts?.length === 0 && (
            <p className="dash-muted">No connected ad accounts.</p>
          )}
          {accounts?.map((a) => (
            <div key={a.id} className="dash-filter-row">
              <span className="dash-filter-line">
                <button
                  type="button"
                  className="dash-filter-expand"
                  aria-label={expanded.has(a.id) ? "Collapse campaigns" : "Expand campaigns"}
                  aria-expanded={expanded.has(a.id)}
                  onClick={() => toggleExpand(a)}
                >
                  {expanded.has(a.id) ? "▾" : "▸"}
                </button>
                <label className="mg-check dash-filter-check">
                  <input
                    type="checkbox"
                    checked={accountIds.includes(a.external_id)}
                    onChange={() => toggleAccount(a)}
                  />
                  <PlatformChip name={a.platform} /> {a.name}
                </label>
              </span>
              {expanded.has(a.id) && (
                <div className="dash-filter-sub">
                  {campaignsByAccount[a.id] === undefined && (
                    <SkeletonText lines={2} />
                  )}
                  {campaignsByAccount[a.id]?.length === 0 && (
                    <p className="dash-muted">No campaigns.</p>
                  )}
                  {campaignsByAccount[a.id]?.map((c) => (
                    <label key={c.id} className="mg-check dash-filter-check">
                      <input
                        type="checkbox"
                        checked={campaignIds.includes(c.external_id)}
                        onChange={() => toggleCampaign(c)}
                      />
                      {c.name}
                    </label>
                  ))}
                </div>
              )}
            </div>
          ))}
          {count > 0 && (
            <Button variant="ghost" size="sm" onClick={() => onChange([], [])}>
              Clear filter
            </Button>
          )}
        </div>
      )}
    </span>
  );
}

function WidgetCard({
  slot,
  index,
  count,
  arrange,
  dragging,
  gridRef,
  onArrangeKey,
  onDragStart,
  onDragEnter,
  onDragEnd,
  onResize,
  onRemove,
  children,
}: {
  slot: WidgetSlot;
  index: number;
  count: number;
  arrange: boolean;
  dragging: boolean;
  gridRef: React.RefObject<HTMLDivElement | null>;
  onArrangeKey: (e: React.KeyboardEvent) => void;
  onDragStart: () => void;
  onDragEnter: () => void;
  onDragEnd: () => void;
  onResize: (w: number, h: number, done: boolean) => void;
  onRemove: () => void;
  children: React.ReactNode;
}) {
  const def = WIDGET_REGISTRY[slot.type];

  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const grid = gridRef.current;
    if (!grid) return;
    const cellW = (grid.clientWidth - GAP_PX * (COLS - 1)) / COLS;
    const startX = e.clientX;
    const startY = e.clientY;
    const startW = slot.w;
    const startH = slot.h;
    const move = (ev: PointerEvent) => {
      const dw = Math.round((ev.clientX - startX) / (cellW + GAP_PX));
      const dh = Math.round((ev.clientY - startY) / (ROW_PX + GAP_PX));
      onResize(
        clamp(startW + dw, def.minW, COLS),
        clamp(startH + dh, def.minH, MAX_H),
        false,
      );
    };
    const up = (ev: PointerEvent) => {
      const dw = Math.round((ev.clientX - startX) / (cellW + GAP_PX));
      const dh = Math.round((ev.clientY - startY) / (ROW_PX + GAP_PX));
      onResize(
        clamp(startW + dw, def.minW, COLS),
        clamp(startH + dh, def.minH, MAX_H),
        true,
      );
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <div
      className={[
        "card",
        "dash-widget",
        dragging ? "dragging" : "",
        arrange ? "dash-widget--arrange" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      style={{ gridColumn: `span ${slot.w}`, gridRow: `span ${slot.h}`, position: "relative" }}
      tabIndex={arrange ? 0 : undefined}
      role={arrange ? "button" : undefined}
      aria-label={
        arrange
          ? `${def.title}, position ${index + 1} of ${count}, ${slot.w} by ${slot.h}. Arrows move, Shift+arrows resize.`
          : undefined
      }
      onKeyDown={arrange ? onArrangeKey : undefined}
      onDragOver={(e) => e.preventDefault()}
      onDragEnter={(e) => {
        e.preventDefault();
        onDragEnter();
      }}
      onDrop={(e) => e.preventDefault()}
    >
      <div
        className="dash-widget-head"
        draggable={!arrange}
        onDragStart={(e) => {
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", slot.type);
          onDragStart();
        }}
        onDragEnd={onDragEnd}
        title="Drag to rearrange"
      >
        <span className="dash-grip" aria-hidden="true">
          <GripVertical size={16} />
        </span>
        <h4>{def.title}</h4>
        <Button
          className="dash-widget-remove"
          variant="ghost"
          size="sm"
          aria-label={`Remove ${def.title}`}
          title="Remove widget"
          onClick={onRemove}
        >
          <X size={16} aria-hidden="true" />
        </Button>
      </div>
      <div className="dash-widget-body">{children}</div>
      <div
        className="dash-resize"
        title="Drag to resize"
        aria-hidden="true"
        onPointerDown={startResize}
      >
        <GripVertical size={12} />
      </div>
    </div>
  );
}
