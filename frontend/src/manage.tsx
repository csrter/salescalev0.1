/**
 * Staged-change flow: every write action anywhere in the UI goes through
 * useManage().stage(...), which stages the change server-side and opens the
 * confirmation modal showing the exact before/after diff the backend will
 * apply. Nothing executes until the user clicks Confirm — matching the
 * server-side guarantee that there is no unstaged write path.
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
  cancelChange,
  executeChange,
  listAudit,
  listChanges,
  stageChange,
  type AuditEntry,
  type PendingChange,
  type StageChangeBody,
} from "./api";

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

export function ManageProvider({ children }: { children: ReactNode }) {
  const [modal, setModal] = useState<ModalState | null>(null);

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

  const runExecute = async () => {
    if (!modal) return;
    setModal({ ...modal, busy: true, error: null });
    try {
      const executed = await executeChange(modal.change.id);
      modal.onExecuted?.(executed);
      setModal(null);
    } catch (e) {
      setModal({ ...modal, busy: false, error: (e as Error).message });
    }
  };

  const runCancel = async () => {
    if (!modal) return;
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
        <ConfirmModal
          change={modal.change}
          busy={modal.busy}
          error={modal.error}
          onConfirm={runExecute}
          onCancel={runCancel}
        />
      )}
    </ManageContext.Provider>
  );
}

function fmtValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number" && v >= 10_000) {
    // budget/bid micros → dollars for readability
    return `$${(v / 1_000_000).toFixed(2)}`;
  }
  return String(v);
}

const ACTION_LABELS: Record<string, string> = {
  create: "Create",
  update: "Update",
  pause: "Pause",
  resume: "Resume",
  add: "Add",
  remove: "Remove",
};

function ConfirmModal({
  change,
  busy,
  error,
  onConfirm,
  onCancel,
}: {
  change: PendingChange;
  busy: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="modal-backdrop">
      <div className="modal">
        <h3>
          Confirm: {ACTION_LABELS[change.action] ?? change.action}{" "}
          {change.entity_type.replace("_", " ")}
          {change.entity_name ? ` — ${change.entity_name}` : ""}
        </h3>
        <p className="muted">
          Platform: <strong>{change.platform}</strong>. This will change the
          live ad account. Nothing has been written yet.
        </p>
        <table className="diff">
          <thead>
            <tr>
              <th>Field</th>
              <th>Before</th>
              <th>After</th>
            </tr>
          </thead>
          <tbody>
            {change.diff.map((row) => (
              <tr key={row.field}>
                <td>{row.field}</td>
                <td className="before">{fmtValue(row.before)}</td>
                <td className="after">{fmtValue(row.after)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {error && <p className="error">{error}</p>}
        <div className="modal-actions">
          <button onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button className="danger" onClick={onConfirm} disabled={busy}>
            {busy ? "Applying…" : "Confirm & apply"}
          </button>
        </div>
      </div>
    </div>
  );
}

export function PendingChangesPanel() {
  const { confirm } = useManage();
  const [changes, setChanges] = useState<PendingChange[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => {
    listChanges("pending").then(setChanges).catch((e) => setError(e.message));
  }, []);
  useEffect(load, [load]);

  if (error) return <p className="error">{error}</p>;
  return (
    <div>
      <h2>Pending changes</h2>
      {changes.length === 0 && <p className="muted">Nothing staged.</p>}
      <ul className="cards">
        {changes.map((c) => (
          <li key={c.id}>
            <strong>
              {ACTION_LABELS[c.action] ?? c.action} {c.entity_type}
              {c.entity_name ? ` — ${c.entity_name}` : ""}
            </strong>
            <span className="muted">
              {c.platform} · staged {new Date(c.created_at).toLocaleString()}
            </span>
            <span>
              <button onClick={() => confirm(c, load)}>Review & confirm</button>
              <button
                onClick={() => cancelChange(c.id).then(load)}
                className="link"
              >
                Discard
              </button>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AuditLogView() {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [platform, setPlatform] = useState("");
  const [status, setStatus] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const params: Record<string, string> = {};
    if (platform) params.platform = platform;
    if (status) params.status = status;
    listAudit(params).then(setEntries).catch((e) => setError(e.message));
  }, [platform, status]);

  return (
    <div>
      <h2>Audit log</h2>
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
      {error && <p className="error">{error}</p>}
      {entries.length === 0 && <p className="muted">No entries.</p>}
      <table className="audit">
        <thead>
          <tr>
            <th>When</th>
            <th>Who</th>
            <th>Platform</th>
            <th>Entity</th>
            <th>Action</th>
            <th>Change</th>
            <th>Outcome</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => (
            <tr key={e.id}>
              <td>{new Date(e.created_at).toLocaleString()}</td>
              <td title={e.user_email}>{e.user_name}</td>
              <td>{e.platform}</td>
              <td>
                {e.entity_type}
                {e.entity_name ? ` — ${e.entity_name}` : ""}
              </td>
              <td>{e.action}</td>
              <td>
                {e.diff
                  .map((d) => `${d.field}: ${fmtValue(d.before)} → ${fmtValue(d.after)}`)
                  .join("; ")}
              </td>
              <td>
                <span className={`badge ${e.status}`}>{e.status}</span>
                {e.error_detail ? (
                  <span className="muted"> {e.error_detail}</span>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
