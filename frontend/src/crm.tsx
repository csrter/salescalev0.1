/**
 * Phase 6 — Salescale CRM view (per client). UI-revamp build (DESIGN.md §7).
 *
 * Team roles get the full workspace: a pipeline kanban (§4.15) with an
 * accessible "Move to stage" keyboard path alongside the HTML5-drag mouse
 * path, the lead list on the shared DataTable, a floating glass contact
 * drawer with Dialog-grade focus semantics (trap, Escape, focus return), and
 * admin setup (stage editor, Organization qualified-lead criteria, native
 * lead-form routing, external CRM sync).
 *
 * Client-role users get the same board and lead list read-only — the backend
 * already excludes internal-only fields and activities, so this component
 * renders what it receives and hides the write controls.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import {
  ADMIN_ROLES,
  TEAM_ROLES,
  api,
  bulkDeleteContacts,
  deleteContact,
  updateContact,
  verifyContacts,
  type Session,
  type VerificationStatus,
} from "./api";
import { DataTable, type Column } from "./components/DataTable";
import { ConfirmDialog, type ReceiptRow } from "./components/Dialog";
import {
  Alert,
  Badge,
  Button,
  EmptyState,
  Field,
  PlatformChip,
  Skeleton,
  SkeletonText,
} from "./components/ui";
import { useToast } from "./components/Toast";
import { ChevronRight, Inbox, Pencil, Plus, Settings, Trash2 } from "./components/icons";
import {
  CsvImportDialog,
  CustomFieldInputs,
  CustomFieldsPanel,
  FieldManager,
  customFieldColumns,
  useCustomFieldDefs,
  type CustomFieldDef,
  type CustomValues,
} from "./crm_custom";
import "./styles/views/crm.css";

// API base (matches api.ts) — used to build the Google lead-form webhook URL
// correctly in every environment (the old :8000 port-rewrite broke in prod).
const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const STALE_DAYS = 14;

interface Stage {
  id: string;
  name: string;
  position: number;
  is_qualified_stage: boolean;
}

interface DealRow {
  id: string;
  contact_id: string;
  stage_id: string;
  name: string;
  value_cents: number | null;
  status: string;
}

interface ContactRow {
  id: string;
  first_name: string | null;
  last_name: string | null;
  email: string | null;
  phone: string | null;
  city: string | null;
  state: string | null;
  company_name: string | null;
  source: string | null;
  qualified_at: string | null;
  created_at: string;
  // Phase 12 — present in team payloads only (verification is agency
  // workflow, never a client-portal field).
  verification_status?: VerificationStatus | null;
  verified_at?: string | null;
  qualification?: Record<string, boolean> | null;
  custom_fields?: CustomValues | null;
  attribution?: {
    platform: string | null;
    utm_source: string | null;
    utm_campaign: string | null;
    has_click_id: boolean;
  } | null;
}

interface Board {
  pipeline: { id: string; name: string };
  stages: Stage[];
  deals_by_stage: Record<string, DealRow[]>;
  won: DealRow[];
  lost: DealRow[];
  contacts: Record<string, ContactRow>;
  read_only: boolean;
}

interface Criterion {
  key: string;
  label: string;
}

// --- formatting helpers ---

const money = (cents?: number | null) =>
  cents == null ? null : `$${(cents / 100).toLocaleString()}`;

const contactName = (c?: ContactRow | null) =>
  c
    ? [c.first_name, c.last_name].filter(Boolean).join(" ") ||
      c.email ||
      c.phone ||
      "Unnamed lead"
    : "Unknown";

const daysSince = (iso: string) =>
  Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);

const absoluteTime = (iso: string) => new Date(iso).toLocaleString();

/** Compact relative time ("3d ago", "just now"); pair with an absolute title. */
function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.round(diff / 1000);
  if (s < 45) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.round(h / 24);
  if (d < 30) return `${d}d ago`;
  const mo = Math.round(d / 30);
  if (mo < 12) return `${mo}mo ago`;
  return `${Math.round(mo / 12)}y ago`;
}

function Timestamp({ iso, prefix = "" }: { iso: string; prefix?: string }) {
  return (
    <time className="crm-time" dateTime={iso} title={absoluteTime(iso)}>
      {prefix}
      {relativeTime(iso)}
    </time>
  );
}

// --- small shared bits ---

function QualifiedBadge({ contact }: { contact?: ContactRow | null }) {
  if (!contact) return null;
  return contact.qualified_at ? (
    <Badge tone="ok">qualified</Badge>
  ) : (
    <Badge tone="neutral">unqualified</Badge>
  );
}

/** Phase 12 email-verification verdict. Renders nothing when the field is
 * absent (client-role payloads) — the badge is a team-facing signal. */
function VerificationBadge({ contact }: { contact?: ContactRow | null }) {
  const status = contact?.verification_status;
  if (!status) return null;
  return (
    <Badge tone={status}>
      {status === "unverified" ? "unverified email" : `email ${status}`}
    </Badge>
  );
}

function AttributionChips({ contact }: { contact?: ContactRow | null }) {
  const a = contact?.attribution;
  if (!a) return null;
  const hasTrail = a.platform || a.utm_source || a.has_click_id;
  return (
    <span className="crm-attr">
      {a.platform && <PlatformChip name={a.platform} />}
      {a.utm_source && <Badge tone="neutral">utm: {a.utm_source}</Badge>}
      {a.has_click_id && <Badge tone="neutral">click ID</Badge>}
      {!hasTrail && <span className="crm-attr-empty">no attribution trail</span>}
    </span>
  );
}

export function CrmView({
  clientId,
  session,
}: {
  clientId: string;
  session: Session;
}) {
  const isTeam = TEAM_ROLES.includes(session.role);
  const isAdmin = ADMIN_ROLES.includes(session.role);
  const toast = useToast();
  const [board, setBoard] = useState<Board | null>(null);
  const [contacts, setContacts] = useState<ContactRow[]>([]);
  const [criteria, setCriteria] = useState<Criterion[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showSetup, setShowSetup] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [refetching, setRefetching] = useState(false);
  const [bump, setBump] = useState(0);
  const refresh = useCallback(() => setBump((b) => b + 1), []);
  const closeDrawer = useCallback(() => setSelectedId(null), []);
  // Custom field definitions (Phase 14) — any role loads them so contact views
  // can label/render values; the values themselves are visibility-filtered
  // server-side. Team roles get archived too via the manager's own fetch.
  const { active: customDefs, reload: reloadDefs } = useCustomFieldDefs(true);

  useEffect(() => {
    let alive = true;
    setRefetching(bump > 0);
    (async () => {
      try {
        const [b, cs] = await Promise.all([
          api<Board>(`/api/crm/board?client_id=${clientId}`),
          api<ContactRow[]>(`/api/crm/contacts?client_id=${clientId}`),
        ]);
        if (!alive) return;
        setBoard(b);
        setContacts(cs);
        setError(null);
      } catch (e) {
        if (alive) setError((e as Error).message);
      } finally {
        if (alive) setRefetching(false);
      }
    })();
    if (isTeam)
      api<{ criteria: Criterion[] }>("/api/orgs/me/qualified-lead-criteria")
        .then((r) => alive && setCriteria(r.criteria))
        .catch(() => {});
    return () => {
      alive = false;
    };
  }, [clientId, bump, isTeam]);

  const canDrag = isTeam && board != null && !board.read_only;

  if (!board) {
    if (error)
      return (
        <section className="crm">
          <Alert tone="danger" title="Couldn't load the CRM">
            <div className="crm-alert-body">
              <span>{error}</span>
              <Button size="sm" onClick={refresh}>
                Retry
              </Button>
            </div>
          </Alert>
        </section>
      );
    return (
      <section className="crm">
        <div className="crm-board" aria-hidden="true">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="kanban-lane">
              <Skeleton height="0.85em" width="55%" />
              <Skeleton height="3.4em" />
              <Skeleton height="3.4em" />
            </div>
          ))}
        </div>
      </section>
    );
  }

  return (
    <section className="crm">
      <div className="crm-toolbar">
        <h3 className="crm-subhead">{board.pipeline.name}</h3>
        <span className="crm-count">
          {board.won.length} won · {board.lost.length} lost
        </span>
        {isAdmin && (
          <div className="crm-toolbar-spacer">
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={showSetup}
              onClick={() => setShowSetup((s) => !s)}
            >
              {showSetup ? "Hide setup" : "CRM setup"}
            </Button>
          </div>
        )}
      </div>

      {error && (
        <Alert tone="danger">
          <div className="crm-alert-body">
            <span>{error}</span>
            <Button size="sm" onClick={refresh}>
              Retry
            </Button>
          </div>
        </Alert>
      )}

      {showSetup && isAdmin && (
        <SetupPanel
          clientId={clientId}
          board={board}
          criteria={criteria}
          onChanged={refresh}
          onFieldsChanged={() => {
            refresh();
            reloadDefs();
          }}
        />
      )}

      <KanbanBoard
        board={board}
        canManage={canDrag}
        onSelect={setSelectedId}
        onMoved={refresh}
        toast={toast}
      />

      <LeadList
        contacts={contacts}
        clientId={clientId}
        isTeam={isTeam}
        isAdmin={isAdmin}
        customDefs={customDefs}
        selectedId={selectedId}
        refetching={refetching}
        onSelect={setSelectedId}
        onCreated={refresh}
      />

      {selectedId && (
        <ContactDrawer
          key={selectedId}
          contactId={selectedId}
          clientId={clientId}
          session={session}
          criteria={criteria}
          stages={board.stages}
          customDefs={customDefs}
          onClose={closeDrawer}
          onChanged={refresh}
        />
      )}
    </section>
  );
}

// --- Kanban ---

type ToastFn = ReturnType<typeof useToast>;

function KanbanBoard({
  board,
  canManage,
  onSelect,
  onMoved,
  toast,
}: {
  board: Board;
  canManage: boolean;
  onSelect: (contactId: string) => void;
  onMoved: () => void;
  toast: ToastFn;
}) {
  // Optimistic local placement: move a card instantly, resync from props once
  // the parent refetch lands (no snap-back flash on latency).
  const [local, setLocal] = useState<Record<string, DealRow[]>>(
    board.deals_by_stage
  );
  const [dragDealId, setDragDealId] = useState<string | null>(null);
  const [overStage, setOverStage] = useState<string | null>(null);
  const [announce, setAnnounce] = useState("");

  useEffect(() => setLocal(board.deals_by_stage), [board.deals_by_stage]);

  const stageOf = useCallback(
    (dealId: string) =>
      board.stages.find((s) =>
        (local[s.id] ?? []).some((d) => d.id === dealId)
      ),
    [board.stages, local]
  );

  const move = useCallback(
    async (dealId: string, toStageId: string) => {
      const from = stageOf(dealId);
      if (!from || from.id === toStageId) return;
      const deal = (local[from.id] ?? []).find((d) => d.id === dealId);
      if (!deal) return;
      const toStage = board.stages.find((s) => s.id === toStageId);
      // optimistic
      setLocal((cur) => {
        const next = { ...cur };
        next[from.id] = (cur[from.id] ?? []).filter((d) => d.id !== dealId);
        next[toStageId] = [...(cur[toStageId] ?? []), { ...deal, stage_id: toStageId }];
        return next;
      });
      setAnnounce(`Moved ${contactName(board.contacts[deal.contact_id])} to ${toStage?.name}`);
      try {
        await api(`/api/crm/deals/${dealId}`, {
          method: "PATCH",
          body: JSON.stringify({ stage_id: toStageId }),
        });
        onMoved();
      } catch (e) {
        setLocal(board.deals_by_stage); // revert
        toast((e as Error).message, "error");
      }
    },
    [board, local, stageOf, onMoved, toast]
  );

  const closeDeal = useCallback(
    async (dealId: string, status: "won" | "lost") => {
      const from = stageOf(dealId);
      if (from)
        setLocal((cur) => ({
          ...cur,
          [from.id]: (cur[from.id] ?? []).filter((d) => d.id !== dealId),
        }));
      try {
        await api(`/api/crm/deals/${dealId}`, {
          method: "PATCH",
          body: JSON.stringify({ status }),
        });
        toast(`Deal marked ${status}`, "ok");
        onMoved();
      } catch (e) {
        setLocal(board.deals_by_stage);
        toast((e as Error).message, "error");
      }
    },
    [board.deals_by_stage, stageOf, onMoved, toast]
  );

  const drop = (stageId: string) => {
    setOverStage(null);
    if (dragDealId) void move(dragDealId, stageId);
    setDragDealId(null);
  };

  return (
    <div className="crm-board-wrap">
      <div className="crm-board">
        {board.stages.map((stage) => {
          const deals = local[stage.id] ?? [];
          return (
            <div
              key={stage.id}
              className={`kanban-lane ${overStage === stage.id ? "kanban-lane--drop" : ""}`.trim()}
              onDragOver={(e) => {
                if (!canManage) return;
                e.preventDefault();
                setOverStage(stage.id);
              }}
              onDragLeave={() => setOverStage((s) => (s === stage.id ? null : s))}
              onDrop={(e) => {
                e.preventDefault();
                drop(stage.id);
              }}
            >
              <div className="kanban-lane-head">
                <span>{stage.name}</span>
                {stage.is_qualified_stage && (
                  <span title="Deals here mark the lead qualified">
                    <Badge tone="ok">qualifies</Badge>
                  </span>
                )}
                <span className="crm-lane-count">
                  <Badge tone="neutral">{deals.length}</Badge>
                </span>
              </div>
              {deals.map((deal) => (
                <DealCard
                  key={deal.id}
                  deal={deal}
                  contact={board.contacts[deal.contact_id]}
                  stages={board.stages}
                  canManage={canManage}
                  dragging={dragDealId === deal.id}
                  onDragStart={() => setDragDealId(deal.id)}
                  onDragEnd={() => setDragDealId(null)}
                  onOpen={() => onSelect(deal.contact_id)}
                  onMove={(to) => move(deal.id, to)}
                  onClose={(status) => closeDeal(deal.id, status)}
                />
              ))}
              {deals.length === 0 && <p className="crm-lane-empty">No deals</p>}
            </div>
          );
        })}
      </div>

      {canManage && (
        <p className="crm-note">
          Drag a card between stages, or focus a card and use the “Move to
          stage” menu ([ and ] shortcuts). Dropping into the qualifying stage
          marks that lead qualified — the LQA-CPL metric and guarantee tracker
          update from the same change.
        </p>
      )}

      <div className="visually-hidden" role="status" aria-live="polite">
        {announce}
      </div>
    </div>
  );
}

function DealCard({
  deal,
  contact,
  stages,
  canManage,
  dragging,
  onDragStart,
  onDragEnd,
  onOpen,
  onMove,
  onClose,
}: {
  deal: DealRow;
  contact?: ContactRow;
  stages: Stage[];
  canManage: boolean;
  dragging: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onOpen: () => void;
  onMove: (toStageId: string) => void;
  onClose: (status: "won" | "lost") => void;
}) {
  const [confirm, setConfirm] = useState<"won" | "lost" | null>(null);
  const name = contactName(contact);
  const stageIdx = stages.findIndex((s) => s.id === deal.stage_id);
  const stale = contact ? daysSince(contact.created_at) >= STALE_DAYS : false;

  const onKeyDown = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    if (!canManage) return;
    if (e.key === "[" && stageIdx > 0) {
      e.preventDefault();
      onMove(stages[stageIdx - 1].id);
    } else if (e.key === "]" && stageIdx < stages.length - 1) {
      e.preventDefault();
      onMove(stages[stageIdx + 1].id);
    }
  };

  return (
    <div
      className={`kanban-card ${dragging ? "kanban-card--dragging" : ""}`.trim()}
      draggable={canManage}
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", deal.id);
        onDragStart();
      }}
      onDragEnd={onDragEnd}
    >
      <button className="crm-card-open" onClick={onOpen} onKeyDown={onKeyDown}>
        <span className="crm-card-line">
          <span className="kanban-card-name">{name}</span>
          {money(deal.value_cents) && (
            <span className="kanban-card-value">{money(deal.value_cents)}</span>
          )}
        </span>
        {deal.name !== name && <span className="crm-card-sub">{deal.name}</span>}
      </button>

      <div className="crm-card-badges">
        <QualifiedBadge contact={contact} />
        {contact?.attribution?.platform && (
          <PlatformChip name={contact.attribution.platform} />
        )}
        {contact && (
          <span
            className="crm-age"
            title={`Lead added ${daysSince(contact.created_at)}d ago`}
          >
            <span
              className={`kanban-age-dot ${stale ? "kanban-age-dot--stale" : ""}`.trim()}
              aria-hidden="true"
            />
          </span>
        )}
      </div>

      {canManage && (
        <div className="crm-card-actions">
          <MoveStageMenu
            stages={stages}
            currentStageId={deal.stage_id}
            onMove={onMove}
          />
          <span className="crm-card-actions-spacer" />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setConfirm("won")}
          >
            Won
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setConfirm("lost")}
          >
            Lost
          </Button>
        </div>
      )}

      <ConfirmDialog
        open={confirm !== null}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          if (confirm) onClose(confirm);
          setConfirm(null);
        }}
        title={confirm === "lost" ? "Mark deal lost?" : "Mark deal won?"}
        tone={confirm === "lost" ? "danger" : "warn"}
        confirmLabel={confirm === "lost" ? "Mark lost" : "Mark won"}
        cancelLabel="Cancel"
        rows={
          confirm
            ? ([
                {
                  field: `${name} · ${deal.name}`,
                  oldValue: "open",
                  newValue: confirm,
                },
              ] satisfies ReceiptRow[])
            : []
        }
      >
        <p className="crm-note">
          This closes the deal and removes it from the pipeline board.
        </p>
      </ConfirmDialog>
    </div>
  );
}

function MoveStageMenu({
  stages,
  currentStageId,
  onMove,
}: {
  stages: Stage[];
  currentStageId: string;
  onMove: (toStageId: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  return (
    <div className="crm-menu" ref={ref}>
      <Button
        variant="default"
        size="sm"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        Move to stage
        <ChevronRight size={14} aria-hidden="true" />
      </Button>
      {open && (
        <div className="crm-menu-pop" role="menu">
          <p className="crm-menu-label">Move to stage</p>
          {stages.map((s) => (
            <button
              key={s.id}
              type="button"
              role="menuitem"
              className="crm-menu-item"
              aria-current={s.id === currentStageId}
              disabled={s.id === currentStageId}
              onClick={() => {
                onMove(s.id);
                setOpen(false);
              }}
            >
              {s.name}
              {s.id === currentStageId && <span className="crm-menu-here">current</span>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// --- Lead list ---

interface CfFilter {
  key: string;
  op: string;
  value: string;
}

/** Client-side match mirroring the backend filter semantics, so the loaded
 * list narrows instantly. The server endpoint applies the same shapes for API
 * consumers (and uses the GIN index at scale). */
function matchesFilter(c: ContactRow, def: CustomFieldDef, f: CfFilter): boolean {
  const v = c.custom_fields?.[f.key];
  if (f.value === "" && f.op !== "eq") return true;
  switch (def.field_type) {
    case "number": {
      const n = v == null ? null : Number(v);
      const t = Number(f.value);
      if (n == null || Number.isNaN(n)) return false;
      return f.op === "gte" ? n >= t : f.op === "lte" ? n <= t : n === t;
    }
    case "boolean":
      return Boolean(v) === (f.value === "true");
    case "date": {
      const s = v ? String(v) : "";
      if (!s) return false;
      return f.op === "gte" ? s >= f.value : s <= f.value;
    }
    case "select":
      return String(v ?? "") === f.value;
    case "multi_select":
      return Array.isArray(v) && v.includes(f.value);
    default:
      return String(v ?? "").toLowerCase().includes(f.value.toLowerCase());
  }
}

/** Choosable non-custom contact columns (Phase-12 contract adds these fields to
 * list payloads). Kept alongside the custom-field column choices in the picker. */
const SYS_COLUMNS: { key: string; label: string; get: (c: ContactRow) => string | null }[] = [
  { key: "city", label: "City", get: (c) => c.city },
  { key: "state", label: "State", get: (c) => c.state },
  { key: "company_name", label: "Business name", get: (c) => c.company_name },
];

function LeadList({
  contacts,
  clientId,
  isTeam,
  isAdmin,
  customDefs,
  selectedId,
  refetching,
  onSelect,
  onCreated,
}: {
  contacts: ContactRow[];
  clientId: string;
  isTeam: boolean;
  isAdmin: boolean;
  customDefs: CustomFieldDef[];
  selectedId: string | null;
  refetching: boolean;
  onSelect: (id: string) => void;
  onCreated: () => void;
}) {
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [showCols, setShowCols] = useState(false);
  const [showFilters, setShowFilters] = useState(false);
  const [cols, setCols] = useState<string[]>([]);
  const [sysCols, setSysCols] = useState<string[]>([]);
  const [filters, setFilters] = useState<CfFilter[]>([]);
  const [verifFilter, setVerifFilter] = useState<string>("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [confirmingBulk, setConfirmingBulk] = useState(false);
  const [deleting, setDeleting] = useState(false);

  // System-column choices persist client-side only (the crm-columns preference
  // stores custom-field keys; keeping system columns out of it avoids any
  // backend key-validation coupling).
  useEffect(() => {
    try {
      const raw = localStorage.getItem(`crm-syscols:${clientId}`);
      setSysCols(raw ? (JSON.parse(raw) as string[]) : []);
    } catch {
      setSysCols([]);
    }
  }, [clientId]);

  const saveSysCols = (next: string[]) => {
    setSysCols(next);
    try {
      localStorage.setItem(`crm-syscols:${clientId}`, JSON.stringify(next));
    } catch {
      /* ignore quota/availability errors — in-session state still applies */
    }
  };

  // Per-user column choice (Phase 4 preference pattern), loaded per client view.
  useEffect(() => {
    let alive = true;
    api<{ columns: string[] | null }>(`/api/dashboard/crm-columns?client_id=${clientId}`)
      .then((r) => alive && setCols(r.columns ?? []))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [clientId]);

  const saveCols = (next: string[]) => {
    setCols(next);
    api(`/api/dashboard/crm-columns?client_id=${clientId}`, {
      method: "PUT",
      body: JSON.stringify({ columns: next }),
    }).catch(() => {});
  };

  const defByKey = useMemo(
    () => Object.fromEntries(customDefs.map((d) => [d.key, d])),
    [customDefs]
  );
  const chosenDefs = useMemo(
    () =>
      cols
        .map((k) => defByKey[k])
        .filter((d): d is CustomFieldDef => Boolean(d))
        .sort((a, b) => a.sort_order - b.sort_order),
    [cols, defByKey]
  );

  const rows = useMemo(() => {
    let out = contacts;
    if (verifFilter)
      out = out.filter((c) => (c.verification_status ?? "unverified") === verifFilter);
    if (filters.length === 0) return out;
    return out.filter((c) =>
      filters.every((f) => {
        const d = defByKey[f.key];
        return d ? matchesFilter(c, d, f) : true;
      })
    );
  }, [contacts, filters, defByKey, verifFilter]);

  // --- bulk selection (admin-only) ---
  const visibleIds = useMemo(() => rows.map((r) => r.id), [rows]);
  const selectedVisible = useMemo(
    () => visibleIds.filter((id) => selected.has(id)),
    [visibleIds, selected]
  );
  const allSelected = rows.length > 0 && selectedVisible.length === rows.length;

  const toggleOne = (id: string, on: boolean) =>
    setSelected((s) => {
      const next = new Set(s);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  const toggleAll = (on: boolean) =>
    setSelected((s) => {
      const next = new Set(s);
      for (const id of visibleIds) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });

  const doBulkDelete = () => {
    if (deleting || selectedVisible.length === 0) return;
    setDeleting(true);
    bulkDeleteContacts(selectedVisible)
      .then((r) => {
        toast(`Deleted ${r.deleted} lead${r.deleted === 1 ? "" : "s"}`, "ok");
        setSelected(new Set());
        setConfirmingBulk(false);
        onCreated();
      })
      .catch((e) => toast((e as Error).message, "error"))
      .finally(() => setDeleting(false));
  };

  const selectColumn: Column<ContactRow> = {
    key: "select",
    header: (
      <input
        type="checkbox"
        aria-label="Select all leads"
        checked={allSelected}
        ref={(el) => {
          if (el) el.indeterminate = selectedVisible.length > 0 && !allSelected;
        }}
        onChange={(e) => toggleAll(e.target.checked)}
      />
    ),
    render: (c) => (
      <input
        type="checkbox"
        aria-label={`Select ${contactName(c)}`}
        checked={selected.has(c.id)}
        onClick={(e) => e.stopPropagation()}
        onChange={(e) => toggleOne(c.id, e.target.checked)}
      />
    ),
  };

  const sysColumns: Column<ContactRow>[] = SYS_COLUMNS.filter((s) =>
    sysCols.includes(s.key)
  ).map((s) => ({
    key: `sys_${s.key}`,
    header: s.label,
    render: (c) => <span className="crm-muted">{s.get(c) || "—"}</span>,
    sortValue: (c) => s.get(c) ?? "",
  }));

  const columns: Column<ContactRow>[] = [
    ...(isAdmin ? [selectColumn] : []),
    {
      key: "lead",
      header: "Lead",
      render: (c) => <strong>{contactName(c)}</strong>,
      sortValue: (c) => contactName(c),
    },
    {
      key: "contact",
      header: "Contact info",
      render: (c) => (
        <span className="crm-muted">
          {[c.email, c.phone].filter(Boolean).join(" · ") || "—"}
        </span>
      ),
    },
    {
      key: "source",
      header: "Source",
      render: (c) => (
        <span className="crm-muted">{c.source?.replace(/_/g, " ") ?? "—"}</span>
      ),
      sortValue: (c) => c.source ?? "",
    },
    ...sysColumns,
    ...customFieldColumns<ContactRow>(chosenDefs),
    {
      key: "attribution",
      header: "Attribution",
      render: (c) => <AttributionChips contact={c} />,
      sortValue: (c) => c.attribution?.platform ?? "",
    },
    {
      key: "status",
      header: "Status",
      render: (c) => <QualifiedBadge contact={c} />,
      sortValue: (c) => (c.qualified_at ? 1 : 0),
    },
    ...(isTeam
      ? [
          {
            key: "verification",
            header: "Verification",
            render: (c) => <VerificationBadge contact={c} />,
            sortValue: (c) => c.verification_status ?? "",
          } satisfies Column<ContactRow>,
        ]
      : []),
    {
      key: "created",
      header: "Created",
      render: (c) => <Timestamp iso={c.created_at} />,
      sortValue: (c) => c.created_at,
    },
  ];

  // Which fields the caller may see/filter/column on (client role: visible only;
  // the server already filters values, this keeps the pickers honest too).
  const pickableDefs = customDefs;

  return (
    <div className="crm-leadlist">
      <div className="crm-toolbar">
        <h4 className="crm-subhead crm-subhead--sm">Leads ({rows.length})</h4>
        <div className="crm-toolbar-spacer crm-leadlist-actions">
          {isTeam && (
            <select
              className="crm-verif-filter"
              aria-label="Filter by email verification"
              value={verifFilter}
              onChange={(e) => setVerifFilter(e.target.value)}
            >
              <option value="">All emails</option>
              <option value="valid">Valid</option>
              <option value="risky">Risky</option>
              <option value="invalid">Invalid</option>
              <option value="unknown">Unknown</option>
              <option value="unverified">Unverified</option>
            </select>
          )}
          {pickableDefs.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={showFilters}
              onClick={() => setShowFilters((s) => !s)}
            >
              Filter{filters.length ? ` (${filters.length})` : ""}
            </Button>
          )}
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={showCols}
            onClick={() => setShowCols((s) => !s)}
          >
            <Settings size={14} /> Columns
          </Button>
          {isAdmin && (
            <Button variant="ghost" size="sm" onClick={() => setShowImport(true)}>
              Import
            </Button>
          )}
          {isTeam && (
            <Button
              variant="ghost"
              size="sm"
              aria-expanded={adding}
              onClick={() => setAdding((a) => !a)}
            >
              {adding ? "Cancel" : (<><Plus size={14} /> Add contact</>)}
            </Button>
          )}
        </div>
      </div>

      {showCols && (
        <div className="crm-col-picker" role="group" aria-label="Table columns">
          {SYS_COLUMNS.map((s) => (
            <label key={s.key} className="crm-check">
              <input
                type="checkbox"
                checked={sysCols.includes(s.key)}
                onChange={(e) =>
                  saveSysCols(
                    e.target.checked
                      ? [...sysCols, s.key]
                      : sysCols.filter((k) => k !== s.key)
                  )
                }
              />
              <span>{s.label}</span>
            </label>
          ))}
          {pickableDefs.map((d) => (
            <label key={d.id} className="crm-check">
              <input
                type="checkbox"
                checked={cols.includes(d.key)}
                onChange={(e) =>
                  saveCols(
                    e.target.checked
                      ? [...cols, d.key]
                      : cols.filter((k) => k !== d.key)
                  )
                }
              />
              <span>{d.label}</span>
            </label>
          ))}
        </div>
      )}

      {showFilters && pickableDefs.length > 0 && (
        <FilterBar defs={pickableDefs} filters={filters} onChange={setFilters} />
      )}

      {adding && (
        <NewContactForm
          clientId={clientId}
          customDefs={customDefs}
          onCreated={() => {
            setAdding(false);
            onCreated();
          }}
        />
      )}

      {isAdmin && selectedVisible.length > 0 && (
        <div className="crm-bulk-bar" role="region" aria-label="Bulk actions">
          <span className="crm-count">
            {selectedVisible.length} selected
          </span>
          <div className="crm-toolbar-spacer">
            {confirmingBulk ? (
              <>
                <span className="crm-note">
                  Delete {selectedVisible.length} lead
                  {selectedVisible.length === 1 ? "" : "s"}? This can’t be undone.
                </span>
                <Button
                  variant="danger"
                  size="sm"
                  busy={deleting}
                  onClick={doBulkDelete}
                >
                  <Trash2 size={14} /> Delete
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={deleting}
                  onClick={() => setConfirmingBulk(false)}
                >
                  Cancel
                </Button>
              </>
            ) : (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSelected(new Set())}
                >
                  Clear
                </Button>
                <Button
                  variant="danger-outline"
                  size="sm"
                  onClick={() => setConfirmingBulk(true)}
                >
                  <Trash2 size={14} /> Delete selected
                </Button>
              </>
            )}
          </div>
        </div>
      )}

      <DataTable<ContactRow>
        rows={rows}
        rowKey={(c) => c.id}
        onRowClick={(c) => onSelect(c.id)}
        selectedKey={selectedId}
        initialSort="-created"
        refetching={refetching}
        caption="Leads for this client, with source and attribution"
        empty={
          <EmptyState icon={<Inbox />} title="No leads yet">
            Leads arrive here automatically from Instant Forms, Lead Form ads,
            and landing pages — with their attribution already attached.
          </EmptyState>
        }
        columns={columns}
      />

      {showImport && (
        <CsvImportDialog
          clientId={clientId}
          defs={customDefs}
          onClose={() => setShowImport(false)}
          onDone={onCreated}
        />
      )}
    </div>
  );
}

function FilterBar({
  defs,
  filters,
  onChange,
}: {
  defs: CustomFieldDef[];
  filters: CfFilter[];
  onChange: (f: CfFilter[]) => void;
}) {
  const add = () => {
    const d = defs[0];
    onChange([...filters, { key: d.key, op: defaultOp(d.field_type), value: "" }]);
  };
  const set = (i: number, patch: Partial<CfFilter>) =>
    onChange(filters.map((f, j) => (j === i ? { ...f, ...patch } : f)));

  return (
    <div className="crm-filter-bar">
      {filters.map((f, i) => {
        const d = defs.find((x) => x.key === f.key) ?? defs[0];
        return (
          <div key={i} className="crm-filter-row">
            <select
              value={f.key}
              onChange={(e) => {
                const nd = defs.find((x) => x.key === e.target.value)!;
                set(i, { key: nd.key, op: defaultOp(nd.field_type), value: "" });
              }}
            >
              {defs.map((x) => (
                <option key={x.key} value={x.key}>
                  {x.label}
                </option>
              ))}
            </select>
            <FilterValue def={d} filter={f} onSet={(patch) => set(i, patch)} />
            <Button
              variant="ghost"
              size="sm"
              aria-label="Remove filter"
              onClick={() => onChange(filters.filter((_, j) => j !== i))}
            >
              ×
            </Button>
          </div>
        );
      })}
      <Button variant="ghost" size="sm" onClick={add}>
        <Plus size={13} /> Add filter
      </Button>
    </div>
  );
}

const defaultOp = (t: string) =>
  t === "number" || t === "date" ? "gte" : t === "boolean" ? "eq" : t === "text" || t === "url" ? "contains" : "is";

function FilterValue({
  def,
  filter,
  onSet,
}: {
  def: CustomFieldDef;
  filter: CfFilter;
  onSet: (patch: Partial<CfFilter>) => void;
}) {
  if (def.field_type === "number" || def.field_type === "date") {
    return (
      <>
        <select value={filter.op} onChange={(e) => onSet({ op: e.target.value })}>
          <option value="gte">≥</option>
          <option value="lte">≤</option>
        </select>
        <input
          type={def.field_type === "date" ? "date" : "number"}
          value={filter.value}
          onChange={(e) => onSet({ value: e.target.value })}
        />
      </>
    );
  }
  if (def.field_type === "boolean") {
    return (
      <select value={filter.value} onChange={(e) => onSet({ value: e.target.value, op: "eq" })}>
        <option value="">—</option>
        <option value="true">Yes</option>
        <option value="false">No</option>
      </select>
    );
  }
  if (def.field_type === "select" || def.field_type === "multi_select") {
    return (
      <select
        value={filter.value}
        onChange={(e) => onSet({ value: e.target.value, op: def.field_type === "select" ? "is" : "any_of" })}
      >
        <option value="">—</option>
        {(def.options ?? []).map((o) => (
          <option key={o.key} value={o.key}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      placeholder="contains…"
      value={filter.value}
      onChange={(e) => onSet({ value: e.target.value, op: "contains" })}
    />
  );
}

function NewContactForm({
  clientId,
  customDefs,
  onCreated,
}: {
  clientId: string;
  customDefs: CustomFieldDef[];
  onCreated: () => void;
}) {
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [custom, setCustom] = useState<CustomValues>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const empty = !first && !last && !email && !phone;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (empty || busy) return;
    setBusy(true);
    api("/api/crm/contacts", {
      method: "POST",
      body: JSON.stringify({
        client_id: clientId,
        first_name: first || null,
        last_name: last || null,
        email: email || null,
        phone: phone || null,
        custom_fields: Object.keys(custom).length ? custom : undefined,
      }),
    })
      .then(onCreated)
      .catch((err) => setError((err as Error).message))
      .finally(() => setBusy(false));
  };

  return (
    <form className="crm-form" onSubmit={submit}>
      <Field label="First name">
        <input value={first} onChange={(e) => setFirst(e.target.value)} />
      </Field>
      <Field label="Last name">
        <input value={last} onChange={(e) => setLast(e.target.value)} />
      </Field>
      <Field label="Email">
        <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
      </Field>
      <Field label="Phone">
        <input value={phone} onChange={(e) => setPhone(e.target.value)} />
      </Field>
      <CustomFieldInputs
        defs={customDefs}
        values={custom}
        onChange={(k, v) => setCustom((p) => ({ ...p, [k]: v }))}
      />
      <div className="crm-form-actions" role="group">
        <Button type="submit" variant="primary" disabled={empty} busy={busy}>
          Add contact
        </Button>
        {error && (
          <span className="crm-form-error" role="alert">
            {error}
          </span>
        )}
      </div>
    </form>
  );
}

// --- Contact drawer ---

interface ContactDetail extends ContactRow {
  activities: {
    id: string;
    type: string;
    body: string | null;
    is_internal: boolean;
    occurred_at: string;
  }[];
  deals: DealRow[];
  tasks?: {
    id: string;
    title: string;
    due_at: string | null;
    completed_at: string | null;
    assigned_to_user_id: string | null;
  }[];
}

/** Dialog-grade a11y for the floating drawer: focus in, trap Tab, Escape to
 * close, restore focus to the invoker on unmount. Runs once (drawer is
 * conditionally mounted = one open per mount). */
function useDrawerA11y(onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const panel = ref.current;
    const restore = document.activeElement as HTMLElement | null;
    const focusables = () =>
      Array.from(panel?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    (focusables()[0] ?? panel)?.focus();

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const f = focusables();
      if (f.length === 0) {
        e.preventDefault();
        return;
      }
      const first = f[0];
      const last = f[f.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && active === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    panel?.addEventListener("keydown", onKey);
    return () => {
      panel?.removeEventListener("keydown", onKey);
      restore?.focus?.();
    };
  }, []);

  return ref;
}

function ContactDrawer({
  contactId,
  clientId,
  session,
  criteria,
  stages,
  customDefs,
  onClose,
  onChanged,
}: {
  contactId: string;
  clientId: string;
  session: Session;
  criteria: Criterion[];
  stages: Stage[];
  customDefs: CustomFieldDef[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const isTeam = TEAM_ROLES.includes(session.role);
  const isAdmin = ADMIN_ROLES.includes(session.role);
  const toast = useToast();
  const [detail, setDetail] = useState<ContactDetail | null>(null);
  const [members, setMembers] = useState<
    { id: string; full_name: string; role: string }[]
  >([]);
  const [error, setError] = useState<string | null>(null);
  const [bump, setBump] = useState(0);
  const reload = useCallback(() => setBump((b) => b + 1), []);
  const panelRef = useDrawerA11y(onClose);
  const titleId = `crm-drawer-${contactId}`;
  const [verifying, setVerifying] = useState(false);

  const runVerify = async () => {
    if (verifying) return;
    setVerifying(true);
    try {
      const r = await verifyContacts([contactId]);
      const status = r.verified[contactId]?.verification_status;
      toast(status ? `Email verified: ${status}` : "No email to verify", "ok");
      reload();
      onChanged();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setVerifying(false);
    }
  };

  useEffect(() => {
    let alive = true;
    api<ContactDetail>(`/api/crm/contacts/${contactId}`)
      .then((d) => alive && setDetail(d))
      .catch((e) => alive && setError((e as Error).message));
    if (isTeam)
      api<{ id: string; full_name: string; role: string }[]>(
        "/api/orgs/me/members"
      )
        .then((ms) => alive && setMembers(ms.filter((m) => m.role !== "client")))
        .catch(() => {});
    return () => {
      alive = false;
    };
  }, [contactId, bump, isTeam]);

  const openDeal = detail?.deals.find((d) => d.status === "open");

  const body = (): ReactNode => {
    if (error)
      return (
        <Alert tone="danger" title="Couldn't load this lead">
          {error}
        </Alert>
      );
    if (!detail) return <SkeletonText lines={6} />;

    return (
      <>
        <IdentityBlock
          detail={detail}
          canEdit={isTeam}
          onSaved={() => {
            reload();
            onChanged();
          }}
        />

        {isTeam && detail.email && (
          <div className="crm-verify-row">
            <Button
              variant="ghost"
              size="sm"
              busy={verifying}
              onClick={() => void runVerify()}
            >
              {detail.verification_status === "unverified"
                ? "Verify email"
                : "Re-verify email"}
            </Button>
            {detail.verified_at && (
              <Timestamp iso={detail.verified_at} prefix="checked " />
            )}
          </div>
        )}

        {isTeam && (
          <QualificationPanel
            detail={detail}
            criteria={criteria}
            onChanged={() => {
              reload();
              onChanged();
            }}
          />
        )}

        <CustomFieldsPanel
          contactId={detail.id}
          defs={customDefs}
          values={detail.custom_fields ?? {}}
          canEdit={isTeam}
          onSaved={() => {
            reload();
            onChanged();
          }}
        />

        <section className="crm-section">
          <h6 className="crm-overline">Deals</h6>
          {detail.deals.length === 0 && <p className="crm-muted">No deals yet.</p>}
          <ul className="crm-deal-list">
            {detail.deals.map((d) => (
              <li key={d.id}>
                <Badge tone={d.status}>{d.status}</Badge>
                <strong>{d.name}</strong>
                <span className="crm-muted">
                  {money(d.value_cents) ?? ""}{" "}
                  {d.status === "open"
                    ? stages.find((s) => s.id === d.stage_id)?.name ?? ""
                    : ""}
                </span>
              </li>
            ))}
          </ul>
          {isTeam && !openDeal && (
            <NewDealForm
              clientId={clientId}
              contactId={detail.id}
              onCreated={() => {
                reload();
                onChanged();
              }}
            />
          )}
        </section>

        <section className="crm-section">
          <h6 className="crm-overline">Activity</h6>
          {isTeam && <NewActivityForm contactId={detail.id} onCreated={reload} />}
          <ul className="crm-timeline">
            {detail.activities.map((a) => (
              <li key={a.id}>
                <Badge tone="neutral">{a.type}</Badge>
                {a.is_internal && (
                  <span title="Never shown to client logins">
                    <Badge tone="warn">internal</Badge>
                  </span>
                )}
                {a.body && <span className="crm-ti-body">{a.body}</span>}
                <Timestamp iso={a.occurred_at} />
              </li>
            ))}
            {detail.activities.length === 0 && (
              <li className="crm-muted">No activity logged yet.</li>
            )}
          </ul>
        </section>

        {isTeam && detail.tasks && (
          <section className="crm-section">
            <h6 className="crm-overline">Tasks</h6>
            <NewTaskForm
              clientId={clientId}
              contactId={detail.id}
              members={members}
              onCreated={reload}
            />
            <ul className="crm-timeline">
              {detail.tasks.map((t) => (
                <li key={t.id} className={t.completed_at ? "crm-task--done" : ""}>
                  <label className="crm-check">
                    <input
                      type="checkbox"
                      checked={!!t.completed_at}
                      onChange={(e) =>
                        api(`/api/crm/tasks/${t.id}`, {
                          method: "PATCH",
                          body: JSON.stringify({ completed: e.target.checked }),
                        })
                          .then(reload)
                          .catch((err) => toast((err as Error).message, "error"))
                      }
                    />
                    <span>{t.title}</span>
                  </label>
                  <span className="crm-muted">
                    {t.due_at ? (
                      <Timestamp iso={t.due_at} prefix="due " />
                    ) : null}
                    {t.assigned_to_user_id
                      ? ` · ${
                          members.find((m) => m.id === t.assigned_to_user_id)
                            ?.full_name ?? "assigned"
                        }`
                      : ""}
                  </span>
                </li>
              ))}
              {detail.tasks.length === 0 && (
                <li className="crm-muted">No open tasks.</li>
              )}
            </ul>
          </section>
        )}

        {isAdmin && (
          <DeleteContact
            contactId={detail.id}
            name={contactName(detail)}
            onDeleted={() => {
              onChanged();
              onClose();
            }}
          />
        )}
      </>
    );
  };

  return (
    <div
      className="crm-drawer-scrim"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="crm-drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <div className="crm-drawer-head">
          <h4 className="crm-subhead crm-subhead--sm" id={titleId}>
            {contactName(detail ?? { id: contactId } as ContactRow)}
          </h4>
          <QualifiedBadge contact={detail} />
          <VerificationBadge contact={detail} />
          <div className="crm-toolbar-spacer">
            <Button variant="ghost" size="sm" onClick={onClose}>
              Close
            </Button>
          </div>
        </div>
        {body()}
      </div>
    </div>
  );
}

/** The contact's identity (name/email/phone/city/state/company) as a read view
 * with an inline Edit form for team roles. Save PATCHes the partial and reuses
 * the drawer + list refresh (onSaved). */
function IdentityBlock({
  detail,
  canEdit,
  onSaved,
}: {
  detail: ContactDetail;
  canEdit: boolean;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState({
    first_name: "",
    last_name: "",
    email: "",
    phone: "",
    city: "",
    state: "",
    company_name: "",
  });

  const startEdit = () => {
    setForm({
      first_name: detail.first_name ?? "",
      last_name: detail.last_name ?? "",
      email: detail.email ?? "",
      phone: detail.phone ?? "",
      city: detail.city ?? "",
      state: detail.state ?? "",
      company_name: detail.company_name ?? "",
    });
    setError(null);
    setEditing(true);
  };

  const set = (patch: Partial<typeof form>) => setForm((f) => ({ ...f, ...patch }));

  const save = () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    updateContact(detail.id, {
      first_name: form.first_name.trim() || null,
      last_name: form.last_name.trim() || null,
      email: form.email.trim() || null,
      phone: form.phone.trim() || null,
      city: form.city.trim() || null,
      state: form.state.trim() || null,
      company_name: form.company_name.trim() || null,
    })
      .then(() => {
        setEditing(false);
        onSaved();
        toast("Contact updated", "ok");
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setBusy(false));
  };

  if (editing) {
    return (
      <div className="crm-form crm-identity-edit">
        <Field label="First name">
          <input
            value={form.first_name}
            onChange={(e) => set({ first_name: e.target.value })}
          />
        </Field>
        <Field label="Last name">
          <input
            value={form.last_name}
            onChange={(e) => set({ last_name: e.target.value })}
          />
        </Field>
        <Field label="Email">
          <input
            type="email"
            value={form.email}
            onChange={(e) => set({ email: e.target.value })}
          />
        </Field>
        <Field label="Phone">
          <input value={form.phone} onChange={(e) => set({ phone: e.target.value })} />
        </Field>
        <Field label="City">
          <input value={form.city} onChange={(e) => set({ city: e.target.value })} />
        </Field>
        <Field label="State">
          <input value={form.state} onChange={(e) => set({ state: e.target.value })} />
        </Field>
        <Field label="Business name">
          <input
            value={form.company_name}
            onChange={(e) => set({ company_name: e.target.value })}
          />
        </Field>
        {error && (
          <span className="crm-form-error" role="alert">
            {error}
          </span>
        )}
        <div className="crm-form-actions">
          <Button variant="primary" size="sm" busy={busy} onClick={save}>
            Save
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={busy}
            onClick={() => {
              setEditing(false);
              setError(null);
            }}
          >
            Cancel
          </Button>
        </div>
      </div>
    );
  }

  const location = [detail.city, detail.state].filter(Boolean).join(", ");
  const orgLine = [detail.company_name, location].filter(Boolean).join(" · ");

  return (
    <div className="crm-identity">
      <p className="crm-muted">
        {[detail.email, detail.phone].filter(Boolean).join(" · ") || "No contact info"}
        {detail.source ? ` · via ${detail.source.replace(/_/g, " ")}` : ""}
      </p>
      {orgLine && <p className="crm-muted">{orgLine}</p>}
      <AttributionChips contact={detail} />
      {canEdit && (
        <div className="crm-identity-actions">
          <Button variant="ghost" size="sm" onClick={startEdit}>
            <Pencil size={13} /> Edit info
          </Button>
        </div>
      )}
    </div>
  );
}

/** Admin-only destructive delete with an inline two-step confirm (matches the
 * custom-field hard-delete idiom). On success the parent closes the drawer and
 * refreshes the list. */
function DeleteContact({
  contactId,
  name,
  onDeleted,
}: {
  contactId: string;
  name: string;
  onDeleted: () => void;
}) {
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const del = () => {
    if (busy) return;
    setBusy(true);
    deleteContact(contactId)
      .then(() => {
        toast("Lead deleted", "ok");
        onDeleted();
      })
      .catch((e) => {
        toast((e as Error).message, "error");
        setBusy(false);
      });
  };

  return (
    <section className="crm-section crm-danger-zone">
      {confirming ? (
        <>
          <p className="crm-note">
            Permanently delete <strong>{name}</strong> and all of its deals,
            activity and tasks? This can’t be undone.
          </p>
          <div className="crm-form-actions">
            <Button variant="danger" size="sm" busy={busy} onClick={del}>
              <Trash2 size={14} /> Delete lead
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => setConfirming(false)}
            >
              Cancel
            </Button>
          </div>
        </>
      ) : (
        <div className="crm-form-actions">
          <Button
            variant="danger-outline"
            size="sm"
            onClick={() => setConfirming(true)}
          >
            <Trash2 size={14} /> Delete lead
          </Button>
        </div>
      )}
    </section>
  );
}

function QualificationPanel({
  detail,
  criteria,
  onChanged,
}: {
  detail: ContactDetail;
  criteria: Criterion[];
  onChanged: () => void;
}) {
  const toast = useToast();
  const put = (body: Record<string, unknown>) =>
    api(`/api/crm/contacts/${detail.id}/qualification`, {
      method: "PUT",
      body: JSON.stringify(body),
    })
      .then(onChanged)
      .catch((e) => toast((e as Error).message, "error"));

  return (
    <div className="crm-qualify">
      <h6 className="crm-overline">Qualification</h6>
      {criteria.length > 0 ? (
        <>
          <p className="crm-muted">
            Your organization's criteria — all checked = qualified. One change
            updates LQA-CPL and the guarantee tracker.
          </p>
          <div className="crm-qualify-list">
            {criteria.map((c) => (
              <label key={c.key} className="crm-check">
                <input
                  type="checkbox"
                  checked={!!detail.qualification?.[c.key]}
                  onChange={(e) => put({ checklist: { [c.key]: e.target.checked } })}
                />
                <span>{c.label}</span>
              </label>
            ))}
          </div>
        </>
      ) : (
        <label className="crm-check">
          <input
            type="checkbox"
            checked={!!detail.qualified_at}
            onChange={(e) => put({ qualified: e.target.checked })}
          />
          <span>
            Qualified lead{" "}
            <span className="crm-muted">
              (no checklist configured — set criteria in CRM setup)
            </span>
          </span>
        </label>
      )}
    </div>
  );
}

function NewDealForm({
  clientId,
  contactId,
  onCreated,
}: {
  clientId: string;
  contactId: string;
  onCreated: () => void;
}) {
  const [value, setValue] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    api("/api/crm/deals", {
      method: "POST",
      body: JSON.stringify({
        client_id: clientId,
        contact_id: contactId,
        value_cents: value ? Math.round(Number(value) * 100) : null,
      }),
    })
      .then(onCreated)
      .catch((err) => setError((err as Error).message))
      .finally(() => setBusy(false));
  };

  return (
    <form className="crm-form" onSubmit={submit}>
      <Field label="Deal value" optional error={error ?? undefined}>
        <input
          type="number"
          min="0"
          inputMode="decimal"
          placeholder="0"
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
      </Field>
      <div className="crm-form-actions">
        <Button type="submit" busy={busy}>
          Start deal
        </Button>
      </div>
    </form>
  );
}

function NewActivityForm({
  contactId,
  onCreated,
}: {
  contactId: string;
  onCreated: () => void;
}) {
  const [type, setType] = useState("note");
  const [body, setBody] = useState("");
  const [internal, setInternal] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!body || busy) return;
    setBusy(true);
    api("/api/crm/activities", {
      method: "POST",
      body: JSON.stringify({
        contact_id: contactId,
        type,
        body,
        is_internal: internal,
      }),
    })
      .then(() => {
        setBody("");
        setInternal(false);
        onCreated();
      })
      .catch((err) => setError((err as Error).message))
      .finally(() => setBusy(false));
  };

  return (
    <form className="crm-form" onSubmit={submit}>
      <Field label="Type">
        <select value={type} onChange={(e) => setType(e.target.value)}>
          {["note", "call", "email", "sms", "meeting"].map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </Field>
      <Field label="What happened?" error={error ?? undefined}>
        <input value={body} onChange={(e) => setBody(e.target.value)} />
      </Field>
      <div className="crm-form-actions">
        <label className="crm-check">
          <input
            type="checkbox"
            checked={internal}
            onChange={(e) => setInternal(e.target.checked)}
          />
          <span>Internal only</span>
        </label>
        <Button type="submit" disabled={!body} busy={busy}>
          Log
        </Button>
      </div>
    </form>
  );
}

function NewTaskForm({
  clientId,
  contactId,
  members,
  onCreated,
}: {
  clientId: string;
  contactId: string;
  members: { id: string; full_name: string }[];
  onCreated: () => void;
}) {
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [assignee, setAssignee] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!title || busy) return;
    setBusy(true);
    api("/api/crm/tasks", {
      method: "POST",
      body: JSON.stringify({
        client_id: clientId,
        contact_id: contactId,
        title,
        due_at: due ? new Date(due).toISOString() : null,
        assigned_to_user_id: assignee || null,
      }),
    })
      .then(() => {
        setTitle("");
        setDue("");
        setAssignee("");
        onCreated();
      })
      .catch((err) => setError((err as Error).message))
      .finally(() => setBusy(false));
  };

  return (
    <form className="crm-form" onSubmit={submit}>
      <Field label="Follow-up task" error={error ?? undefined}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} />
      </Field>
      <Field label="Due" optional>
        <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
      </Field>
      <Field label="Assignee">
        <select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
          <option value="">me</option>
          {members.map((m) => (
            <option key={m.id} value={m.id}>
              {m.full_name}
            </option>
          ))}
        </select>
      </Field>
      <div className="crm-form-actions">
        <Button type="submit" disabled={!title} busy={busy}>
          Add task
        </Button>
      </div>
    </form>
  );
}

// --- Admin setup: stages, criteria, lead-form routing, external sync ---

interface StageRow {
  id?: string;
  name: string;
  is_qualified_stage: boolean;
}

function SetupPanel({
  clientId,
  board,
  criteria,
  onChanged,
  onFieldsChanged,
}: {
  clientId: string;
  board: Board;
  criteria: Criterion[];
  onChanged: () => void;
  onFieldsChanged: () => void;
}) {
  return (
    <div className="crm-setup">
      <StageEditor board={board} onChanged={onChanged} />
      <CriteriaEditor criteria={criteria} onChanged={onChanged} />
      <FieldManager onChanged={onFieldsChanged} />
      <LeadFormRouting clientId={clientId} />
      <ExternalSyncConfig clientId={clientId} />
    </div>
  );
}

function StageEditor({
  board,
  onChanged,
}: {
  board: Board;
  onChanged: () => void;
}) {
  const initial = useMemo<StageRow[]>(
    () =>
      board.stages.map((s) => ({
        id: s.id,
        name: s.name,
        is_qualified_stage: s.is_qualified_stage,
      })),
    [board.stages]
  );
  const [rows, setRows] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Resync after a save/refresh reflows the server's stage ids/order.
  useEffect(() => setRows(initial), [initial]);

  const set = (
    i: number,
    patch: Partial<{ name: string; is_qualified_stage: boolean }>
  ) => setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));

  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!rows.length || rows.some((r) => !r.name) || busy) return;
    setBusy(true);
    api(`/api/crm/pipelines/${board.pipeline.id}/stages`, {
      method: "PUT",
      body: JSON.stringify({ stages: rows }),
    })
      .then(onChanged)
      .catch((err) => setError((err as Error).message))
      .finally(() => setBusy(false));
  };

  return (
    <form className="crm-setup-block" onSubmit={save}>
      <h5 className="crm-subhead crm-subhead--sm">Pipeline stages (this client)</h5>
      {rows.map((r, i) => (
        <div key={r.id ?? `new-${i}`} className="crm-form">
          <Field label={`Stage ${i + 1}`}>
            <input value={r.name} onChange={(e) => set(i, { name: e.target.value })} />
          </Field>
          <label
            className="crm-check"
            title="Deals entering this stage mark the lead qualified"
          >
            <input
              type="radio"
              name="qualified-stage"
              checked={r.is_qualified_stage}
              onChange={() =>
                setRows(rows.map((row, j) => ({ ...row, is_qualified_stage: j === i })))
              }
            />
            <span>qualifies</span>
          </label>
          <div className="crm-form-actions">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setRows(rows.filter((_, j) => j !== i))}
            >
              Remove
            </Button>
          </div>
        </div>
      ))}
      {error && (
        <span className="crm-form-error" role="alert">
          {error}
        </span>
      )}
      <div className="crm-form-actions">
        <Button
          variant="default"
          size="sm"
          onClick={() =>
            setRows([...rows, { name: "New stage", is_qualified_stage: false }])
          }
        >
          <Plus size={14} /> Add stage
        </Button>
        <Button
          type="submit"
          variant="primary"
          size="sm"
          disabled={!rows.length || rows.some((r) => !r.name)}
          busy={busy}
        >
          Save stages
        </Button>
      </div>
    </form>
  );
}

const slug = (label: string) =>
  label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 50) || "criterion";

function CriteriaEditor({
  criteria,
  onChanged,
}: {
  criteria: Criterion[];
  onChanged: () => void;
}) {
  const [rows, setRows] = useState<Criterion[]>(criteria);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setRows(criteria), [criteria]);

  const save = (e: FormEvent) => {
    e.preventDefault();
    if (rows.some((r) => !r.label) || busy) return;
    setBusy(true);
    api("/api/orgs/me/qualified-lead-criteria", {
      method: "PUT",
      body: JSON.stringify({
        criteria: rows.map((r) => ({ key: r.key || slug(r.label), label: r.label })),
      }),
    })
      .then(onChanged)
      .catch((err) => setError((err as Error).message))
      .finally(() => setBusy(false));
  };

  return (
    <form className="crm-setup-block" onSubmit={save}>
      <h5 className="crm-subhead crm-subhead--sm">
        Qualified-lead criteria (whole organization)
      </h5>
      <p className="crm-muted">
        Your agency's own definition — e.g. a trial-sprint checklist. Leave
        empty for a simple qualified toggle.
      </p>
      {rows.map((r, i) => (
        <div key={i} className="crm-form">
          <Field label={`Criterion ${i + 1}`}>
            <input
              value={r.label}
              onChange={(e) =>
                setRows(
                  rows.map((row, j) =>
                    j === i
                      ? { key: slug(e.target.value), label: e.target.value }
                      : row
                  )
                )
              }
            />
          </Field>
          <div className="crm-form-actions">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setRows(rows.filter((_, j) => j !== i))}
            >
              Remove
            </Button>
          </div>
        </div>
      ))}
      {error && (
        <span className="crm-form-error" role="alert">
          {error}
        </span>
      )}
      <div className="crm-form-actions">
        <Button
          variant="default"
          size="sm"
          onClick={() => setRows([...rows, { key: "", label: "" }])}
        >
          <Plus size={14} /> Add criterion
        </Button>
        <Button
          type="submit"
          variant="primary"
          size="sm"
          disabled={rows.some((r) => !r.label)}
          busy={busy}
        >
          Save criteria
        </Button>
      </div>
    </form>
  );
}

function LeadFormRouting({ clientId }: { clientId: string }) {
  const toast = useToast();
  const [configs, setConfigs] = useState<
    { platform: string; external_key: string; enabled: boolean }[]
  >([]);
  const [pageId, setPageId] = useState("");
  const [googleKey, setGoogleKey] = useState("");

  const load = useCallback(() => {
    api<{ platform: string; external_key: string; enabled: boolean }[]>(
      `/api/clients/${clientId}/lead-forms`
    )
      .then((cs) => {
        setConfigs(cs);
        setPageId(cs.find((c) => c.platform === "meta")?.external_key ?? "");
        setGoogleKey(cs.find((c) => c.platform === "google")?.external_key ?? "");
      })
      .catch(() => {});
  }, [clientId]);
  useEffect(load, [load]);

  const save = (platform: string, key: string) =>
    api(`/api/clients/${clientId}/lead-forms/${platform}`, {
      method: "PUT",
      body: JSON.stringify({ external_key: key, enabled: true }),
    })
      .then(() => {
        toast(`${platform} routing saved`, "ok");
        load();
      })
      .catch((e) => toast((e as Error).message, "error"));

  const googleUrl = `${API_BASE}/api/webhooks/google/lead-form/${clientId}`;

  return (
    <div className="crm-setup-block">
      <h5 className="crm-subhead crm-subhead--sm">Native lead-form ingestion</h5>
      <form
        className="crm-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (pageId) save("meta", pageId);
        }}
      >
        <Field label="Meta Page ID" description="Instant Forms">
          <input value={pageId} onChange={(e) => setPageId(e.target.value)} />
        </Field>
        <div className="crm-form-actions">
          <Button type="submit" size="sm" disabled={!pageId}>
            Save Meta routing
          </Button>
        </div>
      </form>
      <form
        className="crm-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (googleKey) save("google", googleKey);
        }}
      >
        <Field label="Google lead form key">
          <input value={googleKey} onChange={(e) => setGoogleKey(e.target.value)} />
        </Field>
        <div className="crm-form-actions">
          <Button type="submit" size="sm" disabled={!googleKey}>
            Save Google key
          </Button>
        </div>
      </form>
      <Alert tone="info">
        Google Ads → lead form → webhook: URL <code>{googleUrl}</code> with the
        key above. Meta leads arrive via the app-level leadgen webhook and are
        routed here by Page ID.
      </Alert>
      {configs.length > 0 && (
        <p className="crm-muted">
          Configured:{" "}
          {configs.map((c) => `${c.platform} (${c.external_key})`).join(", ")}
        </p>
      )}
    </div>
  );
}

function ExternalSyncConfig({ clientId }: { clientId: string }) {
  const toast = useToast();
  const [state, setState] = useState<{
    configured: boolean;
    enabled?: boolean;
    url?: string;
  } | null>(null);
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmDisable, setConfirmDisable] = useState(false);

  const load = useCallback(() => {
    api<{ configured: boolean; enabled?: boolean; url?: string }>(
      `/api/clients/${clientId}/external-sync`
    )
      .then((s) => {
        setState(s);
        setUrl(s.url ?? "");
      })
      .catch(() => {});
  }, [clientId]);
  useEffect(load, [load]);

  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!url || secret.length < 8 || busy) return;
    setBusy(true);
    api(`/api/clients/${clientId}/external-sync`, {
      method: "PUT",
      body: JSON.stringify({ enabled: true, url, secret }),
    })
      .then(() => {
        toast("Sync enabled", "ok");
        setSecret("");
        load();
      })
      .catch((err) => toast((err as Error).message, "error"))
      .finally(() => setBusy(false));
  };

  const disable = () =>
    api(`/api/clients/${clientId}/external-sync`, { method: "DELETE" })
      .then(() => {
        toast("Sync removed", "ok");
        load();
      })
      .catch((err) => toast((err as Error).message, "error"));

  return (
    <div className="crm-setup-block">
      <h5 className="crm-subhead crm-subhead--sm">External CRM sync (optional)</h5>
      <p className="crm-muted">
        For clients whose nurture automation still runs in an external CRM:
        status changes push to this webhook, and the external system can post
        back to <code>/api/crm/external-sync/{clientId}</code> with the shared
        secret. Salescale stays the source of truth for reporting.
      </p>
      <form className="crm-form" onSubmit={save}>
        <Field label="External webhook URL">
          <input value={url} onChange={(e) => setUrl(e.target.value)} />
        </Field>
        <Field
          label="Shared secret"
          description={
            state?.configured
              ? "Unchanged unless set"
              : "Minimum 8 characters"
          }
        >
          <input
            type="password"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
          />
        </Field>
        <div className="crm-form-actions">
          <Button
            type="submit"
            variant="primary"
            size="sm"
            disabled={!url || secret.length < 8}
            busy={busy}
          >
            Enable sync
          </Button>
          {state?.configured && (
            <Button
              variant="danger-outline"
              size="sm"
              onClick={() => setConfirmDisable(true)}
            >
              Disable
            </Button>
          )}
        </div>
      </form>
      {state?.configured && (
        <p className="crm-muted">
          Currently {state.enabled ? "enabled" : "disabled"} → {state.url}
        </p>
      )}

      <ConfirmDialog
        open={confirmDisable}
        onCancel={() => setConfirmDisable(false)}
        onConfirm={() => {
          setConfirmDisable(false);
          disable();
        }}
        title="Disable external sync?"
        tone="danger"
        confirmLabel="Disable sync"
        cancelLabel="Cancel"
        rows={[
          {
            field: "External CRM sync",
            oldValue: "enabled",
            newValue: "disabled",
          },
        ]}
      >
        <p className="crm-note">
          Status changes will stop pushing to the external webhook. Salescale
          data is unaffected.
        </p>
      </ConfirmDialog>
    </div>
  );
}
