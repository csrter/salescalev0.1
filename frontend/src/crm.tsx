/**
 * Phase 6 — Salescale CRM view (per client).
 *
 * Team roles get the full workspace: drag-and-drop pipeline board (stages
 * are per-client data, editable by admins), the lead list with attribution
 * chips, a contact drawer (qualification checklist, activities, tasks,
 * deals), and admin setup (stage editor, Organization qualified-lead
 * criteria, native lead-form routing, external CRM sync).
 *
 * Client-role users get the same board and lead list read-only — the
 * backend already excludes internal-only fields and activities, so this
 * component simply renders what it receives and hides the write controls.
 */

import { useCallback, useEffect, useState } from "react";
import { ADMIN_ROLES, TEAM_ROLES, api, type Session } from "./api";

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
  source: string | null;
  qualified_at: string | null;
  created_at: string;
  qualification?: Record<string, boolean> | null;
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

const money = (cents?: number | null) =>
  cents == null ? null : `$${(cents / 100).toLocaleString()}`;

const contactName = (c?: ContactRow | null) =>
  c
    ? [c.first_name, c.last_name].filter(Boolean).join(" ") ||
      c.email ||
      c.phone ||
      "Unnamed lead"
    : "Unknown";

function QualifiedBadge({ contact }: { contact?: ContactRow | null }) {
  if (!contact) return null;
  return contact.qualified_at ? (
    <span className="badge ok">qualified</span>
  ) : (
    <span className="badge none">unqualified</span>
  );
}

function AttributionChips({ contact }: { contact?: ContactRow | null }) {
  const a = contact?.attribution;
  if (!a) return null;
  return (
    <span className="attr-chips">
      {a.platform && <span className={`platform ${a.platform}`}>{a.platform}</span>}
      {a.utm_source && <span className="chip">utm: {a.utm_source}</span>}
      {a.has_click_id && <span className="chip">click id ✓</span>}
      {!a.platform && !a.utm_source && (
        <span className="chip muted-chip">no attribution trail</span>
      )}
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
  const [board, setBoard] = useState<Board | null>(null);
  const [contacts, setContacts] = useState<ContactRow[]>([]);
  const [criteria, setCriteria] = useState<Criterion[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showSetup, setShowSetup] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bump, setBump] = useState(0);
  const refresh = () => setBump((b) => b + 1);

  useEffect(() => {
    api<Board>(`/api/crm/board?client_id=${clientId}`)
      .then(setBoard)
      .catch((e) => setError((e as Error).message));
    api<ContactRow[]>(`/api/crm/contacts?client_id=${clientId}`)
      .then(setContacts)
      .catch((e) => setError((e as Error).message));
    if (isTeam)
      api<{ criteria: Criterion[] }>("/api/orgs/me/qualified-lead-criteria")
        .then((r) => setCriteria(r.criteria))
        .catch(() => {});
  }, [clientId, bump, isTeam]);

  const canDrag = isTeam && board != null && !board.read_only;

  if (error) return <p className="error">{error}</p>;
  if (!board) return <p className="muted">Loading CRM…</p>;

  return (
    <section className="crm">
      <div className="crm-toolbar">
        <h3>{board.pipeline.name}</h3>
        <span className="muted">
          {board.won.length} won · {board.lost.length} lost
        </span>
        {isAdmin && (
          <button className="link" onClick={() => setShowSetup(!showSetup)}>
            {showSetup ? "Hide setup" : "CRM setup"}
          </button>
        )}
      </div>
      {showSetup && isAdmin && (
        <SetupPanel
          clientId={clientId}
          board={board}
          criteria={criteria}
          onChanged={refresh}
        />
      )}
      <KanbanBoard
        board={board}
        canDrag={canDrag}
        onSelect={setSelectedId}
        onMoved={refresh}
      />
      <div className="crm-lower">
        <LeadList
          contacts={contacts}
          clientId={clientId}
          isTeam={isTeam}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onCreated={refresh}
        />
        {selectedId && (
          <ContactDrawer
            contactId={selectedId}
            clientId={clientId}
            session={session}
            criteria={criteria}
            stages={board.stages}
            onClose={() => setSelectedId(null)}
            onChanged={refresh}
          />
        )}
      </div>
    </section>
  );
}

// --- Kanban ---

function KanbanBoard({
  board,
  canDrag,
  onSelect,
  onMoved,
}: {
  board: Board;
  canDrag: boolean;
  onSelect: (contactId: string) => void;
  onMoved: () => void;
}) {
  const [dragDealId, setDragDealId] = useState<string | null>(null);
  const [overStage, setOverStage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const drop = async (stageId: string) => {
    setOverStage(null);
    if (!dragDealId) return;
    const from = board.stages.find((s) =>
      (board.deals_by_stage[s.id] ?? []).some((d) => d.id === dragDealId)
    );
    if (from?.id === stageId) return;
    try {
      await api(`/api/crm/deals/${dragDealId}`, {
        method: "PATCH",
        body: JSON.stringify({ stage_id: stageId }),
      });
      onMoved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setDragDealId(null);
    }
  };

  return (
    <div>
      {error && <p className="error">{error}</p>}
      <div className="crm-board">
        {board.stages.map((stage) => (
          <div
            key={stage.id}
            className={`crm-col ${overStage === stage.id ? "drop-target" : ""}`}
            onDragOver={(e) => {
              if (!canDrag) return;
              e.preventDefault();
              setOverStage(stage.id);
            }}
            onDragLeave={() => setOverStage(null)}
            onDrop={(e) => {
              e.preventDefault();
              drop(stage.id);
            }}
          >
            <div className="crm-col-head">
              <strong>{stage.name}</strong>
              {stage.is_qualified_stage && (
                <span className="badge ok" title="Deals here mark the lead qualified">
                  ✓ qualifies
                </span>
              )}
              <span className="muted">
                {(board.deals_by_stage[stage.id] ?? []).length}
              </span>
            </div>
            {(board.deals_by_stage[stage.id] ?? []).map((deal) => (
              <DealCard
                key={deal.id}
                deal={deal}
                contact={board.contacts[deal.contact_id]}
                draggable={canDrag}
                dragging={dragDealId === deal.id}
                onDragStart={() => setDragDealId(deal.id)}
                onDragEnd={() => setDragDealId(null)}
                onSelect={() => onSelect(deal.contact_id)}
                canClose={canDrag}
                onClosed={onMoved}
              />
            ))}
            {(board.deals_by_stage[stage.id] ?? []).length === 0 && (
              <p className="muted crm-empty">no deals</p>
            )}
          </div>
        ))}
      </div>
      {canDrag && (
        <p className="muted footnote">
          Drag a card between stages. Dropping into the ✓ stage marks that
          lead qualified — the LQA-CPL metric and guarantee tracker update
          from the same change.
        </p>
      )}
    </div>
  );
}

function DealCard({
  deal,
  contact,
  draggable,
  dragging,
  onDragStart,
  onDragEnd,
  onSelect,
  canClose,
  onClosed,
}: {
  deal: DealRow;
  contact?: ContactRow;
  draggable: boolean;
  dragging: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onSelect: () => void;
  canClose: boolean;
  onClosed: () => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const close = async (status: "won" | "lost") => {
    try {
      await api(`/api/crm/deals/${deal.id}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      onClosed();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <div
      className={`crm-card ${dragging ? "dragging" : ""}`}
      draggable={draggable}
      onDragStart={(e) => {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", deal.id);
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      onClick={onSelect}
      title={draggable ? "Drag to change stage · click for details" : "Click for details"}
    >
      <strong>{contactName(contact)}</strong>
      <span className="crm-card-meta">
        {deal.name !== contactName(contact) && (
          <span className="muted">{deal.name}</span>
        )}
        {money(deal.value_cents) && <span>{money(deal.value_cents)}</span>}
      </span>
      <span className="crm-card-badges">
        <QualifiedBadge contact={contact} />
        {contact?.attribution?.platform && (
          <span className={`platform ${contact.attribution.platform}`}>
            {contact.attribution.platform}
          </span>
        )}
      </span>
      {canClose && (
        <span className="row-actions" onClick={(e) => e.stopPropagation()}>
          <button className="link" onClick={() => close("won")}>
            Won
          </button>
          <button className="link" onClick={() => close("lost")}>
            Lost
          </button>
        </span>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

// --- Lead list ---

function LeadList({
  contacts,
  clientId,
  isTeam,
  selectedId,
  onSelect,
  onCreated,
}: {
  contacts: ContactRow[];
  clientId: string;
  isTeam: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreated: () => void;
}) {
  const [adding, setAdding] = useState(false);
  return (
    <div className="crm-leads">
      <div className="crm-toolbar">
        <h4>Leads ({contacts.length})</h4>
        {isTeam && (
          <button className="link" onClick={() => setAdding(!adding)}>
            {adding ? "Cancel" : "+ Add contact"}
          </button>
        )}
      </div>
      {adding && (
        <NewContactForm
          clientId={clientId}
          onCreated={() => {
            setAdding(false);
            onCreated();
          }}
        />
      )}
      <table className="compact">
        <thead>
          <tr>
            <th>Lead</th>
            <th>Contact info</th>
            <th>Source</th>
            <th>Attribution</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {contacts.map((c) => (
            <tr
              key={c.id}
              className={`clickable ${selectedId === c.id ? "selected" : ""}`}
              onClick={() => onSelect(c.id)}
            >
              <td>
                <strong>{contactName(c)}</strong>
              </td>
              <td className="muted">
                {[c.email, c.phone].filter(Boolean).join(" · ") || "—"}
              </td>
              <td className="muted">{c.source?.replace(/_/g, " ") ?? "—"}</td>
              <td>
                <AttributionChips contact={c} />
              </td>
              <td>
                <QualifiedBadge contact={c} />
              </td>
            </tr>
          ))}
          {contacts.length === 0 && (
            <tr>
              <td colSpan={5} className="muted">
                No leads yet — they arrive here automatically from Instant
                Forms, Lead Form ads, and landing pages.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function NewContactForm({
  clientId,
  onCreated,
}: {
  clientId: string;
  onCreated: () => void;
}) {
  const [first, setFirst] = useState("");
  const [last, setLast] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="inline-form">
      <input placeholder="First name" value={first} onChange={(e) => setFirst(e.target.value)} />
      <input placeholder="Last name" value={last} onChange={(e) => setLast(e.target.value)} />
      <input placeholder="Email" value={email} onChange={(e) => setEmail(e.target.value)} />
      <input placeholder="Phone" value={phone} onChange={(e) => setPhone(e.target.value)} />
      <button
        disabled={!first && !last && !email && !phone}
        onClick={() =>
          api("/api/crm/contacts", {
            method: "POST",
            body: JSON.stringify({
              client_id: clientId,
              first_name: first || null,
              last_name: last || null,
              email: email || null,
              phone: phone || null,
            }),
          })
            .then(onCreated)
            .catch((e) => setError((e as Error).message))
        }
      >
        Add contact
      </button>
      {error && <p className="error">{error}</p>}
    </div>
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

function ContactDrawer({
  contactId,
  clientId,
  session,
  criteria,
  stages,
  onClose,
  onChanged,
}: {
  contactId: string;
  clientId: string;
  session: Session;
  criteria: Criterion[];
  stages: Stage[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const isTeam = TEAM_ROLES.includes(session.role);
  const [detail, setDetail] = useState<ContactDetail | null>(null);
  const [members, setMembers] = useState<{ id: string; full_name: string; role: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [bump, setBump] = useState(0);

  const reload = useCallback(() => setBump((b) => b + 1), []);

  useEffect(() => {
    api<ContactDetail>(`/api/crm/contacts/${contactId}`)
      .then(setDetail)
      .catch((e) => setError((e as Error).message));
    if (isTeam)
      api<{ id: string; full_name: string; role: string }[]>("/api/orgs/me/members")
        .then((ms) => setMembers(ms.filter((m) => m.role !== "client")))
        .catch(() => {});
  }, [contactId, bump, isTeam]);

  if (error) return <p className="error">{error}</p>;
  if (!detail) return <p className="muted">Loading contact…</p>;

  const openDeal = detail.deals.find((d) => d.status === "open");

  return (
    <div className="crm-drawer">
      <div className="crm-toolbar">
        <h4>{contactName(detail)}</h4>
        <QualifiedBadge contact={detail} />
        <button className="link" onClick={onClose}>
          Close
        </button>
      </div>
      <p className="muted">
        {[detail.email, detail.phone].filter(Boolean).join(" · ")}
        {detail.source ? ` · via ${detail.source.replace(/_/g, " ")}` : ""}
      </p>
      <AttributionChips contact={detail} />

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

      <h5>Deals</h5>
      {detail.deals.length === 0 && <p className="muted">No deals yet.</p>}
      <ul className="alert-list">
        {detail.deals.map((d) => (
          <li key={d.id}>
            <span className={`badge ${d.status === "won" ? "ok" : d.status === "lost" ? "failed" : "active"}`}>
              {d.status}
            </span>
            <strong>{d.name}</strong>
            <span className="muted">
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

      <h5>Activity</h5>
      {isTeam && <NewActivityForm contactId={detail.id} onCreated={reload} />}
      <ul className="crm-timeline">
        {detail.activities.map((a) => (
          <li key={a.id}>
            <span className="badge">{a.type}</span>
            {a.is_internal && (
              <span className="badge warn" title="Never shown to client logins">
                internal
              </span>
            )}
            <span>{a.body}</span>
            <span className="muted"> {new Date(a.occurred_at).toLocaleString()}</span>
          </li>
        ))}
        {detail.activities.length === 0 && (
          <li className="muted">No activity logged yet.</li>
        )}
      </ul>

      {isTeam && detail.tasks && (
        <>
          <h5>Tasks</h5>
          <NewTaskForm
            clientId={clientId}
            contactId={detail.id}
            members={members}
            defaultAssignee={members.length ? undefined : undefined}
            onCreated={reload}
          />
          <ul className="crm-timeline">
            {detail.tasks.map((t) => (
              <li key={t.id} className={t.completed_at ? "muted" : ""}>
                <input
                  type="checkbox"
                  checked={!!t.completed_at}
                  onChange={(e) =>
                    api(`/api/crm/tasks/${t.id}`, {
                      method: "PATCH",
                      body: JSON.stringify({ completed: e.target.checked }),
                    }).then(reload)
                  }
                />
                <span>{t.title}</span>
                <span className="muted">
                  {t.due_at ? ` due ${new Date(t.due_at).toLocaleDateString()}` : ""}
                  {t.assigned_to_user_id
                    ? ` · ${members.find((m) => m.id === t.assigned_to_user_id)?.full_name ?? "assigned"}`
                    : ""}
                </span>
              </li>
            ))}
            {detail.tasks.length === 0 && <li className="muted">No open tasks.</li>}
          </ul>
        </>
      )}
    </div>
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
  const [error, setError] = useState<string | null>(null);
  const put = (body: Record<string, unknown>) =>
    api(`/api/crm/contacts/${detail.id}/qualification`, {
      method: "PUT",
      body: JSON.stringify(body),
    })
      .then(onChanged)
      .catch((e) => setError((e as Error).message));

  return (
    <div className="crm-qualify">
      <h5>Qualification</h5>
      {criteria.length > 0 ? (
        <>
          <p className="muted">
            Your organization's criteria — all checked = qualified. One
            change updates LQA-CPL and the guarantee tracker.
          </p>
          {criteria.map((c) => (
            <label key={c.key} className="crm-check">
              <input
                type="checkbox"
                checked={!!detail.qualification?.[c.key]}
                onChange={(e) => put({ checklist: { [c.key]: e.target.checked } })}
              />
              {c.label}
            </label>
          ))}
        </>
      ) : (
        <label className="crm-check">
          <input
            type="checkbox"
            checked={!!detail.qualified_at}
            onChange={(e) => put({ qualified: e.target.checked })}
          />
          Qualified lead
          <span className="muted">
            {" "}
            (no checklist configured — set criteria in CRM setup)
          </span>
        </label>
      )}
      {error && <p className="error">{error}</p>}
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
  return (
    <div className="inline-form">
      <input
        placeholder="Deal value $ (optional)"
        type="number"
        min="0"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <button
        onClick={() =>
          api("/api/crm/deals", {
            method: "POST",
            body: JSON.stringify({
              client_id: clientId,
              contact_id: contactId,
              value_cents: value ? Math.round(Number(value) * 100) : null,
            }),
          })
            .then(onCreated)
            .catch((e) => setError((e as Error).message))
        }
      >
        Start deal
      </button>
      {error && <p className="error">{error}</p>}
    </div>
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
  return (
    <div className="inline-form">
      <select value={type} onChange={(e) => setType(e.target.value)}>
        {["note", "call", "email", "sms", "meeting"].map((t) => (
          <option key={t} value={t}>
            {t}
          </option>
        ))}
      </select>
      <input
        placeholder="What happened?"
        value={body}
        onChange={(e) => setBody(e.target.value)}
      />
      <label className="crm-check">
        <input
          type="checkbox"
          checked={internal}
          onChange={(e) => setInternal(e.target.checked)}
        />
        internal only
      </label>
      <button
        disabled={!body}
        onClick={() =>
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
              onCreated();
            })
            .catch((e) => setError((e as Error).message))
        }
      >
        Log
      </button>
      {error && <p className="error">{error}</p>}
    </div>
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
  defaultAssignee?: string;
  onCreated: () => void;
}) {
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [assignee, setAssignee] = useState("");
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="inline-form">
      <input
        placeholder="Follow-up task"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
      />
      <input type="date" value={due} onChange={(e) => setDue(e.target.value)} />
      <select value={assignee} onChange={(e) => setAssignee(e.target.value)}>
        <option value="">me</option>
        {members.map((m) => (
          <option key={m.id} value={m.id}>
            {m.full_name}
          </option>
        ))}
      </select>
      <button
        disabled={!title}
        onClick={() =>
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
              onCreated();
            })
            .catch((e) => setError((e as Error).message))
        }
      >
        Add task
      </button>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

// --- Admin setup: stages, criteria, lead-form routing, external sync ---

function SetupPanel({
  clientId,
  board,
  criteria,
  onChanged,
}: {
  clientId: string;
  board: Board;
  criteria: Criterion[];
  onChanged: () => void;
}) {
  return (
    <div className="crm-setup">
      <StageEditor board={board} onChanged={onChanged} />
      <CriteriaEditor criteria={criteria} onChanged={onChanged} />
      <LeadFormRouting clientId={clientId} />
      <ExternalSyncConfig clientId={clientId} />
    </div>
  );
}

function StageEditor({ board, onChanged }: { board: Board; onChanged: () => void }) {
  const [rows, setRows] = useState<{ id?: string; name: string; is_qualified_stage: boolean }[]>(
    board.stages.map((s) => ({
      id: s.id,
      name: s.name,
      is_qualified_stage: s.is_qualified_stage,
    }))
  );
  const [error, setError] = useState<string | null>(null);
  const set = (i: number, patch: Partial<{ name: string; is_qualified_stage: boolean }>) =>
    setRows(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="crm-setup-block">
      <h5>Pipeline stages (this client)</h5>
      {rows.map((r, i) => (
        <div key={r.id ?? `new-${i}`} className="inline-form">
          <input value={r.name} onChange={(e) => set(i, { name: e.target.value })} />
          <label className="crm-check" title="Deals entering this stage mark the lead qualified">
            <input
              type="radio"
              name="qualified-stage"
              checked={r.is_qualified_stage}
              onChange={() =>
                setRows(rows.map((row, j) => ({ ...row, is_qualified_stage: j === i })))
              }
            />
            qualifies
          </label>
          <button className="link" onClick={() => setRows(rows.filter((_, j) => j !== i))}>
            remove
          </button>
        </div>
      ))}
      <div className="inline-form">
        <button
          className="link"
          onClick={() => setRows([...rows, { name: "New stage", is_qualified_stage: false }])}
        >
          + Add stage
        </button>
        <button
          disabled={!rows.length || rows.some((r) => !r.name)}
          onClick={() =>
            api(`/api/crm/pipelines/${board.pipeline.id}/stages`, {
              method: "PUT",
              body: JSON.stringify({ stages: rows }),
            })
              .then(onChanged)
              .catch((e) => setError((e as Error).message))
          }
        >
          Save stages
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
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
  useEffect(() => setRows(criteria), [criteria]);
  return (
    <div className="crm-setup-block">
      <h5>Qualified-lead criteria (whole organization)</h5>
      <p className="muted">
        Your agency's own definition — e.g. a trial-sprint checklist. Leave
        empty for a simple qualified toggle.
      </p>
      {rows.map((r, i) => (
        <div key={i} className="inline-form">
          <input
            value={r.label}
            placeholder="Criterion"
            onChange={(e) =>
              setRows(
                rows.map((row, j) =>
                  j === i ? { key: slug(e.target.value), label: e.target.value } : row
                )
              )
            }
          />
          <button className="link" onClick={() => setRows(rows.filter((_, j) => j !== i))}>
            remove
          </button>
        </div>
      ))}
      <div className="inline-form">
        <button
          className="link"
          onClick={() => setRows([...rows, { key: "", label: "" }])}
        >
          + Add criterion
        </button>
        <button
          disabled={rows.some((r) => !r.label)}
          onClick={() =>
            api("/api/orgs/me/qualified-lead-criteria", {
              method: "PUT",
              body: JSON.stringify({
                criteria: rows.map((r) => ({ key: r.key || slug(r.label), label: r.label })),
              }),
            })
              .then(onChanged)
              .catch((e) => setError((e as Error).message))
          }
        >
          Save criteria
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function LeadFormRouting({ clientId }: { clientId: string }) {
  const [configs, setConfigs] = useState<
    { platform: string; external_key: string; enabled: boolean }[]
  >([]);
  const [pageId, setPageId] = useState("");
  const [googleKey, setGoogleKey] = useState("");
  const [note, setNote] = useState<string | null>(null);

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
        setNote(`${platform} routing saved`);
        load();
      })
      .catch((e) => setNote((e as Error).message));

  const googleUrl = `${location.origin.replace(/:\d+$/, ":8000")}/api/webhooks/google/lead-form/${clientId}`;

  return (
    <div className="crm-setup-block">
      <h5>Native lead-form ingestion</h5>
      <div className="inline-form">
        <input
          placeholder="Meta Page ID (Instant Forms)"
          value={pageId}
          onChange={(e) => setPageId(e.target.value)}
        />
        <button disabled={!pageId} onClick={() => save("meta", pageId)}>
          Save Meta routing
        </button>
      </div>
      <div className="inline-form">
        <input
          placeholder="Google lead form key"
          value={googleKey}
          onChange={(e) => setGoogleKey(e.target.value)}
        />
        <button disabled={!googleKey} onClick={() => save("google", googleKey)}>
          Save Google key
        </button>
      </div>
      <p className="muted footnote">
        Google Ads → lead form → webhook: URL <code>{googleUrl}</code> with the
        key above. Meta leads arrive via the app-level leadgen webhook and are
        routed here by Page ID.
      </p>
      {note && <p className="muted">{note}</p>}
      {configs.length > 0 && (
        <p className="muted">
          Configured: {configs.map((c) => `${c.platform} (${c.external_key})`).join(", ")}
        </p>
      )}
    </div>
  );
}

function ExternalSyncConfig({ clientId }: { clientId: string }) {
  const [state, setState] = useState<{ configured: boolean; enabled?: boolean; url?: string } | null>(null);
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [note, setNote] = useState<string | null>(null);

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

  return (
    <div className="crm-setup-block">
      <h5>External CRM sync (optional)</h5>
      <p className="muted">
        For clients whose nurture automation still runs in an external CRM:
        status changes push to this webhook, and the external system can post
        back to <code>/api/crm/external-sync/{clientId}</code> with the shared
        secret. Salescale stays the source of truth for reporting.
      </p>
      <div className="inline-form">
        <input
          placeholder="External webhook URL"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <input
          placeholder={state?.configured ? "Shared secret (unchanged unless set)" : "Shared secret (min 8 chars)"}
          value={secret}
          onChange={(e) => setSecret(e.target.value)}
        />
        <button
          disabled={!url || secret.length < 8}
          onClick={() =>
            api(`/api/clients/${clientId}/external-sync`, {
              method: "PUT",
              body: JSON.stringify({ enabled: true, url, secret }),
            })
              .then(() => {
                setNote("Sync enabled");
                setSecret("");
                load();
              })
              .catch((e) => setNote((e as Error).message))
          }
        >
          Enable sync
        </button>
        {state?.configured && (
          <button
            className="link"
            onClick={() =>
              api(`/api/clients/${clientId}/external-sync`, { method: "DELETE" })
                .then(() => {
                  setNote("Sync removed");
                  load();
                })
                .catch((e) => setNote((e as Error).message))
            }
          >
            Disable
          </button>
        )}
      </div>
      {state?.configured && (
        <p className="muted">
          Currently {state.enabled ? "enabled" : "disabled"} → {state.url}
        </p>
      )}
      {note && <p className="muted">{note}</p>}
    </div>
  );
}
