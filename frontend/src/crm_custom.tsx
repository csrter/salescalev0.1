/**
 * Phase 14 — custom CRM field UI, kept in its own module so crm.tsx stays
 * readable. Everything here rides the same Deep Cobalt primitives and the same
 * `api()` helper as the rest of the CRM view.
 *
 * Exports:
 *  - useCustomFieldDefs()      fetch + reload the org's field definitions
 *  - CustomFieldControl        one form input for a field, by type
 *  - renderCustomValue()       read-only display of a stored value
 *  - customFieldColumns()      DataTable columns for chosen custom fields
 *  - CustomFieldsPanel         drawer section: view + inline edit values
 *  - FieldManager              CRM-setup block: create/edit/reorder/archive
 *  - CsvImportDialog           CSV import with column mapping + create-in-place
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, getSession } from "./api";
import type { Column } from "./components/DataTable";
import { Dialog } from "./components/Dialog";
import { Alert, Badge, Button, Field } from "./components/ui";
import { useToast } from "./components/Toast";
import { ChevronDown, ChevronUp, Eye, Pencil, Plus, Trash2, X } from "./components/icons";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

export type CustomFieldType =
  | "text"
  | "number"
  | "select"
  | "multi_select"
  | "date"
  | "boolean"
  | "url";

export const FIELD_TYPE_LABELS: Record<CustomFieldType, string> = {
  text: "Text",
  number: "Number",
  select: "Select (one)",
  multi_select: "Select (many)",
  date: "Date",
  boolean: "Yes / No",
  url: "URL",
};

export interface CustomFieldOption {
  key: string;
  label: string;
}

export interface CustomFieldDef {
  id: string;
  entity_type: string;
  label: string;
  key: string;
  field_type: CustomFieldType;
  options: CustomFieldOption[] | null;
  required: boolean;
  visible_to_clients: boolean;
  sort_order: number;
  archived_at: string | null;
  created_at: string;
}

export type CustomValues = Record<string, unknown>;

const hasOptions = (t: CustomFieldType) => t === "select" || t === "multi_select";

// --- data hook ---

export function useCustomFieldDefs(enabled: boolean) {
  const [defs, setDefs] = useState<CustomFieldDef[]>([]);
  const [bump, setBump] = useState(0);
  const reload = useCallback(() => setBump((b) => b + 1), []);
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    api<CustomFieldDef[]>("/api/crm/custom-fields")
      .then((d) => alive && setDefs(d))
      .catch(() => alive && setDefs([]));
    return () => {
      alive = false;
    };
  }, [enabled, bump]);
  const active = useMemo(() => defs.filter((d) => !d.archived_at), [defs]);
  return { defs, active, reload };
}

// --- value rendering ---

export function renderCustomValue(def: CustomFieldDef, value: unknown): ReactNode {
  if (value === null || value === undefined || value === "") return null;
  switch (def.field_type) {
    case "boolean":
      return <Badge tone={value ? "ok" : "neutral"}>{value ? "Yes" : "No"}</Badge>;
    case "date":
      return new Date(String(value) + "T00:00:00").toLocaleDateString();
    case "url":
      return (
        <a href={String(value)} target="_blank" rel="noreferrer noopener">
          {String(value)}
        </a>
      );
    case "select": {
      const opt = def.options?.find((o) => o.key === value);
      return opt ? opt.label : <em className="crm-muted">(removed option)</em>;
    }
    case "multi_select": {
      const vals = Array.isArray(value) ? value : [value];
      return (
        <span className="crm-cf-chips">
          {vals.map((v) => {
            const opt = def.options?.find((o) => o.key === v);
            return (
              <Badge key={String(v)} tone="neutral">
                {opt ? opt.label : "(removed option)"}
              </Badge>
            );
          })}
        </span>
      );
    }
    default:
      return String(value);
  }
}

// --- one form control per field type ---

export function CustomFieldControl({
  def,
  value,
  onChange,
}: {
  def: CustomFieldDef;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  switch (def.field_type) {
    case "number":
      return (
        <input
          type="number"
          value={value == null ? "" : String(value)}
          onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
        />
      );
    case "date":
      return (
        <input
          type="date"
          value={value ? String(value) : ""}
          onChange={(e) => onChange(e.target.value || null)}
        />
      );
    case "url":
      return (
        <input
          type="url"
          placeholder="https://…"
          value={value ? String(value) : ""}
          onChange={(e) => onChange(e.target.value || null)}
        />
      );
    case "boolean":
      return (
        <label className="crm-check">
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => onChange(e.target.checked)}
          />
          <span>Yes</span>
        </label>
      );
    case "select":
      return (
        <select
          value={value ? String(value) : ""}
          onChange={(e) => onChange(e.target.value || null)}
        >
          <option value="">—</option>
          {(def.options ?? []).map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
      );
    case "multi_select": {
      const current = new Set(Array.isArray(value) ? (value as string[]) : []);
      return (
        <div className="crm-cf-multi" role="group" aria-label={def.label}>
          {(def.options ?? []).map((o) => (
            <label key={o.key} className="crm-check">
              <input
                type="checkbox"
                checked={current.has(o.key)}
                onChange={(e) => {
                  const next = new Set(current);
                  if (e.target.checked) next.add(o.key);
                  else next.delete(o.key);
                  onChange([...next]);
                }}
              />
              <span>{o.label}</span>
            </label>
          ))}
        </div>
      );
    }
    default:
      return (
        <input
          value={value ? String(value) : ""}
          onChange={(e) => onChange(e.target.value || null)}
        />
      );
  }
}

/** Active fields as a labelled input grid — used by the new-contact form and
 * the contact drawer editor. */
export function CustomFieldInputs({
  defs,
  values,
  onChange,
}: {
  defs: CustomFieldDef[];
  values: CustomValues;
  onChange: (key: string, v: unknown) => void;
}) {
  if (defs.length === 0) return null;
  return (
    <>
      {defs.map((d) => (
        <Field
          key={d.id}
          label={
            <>
              {d.label}
              {d.required && <span className="crm-req"> *</span>}
            </>
          }
        >
          <CustomFieldControl
            def={d}
            value={values[d.key]}
            onChange={(v) => onChange(d.key, v)}
          />
        </Field>
      ))}
    </>
  );
}

// --- DataTable columns for the lead list ---

export function customFieldColumns<T extends { custom_fields?: CustomValues | null }>(
  defs: CustomFieldDef[]
): Column<T>[] {
  return defs.map((d) => ({
    key: `cf_${d.key}`,
    header: d.label,
    render: (row: T) => {
      const node = renderCustomValue(d, row.custom_fields?.[d.key]);
      return node ?? <span className="crm-muted">—</span>;
    },
    sortValue: (row: T) => {
      const v = row.custom_fields?.[d.key];
      if (v == null) return "";
      if (d.field_type === "number") return Number(v);
      if (Array.isArray(v)) return v.join(",");
      if (typeof v === "boolean") return v ? 1 : 0;
      return String(v);
    },
  }));
}

// --- drawer: view + inline edit of a contact's custom fields ---

export function CustomFieldsPanel({
  contactId,
  defs,
  values,
  canEdit,
  onSaved,
}: {
  contactId: string;
  defs: CustomFieldDef[];
  values: CustomValues;
  canEdit: boolean;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<CustomValues>(values);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setDraft(values), [values]);

  // Fields that either exist as active defs or already hold a value (so an
  // archived field with data still shows in the detail view).
  const activeDefs = defs.filter((d) => !d.archived_at);
  const shown = defs.filter(
    (d) => !d.archived_at || values[d.key] != null
  );
  if (shown.length === 0 && !canEdit) return null;

  const save = () => {
    setBusy(true);
    setError(null);
    api(`/api/crm/contacts/${contactId}`, {
      method: "PATCH",
      body: JSON.stringify({ custom_fields: draft }),
    })
      .then(() => {
        setEditing(false);
        onSaved();
        toast("Custom fields saved", "ok");
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setBusy(false));
  };

  return (
    <section className="crm-section">
      <div className="crm-section-head">
        <h6 className="crm-overline">Custom fields</h6>
        {canEdit && activeDefs.length > 0 && !editing && (
          <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
            <Pencil size={13} /> Edit
          </Button>
        )}
      </div>

      {!editing && (
        <>
          {shown.length === 0 && <p className="crm-muted">No custom fields set.</p>}
          <dl className="crm-cf-list">
            {shown.map((d) => (
              <div key={d.id} className="crm-cf-row">
                <dt>{d.label}</dt>
                <dd>
                  {renderCustomValue(d, values[d.key]) ?? (
                    <span className="crm-muted">—</span>
                  )}
                </dd>
              </div>
            ))}
          </dl>
        </>
      )}

      {editing && (
        <div className="crm-form crm-cf-edit">
          <CustomFieldInputs
            defs={activeDefs}
            values={draft}
            onChange={(k, v) => setDraft((p) => ({ ...p, [k]: v }))}
          />
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
              onClick={() => {
                setDraft(values);
                setEditing(false);
                setError(null);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}

// --- CRM setup: the field manager ---

interface DraftOption {
  key?: string;
  label: string;
}

function FieldCreateForm({ onCreated }: { onCreated: () => void }) {
  const [label, setLabel] = useState("");
  const [type, setType] = useState<CustomFieldType>("text");
  const [options, setOptions] = useState<DraftOption[]>([{ label: "" }]);
  const [required, setRequired] = useState(false);
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    if (!label.trim() || busy) return;
    const body: Record<string, unknown> = {
      label: label.trim(),
      field_type: type,
      required,
      visible_to_clients: visible,
    };
    if (hasOptions(type)) {
      const opts = options.map((o) => ({ label: o.label.trim() })).filter((o) => o.label);
      if (opts.length === 0) {
        setError("Add at least one option");
        return;
      }
      body.options = opts;
    }
    setBusy(true);
    setError(null);
    api("/api/crm/custom-fields", { method: "POST", body: JSON.stringify(body) })
      .then(() => {
        setLabel("");
        setType("text");
        setOptions([{ label: "" }]);
        setRequired(false);
        setVisible(false);
        onCreated();
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setBusy(false));
  };

  return (
    <div className="crm-form crm-cf-create">
      <Field label="Field name">
        <input
          value={label}
          placeholder="e.g. Number of trucks"
          onChange={(e) => setLabel(e.target.value)}
        />
      </Field>
      <Field label="Type">
        <select value={type} onChange={(e) => setType(e.target.value as CustomFieldType)}>
          {Object.entries(FIELD_TYPE_LABELS).map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      </Field>
      {hasOptions(type) && (
        <div className="crm-cf-options">
          <span className="crm-overline">Options</span>
          {options.map((o, i) => (
            <div key={i} className="crm-cf-option-row">
              <input
                value={o.label}
                placeholder={`Option ${i + 1}`}
                onChange={(e) =>
                  setOptions(options.map((x, j) => (j === i ? { label: e.target.value } : x)))
                }
              />
              <Button
                variant="ghost"
                size="sm"
                aria-label="Remove option"
                onClick={() => setOptions(options.filter((_, j) => j !== i))}
              >
                <X size={13} />
              </Button>
            </div>
          ))}
          <Button variant="ghost" size="sm" onClick={() => setOptions([...options, { label: "" }])}>
            <Plus size={13} /> Add option
          </Button>
        </div>
      )}
      <label className="crm-check">
        <input type="checkbox" checked={required} onChange={(e) => setRequired(e.target.checked)} />
        <span>Required</span>
      </label>
      <label className="crm-check" title="Show this field in the client portal">
        <input type="checkbox" checked={visible} onChange={(e) => setVisible(e.target.checked)} />
        <span>Visible to clients</span>
      </label>
      {error && (
        <span className="crm-form-error" role="alert">
          {error}
        </span>
      )}
      <div className="crm-form-actions">
        <Button variant="primary" size="sm" busy={busy} disabled={!label.trim()} onClick={submit}>
          <Plus size={14} /> Add field
        </Button>
      </div>
    </div>
  );
}

/** Raw PATCH that can read a 409 remap body (api() flattens detail to a
 * string, losing the in_use list). */
async function patchOptions(
  defId: string,
  options: DraftOption[],
  remap?: Record<string, string>
): Promise<{ ok: true } | { ok: false; inUse: string[] } | { ok: false; message: string }> {
  const session = getSession();
  const resp = await fetch(`${API_BASE}/api/crm/custom-fields/${defId}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
      ...(session ? { Authorization: `Bearer ${session.access_token}` } : {}),
    },
    body: JSON.stringify({ options, option_remap: remap }),
  });
  if (resp.ok) return { ok: true };
  const body = await resp.json().catch(() => ({}));
  if (resp.status === 409 && body.detail?.in_use)
    return { ok: false, inUse: body.detail.in_use as string[] };
  return { ok: false, message: body.detail?.message ?? body.detail ?? `HTTP ${resp.status}` };
}

function OptionsDialog({
  def,
  onClose,
  onSaved,
}: {
  def: CustomFieldDef;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [options, setOptions] = useState<DraftOption[]>(
    (def.options ?? []).map((o) => ({ key: o.key, label: o.label }))
  );
  const [inUse, setInUse] = useState<string[] | null>(null);
  const [remap, setRemap] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const keptKeys = new Set(options.map((o) => o.key).filter(Boolean) as string[]);
  const removedInUse = (inUse ?? []).filter((k) => !keptKeys.has(k));

  const save = async (withRemap?: Record<string, string>) => {
    setBusy(true);
    setError(null);
    const payload = options.map((o) => ({ key: o.key, label: o.label.trim() })).filter((o) => o.label);
    const res = await patchOptions(def.id, payload, withRemap);
    setBusy(false);
    if (res.ok) {
      onSaved();
      onClose();
    } else if ("inUse" in res) {
      setInUse(res.inUse);
    } else {
      setError(res.message);
    }
  };

  return (
    <Dialog open onClose={onClose} title={`Options — ${def.label}`} size="sm">
      <div className="crm-form">
        {options.map((o, i) => (
          <div key={i} className="crm-cf-option-row">
            <input
              value={o.label}
              onChange={(e) =>
                setOptions(options.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))
              }
            />
            <Button
              variant="ghost"
              size="sm"
              aria-label="Remove option"
              onClick={() => setOptions(options.filter((_, j) => j !== i))}
            >
              <X size={13} />
            </Button>
          </div>
        ))}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setOptions([...options, { label: "" }])}
        >
          <Plus size={13} /> Add option
        </Button>

        {removedInUse.length > 0 && (
          <Alert tone="warn" title="Some removed options are in use">
            <p className="crm-muted">
              Choose what happens to contacts that still have these values. “Keep” leaves
              them attached but shown as “(removed option)”.
            </p>
            {removedInUse.map((k) => {
              const oldLabel = def.options?.find((o) => o.key === k)?.label ?? k;
              return (
                <Field key={k} label={oldLabel}>
                  <select
                    value={remap[k] ?? ""}
                    onChange={(e) => setRemap({ ...remap, [k]: e.target.value })}
                  >
                    <option value="">Keep as “(removed option)”</option>
                    {options
                      .filter((o) => o.key && keptKeys.has(o.key))
                      .map((o) => (
                        <option key={o.key} value={o.key}>
                          Remap to {o.label}
                        </option>
                      ))}
                  </select>
                </Field>
              );
            })}
          </Alert>
        )}
        {error && (
          <span className="crm-form-error" role="alert">
            {error}
          </span>
        )}
      </div>
      <div className="crm-form-actions" style={{ marginTop: "0.75rem" }}>
        <Button
          variant="primary"
          size="sm"
          busy={busy}
          onClick={() =>
            save(
              inUse
                ? Object.fromEntries(Object.entries(remap).filter(([, v]) => v))
                : undefined
            )
          }
        >
          {inUse ? "Apply changes" : "Save options"}
        </Button>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </Dialog>
  );
}

function FieldRow({
  def,
  onChanged,
}: {
  def: CustomFieldDef;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [label, setLabel] = useState(def.label);
  const [editingOptions, setEditingOptions] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  useEffect(() => setLabel(def.label), [def.label]);

  const patch = (body: Record<string, unknown>) =>
    api(`/api/crm/custom-fields/${def.id}`, { method: "PATCH", body: JSON.stringify(body) })
      .then(onChanged)
      .catch((e) => toast((e as Error).message, "error"));

  const archive = (archived: boolean) =>
    api(`/api/crm/custom-fields/${def.id}/${archived ? "archive" : "unarchive"}`, {
      method: "POST",
    })
      .then(onChanged)
      .catch((e) => toast((e as Error).message, "error"));

  const del = () =>
    api(`/api/crm/custom-fields/${def.id}`, { method: "DELETE" })
      .then(() => {
        setConfirmDelete(false);
        onChanged();
        toast(`Deleted “${def.label}” and scrubbed its values`, "ok");
      })
      .catch((e) => toast((e as Error).message, "error"));

  return (
    <div className={`crm-cf-manage-row${def.archived_at ? " is-archived" : ""}`}>
      <div className="crm-cf-manage-main">
        <input
          className="crm-cf-label-input"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          onBlur={() => label.trim() && label !== def.label && patch({ label: label.trim() })}
          aria-label={`Rename ${def.label}`}
        />
        <code className="crm-cf-key" title="Immutable API key">
          {def.key}
        </code>
        <Badge tone="neutral">{FIELD_TYPE_LABELS[def.field_type]}</Badge>
        {def.archived_at && <Badge tone="warn">archived</Badge>}
      </div>

      <div className="crm-cf-manage-controls">
        {hasOptions(def.field_type) && !def.archived_at && (
          <Button variant="ghost" size="sm" onClick={() => setEditingOptions(true)}>
            Options
          </Button>
        )}
        <label className="crm-check" title="Required on the contact form">
          <input
            type="checkbox"
            checked={def.required}
            onChange={(e) => patch({ required: e.target.checked })}
          />
          <span>Req</span>
        </label>
        <label className="crm-check" title="Visible in the client portal">
          <input
            type="checkbox"
            checked={def.visible_to_clients}
            onChange={(e) => patch({ visible_to_clients: e.target.checked })}
          />
          <Eye size={13} />
        </label>
        {def.archived_at ? (
          <Button variant="ghost" size="sm" onClick={() => archive(false)}>
            Restore
          </Button>
        ) : (
          <Button variant="ghost" size="sm" onClick={() => archive(true)}>
            Archive
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          aria-label={`Delete ${def.label}`}
          onClick={() => setConfirmDelete(true)}
        >
          <Trash2 size={14} />
        </Button>
      </div>

      {editingOptions && (
        <OptionsDialog
          def={def}
          onClose={() => setEditingOptions(false)}
          onSaved={onChanged}
        />
      )}

      {confirmDelete && (
        <Dialog
          open
          onClose={() => setConfirmDelete(false)}
          title={`Delete “${def.label}”?`}
          size="sm"
        >
          <p>
            This permanently deletes the field and <strong>scrubs its stored values from
            every contact</strong>. This can’t be undone. To hide the field but keep its
            data, archive it instead.
          </p>
          <div className="crm-form-actions" style={{ marginTop: "0.75rem" }}>
            <Button variant="danger" size="sm" onClick={del}>
              Delete &amp; scrub
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirmDelete(false)}>
              Cancel
            </Button>
          </div>
        </Dialog>
      )}
    </div>
  );
}

export function FieldManager({ onChanged }: { onChanged: () => void }) {
  const toast = useToast();
  const { defs, reload } = useCustomFieldDefsAll();
  const [showArchived, setShowArchived] = useState(false);
  const [usage, setUsage] = useState<{ used: number; limit: number } | null>(null);

  const refresh = useCallback(() => {
    reload();
    onChanged();
    api<{ used: number; limit: number }>("/api/crm/custom-fields/usage")
      .then(setUsage)
      .catch(() => {});
  }, [reload, onChanged]);

  useEffect(() => {
    api<{ used: number; limit: number }>("/api/crm/custom-fields/usage")
      .then(setUsage)
      .catch(() => {});
  }, [defs]);

  const active = defs.filter((d) => !d.archived_at);
  const archived = defs.filter((d) => d.archived_at);
  const ordered = [...active].sort((a, b) => a.sort_order - b.sort_order);

  const reorder = (ids: string[]) =>
    api<CustomFieldDef[]>("/api/crm/custom-fields/reorder", {
      method: "POST",
      body: JSON.stringify({ ids }),
    })
      .then(refresh)
      .catch((e) => toast((e as Error).message, "error"));

  const moveField = (idx: number, dir: -1 | 1) => {
    const ids = ordered.map((d) => d.id);
    const j = idx + dir;
    if (j < 0 || j >= ids.length) return;
    [ids[idx], ids[j]] = [ids[j], ids[idx]];
    reorder(ids);
  };

  return (
    <div className="crm-setup-block">
      <div className="crm-section-head">
        <h5 className="crm-subhead crm-subhead--sm">
          Custom fields <span className="crm-muted">(organization-wide)</span>
        </h5>
        {usage && (
          <span className="crm-count">
            {usage.used} of {usage.limit} used
          </span>
        )}
      </div>

      <div className="crm-cf-manage-list">
        {ordered.map((d, i) => (
          <div key={d.id} className="crm-cf-manage-item">
            <div className="crm-cf-reorder">
              <button
                type="button"
                aria-label="Move up"
                disabled={i === 0}
                onClick={() => moveField(i, -1)}
              >
                <ChevronUp size={14} />
              </button>
              <button
                type="button"
                aria-label="Move down"
                disabled={i === ordered.length - 1}
                onClick={() => moveField(i, 1)}
              >
                <ChevronDown size={14} />
              </button>
            </div>
            <FieldRow def={d} onChanged={refresh} />
          </div>
        ))}
        {ordered.length === 0 && (
          <p className="crm-muted">No custom fields yet. Add one below.</p>
        )}
      </div>

      {archived.length > 0 && (
        <div className="crm-cf-archived">
          <Button variant="ghost" size="sm" onClick={() => setShowArchived((s) => !s)}>
            {showArchived ? "Hide" : "Show"} archived ({archived.length})
          </Button>
          {showArchived &&
            archived.map((d) => (
              <div key={d.id} className="crm-cf-manage-item">
                <div className="crm-cf-reorder" aria-hidden />
                <FieldRow def={d} onChanged={refresh} />
              </div>
            ))}
        </div>
      )}

      <FieldCreateForm onCreated={refresh} />
    </div>
  );
}

/** Like useCustomFieldDefs but always includes archived (manager needs both). */
function useCustomFieldDefsAll() {
  const [defs, setDefs] = useState<CustomFieldDef[]>([]);
  const [bump, setBump] = useState(0);
  const reload = useCallback(() => setBump((b) => b + 1), []);
  useEffect(() => {
    let alive = true;
    api<CustomFieldDef[]>("/api/crm/custom-fields?include_archived=true")
      .then((d) => alive && setDefs(d))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [bump]);
  return { defs, reload };
}

// --- CSV import ---

type MappingTarget = string; // "first_name" | "email" | ... | "custom:<key>" | "new" | "skip"

const SYSTEM_TARGETS: { value: string; label: string }[] = [
  { value: "skip", label: "Skip" },
  { value: "first_name", label: "First name" },
  { value: "last_name", label: "Last name" },
  { value: "full_name", label: "Full name" },
  { value: "email", label: "Email" },
  { value: "phone", label: "Phone" },
  { value: "city", label: "City" },
  { value: "state", label: "State" },
  { value: "company", label: "Business name" },
];

/** Header → system target synonym table (first match wins, most specific
 * first). Headers are normalized to a-z0-9 only before lookup, so "First Name",
 * "first_name", "FIRST-NAME" all collapse to "firstname". */
const HEADER_SYNONYMS: { target: string; keys: string[] }[] = [
  { target: "first_name", keys: ["firstname", "first", "fname", "givenname", "forename"] },
  { target: "last_name", keys: ["lastname", "last", "lname", "surname", "familyname"] },
  { target: "full_name", keys: ["name", "fullname", "contactname", "contact", "leadname"] },
  { target: "email", keys: ["email", "emailaddress", "mail", "workemail"] },
  {
    target: "phone",
    keys: [
      "phone",
      "phonenumber",
      "mobile",
      "cell",
      "cellphone",
      "telephone",
      "tel",
      "mobilenumber",
      "contactnumber",
    ],
  },
  { target: "city", keys: ["city", "town", "locality"] },
  { target: "state", keys: ["state", "province", "region", "stateprovince"] },
  {
    target: "company",
    keys: [
      "company",
      "companyname",
      "business",
      "businessname",
      "organization",
      "organisation",
      "employer",
      "accountname",
    ],
  },
];

const normalizeHeader = (h: string) => h.toLowerCase().replace(/[^a-z0-9]/g, "");

/** Minimal CSV parse (handles quoted fields + commas + CRLF). Good enough for
 * the paste-a-CSV import; large/edge files are a later concern. */
function parseCsv(text: string): { headers: string[]; rows: Record<string, string>[] } {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') {
        cell += '"';
        i++;
      } else if (c === '"') quoted = false;
      else cell += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") {
      row.push(cell);
      cell = "";
    } else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell);
      cell = "";
      if (row.some((v) => v !== "")) rows.push(row);
      row = [];
    } else cell += c;
  }
  if (cell !== "" || row.length) {
    row.push(cell);
    if (row.some((v) => v !== "")) rows.push(row);
  }
  if (rows.length === 0) return { headers: [], rows: [] };
  const headers = rows[0].map((h) => h.trim());
  const out = rows.slice(1).map((r) => {
    const obj: Record<string, string> = {};
    headers.forEach((h, i) => (obj[h] = (r[i] ?? "").trim()));
    return obj;
  });
  return { headers, rows: out };
}

/** Stringify a scalar/complex JSON value for a flat cell. null/undefined → "";
 * strings pass through; numbers/booleans stringify; objects/arrays JSON-encode. */
function jsonCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

const ROW_ARRAY_KEYS = ["contacts", "leads", "rows", "data", "records"];

/** Parse a JSON contacts export into the same {headers, rows} shape parseCsv
 * produces. Accepts either a top-level array of flat objects, or an object
 * whose first array-valued key (preferring the conventional names above, else
 * the first array value found) holds the rows. Headers are the union of keys
 * in first-seen order. */
function parseJson(text: string): { headers: string[]; rows: Record<string, string>[] } {
  const data = JSON.parse(text);
  let arr: unknown;
  if (Array.isArray(data)) {
    arr = data;
  } else if (data && typeof data === "object") {
    const obj = data as Record<string, unknown>;
    const namedKey = ROW_ARRAY_KEYS.find((k) => Array.isArray(obj[k]));
    if (namedKey) arr = obj[namedKey];
    else arr = Object.values(obj).find((v) => Array.isArray(v));
  }
  if (!Array.isArray(arr)) return { headers: [], rows: [] };

  const headers: string[] = [];
  const seen = new Set<string>();
  const rows: Record<string, string>[] = [];
  for (const item of arr) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const rec = item as Record<string, unknown>;
    const out: Record<string, string> = {};
    for (const [k, v] of Object.entries(rec)) {
      if (!seen.has(k)) {
        seen.add(k);
        headers.push(k);
      }
      out[k] = jsonCell(v);
    }
    rows.push(out);
  }
  return { headers, rows };
}

function inferType(values: string[]): CustomFieldType {
  const nonEmpty = values.filter((v) => v !== "");
  if (nonEmpty.length === 0) return "text";
  if (nonEmpty.every((v) => !Number.isNaN(Number(v)))) return "number";
  if (nonEmpty.every((v) => /^(true|false|yes|no)$/i.test(v))) return "boolean";
  if (nonEmpty.every((v) => /^\d{4}-\d{2}-\d{2}/.test(v))) return "date";
  return "text";
}

export function CsvImportDialog({
  clientId,
  defs,
  onClose,
  onDone,
}: {
  clientId: string;
  defs: CustomFieldDef[];
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [parsed, setParsed] = useState<{ headers: string[]; rows: Record<string, string>[] } | null>(
    null
  );
  const [mapping, setMapping] = useState<Record<string, MappingTarget>>({});
  const [newTypes, setNewTypes] = useState<Record<string, CustomFieldType>>({});
  const [busy, setBusy] = useState(false);
  const [verify, setVerify] = useState(true);
  const [result, setResult] = useState<{ imported: number; failed: { row: number; error: string }[] } | null>(
    null
  );
  const [error, setError] = useState<string | null>(null);

  const load = (text: string, isJson: boolean) => {
    const p = isJson ? parseJson(text) : parseCsv(text);
    if (p.headers.length === 0) {
      setError(
        isJson
          ? "Couldn’t find any contact rows in that JSON file."
          : "Couldn’t find any columns in that CSV."
      );
      return;
    }
    // Auto-map headers via the normalized synonym table. Each system target is
    // assigned at most once — the earliest (most specific) matching column wins.
    const guess: Record<string, MappingTarget> = {};
    const usedTargets = new Set<string>();
    for (const h of p.headers) {
      const norm = normalizeHeader(h);
      const hit = HEADER_SYNONYMS.find(
        (s) => !usedTargets.has(s.target) && s.keys.includes(norm)
      );
      if (hit) {
        guess[h] = hit.target;
        usedTargets.add(hit.target);
      } else {
        const match = defs.find(
          (d) => normalizeHeader(d.label) === norm && !d.archived_at
        );
        guess[h] = match ? `custom:${match.key}` : "skip";
      }
    }
    // Only keep a full_name mapping when neither first_name nor last_name was
    // detected on any column (the backend splits full_name into both).
    if (usedTargets.has("first_name") || usedTargets.has("last_name")) {
      for (const h of Object.keys(guess))
        if (guess[h] === "full_name") guess[h] = "skip";
    }
    setMapping(guess);
    setParsed(p);
    setError(null);
    setResult(null);
  };

  const onFile = (f: File) => {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result ?? "");
      const trimmed = text.trimStart();
      const isJson =
        /\.json$/i.test(f.name) ||
        trimmed.startsWith("[") ||
        trimmed.startsWith("{");
      try {
        load(text, isJson);
      } catch (err) {
        setError(
          isJson
            ? `Couldn’t parse that JSON file: ${(err as Error).message}`
            : (err as Error).message
        );
      }
    };
    reader.readAsText(f);
  };

  const submit = () => {
    if (!parsed) return;
    const new_fields = parsed.headers
      .filter((h) => mapping[h] === "new")
      .map((h) => ({
        column: h,
        label: h,
        field_type: newTypes[h] ?? "text",
      }));
    setBusy(true);
    setError(null);
    api<{ imported: number; failed: { row: number; error: string }[]; created_fields: unknown[] }>(
      "/api/crm/contacts/import",
      {
        method: "POST",
        body: JSON.stringify({
          client_id: clientId,
          mapping,
          rows: parsed.rows,
          new_fields,
          verify,
        }),
      }
    )
      .then((r) => {
        setResult(r);
        onDone();
        toast(
          verify && r.imported > 0
            ? `Imported ${r.imported} contact(s) — verifying emails in the background`
            : `Imported ${r.imported} contact(s)`,
          "ok"
        );
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setBusy(false));
  };

  const activeDefs = defs.filter((d) => !d.archived_at);

  return (
    <Dialog open onClose={onClose} title="Import contacts from CSV or JSON" size="lg">
      {!parsed && (
        <div className="crm-csv-drop">
          <p className="crm-muted">
            Upload a CSV (first row = column headers) or a JSON file (an array of
            contact objects, or an object with a <code>contacts</code>/<code>rows</code>
            array). You’ll map each column to a contact field — including custom
            fields, which you can create on the spot.
          </p>
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.json,text/csv,application/json"
            onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
          />
          {error && (
            <span className="crm-form-error" role="alert">
              {error}
            </span>
          )}
        </div>
      )}

      {parsed && !result && (
        <>
          <p className="crm-muted">
            {parsed.rows.length} row(s). Map each column:
          </p>
          <div className="crm-csv-map">
            {parsed.headers.map((h) => {
              const sample = parsed.rows.slice(0, 3).map((r) => r[h]).filter(Boolean)[0];
              return (
                <div key={h} className="crm-csv-map-row">
                  <div className="crm-csv-col">
                    <strong>{h}</strong>
                    {sample && <span className="crm-muted"> e.g. {sample}</span>}
                  </div>
                  <select
                    value={mapping[h] ?? "skip"}
                    onChange={(e) => {
                      setMapping({ ...mapping, [h]: e.target.value });
                      if (e.target.value === "new" && !newTypes[h])
                        setNewTypes({
                          ...newTypes,
                          [h]: inferType(parsed.rows.map((r) => r[h])),
                        });
                    }}
                  >
                    <optgroup label="Contact fields">
                      {SYSTEM_TARGETS.map((t) => (
                        <option key={t.value} value={t.value}>
                          {t.label}
                        </option>
                      ))}
                    </optgroup>
                    {activeDefs.length > 0 && (
                      <optgroup label="Custom fields">
                        {activeDefs.map((d) => (
                          <option key={d.key} value={`custom:${d.key}`}>
                            {d.label}
                          </option>
                        ))}
                      </optgroup>
                    )}
                    <optgroup label="New">
                      <option value="new">+ Create new custom field…</option>
                    </optgroup>
                  </select>
                  {mapping[h] === "new" && (
                    <select
                      aria-label={`Type for new field ${h}`}
                      value={newTypes[h] ?? "text"}
                      onChange={(e) =>
                        setNewTypes({ ...newTypes, [h]: e.target.value as CustomFieldType })
                      }
                    >
                      {Object.entries(FIELD_TYPE_LABELS)
                        .filter(([v]) => !hasOptions(v as CustomFieldType))
                        .map(([v, l]) => (
                          <option key={v} value={v}>
                            {l}
                          </option>
                        ))}
                    </select>
                  )}
                </div>
              );
            })}
          </div>
          {error && (
            <span className="crm-form-error" role="alert">
              {error}
            </span>
          )}
          <label className="crm-check">
            <input
              type="checkbox"
              checked={verify}
              onChange={(e) => setVerify(e.target.checked)}
            />
            <span>Verify email addresses after import (uses your monthly quota)</span>
          </label>
          <div className="crm-form-actions" style={{ marginTop: "0.75rem" }}>
            <Button variant="primary" size="sm" busy={busy} onClick={submit}>
              Import {parsed.rows.length} row(s)
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setParsed(null)}>
              Choose another file
            </Button>
          </div>
        </>
      )}

      {result && (
        <div className="crm-csv-result">
          <Alert tone={result.failed.length ? "warn" : "ok"}>
            Imported <strong>{result.imported}</strong> contact(s).
            {result.failed.length > 0 && ` ${result.failed.length} row(s) skipped.`}
          </Alert>
          {result.failed.length > 0 && (
            <ul className="crm-csv-errors">
              {result.failed.slice(0, 50).map((f) => (
                <li key={f.row}>
                  Row {f.row + 1}: {f.error}
                </li>
              ))}
            </ul>
          )}
          <div className="crm-form-actions" style={{ marginTop: "0.75rem" }}>
            <Button variant="primary" size="sm" onClick={onClose}>
              Done
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}
