/**
 * Phase 4 customizable dashboard.
 *
 * A real widget system, not a fixed grid: widgets are addable, removable,
 * resizable (drag the corner handle, in 12-col × row units), and
 * rearrangeable (drag the widget header), and the resulting layout is
 * saved per user per client view via /api/dashboard/layout. No saved
 * layout means the role default below.
 *
 * The platform filter is owned by the page (one toggle governs every
 * widget) and passed down; see widgets.tsx for how each widget honors it.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, TEAM_ROLES, type Session } from "./api";
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
  overview: {
    title: "Blended performance",
    component: OverviewWidget,
    minW: 6,
    minH: 1,
  },
  channel_mix: {
    title: "Channel mix",
    component: ChannelMixWidget,
    minW: 5,
    minH: 2,
  },
  spend_pacing: {
    title: "Spend & pacing",
    component: SpendPacingWidget,
    minW: 4,
    minH: 2,
  },
  funnel_tiers: {
    title: "Funnel tiers",
    component: FunnelTiersWidget,
    minW: 4,
    minH: 2,
  },
  guarantee: {
    title: "Guarantee tracker",
    component: GuaranteeWidget,
    minW: 4,
    minH: 2,
  },
  fatigue: {
    title: "Creative fatigue (Meta)",
    component: FatigueWidget,
    minW: 4,
    minH: 1,
  },
  quality: {
    title: "Quality alerts (Google)",
    component: QualityWidget,
    minW: 4,
    minH: 1,
  },
  reconciliation: {
    title: "Attribution discrepancies",
    component: ReconciliationWidget,
    minW: 4,
    minH: 2,
  },
  campaigns: {
    title: "Campaigns (all platforms)",
    component: CampaignTableWidget,
    minW: 6,
    minH: 2,
  },
  benchmark: {
    title: "Vertical benchmark",
    component: BenchmarkWidget,
    teamOnly: true,
    minW: 4,
    minH: 1,
  },
  conversion_health: {
    title: "Conversion tracking (server-side)",
    component: ConversionHealthWidget,
    teamOnly: true,
    minW: 6,
    minH: 2,
  },
  utm_builder: {
    title: "UTM builder",
    component: UtmBuilderWidget,
    teamOnly: true,
    minW: 6,
    minH: 2,
  },
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
    ([, def]) => isTeam || !def.teamOnly
  );
  const [widgets, setWidgets] = useState<WidgetSlot[] | null>(null);
  const [saveState, setSaveState] = useState<"saved" | "saving" | "error">(
    "saved"
  );
  const [refresh, setRefresh] = useState(0);
  const [syncing, setSyncing] = useState(false);
  const [syncNote, setSyncNote] = useState<string | null>(null);
  // Which widget is mid-drag; live-updated as it passes over siblings so
  // the reorder animates in place (standard sortable pattern).
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const gridRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setWidgets(null);
    api<{ widgets: WidgetSlot[] | null }>(
      `/api/dashboard/layout?client_id=${clientId}`
    )
      .then((r) =>
        setWidgets(
          (r.widgets ?? (isTeam ? TEAM_DEFAULT : CLIENT_DEFAULT)).filter(
            (w) => w.type in WIDGET_REGISTRY
          )
        )
      )
      .catch(() => setWidgets(isTeam ? TEAM_DEFAULT : CLIENT_DEFAULT));
  }, [clientId, isTeam]);

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
    [clientId]
  );

  const update = (next: WidgetSlot[]) => {
    setWidgets(next);
    persist(next);
  };

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
      setRefresh((b) => b + 1);
    } catch (e) {
      setSyncNote((e as Error).message);
    } finally {
      setSyncing(false);
    }
  };

  if (widgets === null) return <p className="muted">Loading dashboard…</p>;

  const present = new Set(widgets.map((w) => w.type));
  const addable = allowed.filter(([type]) => !present.has(type));

  return (
    <section className="dashboard">
      <div className="dash-toolbar">
        <h3>Dashboard</h3>
        <span className={`save-state ${saveState}`}>
          {saveState === "saving"
            ? "saving layout…"
            : saveState === "error"
              ? "layout save failed"
              : "layout saved"}
        </span>
        {syncNote && <span className="muted">{syncNote}</span>}
        <span className="dash-actions">
          {isTeam && (
            <button onClick={sync} disabled={syncing}>
              {syncing ? "Syncing…" : "Sync insights"}
            </button>
          )}
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
      <div className="widget-grid" ref={gridRef}>
        {widgets.map((slot, i) => {
          const Body = WIDGET_REGISTRY[slot.type].component;
          return (
            <WidgetCard
              key={slot.type}
              slot={slot}
              dragging={dragIndex === i}
              gridRef={gridRef}
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
                const next = widgets.map((s, j) =>
                  j === i ? { ...s, w, h } : s
                );
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
              />
            </WidgetCard>
          );
        })}
      </div>
      {widgets.length === 0 && (
        <p className="muted">
          Empty dashboard — use “Add widget” to build your view.
        </p>
      )}
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
  return (
    <span className="add-widget">
      <button onClick={() => setOpen(!open)} disabled={!addable.length}>
        + Add widget
      </button>
      {open && (
        <div className="add-widget-menu">
          {addable.map(([type, def]) => (
            <button
              key={type}
              className="link"
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

function WidgetCard({
  slot,
  dragging,
  gridRef,
  onDragStart,
  onDragEnter,
  onDragEnd,
  onResize,
  onRemove,
  children,
}: {
  slot: WidgetSlot;
  dragging: boolean;
  gridRef: React.RefObject<HTMLDivElement | null>;
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
    const clamp = (v: number, lo: number, hi: number) =>
      Math.min(Math.max(v, lo), hi);
    const move = (ev: PointerEvent) => {
      const dw = Math.round((ev.clientX - startX) / (cellW + GAP_PX));
      const dh = Math.round((ev.clientY - startY) / (ROW_PX + GAP_PX));
      onResize(
        clamp(startW + dw, def.minW, COLS),
        clamp(startH + dh, def.minH, MAX_H),
        false
      );
    };
    const up = (ev: PointerEvent) => {
      const dw = Math.round((ev.clientX - startX) / (cellW + GAP_PX));
      const dh = Math.round((ev.clientY - startY) / (ROW_PX + GAP_PX));
      onResize(
        clamp(startW + dw, def.minW, COLS),
        clamp(startH + dh, def.minH, MAX_H),
        true
      );
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <div
      className={`widget-card ${dragging ? "dragging" : ""}`}
      style={{ gridColumn: `span ${slot.w}`, gridRow: `span ${slot.h}` }}
      onDragOver={(e) => e.preventDefault()}
      onDragEnter={(e) => {
        e.preventDefault();
        onDragEnter();
      }}
      onDrop={(e) => e.preventDefault()}
    >
      <div
        className="widget-head"
        draggable
        onDragStart={(e) => {
          e.dataTransfer.effectAllowed = "move";
          e.dataTransfer.setData("text/plain", slot.type);
          onDragStart();
        }}
        onDragEnd={onDragEnd}
        title="Drag to rearrange"
      >
        <span className="drag-dots" aria-hidden>
          ⠿
        </span>
        <h4>{def.title}</h4>
        <button
          className="widget-remove"
          title="Remove widget"
          onClick={onRemove}
        >
          ×
        </button>
      </div>
      <div className="widget-body">{children}</div>
      <div
        className="resize-handle"
        title="Drag to resize"
        onPointerDown={startResize}
      />
    </div>
  );
}
