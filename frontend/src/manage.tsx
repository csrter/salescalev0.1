/**
 * Staged-change flow: every write action anywhere in the UI goes through
 * useManage().stage(...), which stages the change server-side and opens the
 * confirmation modal showing the exact before/after diff the backend will
 * apply. Nothing executes until the user clicks Confirm — matching the
 * server-side guarantee that there is no unstaged write path.
 *
 * The confirmation renders as the Change Receipt (DESIGN.md §4.5) via
 * ConfirmDialog: warn/danger edge, struck old → bold new in mono, abs+% delta
 * for money fields, Cancel-first focus, confirm disabled until rows render,
 * scrim-close off. The stage → confirm → execute call sequence is identical
 * to the pre-revamp flow — presentation only, no bypass.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import {
  api,
  cancelChange,
  executeChange,
  getPlatforms,
  listAudit,
  listChanges,
  stageChange,
  type AuditEntry,
  type Client,
  type PendingChange,
  type StageChangeBody,
} from "./api";
import { DataTable } from "./components/DataTable";
import {
  ConfirmDialog,
  formatMoneyDelta,
  type ReceiptRow,
} from "./components/Dialog";
import { useToast } from "./components/Toast";
import {
  Alert,
  Badge,
  Button,
  EmptyState,
  PlatformChip,
} from "./components/ui";

interface ManageContextValue {
  stage: (
    body: StageChangeBody,
    onExecuted?: (change: PendingChange) => void
  ) => Promise<void>;
  confirm: (
    change: PendingChange,
    onExecuted?: (change: PendingChange) => void
  ) => void;
}

const ManageContext = createContext<ManageContextValue | null>(null);

export function useManage(): ManageContextValue {
  const ctx = useContext(ManageContext);
  if (!ctx) throw new Error("useManage outside ManageProvider");
  return ctx;
}

interface ModalState {
  change: PendingChange;
  onExecuted?: (change: PendingChange) => void;
  busy: boolean;
  error: string | null;
}

/** Names for the receipt rows ("{Client} · {Campaign} · {Field}" + platform
 * chip). Fetched lazily on first confirm and cached for the session. */
interface ReceiptNames {
  clients: Record<string, string>;
  platforms: Record<string, string>;
}

function pingSave(phase: "saving" | "saved" | "error") {
  window.dispatchEvent(new CustomEvent("save-tick", { detail: { phase } }));
}

export function ManageProvider({ children }: { children: ReactNode }) {
  const [modal, setModal] = useState<ModalState | null>(null);
  const [names, setNames] = useState<ReceiptNames | null>(null);
  const toast = useToast();

  const confirm = useCallback(
    (change: PendingChange, onExecuted?: (c: PendingChange) => void) => {
      setModal({ change, onExecuted, busy: false, error: null });
    },
    []
  );

  const stage = useCallback(
    async (body: StageChangeBody, onExecuted?: (c: PendingChange) => void) => {
      const change = await stageChange(body);
      confirm(change, onExecuted);
    },
    [confirm]
  );

  // Resolve client + platform display names once a receipt is on screen.
  useEffect(() => {
    if (!modal || names) return;
    let stale = false;
    Promise.allSettled([api<Client[]>("/api/clients"), getPlatforms()]).then(
      ([c, p]) => {
        if (stale) return;
        const clients: Record<string, string> = {};
        if (c.status === "fulfilled")
          c.value.forEach((x) => (clients[x.id] = x.name));
        const platforms: Record<string, string> = {};
        if (p.status === "fulfilled")
          p.value.forEach((x) => (platforms[x.id] = x.name));
        setNames({ clients, platforms });
      }
    );
    return () => {
      stale = true;
    };
  }, [modal, names]);

  const runExecute = async () => {
    if (!modal) return;
    setModal({ ...modal, busy: true, error: null });
    pingSave("saving");
    try {
      const executed = await executeChange(modal.change.id);
      pingSave("saved");
      const c = modal.change;
      toast(
        `Applied to ${names?.platforms[c.platform] ?? c.platform}: ` +
          `${ACTION_LABELS[c.action] ?? c.action} ${c.entity_type.replace(/_/g, " ")}` +
          (c.entity_name ? ` — ${c.entity_name}` : ""),
        "ok"
      );
      modal.onExecuted?.(executed);
      setModal(null);
    } catch (e) {
      pingSave("error");
      setModal({ ...modal, busy: false, error: (e as Error).message });
    }
  };

  const runCancel = async () => {
    if (!modal || modal.busy) return;
    try {
      await cancelChange(modal.change.id);
    } catch {
      // already expired/canceled server-side — closing is still correct
    }
    setModal(null);
  };

  return (
    <ManageContext.Provider value={{ stage, confirm }}>
      {children}
      {modal && (
        <ChangeReceipt
          change={modal.change}
          busy={modal.busy}
          error={modal.error}
          names={names}
          onConfirm={runExecute}
          onCancel={runCancel}
        />
      )}
    </ManageContext.Provider>
  );
}

/* --- value formatting: FIELD-driven money handling (never magnitude-guessed).
   A known set of budget/bid field names (plus the platform-wide `_micros`
   suffix convention) formats micros → dollars; every other numeric field
   renders raw. --- */

const MONEY_FIELDS = new Set([
  "daily_budget_micros",
  "lifetime_budget_micros",
  "bid_micros",
  "budget_micros",
  "cpc_bid_micros",
  "target_cpa_micros",
]);

function isMoneyField(field: string): boolean {
  return MONEY_FIELDS.has(field) || field.endsWith("_micros");
}

function fmtValue(field: string, v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (isMoneyField(field) && typeof v === "number")
    return `$${(v / 1_000_000).toFixed(2)}`;
  if (Array.isArray(v)) return v.map(String).join(", ");
  return String(v);
}

/** "daily_budget_micros" → "Daily budget", "final_url" → "Final url". */
function fieldLabel(field: string): string {
  const base = field.replace(/_micros$/, "").replace(/_/g, " ");
  return base.charAt(0).toUpperCase() + base.slice(1);
}

const ACTION_LABELS: Record<string, string> = {
  create: "Create",
  update: "Update",
  pause: "Pause",
  resume: "Resume",
  add: "Add",
  remove: "Remove",
};

/**
 * The Change Receipt (§4.5): the ONLY rendering of the staged-write
 * confirmation. Danger edge when the change pauses spend or reduces budget.
 */
function ChangeReceipt({
  change,
  busy,
  error,
  names,
  onConfirm,
  onCancel,
}: {
  change: PendingChange;
  busy: boolean;
  error: string | null;
  names: ReceiptNames | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const platformName = names?.platforms[change.platform] ?? change.platform;
  const clientName = names?.clients[change.client_id];

  const rows: ReceiptRow[] = change.diff.map((d) => {
    const money =
      isMoneyField(d.field) &&
      typeof d.before === "number" &&
      typeof d.after === "number";
    return {
      key: d.field,
      client: clientName,
      campaign: change.entity_name ?? undefined,
      field: fieldLabel(d.field),
      platform: platformName,
      oldValue: fmtValue(d.field, d.before),
      newValue: fmtValue(d.field, d.after),
      delta: money
        ? formatMoneyDelta(
            (d.before as number) / 1_000_000,
            (d.after as number) / 1_000_000
          )
        : null,
    };
  });

  const reducesBudget = change.diff.some(
    (d) =>
      isMoneyField(d.field) &&
      typeof d.before === "number" &&
      typeof d.after === "number" &&
      d.after < d.before
  );
  const danger =
    change.action === "pause" || change.action === "remove" || reducesBudget;

  return (
    <ConfirmDialog
      open
      rows={rows}
      tone={danger ? "danger" : "warn"}
      busy={busy}
      onCancel={onCancel}
      onConfirm={onConfirm}
      title={`${ACTION_LABELS[change.action] ?? change.action} ${change.entity_type.replace(/_/g, " ")}${
        change.entity_name ? ` — ${change.entity_name}` : ""
      }`}
    >
      <p className="muted">
        Nothing has been written yet — applying executes against the live{" "}
        {platformName} account.
      </p>
      {error && <Alert tone="danger">{error}</Alert>}
    </ConfirmDialog>
  );
}

export function PendingChangesPanel() {
  const { confirm } = useManage();
  const toast = useToast();
  const [changes, setChanges] = useState<PendingChange[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    listChanges("pending")
      .then(setChanges)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);
  useEffect(load, [load]);

  const discard = (id: string) =>
    cancelChange(id)
      .then(() => {
        toast("Staged change discarded", "info");
        load();
      })
      .catch((e) => toast((e as Error).message, "error"));

  return (
    <div>
      <div className="page-head">
        <div>
          <h2>Pending changes</h2>
          <p className="page-sub">
            Staged writes awaiting confirmation — nothing touches a live ad
            account until it's reviewed here.
          </p>
        </div>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      <DataTable<PendingChange>
        loading={loading}
        rows={changes}
        rowKey={(c) => c.id}
        caption="Pending staged changes"
        initialSort="-staged"
        empty={
          <EmptyState title="Nothing staged">
            Changes staged anywhere in the app — budgets, pauses, new campaigns
            — queue here for review before they touch a live ad account.
          </EmptyState>
        }
        columns={[
          {
            key: "staged",
            header: "Staged",
            render: (c) => new Date(c.created_at).toLocaleString(),
            sortValue: (c) => c.created_at,
          },
          {
            key: "platform",
            header: "Platform",
            render: (c) => <PlatformChip name={c.platform} />,
            sortValue: (c) => c.platform,
          },
          {
            key: "entity",
            header: "Entity",
            render: (c) =>
              `${c.entity_type.replace(/_/g, " ")}${
                c.entity_name ? ` — ${c.entity_name}` : ""
              }`,
          },
          {
            key: "action",
            header: "Action",
            render: (c) => ACTION_LABELS[c.action] ?? c.action,
            sortValue: (c) => c.action,
          },
          {
            key: "expires",
            header: "Expires",
            render: (c) => new Date(c.expires_at).toLocaleString(),
            sortValue: (c) => c.expires_at,
          },
          {
            key: "review",
            header: "",
            render: (c) => (
              <span className="row-actions">
                <Button
                  size="sm"
                  variant="primary"
                  onClick={() => confirm(c, load)}
                >
                  Review &amp; confirm
                </Button>
                <Button size="sm" variant="ghost" onClick={() => discard(c.id)}>
                  Discard
                </Button>
              </span>
            ),
          },
        ]}
      />
    </div>
  );
}

export function AuditLogView() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [platform, setPlatform] = useState("");
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params: Record<string, string> = {};
    if (platform) params.platform = platform;
    if (status) params.status = status;
    setLoading(true);
    listAudit(params)
      .then(setEntries)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [platform, status]);

  return (
    <div>
      <div className="page-head">
        <div>
          <h2>Audit log</h2>
          <p className="page-sub">
            Every executed or failed write to a live ad account, who staged it,
            and the exact diff applied.
          </p>
        </div>
      </div>
      <div className="toggle">
        <select value={platform} onChange={(e) => setPlatform(e.target.value)}>
          <option value="">All platforms</option>
          <option value="meta">Meta</option>
          <option value="google">Google</option>
        </select>
        <select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All outcomes</option>
          <option value="success">Success</option>
          <option value="failed">Failed</option>
        </select>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}
      <DataTable<AuditEntry>
        loading={loading}
        rows={entries}
        rowKey={(e) => e.id}
        emptyMessage="No entries."
        initialSort="-when"
        columns={[
          {
            key: "when",
            header: "When",
            render: (e) => new Date(e.created_at).toLocaleString(),
            sortValue: (e) => e.created_at,
          },
          {
            key: "who",
            header: "Who",
            render: (e) => <span title={e.user_email}>{e.user_name}</span>,
            sortValue: (e) => e.user_name,
          },
          {
            key: "platform",
            header: "Platform",
            render: (e) => e.platform,
            sortValue: (e) => e.platform,
          },
          {
            key: "entity",
            header: "Entity",
            render: (e) =>
              `${e.entity_type}${e.entity_name ? ` — ${e.entity_name}` : ""}`,
          },
          {
            key: "action",
            header: "Action",
            render: (e) => e.action,
            sortValue: (e) => e.action,
          },
          {
            key: "change",
            header: "Change",
            render: (e) =>
              e.diff
                .map(
                  (d) =>
                    `${d.field}: ${fmtValue(d.field, d.before)} → ${fmtValue(d.field, d.after)}`
                )
                .join("; "),
          },
          {
            key: "outcome",
            header: "Outcome",
            render: (e) => (
              <>
                <Badge tone={e.status}>{e.status}</Badge>
                {e.error_detail ? (
                  <span className="muted"> {e.error_detail}</span>
                ) : null}
              </>
            ),
            sortValue: (e) => e.status,
          },
        ]}
      />
    </div>
  );
}
