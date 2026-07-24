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
import {
  api,
  getSession,
  createResearchField,
  deleteResearchField,
  listContactLists,
  listResearchFields,
  updateResearchField,
  type ContactList,
  type ResearchFieldDef,
} from "./api";
import type { Column } from "./components/DataTable";
import { Dialog } from "./components/Dialog";
import { Alert, Badge, Button, Field, Segmented } from "./components/ui";
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

// --- AI research fields ("Claygent-lite") ---
// Org-defined research questions, answered per-contact by the AI provider —
// grounded only in the contact's own CRM/enrichment facts and their own
// website (never Meta surfaces, never free-generated). Mirrors FieldManager's
// UX (list/create/edit/archive/delete) but has no options/reorder/visibility
// concerns, since research values are always team-only.

function ResearchFieldCreateForm({ onCreated }: { onCreated: () => void }) {
  const [label, setLabel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [maxWords, setMaxWords] = useState(40);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = () => {
    if (!label.trim() || !prompt.trim() || busy) return;
    setBusy(true);
    setError(null);
    createResearchField({
      label: label.trim(),
      prompt: prompt.trim(),
      max_words: maxWords,
    })
      .then(() => {
        setLabel("");
        setPrompt("");
        setMaxWords(40);
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
          placeholder="e.g. Fleet size"
          onChange={(e) => setLabel(e.target.value)}
        />
      </Field>
      <Field
        label="Research prompt"
        description="What should the AI look for? It only sees this contact/company's CRM data and their own website."
      >
        <textarea
          rows={2}
          value={prompt}
          placeholder="How many service trucks does this business appear to run?"
          onChange={(e) => setPrompt(e.target.value)}
        />
      </Field>
      <Field label="Max words" optional>
        <input
          type="number"
          min={1}
          max={200}
          value={maxWords}
          onChange={(e) => setMaxWords(Number(e.target.value) || 40)}
        />
      </Field>
      {error && (
        <span className="crm-form-error" role="alert">
          {error}
        </span>
      )}
      <div className="crm-form-actions">
        <Button
          variant="primary"
          size="sm"
          busy={busy}
          disabled={!label.trim() || !prompt.trim()}
          onClick={submit}
        >
          <Plus size={14} /> Add research field
        </Button>
      </div>
    </div>
  );
}

function ResearchFieldRow({
  def,
  onChanged,
}: {
  def: ResearchFieldDef;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [label, setLabel] = useState(def.label);
  const [prompt, setPrompt] = useState(def.prompt);
  const [maxWords, setMaxWords] = useState(def.max_words);
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  useEffect(() => {
    setLabel(def.label);
    setPrompt(def.prompt);
    setMaxWords(def.max_words);
  }, [def.label, def.prompt, def.max_words]);

  const save = () => {
    if (!label.trim() || !prompt.trim()) return;
    setBusy(true);
    updateResearchField(def.id, {
      label: label.trim(),
      prompt: prompt.trim(),
      max_words: maxWords,
    })
      .then(() => {
        setEditing(false);
        onChanged();
      })
      .catch((e) => toast((e as Error).message, "error"))
      .finally(() => setBusy(false));
  };

  const archive = (archived: boolean) =>
    updateResearchField(def.id, { archived })
      .then(onChanged)
      .catch((e) => toast((e as Error).message, "error"));

  const del = () =>
    deleteResearchField(def.id)
      .then(() => {
        setConfirmDelete(false);
        onChanged();
        toast(`Deleted “${def.label}” and scrubbed its values`, "ok");
      })
      .catch((e) => toast((e as Error).message, "error"));

  return (
    <div className={`crm-cf-manage-row${def.archived ? " is-archived" : ""}`}>
      <div className="crm-cf-manage-main">
        {editing ? (
          <input
            className="crm-cf-label-input"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            aria-label={`Rename ${def.label}`}
          />
        ) : (
          <span className="crm-cf-label-input">{def.label}</span>
        )}
        <code className="crm-cf-key" title="Use in personalization as {{research.<key>}}">
          {`{{research.${def.key}}}`}
        </code>
        {def.archived && <Badge tone="warn">archived</Badge>}
      </div>

      {editing ? (
        <div className="crm-form crm-research-edit">
          <Field label="Research prompt">
            <textarea
              rows={2}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
          </Field>
          <Field label="Max words" optional>
            <input
              type="number"
              min={1}
              max={200}
              value={maxWords}
              onChange={(e) => setMaxWords(Number(e.target.value) || 40)}
            />
          </Field>
          <div className="crm-form-actions">
            <Button variant="primary" size="sm" busy={busy} onClick={save}>
              Save
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setLabel(def.label);
                setPrompt(def.prompt);
                setMaxWords(def.max_words);
                setEditing(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <>
          <p className="crm-muted crm-research-prompt">{def.prompt}</p>
          <div className="crm-cf-manage-controls">
            <Button variant="ghost" size="sm" onClick={() => setEditing(true)}>
              <Pencil size={13} /> Edit
            </Button>
            {def.archived ? (
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
        </>
      )}

      {confirmDelete && (
        <Dialog
          open
          onClose={() => setConfirmDelete(false)}
          title={`Delete “${def.label}”?`}
          size="sm"
        >
          <p>
            This permanently deletes the research field and <strong>scrubs its
            stored values from every contact</strong>. This can’t be undone. To
            hide it but keep the data, archive it instead.
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

/** CRM-setup block: org-defined AI research prompts, answered per-contact.
 * Renders next to FieldManager in the setup panel. */
export function ResearchFieldManager() {
  const [defs, setDefs] = useState<ResearchFieldDef[]>([]);
  const [showArchived, setShowArchived] = useState(false);

  const refresh = useCallback(() => {
    listResearchFields().then(setDefs).catch(() => {});
  }, []);
  useEffect(refresh, [refresh]);

  const active = defs.filter((d) => !d.archived);
  const archived = defs.filter((d) => d.archived);

  return (
    <div className="crm-setup-block">
      <div className="crm-section-head">
        <h5 className="crm-subhead crm-subhead--sm">
          AI research fields <span className="crm-muted">(organization-wide)</span>
        </h5>
      </div>
      <p className="crm-muted">
        Org-defined research questions the AI answers per contact, grounded
        only in that contact's CRM data and their own website — never
        free-generated, never scraped from Meta. Use a field with{" "}
        <code>{"{{research.<key>}}"}</code> in a campaign step.
      </p>

      <div className="crm-cf-manage-list">
        {active.map((d) => (
          <ResearchFieldRow key={d.id} def={d} onChanged={refresh} />
        ))}
        {active.length === 0 && (
          <p className="crm-muted">No research fields yet. Add one below.</p>
        )}
      </div>

      {archived.length > 0 && (
        <div className="crm-cf-archived">
          <Button variant="ghost" size="sm" onClick={() => setShowArchived((s) => !s)}>
            {showArchived ? "Hide" : "Show"} archived ({archived.length})
          </Button>
          {showArchived &&
            archived.map((d) => (
              <ResearchFieldRow key={d.id} def={d} onChanged={refresh} />
            ))}
        </div>
      )}

      <ResearchFieldCreateForm onCreated={refresh} />
    </div>
  );
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
  { value: "mobile_phone", label: "Mobile phone" },
  { value: "job_title", label: "Position / title" },
  { value: "city", label: "City" },
  { value: "state", label: "State" },
  { value: "zip", label: "Zip code" },
  { value: "company", label: "Business name" },
  { value: "website", label: "Website" },
  { value: "notes", label: "Note (adds activity)" },
  { value: "sms_opt_in", label: "SMS opt-in (consent)" },
];

/** Header → system target synonym table (first match wins, most specific
 * first). Headers are normalized to a-z0-9 only before lookup, so "First Name",
 * "first_name", "FIRST-NAME" all collapse to "firstname". */
const HEADER_SYNONYMS: { target: string; keys: string[] }[] = [
  {
    target: "first_name",
    keys: ["firstname", "first", "fname", "givenname", "forename"],
  },
  { target: "last_name", keys: ["lastname", "last", "lname", "surname", "familyname"] },
  {
    target: "full_name",
    keys: [
      "name",
      "fullname",
      "contactname",
      "contact",
      "leadname",
      "customername",
      "clientname",
    ],
  },
  {
    target: "email",
    keys: [
      "email",
      "emailaddress",
      "mail",
      "workemail",
      "businessemail",
      "primaryemail",
      "email1",
    ],
  },
  {
    target: "mobile_phone",
    keys: ["mobile", "cell", "cellphone", "mobilenumber", "mobilephone", "cellnumber"],
  },
  {
    target: "phone",
    keys: [
      "phone",
      "phonenumber",
      "telephone",
      "tel",
      "workphone",
      "officephone",
      "contactnumber",
      "primaryphone",
      "directline",
      "directphone",
      "phone1",
    ],
  },
  {
    target: "sms_opt_in",
    keys: ["smsoptin", "smsopt", "optin", "optedin", "smsconsent", "textoptin", "consent"],
  },
  {
    target: "job_title",
    keys: [
      "jobtitle",
      "title",
      "position",
      "role",
      "jobrole",
      "designation",
      "occupation",
      "jobposition",
    ],
  },
  {
    target: "city",
    keys: ["city", "town", "locality", "billingcity", "shippingcity", "mailingcity"],
  },
  {
    target: "state",
    keys: [
      "state",
      "province",
      "region",
      "stateprovince",
      "billingstate",
      "shippingstate",
      "mailingstate",
    ],
  },
  {
    target: "zip",
    keys: [
      "zip",
      "zipcode",
      "postalcode",
      "postcode",
      "billingzip",
      "shippingzip",
      "mailingzip",
      "postal",
    ],
  },
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
      "account",
      "firm",
      "brand",
    ],
  },
  {
    target: "website",
    keys: ["website", "url", "domain", "companywebsite", "site", "web", "homepage"],
  },
  {
    target: "notes",
    keys: ["notes", "note", "comments", "comment", "message", "remarks", "description"],
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

/** Flatten one row object into dot-path string cells. Nested plain objects
 * recurse up to `depth` levels ("address":{"city":…} → "address.city"); arrays
 * of scalars join with ", "; anything deeper (or an array of objects) falls back
 * to jsonCell (JSON.stringify). Writes into `out` and appends first-seen keys. */
function flattenRow(
  rec: Record<string, unknown>,
  out: Record<string, string>,
  push: (key: string) => void,
  prefix = "",
  depth = 3,
) {
  for (const [k, v] of Object.entries(rec)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (
      depth > 1 &&
      v &&
      typeof v === "object" &&
      !Array.isArray(v)
    ) {
      flattenRow(v as Record<string, unknown>, out, push, key, depth - 1);
    } else if (Array.isArray(v) && v.every((x) => x === null || typeof x !== "object")) {
      push(key);
      out[key] = v.map((x) => jsonCell(x)).filter((s) => s !== "").join(", ");
    } else {
      push(key);
      out[key] = jsonCell(v);
    }
  }
}

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
  const push = (key: string) => {
    if (!seen.has(key)) {
      seen.add(key);
      headers.push(key);
    }
  };
  for (const item of arr) {
    if (!item || typeof item !== "object" || Array.isArray(item)) continue;
    const out: Record<string, string> = {};
    flattenRow(item as Record<string, unknown>, out, push);
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

type ImportMode = "create" | "update" | "create_or_update";

const IMPORT_MODE_OPTIONS: { value: ImportMode; label: string }[] = [
  { value: "create_or_update", label: "Add & update" },
  { value: "create", label: "Only add new" },
  { value: "update", label: "Only update" },
];

/** Hard ceiling on active custom-field definitions (mirrors the backend cap). */
const CUSTOM_FIELD_CEILING = 100;

interface CreatedFieldOut {
  column: string;
  key: string;
  label: string;
}

interface ImportResult {
  imported: number;
  created: number;
  updated: number;
  unchanged: number;
  skipped: number;
  failed: { row: number; error: string }[];
  created_fields: CreatedFieldOut[];
  skipped_fields: { column: string; reason: string }[];
  verification_queued: boolean;
  list: { id: string; name: string; added: number } | null;
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
  const [fileName, setFileName] = useState<string | null>(null);
  const [mapping, setMapping] = useState<Record<string, MappingTarget>>({});
  const [newTypes, setNewTypes] = useState<Record<string, CustomFieldType>>({});
  const [newLabels, setNewLabels] = useState<Record<string, string>>({});
  const [mode, setMode] = useState<ImportMode>("create_or_update");
  const [smsOptInAll, setSmsOptInAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [verify, setVerify] = useState(true);
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Import-into-list: an existing list id, "__new__" (+ name), or "none".
  const [lists, setLists] = useState<ContactList[]>([]);
  const [listChoice, setListChoice] = useState<string>("none");
  const [newListName, setNewListName] = useState("");
  useEffect(() => {
    let alive = true;
    listContactLists(clientId)
      .then((l) => alive && setLists(l))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [clientId]);

  // Automap must match against the CURRENT field list, not a parent prop that
  // may be stale — e.g. importing a file that creates fields, then importing
  // again before the parent's def list refetches. Without this, those columns
  // re-map to "new" and (absent the backend's reuse guard) would duplicate the
  // fields. Fetch fresh on open; fall back to the prop if the fetch fails.
  const [fetchedDefs, setFetchedDefs] = useState<CustomFieldDef[] | null>(null);
  useEffect(() => {
    let alive = true;
    api<CustomFieldDef[]>("/api/crm/custom-fields")
      .then((d) => alive && setFetchedDefs(d))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  const activeDefs = (fetchedDefs ?? defs).filter((d) => !d.archived_at);

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
    // Auto-map every column by default. Order of precedence per header:
    //  1. system synonym on the full normalized header
    //  2. active custom-field label match on the full header
    //  3. system synonym / custom-field label on the last dot-path segment
    //     (e.g. "address.city" from flattened JSON → city)
    //  4. otherwise create a new custom field (seeded type + header label),
    //     unless the column is entirely empty or the custom-field ceiling is hit
    // Each system target is assigned at most once — the earliest column wins.
    const guess: Record<string, MappingTarget> = {};
    const seedTypes: Record<string, CustomFieldType> = {};
    const seedLabels: Record<string, string> = {};
    const usedTargets = new Set<string>();
    let pendingNew = 0;
    const matchSynonym = (norm: string) =>
      HEADER_SYNONYMS.find((s) => !usedTargets.has(s.target) && s.keys.includes(norm));
    const matchCustom = (norm: string) =>
      activeDefs.find((d) => normalizeHeader(d.label) === norm);
    for (const h of p.headers) {
      const norm = normalizeHeader(h);
      const seg = h.includes(".") ? normalizeHeader(h.split(".").pop() ?? "") : norm;
      const hit = matchSynonym(norm) ?? (seg !== norm ? matchSynonym(seg) : undefined);
      if (hit) {
        guess[h] = hit.target;
        usedTargets.add(hit.target);
        continue;
      }
      const custom = matchCustom(norm) ?? (seg !== norm ? matchCustom(seg) : undefined);
      if (custom) {
        guess[h] = `custom:${custom.key}`;
        continue;
      }
      const allEmpty = p.rows.every((r) => !r[h]);
      if (allEmpty || activeDefs.length + pendingNew >= CUSTOM_FIELD_CEILING) {
        guess[h] = "skip";
        continue;
      }
      guess[h] = "new";
      seedTypes[h] = inferType(p.rows.map((r) => r[h]));
      seedLabels[h] = h;
      pendingNew += 1;
    }
    // Only keep a full_name mapping when neither first_name nor last_name was
    // detected on any column (the backend splits full_name into both).
    if (usedTargets.has("first_name") || usedTargets.has("last_name")) {
      for (const h of Object.keys(guess))
        if (guess[h] === "full_name") guess[h] = "skip";
    }
    setMapping(guess);
    setNewTypes(seedTypes);
    setNewLabels(seedLabels);
    setParsed(p);
    setError(null);
    setResult(null);
  };

  const onFile = (f: File) => {
    setFileName(f.name);
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

  // Batch by BYTE SIZE, not a fixed row count: row width varies wildly (a few
  // system columns vs. dozens of long custom fields), so any fixed count either
  // wastes round-trips on narrow rows or blows the API's request-body cap on
  // wide ones — which is exactly how a 600-row import 413'd. We grow a chunk
  // until its serialized size approaches TARGET_BYTES (comfortably under the
  // server's 2MB cap, leaving room for mapping/new_fields), capping row count
  // too so per-request backend work stays bounded.
  const TARGET_BYTES = 1_200_000;
  const MAX_ROWS_PER_BATCH = 300;
  const enc = new TextEncoder();
  const rowBytes = (r: Record<string, string>) => enc.encode(JSON.stringify(r)).length + 2;
  const batchEnd = (all: Record<string, string>[], start: number): number => {
    let end = start;
    let bytes = 0;
    while (end < all.length) {
      const rb = rowBytes(all[end]);
      // Always take at least one row, even if it alone exceeds the target
      // (pathological; the server would 413 it, surfaced as a per-row failure).
      if (end > start && (bytes + rb > TARGET_BYTES || end - start >= MAX_ROWS_PER_BATCH)) break;
      bytes += rb;
      end++;
    }
    return end;
  };

  const submit = async () => {
    if (!parsed) return;
    if (listChoice === "__new__" && !newListName.trim()) {
      setError("Name the new list first (or choose “No list”).");
      return;
    }
    const rows = parsed.rows;
    const new_fields = parsed.headers
      .filter((h) => mapping[h] === "new")
      .map((h) => ({
        column: h,
        label: (newLabels[h] ?? h).trim() || h,
        field_type: newTypes[h] ?? "text",
      }));
    setBusy(true);
    setError(null);
    setResult(null);

    const agg: ImportResult = {
      imported: 0,
      created: 0,
      updated: 0,
      unchanged: 0,
      skipped: 0,
      failed: [],
      created_fields: [],
      skipped_fields: [],
      verification_queued: false,
      list: null,
    };
    // Batch 1 carries new_fields; later batches reuse the returned custom-field
    // keys by rewriting each new-field column to its "custom:<key>" mapping.
    let currentMapping: Record<string, MappingTarget> = { ...mapping };
    // Same convergence for the target list: batch 1 may create it by name;
    // later batches address it by the id the server returned.
    let listId: string | null =
      listChoice !== "none" && listChoice !== "__new__" ? listChoice : null;
    let sendNewListName = listChoice === "__new__" ? newListName.trim() : null;

    try {
      let start = 0;
      while (start < rows.length) {
        const isFirst = start === 0;
        const end = batchEnd(rows, start);
        const chunk = rows.slice(start, end);
        setProgress({ done: start, total: rows.length });
        const r = await api<ImportResult>("/api/crm/contacts/import", {
          method: "POST",
          body: JSON.stringify({
            client_id: clientId,
            mode,
            file_name: fileName,
            mapping: currentMapping,
            rows: chunk,
            new_fields: isFirst ? new_fields : [],
            verify,
            sms_opt_in_all: smsOptInAll,
            list_id: listId,
            new_list_name: listId ? null : sendNewListName,
          }),
        });
        agg.imported += r.imported ?? 0;
        agg.created += r.created ?? 0;
        agg.updated += r.updated ?? 0;
        agg.unchanged += r.unchanged ?? 0;
        agg.skipped += r.skipped ?? 0;
        // Offset row indices so they reference original (pre-batch) row numbers.
        for (const f of r.failed ?? []) agg.failed.push({ row: f.row + start, error: f.error });
        for (const sf of r.skipped_fields ?? []) agg.skipped_fields.push(sf);
        agg.verification_queued = agg.verification_queued || Boolean(r.verification_queued);
        for (const cf of r.created_fields ?? []) {
          if (!agg.created_fields.some((c) => c.key === cf.key)) agg.created_fields.push(cf);
        }
        if (isFirst && r.created_fields?.length) {
          currentMapping = { ...currentMapping };
          for (const cf of r.created_fields) currentMapping[cf.column] = `custom:${cf.key}`;
        }
        if (r.list) {
          listId = r.list.id;
          sendNewListName = null;
          agg.list = agg.list
            ? { ...r.list, added: agg.list.added + r.list.added }
            : r.list;
        }
        start = end;
      }
      setProgress({ done: rows.length, total: rows.length });
      setResult(agg);
      onDone();
      const done = agg.created + agg.updated;
      toast(
        verify && agg.verification_queued
          ? `Imported ${done} contact(s) — verifying emails in the background`
          : `Imported ${done} contact(s)`,
        "ok"
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  const targetLabel = (h: string, t: MappingTarget): string => {
    if (t === "new") return (newLabels[h] ?? h).trim() || h;
    if (t.startsWith("custom:")) {
      const key = t.slice(7);
      return activeDefs.find((d) => d.key === key)?.label ?? key;
    }
    return SYSTEM_TARGETS.find((s) => s.value === t)?.label ?? t;
  };

  const mappedHeaders = parsed
    ? parsed.headers.filter((h) => mapping[h] && mapping[h] !== "skip")
    : [];

  const downloadFailed = () => {
    if (!result || !parsed) return;
    const headers = parsed.headers;
    const esc = (v: string) => (/[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
    const lines = [[...headers, "error"].map(esc).join(",")];
    for (const f of result.failed) {
      const row = parsed.rows[f.row];
      if (!row) continue;
      lines.push([...headers.map((h) => esc(row[h] ?? "")), esc(f.error)].join(","));
    }
    const blob = new Blob([lines.join("\r\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "failed-rows.csv";
    a.click();
    URL.revokeObjectURL(url);
  };

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
          <div className="crm-form-actions" style={{ marginBottom: "0.5rem" }}>
            <Segmented<ImportMode>
              options={IMPORT_MODE_OPTIONS}
              value={mode}
              onChange={setMode}
              ariaLabel="Import mode"
            />
          </div>
          <p className="crm-muted">
            {parsed.rows.length} row(s). Matches by email or phone; existing values
            are never overwritten. Map each column:
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
                      if (e.target.value === "new") {
                        if (!newTypes[h])
                          setNewTypes({
                            ...newTypes,
                            [h]: inferType(parsed.rows.map((r) => r[h])),
                          });
                        if (!newLabels[h]) setNewLabels({ ...newLabels, [h]: h });
                      }
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
                    <>
                      <input
                        aria-label={`Label for new field ${h}`}
                        placeholder="Field label"
                        value={newLabels[h] ?? h}
                        onChange={(e) =>
                          setNewLabels({ ...newLabels, [h]: e.target.value })
                        }
                      />
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
                    </>
                  )}
                </div>
              );
            })}
          </div>

          {mappedHeaders.length > 0 && (
            <div style={{ overflowX: "auto", marginTop: "0.5rem" }}>
              <table className="crm-csv-preview">
                <thead>
                  <tr>
                    {mappedHeaders.map((h) => (
                      <th key={h} className="crm-muted">
                        {targetLabel(h, mapping[h])}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {parsed.rows.slice(0, 3).map((r, i) => (
                    <tr key={i}>
                      {mappedHeaders.map((h) => (
                        <td key={h}>{r[h]}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {error && (
            <span className="crm-form-error" role="alert">
              {error}
            </span>
          )}
          <label className="crm-check">
            <input
              type="checkbox"
              checked={smsOptInAll}
              onChange={(e) => setSmsOptInAll(e.target.checked)}
            />
            <span>
              These contacts opted in to SMS marketing (e.g. on our website forms)
            </span>
          </label>
          <label className="crm-check">
            <input
              type="checkbox"
              checked={verify}
              onChange={(e) => setVerify(e.target.checked)}
            />
            <span>Verify email addresses after import (uses your monthly quota)</span>
          </label>
          <div className="crm-form-actions" style={{ marginTop: "0.5rem", alignItems: "center", flexWrap: "wrap" }}>
            <label className="crm-muted" htmlFor="csv-import-list">
              Add imported leads to a list
            </label>
            <select
              id="csv-import-list"
              value={listChoice}
              onChange={(e) => setListChoice(e.target.value)}
            >
              <option value="none">No list</option>
              {lists.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.name}
                </option>
              ))}
              <option value="__new__">+ New list…</option>
            </select>
            {listChoice === "__new__" && (
              <input
                aria-label="New list name"
                placeholder="List name"
                maxLength={100}
                value={newListName}
                onChange={(e) => setNewListName(e.target.value)}
              />
            )}
            {listChoice !== "none" && (
              <span className="crm-muted">
                Select this list as the audience when enrolling an SMS or email
                campaign.
              </span>
            )}
          </div>
          {busy && progress && (
            <p className="crm-muted" aria-live="polite">
              Importing… {progress.done.toLocaleString()} of{" "}
              {progress.total.toLocaleString()} rows
            </p>
          )}
          <div className="crm-form-actions" style={{ marginTop: "0.75rem" }}>
            <Button variant="primary" size="sm" busy={busy} onClick={submit}>
              Import {parsed.rows.length} row(s)
            </Button>
            <Button
              variant="ghost"
              size="sm"
              disabled={busy}
              onClick={() => setParsed(null)}
            >
              Choose another file
            </Button>
          </div>
        </>
      )}

      {result && (
        <div className="crm-csv-result">
          <Alert tone={result.failed.length ? "warn" : "ok"}>
            Created <strong>{result.created}</strong> · Updated{" "}
            <strong>{result.updated}</strong> · Unchanged{" "}
            <strong>{result.unchanged}</strong> · Skipped{" "}
            <strong>{result.skipped}</strong> · Failed{" "}
            <strong>{result.failed.length}</strong>
          </Alert>
          {result.verification_queued && (
            <p className="crm-muted">Email verification is running in the background.</p>
          )}
          {result.list && (
            <p className="crm-muted">
              Added <strong>{result.list.added}</strong> lead(s) to list{" "}
              <strong>“{result.list.name}”</strong> — pick it as the audience when
              enrolling an SMS or email campaign.
            </p>
          )}
          {result.created_fields.length > 0 && (
            <p className="crm-muted">
              Created {result.created_fields.length} custom field(s):{" "}
              {result.created_fields.map((f) => f.label).join(", ")}
            </p>
          )}
          {result.skipped_fields.length > 0 && (
            <ul className="crm-csv-errors">
              {result.skipped_fields.map((sf) => (
                <li key={sf.column}>
                  Column “{sf.column}” not created: {sf.reason}
                </li>
              ))}
            </ul>
          )}
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
            {result.failed.length > 0 && (
              <Button variant="ghost" size="sm" onClick={downloadFailed}>
                Download failed rows
              </Button>
            )}
          </div>
        </div>
      )}
    </Dialog>
  );
}
