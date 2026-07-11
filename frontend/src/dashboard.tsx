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
import { api, TEAM_ROLES, type Session } from "./api";
import { Button, EmptyState, Skeleton } from "./components/ui";
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
  // Which widget is mid-drag; live-updated as it passes over siblings so the
  // reorder animates in place (standard sortable pattern).
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
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
