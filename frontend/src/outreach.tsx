/**
 * Outreach — compliant Instagram DM automation.
 *
 * One view, six tabs: Inbox (Rep-accessible), Trigger rules, Sequences,
 * Prospects, Analytics, Accounts. Admin-only tabs are hidden for the member
 * (Rep) role, matching the server-side gates.
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  activateOutreachSequence,
  api,
  createOutreachRule,
  createOutreachSequence,
  deleteOutreachProspect,
  deleteOutreachRule,
  disconnectIgAccount,
  downloadCsv,
  enrichOutreachProspect,
  getHouseClient,
  getOutreachSequence,
  igConnectStart,
  importOutreachProspects,
  listIgAccounts,
  listOutreachProspects,
  listOutreachRules,
  listOutreachSequences,
  openAuthUrl,
  outreachAnalytics,
  outreachApproveMessage,
  outreachAuditExportUrl,
  outreachDiscardMessage,
  outreachEnroll,
  outreachInbox,
  outreachMarkRead,
  outreachMessages,
  outreachPendingMessages,
  outreachReply,
  outreachUnenroll,
  pauseOutreachSequence,
  saveOutreachSteps,
  updateIgAccount,
  updateOutreachRule,
  type Client,
  type IgAccount,
  type OutreachAnalytics,
  type OutreachConvo,
  type OutreachMsg,
  type OutreachProspect,
  type OutreachRule,
  type OutreachSequence,
  type OutreachStep,
  type OutreachTriggerType,
} from "./api";
import { DataTable, type Column } from "./components/DataTable";
import { ConfirmDialog, Dialog, type ReceiptRow } from "./components/Dialog";
import {
  Alert,
  Badge,
  Button,
  EmptyState,
  Field,
  GlassCard,
  Kpi,
  KpiGrid,
  KpiSkeleton,
  Segmented,
  SkeletonText,
  Tabs,
} from "./components/ui";
import { Plus, Send } from "./components/icons";
import { useToast } from "./components/Toast";
import "./styles/views/outreach.css";

const TRIGGER_LABELS: Record<OutreachTriggerType, string> = {
  dm: "New DM",
  story_reply: "Story reply",
  comment: "Comment",
  live_comment: "Live comment",
  mention: "@Mention",
  story_mention: "Story mention",
};

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/**
 * Confirm-on-dirty guard for the hand-rolled editor dialogs: captures a
 * snapshot when the dialog opens and asks before discarding unsaved edits.
 */
function useDirtyGuard(open: boolean, serialized: string): () => boolean {
  const snapshot = useRef(serialized);
  useEffect(() => {
    if (open) snapshot.current = serialized;
    // Snapshot the value present at open time; later edits are compared to it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);
  return () => {
    if (serialized === snapshot.current) return true;
    return window.confirm("Discard unsaved changes?");
  };
}

type Panel = "inbox" | "rules" | "sequences" | "prospects" | "analytics" | "settings";

// The org's own prospect pipeline lives on a hidden "house" Client the server
// gets-or-creates. It isn't returned by /api/clients, so we resolve it
// separately and prepend it to the roster — this label distinguishes it in
// every client picker (filter, import, connect) and resolves outreach rows
// attached to it to a name rather than a bare id.
const HOUSE_CLIENT_LABEL = "My agency (house CRM)";

export function OutreachView({ isAdmin }: { isAdmin: boolean }) {
  const [panel, setPanel] = useState<Panel>("inbox");
  const [clients, setClients] = useState<Client[]>([]);
  const [clientId, setClientId] = useState<string>("");
  const [accounts, setAccounts] = useState<IgAccount[]>([]);

  const refreshAccounts = useCallback(() => {
    listIgAccounts(clientId || undefined).then(setAccounts).catch(() => {});
  }, [clientId]);

  useEffect(() => {
    let alive = true;
    // Load the real client roster and the org's own "house" prospect pipeline
    // in parallel, then prepend the house client so agencies can run outreach
    // for their OWN prospecting — not just client accounts. getHouseClient is
    // team-gated (all team roles pass); degrade gracefully to just the roster
    // if it fails so the pickers still work.
    Promise.all([
      api<Client[]>("/api/clients").catch(() => [] as Client[]),
      getHouseClient()
        .then((r) => r.client_id)
        .catch(() => null),
    ]).then(([roster, houseId]) => {
      if (!alive) return;
      const house: Client[] = houseId
        ? [{ id: houseId, name: HOUSE_CLIENT_LABEL, status: "active" }]
        : [];
      setClients([...house, ...roster]);
    });
    return () => {
      alive = false;
    };
  }, []);
  useEffect(refreshAccounts, [refreshAccounts]);

  const disconnected = accounts.filter((a) => a.status !== "active");
  const panels: { key: Panel; label: string; adminOnly: boolean }[] = [
    { key: "inbox", label: "Inbox", adminOnly: false },
    { key: "rules", label: "Trigger rules", adminOnly: true },
    { key: "sequences", label: "Sequences", adminOnly: true },
    { key: "prospects", label: "Prospects", adminOnly: true },
    { key: "analytics", label: "Analytics", adminOnly: true },
    { key: "settings", label: "Accounts", adminOnly: true },
  ];
  const visible = panels.filter((p) => isAdmin || !p.adminOnly);

  return (
    <div className="outreach">
      {disconnected.length > 0 && (
        <div className="or-banner">
          <Alert tone="warn" title="Reconnect required">
            {disconnected.map((a) => a.username || a.ig_user_id).join(", ")}{" "}
            {disconnected.length === 1 ? "needs" : "need"} to be reconnected —
            automated sequences are paused for{" "}
            {disconnected.length === 1 ? "that account" : "those accounts"}.
            {isAdmin && (
              <Button variant="link" size="sm" onClick={() => setPanel("settings")}>
                Go to Accounts
              </Button>
            )}
          </Alert>
        </div>
      )}

      <div className="or-subnav">
        <Tabs
          ariaLabel="Outreach sections"
          tabs={visible.map((p) => ({ id: p.key, label: p.label }))}
          active={panel}
          onChange={(id) => setPanel(id as Panel)}
        />
        <select
          className="select or-select"
          aria-label="Filter by client"
          value={clientId}
          onChange={(e) => setClientId(e.target.value)}
        >
          <option value="">All clients</option>
          {clients.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </div>

      {panel === "inbox" && (
        <InboxPanel clientId={clientId} isAdmin={isAdmin} accounts={accounts} />
      )}
      {panel === "rules" && isAdmin && (
        <RulesPanel clientId={clientId} accounts={accounts} />
      )}
      {panel === "sequences" && isAdmin && (
        <SequencesPanel clientId={clientId} accounts={accounts} />
      )}
      {panel === "prospects" && isAdmin && (
        <ProspectsPanel clientId={clientId} clients={clients} accounts={accounts} />
      )}
      {panel === "analytics" && isAdmin && <AnalyticsPanel clientId={clientId} />}
      {panel === "settings" && isAdmin && (
        <AccountsPanel
          clientId={clientId}
          clients={clients}
          accounts={accounts}
          onChanged={refreshAccounts}
        />
      )}
    </div>
  );
}

// --- Inbox ---

function InboxPanel({
  clientId,
  isAdmin,
  accounts,
}: {
  clientId: string;
  isAdmin: boolean;
  accounts: IgAccount[];
}) {
  const toast = useToast();
  const [convos, setConvos] = useState<OutreachConvo[] | null>(null);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<OutreachConvo | null>(null);
  const [messages, setMessages] = useState<OutreachMsg[] | null>(null);
  const [draft, setDraft] = useState("");
  const [humanAgent, setHumanAgent] = useState(false);
  const [sending, setSending] = useState(false);
  const [enrollPick, setEnrollPick] = useState("");
  const [sequences, setSequences] = useState<OutreachSequence[]>([]);
  const selectedRef = useRef<string | null>(null);

  const refresh = useCallback(() => {
    outreachInbox(clientId || undefined, search || undefined)
      .then((rows) => {
        setConvos(rows);
        const cur = selectedRef.current;
        if (cur) {
          const updated = rows.find((r) => r.id === cur);
          if (updated) setSelected(updated);
        }
      })
      .catch(() => {});
  }, [clientId, search]);

  // Auto-updating inbox: webhook events land server-side; poll to reflect them.
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 10_000);
    return () => clearInterval(t);
  }, [refresh]);

  useEffect(() => {
    if (isAdmin) listOutreachSequences(clientId || undefined).then(setSequences).catch(() => {});
  }, [clientId, isAdmin]);

  const openConvo = useCallback((c: OutreachConvo) => {
    setSelected(c);
    selectedRef.current = c.id;
    setMessages(null);
    setEnrollPick("");
    outreachMessages(c.id).then(setMessages).catch(() => {});
    if (c.unread_count > 0) outreachMarkRead(c.id).catch(() => {});
  }, []);

  useEffect(() => {
    if (!selected) return;
    const t = setInterval(() => {
      outreachMessages(selected.id).then(setMessages).catch(() => {});
    }, 10_000);
    return () => clearInterval(t);
  }, [selected]);

  const send = async () => {
    if (!selected || !draft.trim()) return;
    setSending(true);
    try {
      await outreachReply(selected.id, {
        text: draft.trim(),
        use_human_agent: humanAgent,
      });
      setDraft("");
      setHumanAgent(false);
      outreachMessages(selected.id).then(setMessages).catch(() => {});
      toast("Reply sent", "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Send failed", "error");
    } finally {
      setSending(false);
    }
  };

  const enroll = async (sequenceId: string) => {
    if (!selected || !sequenceId) return;
    try {
      await outreachEnroll({ sequence_id: sequenceId, conversation_id: selected.id });
      toast("Enrolled in sequence", "ok");
      setEnrollPick("");
      refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Enroll failed", "error");
      setEnrollPick("");
    }
  };

  const unenroll = (id: string) => {
    outreachUnenroll(id)
      .then(() => {
        toast("Exited sequence", "ok");
        refresh();
      })
      .catch((e) => toast(e instanceof Error ? e.message : "Failed", "error"));
  };

  const accountFor = (id: string) => accounts.find((a) => a.id === id);
  const sendDisabled =
    !draft.trim() ||
    (!selected?.window_open &&
      (!selected?.human_agent_available || !humanAgent));

  return (
    <div className="or-inbox">
      <GlassCard className="or-convos">
        <input
          className="input or-search"
          placeholder="Search conversations…"
          aria-label="Search conversations"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {convos === null && <SkeletonText lines={6} />}
        {convos !== null && convos.length === 0 && (
          <EmptyState title="No conversations yet">
            Conversations appear here the moment someone DMs, comments on, or
            mentions a connected Instagram account.
          </EmptyState>
        )}
        {convos?.map((c) => (
          <button
            key={c.id}
            type="button"
            className={`or-convo ${selected?.id === c.id ? "or-convo--active" : ""}`.trim()}
            onClick={() => openConvo(c)}
          >
            <div className="or-convo-top">
              <span className="or-convo-name">
                <span>{c.contact_name || c.peer.username || c.ig_user_id}</span>
                {c.unread_count > 0 && <Badge tone="info">{c.unread_count}</Badge>}
              </span>
              <time className="or-convo-time" title={c.last_message_at ?? undefined}>
                {timeAgo(c.last_message_at)}
              </time>
            </div>
            <span className="or-convo-preview">
              {(c.last_message_preview || "").slice(0, 64)}
            </span>
          </button>
        ))}
      </GlassCard>

      {selected ? (
        <GlassCard className="or-thread">
          <div className="or-thread-head">
            <h3 className="or-thread-title">
              @{selected.peer.username || selected.ig_user_id}
            </h3>
            <span className="or-badges">
              {selected.window_open ? (
                <Badge tone="ok">24h window open</Badge>
              ) : selected.human_agent_available ? (
                <Badge tone="warn">Window closed — human agent only</Badge>
              ) : (
                <Badge tone="danger">Window expired</Badge>
              )}
              <Badge tone="neutral">
                {accountFor(selected.account_id)?.username || "account"}
              </Badge>
            </span>
          </div>

          <div className="or-messages" aria-live="polite">
            {messages === null && <SkeletonText lines={4} />}
            {messages?.map((m) => {
              const muted = m.status === "queued" || m.status === "pending_review";
              return (
                <div
                  key={m.id}
                  className={[
                    "or-msg",
                    m.direction === "out" ? "or-msg--out" : "or-msg--in",
                    muted ? "or-msg--muted" : "",
                  ]
                    .filter(Boolean)
                    .join(" ")}
                >
                  <div>{m.text}</div>
                  <small className="or-msg-meta">
                    {m.direction === "out" ? m.kind : m.event_type}
                    {m.variant ? ` · variant ${m.variant.toUpperCase()}` : ""}
                    {m.message_tag ? ` · ${m.message_tag}` : ""}
                    {" · "}
                    {m.status}
                    {m.error_detail ? ` — ${m.error_detail}` : ""}
                    {" · "}
                    {timeAgo(m.sent_at || m.created_at)}
                  </small>
                </div>
              );
            })}
          </div>

          <div className="or-composer">
            <Field
              label="Reply"
              description={
                selected.window_open
                  ? undefined
                  : selected.human_agent_available
                    ? "Window closed — replies must be sent as a human agent."
                    : "The 7-day human-agent window has expired; wait for the user to re-engage."
              }
            >
              <textarea
                rows={2}
                placeholder={selected.window_open ? "Reply…" : "Reply…"}
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
              />
            </Field>
            <div className="or-composer-actions">
              {!selected.window_open && selected.human_agent_available && (
                <label className="or-check">
                  <input
                    type="checkbox"
                    checked={humanAgent}
                    onChange={(e) => setHumanAgent(e.target.checked)}
                  />
                  Send with HUMAN_AGENT tag (you are replying personally)
                </label>
              )}
              <Button
                variant="primary"
                busy={sending}
                disabled={sendDisabled}
                onClick={send}
              >
                <Send size={16} />
                Send
              </Button>
            </div>
          </div>
        </GlassCard>
      ) : (
        <GlassCard className="or-thread">
          <EmptyState title="Select a conversation">
            Pick a thread to see its history, CRM context, and reply.
          </EmptyState>
        </GlassCard>
      )}

      {selected ? (
        <GlassCard className="or-context">
          <h4 className="or-context-title">Contact context</h4>
          <div className="or-chips">
            {selected.contact_id ? (
              <Badge tone="ok">CRM contact linked</Badge>
            ) : (
              <Badge tone="neutral">No CRM contact yet</Badge>
            )}
            {selected.qualified && <Badge tone="ok">Qualified</Badge>}
            {selected.deal_value_cents != null && (
              <Badge tone="accent">
                Open deals ${(selected.deal_value_cents / 100).toLocaleString()}
              </Badge>
            )}
          </div>

          {(selected.enrollments.length > 0 || (isAdmin && sequences.length > 0)) && (
            <>
              <h4 className="or-context-title">Sequences</h4>
              <div className="or-chips">
                {selected.enrollments.map((e) => (
                  <div key={e.id} className="or-enrollment">
                    <Badge tone={e.status === "active" ? "info" : "neutral"}>
                      {e.sequence_name}: {e.status}
                      {e.exit_reason ? ` (${e.exit_reason})` : ""}
                    </Badge>
                    {isAdmin && e.status === "active" && (
                      <Button variant="link" size="sm" onClick={() => unenroll(e.id)}>
                        Exit
                      </Button>
                    )}
                  </div>
                ))}
                {isAdmin && sequences.length > 0 && (
                  <select
                    className="select"
                    aria-label="Enroll in sequence"
                    value={enrollPick}
                    onChange={(e) => {
                      setEnrollPick(e.target.value);
                      enroll(e.target.value);
                    }}
                  >
                    <option value="" disabled>
                      Enroll in sequence…
                    </option>
                    {sequences
                      .filter((s) => s.client_id === selected.client_id)
                      .map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
                  </select>
                )}
              </div>
            </>
          )}
        </GlassCard>
      ) : (
        <GlassCard className="or-context">
          <h4 className="or-context-title">Contact context</h4>
          <p className="or-muted">
            CRM link, qualification, deals, and sequence enrollment show here once
            you open a conversation.
          </p>
        </GlassCard>
      )}
    </div>
  );
}

// --- Trigger rules ---

const EMPTY_RULE: Partial<OutreachRule> = {
  name: "",
  trigger_type: "comment",
  keywords: [],
  media_ids: [],
  filters: {},
  reply_text: "",
  create_contact: true,
  tag_names: [],
  enroll_sequence_id: null,
  capture_prospect: false,
  once_per_user: true,
  enabled: true,
};

function RulesPanel({ clientId, accounts }: { clientId: string; accounts: IgAccount[] }) {
  const toast = useToast();
  const [rules, setRules] = useState<OutreachRule[] | null>(null);
  const [sequences, setSequences] = useState<OutreachSequence[]>([]);
  const [editing, setEditing] = useState<Partial<OutreachRule> | null>(null);

  const refresh = useCallback(() => {
    listOutreachRules(clientId || undefined).then(setRules).catch(() => {});
    listOutreachSequences(clientId || undefined).then(setSequences).catch(() => {});
  }, [clientId]);
  useEffect(refresh, [refresh]);

  const guard = useDirtyGuard(editing != null, JSON.stringify(editing ?? {}));
  const close = () => {
    if (guard()) setEditing(null);
  };

  const save = async () => {
    if (!editing?.name || !editing.account_id) {
      toast("Name and account are required", "error");
      return;
    }
    try {
      if (editing.id) await updateOutreachRule(editing.id, editing);
      else await createOutreachRule(editing);
      setEditing(null);
      refresh();
      toast("Rule saved", "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Save failed", "error");
    }
  };

  const remove = (r: OutreachRule) => {
    deleteOutreachRule(r.id)
      .then(() => {
        toast("Rule deleted", "ok");
        refresh();
      })
      .catch((e) => toast(e instanceof Error ? e.message : "Delete failed", "error"));
  };

  const columns: Column<OutreachRule>[] = [
    { key: "name", header: "Rule", render: (r) => r.name, sortValue: (r) => r.name },
    {
      key: "trigger",
      header: "Trigger",
      render: (r) => TRIGGER_LABELS[r.trigger_type],
      sortValue: (r) => r.trigger_type,
    },
    {
      key: "keywords",
      header: "Keywords",
      render: (r) => (r.keywords.length ? r.keywords.join(", ") : "any"),
    },
    {
      key: "actions",
      header: "Then",
      render: (r) =>
        [
          r.reply_text ? "reply" : null,
          r.create_contact ? "contact" : null,
          r.tag_names.length ? `tag(${r.tag_names.join(",")})` : null,
          r.enroll_sequence_id ? "enroll" : null,
          r.capture_prospect ? "prospect" : null,
        ]
          .filter(Boolean)
          .join(" + "),
    },
    {
      key: "enabled",
      header: "Status",
      render: (r) =>
        r.enabled ? <Badge tone="ok">on</Badge> : <Badge tone="neutral">off</Badge>,
      sortValue: (r) => (r.enabled ? 1 : 0),
    },
    {
      key: "manage",
      header: "",
      align: "right",
      render: (r) => (
        <>
          <Button variant="ghost" onClick={() => setEditing({ ...r })}>
            Edit
          </Button>
          <Button variant="danger-outline" onClick={() => remove(r)}>
            Delete
          </Button>
        </>
      ),
    },
  ];

  return (
    <div>
      <div className="or-head">
        <p className="or-sub">
          IF an inbound event matches, THEN reply + update the CRM — fully
          automated, always inside Meta's reply windows.
        </p>
        <Button
          variant="primary"
          onClick={() => setEditing({ ...EMPTY_RULE, account_id: accounts[0]?.id })}
        >
          <Plus size={16} />
          New rule
        </Button>
      </div>
      <DataTable
        columns={columns}
        rows={rules ?? []}
        rowKey={(r) => r.id}
        loading={rules === null}
        emptyMessage="No trigger rules yet — create one to start automating inbound engagement."
      />

      <Dialog
        open={editing != null}
        onClose={close}
        closeOnScrim={false}
        size="lg"
        title={editing?.id ? "Edit rule" : "New rule"}
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              Cancel
            </Button>
            <Button variant="primary" onClick={save}>
              Save rule
            </Button>
          </>
        }
      >
        {editing && (
          <div className="or-form">
            <Field label="Name">
              <input
                value={editing.name ?? ""}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              />
            </Field>
            <Field label="Instagram account">
              <select
                value={editing.account_id ?? ""}
                onChange={(e) => setEditing({ ...editing, account_id: e.target.value })}
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    @{a.username || a.ig_user_id}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="IF — trigger">
              <select
                value={editing.trigger_type}
                onChange={(e) =>
                  setEditing({ ...editing, trigger_type: e.target.value as OutreachTriggerType })
                }
              >
                {Object.entries(TRIGGER_LABELS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="…containing keywords (comma-separated, empty = any)">
              <input
                value={(editing.keywords ?? []).join(", ")}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    keywords: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                  })
                }
              />
            </Field>
            <Field label="…on specific post/ad ids" optional>
              <input
                value={(editing.media_ids ?? []).join(", ")}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    media_ids: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                  })
                }
              />
            </Field>
            <Field label="Only engagers with ≥ followers" optional>
              <input
                type="number"
                value={editing.filters?.min_followers ?? ""}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    filters: {
                      ...editing.filters,
                      min_followers: e.target.value ? Number(e.target.value) : undefined,
                    },
                  })
                }
              />
            </Field>
            <Field label="THEN — reply with (private reply for comments)" optional>
              <textarea
                rows={2}
                value={editing.reply_text ?? ""}
                onChange={(e) => setEditing({ ...editing, reply_text: e.target.value })}
                placeholder="Thanks {{username}}! Sending details now."
              />
            </Field>
            <Field label="Apply tags" optional>
              <input
                value={(editing.tag_names ?? []).join(", ")}
                onChange={(e) =>
                  setEditing({
                    ...editing,
                    tag_names: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                  })
                }
              />
            </Field>
            <Field label="Enroll in sequence" optional>
              <select
                value={editing.enroll_sequence_id ?? ""}
                onChange={(e) =>
                  setEditing({ ...editing, enroll_sequence_id: e.target.value || null })
                }
              >
                <option value="">—</option>
                {sequences.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </Field>
            <div className="or-fieldset">
              <span className="field-label">Options</span>
              <div className="or-options">
                <label className="or-check">
                  <input
                    type="checkbox"
                    checked={editing.create_contact ?? true}
                    onChange={(e) => setEditing({ ...editing, create_contact: e.target.checked })}
                  />
                  Create CRM contact
                </label>
                <label className="or-check">
                  <input
                    type="checkbox"
                    checked={editing.capture_prospect ?? false}
                    onChange={(e) => setEditing({ ...editing, capture_prospect: e.target.checked })}
                  />
                  Capture as prospect
                </label>
                <label className="or-check">
                  <input
                    type="checkbox"
                    checked={editing.once_per_user ?? true}
                    onChange={(e) => setEditing({ ...editing, once_per_user: e.target.checked })}
                  />
                  Once per user
                </label>
                <label className="or-check">
                  <input
                    type="checkbox"
                    checked={editing.enabled ?? true}
                    onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })}
                  />
                  Enabled
                </label>
              </div>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  );
}

// --- Sequences ---

function SequencesPanel({ clientId, accounts }: { clientId: string; accounts: IgAccount[] }) {
  const toast = useToast();
  const [sequences, setSequences] = useState<OutreachSequence[] | null>(null);
  const [editing, setEditing] = useState<OutreachSequence | null>(null);
  const [steps, setSteps] = useState<OutreachStep[]>([]);
  const [pending, setPending] = useState<
    { id: string; conversation_id: string; text: string; created_at: string }[]
  >([]);

  const refresh = useCallback(() => {
    listOutreachSequences(clientId || undefined).then(setSequences).catch(() => {});
    outreachPendingMessages(clientId || undefined).then(setPending).catch(() => {});
  }, [clientId]);
  useEffect(refresh, [refresh]);

  const guard = useDirtyGuard(editing != null, JSON.stringify(steps));
  const close = () => {
    if (guard()) setEditing(null);
  };

  const open = async (s: OutreachSequence) => {
    const full = await getOutreachSequence(s.id);
    setEditing(full);
    setSteps(full.steps ?? []);
  };

  const create = async () => {
    const account = accounts[0];
    if (!account) {
      toast("Connect an Instagram account first", "error");
      return;
    }
    try {
      const seq = await createOutreachSequence({
        account_id: account.id,
        name: "New sequence",
      });
      setEditing(seq);
      setSteps([]);
      refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Create failed", "error");
    }
  };

  const saveSteps = async () => {
    if (!editing) return;
    try {
      const updated = await saveOutreachSteps(editing.id, steps);
      setEditing(updated);
      setSteps(updated.steps ?? []);
      refresh();
      toast("Steps saved", "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Save failed", "error");
    }
  };

  const columns: Column<OutreachSequence>[] = [
    { key: "name", header: "Sequence", render: (s) => s.name, sortValue: (s) => s.name },
    {
      key: "status",
      header: "Status",
      render: (s) => (
        <Badge tone={s.status === "active" ? "ok" : s.status === "paused" ? "warn" : "neutral"}>
          {s.status}
        </Badge>
      ),
      sortValue: (s) => s.status,
    },
    {
      key: "flags",
      header: "Behavior",
      render: (s) =>
        [
          s.exit_on_reply ? "exit on reply" : "keeps running on reply",
          s.review_first_day ? "first-day review" : null,
        ]
          .filter(Boolean)
          .join(" · "),
    },
    {
      key: "manage",
      header: "",
      align: "right",
      render: (s) => (
        <>
          <Button variant="ghost" onClick={() => open(s)}>
            Edit
          </Button>
          {s.status === "active" ? (
            <Button
              variant="ghost"
              onClick={() =>
                pauseOutreachSequence(s.id)
                  .then(refresh)
                  .catch((e) => toast(e instanceof Error ? e.message : "Failed", "error"))
              }
            >
              Pause
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={() =>
                activateOutreachSequence(s.id)
                  .then(refresh)
                  .catch((e) => toast(e instanceof Error ? e.message : "Failed", "error"))
              }
            >
              Activate
            </Button>
          )}
        </>
      ),
    },
  ];

  return (
    <div>
      {pending.length > 0 && (
        <GlassCard className="or-pending">
          <h3 className="or-pending-title">Awaiting first-day review ({pending.length})</h3>
          {pending.map((p) => (
            <div key={p.id} className="or-pending-row">
              <span className="or-pending-text">{p.text}</span>
              <Button
                variant="primary"
                onClick={() =>
                  outreachApproveMessage(p.id)
                    .then(refresh)
                    .catch((e) => toast(e instanceof Error ? e.message : "Failed", "error"))
                }
              >
                Approve & send
              </Button>
              <Button
                variant="danger-outline"
                onClick={() =>
                  outreachDiscardMessage(p.id)
                    .then(refresh)
                    .catch((e) => toast(e instanceof Error ? e.message : "Failed", "error"))
                }
              >
                Discard
              </Button>
            </div>
          ))}
        </GlassCard>
      )}
      <div className="or-head">
        <p className="or-sub">
          Automated follow-up flows. Sequences only message people who have
          engaged — sends outside the 24h window queue until it reopens.
        </p>
        <Button variant="primary" onClick={create}>
          <Plus size={16} />
          New sequence
        </Button>
      </div>
      <DataTable
        columns={columns}
        rows={sequences ?? []}
        rowKey={(s) => s.id}
        loading={sequences === null}
        emptyMessage="No sequences yet."
      />

      <Dialog
        open={editing != null}
        onClose={close}
        closeOnScrim={false}
        size="lg"
        title={editing ? `Edit sequence — ${editing.name}` : "Edit sequence"}
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              Close
            </Button>
            <Button variant="primary" onClick={saveSteps}>
              Save steps
            </Button>
          </>
        }
      >
        {editing && (
          <>
            <p className="or-tokens">
              Steps run top to bottom. Personalization tokens:{" "}
              <code>{"{{first_name}}"}</code> <code>{"{{business_name}}"}</code>{" "}
              <code>{"{{username}}"}</code> <code>{"{{vertical}}"}</code>.
            </p>
            {steps.map((step, i) => (
              <StepEditor
                key={i}
                step={step}
                index={i}
                onChange={(next) => setSteps(steps.map((s, j) => (j === i ? next : s)))}
                onRemove={() => setSteps(steps.filter((_, j) => j !== i))}
              />
            ))}
            <div className="or-step-add">
              <Button onClick={() => setSteps([...steps, { kind: "message", text_a: "" }])}>
                <Plus size={16} />
                Message
              </Button>
              <Button onClick={() => setSteps([...steps, { kind: "wait", wait_hours: 24 }])}>
                <Plus size={16} />
                Wait
              </Button>
              <Button
                onClick={() =>
                  setSteps([
                    ...steps,
                    { kind: "condition", condition: "replied", on_true: "exit", on_false: "continue" },
                  ])
                }
              >
                <Plus size={16} />
                Condition
              </Button>
            </div>
          </>
        )}
      </Dialog>
    </div>
  );
}

function StepEditor({
  step,
  index,
  onChange,
  onRemove,
}: {
  step: OutreachStep;
  index: number;
  onChange: (s: OutreachStep) => void;
  onRemove: () => void;
}) {
  return (
    <GlassCard className="or-step">
      <div className="or-step-head">
        <Badge tone="accent">{index + 1}</Badge>
        <strong className="or-step-title">{step.kind}</strong>
        {step.promoted_variant && (
          <Badge tone="ok">variant {step.promoted_variant.toUpperCase()} promoted</Badge>
        )}
        <Button variant="ghost" onClick={onRemove}>
          Remove
        </Button>
      </div>
      {step.kind === "message" && (
        <div className="or-form">
          <Field label="Message (variant A)">
            <textarea
              rows={2}
              value={step.text_a ?? ""}
              onChange={(e) => onChange({ ...step, text_a: e.target.value })}
            />
          </Field>
          <Field label="Variant B — A/B test, auto-promotes the better reply rate" optional>
            <textarea
              rows={2}
              value={step.text_b ?? ""}
              onChange={(e) => onChange({ ...step, text_b: e.target.value || null })}
            />
          </Field>
        </div>
      )}
      {step.kind === "wait" && (
        <Field label="Wait (hours)">
          <input
            type="number"
            min={1}
            value={step.wait_hours ?? 24}
            onChange={(e) => onChange({ ...step, wait_hours: Number(e.target.value) })}
          />
        </Field>
      )}
      {step.kind === "condition" && (
        <div className="or-form">
          <Field label="If they replied">
            <select
              value={step.on_true ?? "exit"}
              onChange={(e) => onChange({ ...step, on_true: e.target.value })}
            >
              <option value="exit">Exit sequence</option>
              <option value="continue">Continue</option>
            </select>
          </Field>
          <Field label="If no reply">
            <select
              value={step.on_false ?? "continue"}
              onChange={(e) => onChange({ ...step, on_false: e.target.value })}
            >
              <option value="continue">Continue</option>
              <option value="exit">Exit sequence</option>
            </select>
          </Field>
        </div>
      )}
    </GlassCard>
  );
}

// --- Prospects ---

function ProspectsPanel({
  clientId,
  clients,
  accounts,
}: {
  clientId: string;
  clients: Client[];
  accounts: IgAccount[];
}) {
  const toast = useToast();
  const [prospects, setProspects] = useState<OutreachProspect[] | null>(null);
  const [importing, setImporting] = useState(false);
  const [handles, setHandles] = useState("");
  const [vertical, setVertical] = useState("");
  const [importClient, setImportClient] = useState("");
  const [sequenceId, setSequenceId] = useState("");
  const [sequences, setSequences] = useState<OutreachSequence[]>([]);

  const refresh = useCallback(() => {
    listOutreachProspects(clientId || undefined).then(setProspects).catch(() => {});
    listOutreachSequences(clientId || undefined).then(setSequences).catch(() => {});
  }, [clientId]);
  useEffect(refresh, [refresh]);

  const guard = useDirtyGuard(
    importing,
    JSON.stringify({ handles, vertical, importClient, sequenceId }),
  );
  const close = () => {
    if (guard()) setImporting(false);
  };

  const doImport = async () => {
    const list = handles
      .split(/[\s,\n]+/)
      .map((h) => h.trim())
      .filter(Boolean);
    if (!importClient || list.length === 0) {
      toast("Pick a client and paste at least one handle", "error");
      return;
    }
    try {
      const res = await importOutreachProspects({
        client_id: importClient,
        handles: list,
        vertical: vertical || undefined,
        sequence_id: sequenceId || undefined,
        account_id: accounts.find((a) => a.client_id === importClient)?.id,
      });
      toast(`Imported ${res.created} (${res.skipped} already present)`, "ok");
      setImporting(false);
      setHandles("");
      refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Import failed", "error");
    }
  };

  const remove = (p: OutreachProspect) => {
    deleteOutreachProspect(p.id)
      .then(() => {
        toast("Prospect removed", "ok");
        refresh();
      })
      .catch((e) => toast(e instanceof Error ? e.message : "Failed", "error"));
  };

  const columns: Column<OutreachProspect>[] = [
    { key: "username", header: "Handle", render: (p) => `@${p.username}`, sortValue: (p) => p.username },
    { key: "vertical", header: "Vertical", render: (p) => p.vertical || "—", sortValue: (p) => p.vertical ?? "" },
    {
      key: "status",
      header: "Status",
      render: (p) => (
        <Badge tone={p.status === "engaged" ? "ok" : "neutral"}>{p.status}</Badge>
      ),
      sortValue: (p) => p.status,
    },
    { key: "source", header: "Source", render: (p) => p.source },
    {
      key: "enrichment",
      header: "Enrichment",
      render: (p) => {
        const followers = p.enrichment["followers_count"];
        const website = p.enrichment["website"];
        return followers || website
          ? `${followers ? `${followers} followers` : ""}${followers && website ? " · " : ""}${website ? String(website) : ""}`
          : "—";
      },
    },
    {
      key: "manage",
      header: "",
      align: "right",
      render: (p) => (
        <>
          <Button
            variant="ghost"
            onClick={() =>
              enrichOutreachProspect(p.id)
                .then((r) => {
                  toast(
                    r.status === "ok" ? "Validated via API" : "Handle not found",
                    r.status === "ok" ? "ok" : "error",
                  );
                  refresh();
                })
                .catch((e) => toast(e instanceof Error ? e.message : "Failed", "error"))
            }
          >
            Validate
          </Button>
          <Button variant="danger-outline" onClick={() => remove(p)}>
            Remove
          </Button>
        </>
      ),
    },
  ];

  return (
    <div>
      <div className="or-head">
        <p className="or-sub">
          A watch list, not a cold-DM list: Instagram's API can't message
          someone who hasn't engaged. Prospects auto-enroll in their sequence
          the moment they DM, comment, or mention you — pair this list with
          organic engagement or ads to spark that first touch.
        </p>
        <Button variant="primary" onClick={() => setImporting(true)}>
          <Plus size={16} />
          Import handles
        </Button>
      </div>
      <DataTable
        columns={columns}
        rows={prospects ?? []}
        rowKey={(p) => p.id}
        loading={prospects === null}
        initialSort="-username"
        emptyMessage="No prospects yet — import a handle list or let trigger rules capture business engagers."
      />

      <Dialog
        open={importing}
        onClose={close}
        closeOnScrim={false}
        title="Import prospect handles"
        footer={
          <>
            <Button variant="ghost" onClick={close}>
              Cancel
            </Button>
            <Button variant="primary" onClick={doImport}>
              Import
            </Button>
          </>
        }
      >
        <div className="or-form">
          <Field label="Client">
            <select value={importClient} onChange={(e) => setImportClient(e.target.value)}>
              <option value="">—</option>
              {clients.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Handles (one per line or comma-separated)">
            <textarea
              rows={5}
              value={handles}
              onChange={(e) => setHandles(e.target.value)}
              placeholder={"@desertplumbingaz\n@valleyhvac"}
            />
          </Field>
          <Field label="Business vertical" optional>
            <input
              value={vertical}
              onChange={(e) => setVertical(e.target.value)}
              placeholder="hvac / plumbing / electrical"
            />
          </Field>
          <Field label="Auto-enroll in sequence on engagement" optional>
            <select value={sequenceId} onChange={(e) => setSequenceId(e.target.value)}>
              <option value="">—</option>
              {sequences.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </Field>
        </div>
      </Dialog>
    </div>
  );
}

// --- Analytics ---

function AnalyticsPanel({ clientId }: { clientId: string }) {
  const toast = useToast();
  const [data, setData] = useState<OutreachAnalytics | null>(null);
  const [days, setDays] = useState(30);

  useEffect(() => {
    setData(null);
    outreachAnalytics(clientId || undefined, days).then(setData).catch(() => {});
  }, [clientId, days]);

  const loading = data === null;
  const h = data?.headline;

  const seqColumns: Column<OutreachAnalytics["sequences"][number]>[] = [
    { key: "name", header: "Sequence", render: (s) => s.name, sortValue: (s) => s.name },
    { key: "enrolled", header: "Enrolled", align: "right", render: (s) => s.enrolled, sortValue: (s) => s.enrolled },
    { key: "sent", header: "Sent", align: "right", render: (s) => s.sent, sortValue: (s) => s.sent },
    { key: "replied", header: "Replied", align: "right", render: (s) => s.replied, sortValue: (s) => s.replied },
    { key: "booked", header: "Booked", align: "right", render: (s) => s.booked, sortValue: (s) => s.booked },
    { key: "closed", header: "Closed", align: "right", render: (s) => s.closed, sortValue: (s) => s.closed },
    {
      key: "rate",
      header: "Reply rate",
      align: "right",
      render: (s) => `${Math.round(s.reply_rate * 100)}%`,
      sortValue: (s) => s.reply_rate,
    },
    {
      key: "variants",
      header: "A/B",
      render: (s) =>
        s.variants.length
          ? s.variants
              .map(
                (v) =>
                  `#${v.step_position + 1} A ${v.a.replies}/${v.a.sent} vs B ${v.b.replies}/${v.b.sent}${v.promoted ? ` → ${v.promoted.toUpperCase()}` : ""}`,
              )
              .join("; ")
          : "—",
    },
  ];
  const ruleColumns: Column<OutreachAnalytics["rules"][number]>[] = [
    { key: "name", header: "Rule", render: (r) => r.name, sortValue: (r) => r.name },
    { key: "type", header: "Trigger", render: (r) => r.trigger_type },
    { key: "fired", header: "Fired", align: "right", render: (r) => r.fired, sortValue: (r) => r.fired },
    { key: "sent", header: "Sent", align: "right", render: (r) => r.sent, sortValue: (r) => r.sent },
    { key: "replies", header: "Replies", align: "right", render: (r) => r.replies, sortValue: (r) => r.replies },
  ];
  const vertColumns: Column<OutreachAnalytics["verticals"][number]>[] = [
    { key: "vertical", header: "Vertical", render: (v) => v.vertical, sortValue: (v) => v.vertical },
    { key: "prospects", header: "Prospects", align: "right", render: (v) => v.prospects, sortValue: (v) => v.prospects },
    { key: "engaged", header: "Engaged", align: "right", render: (v) => v.engaged, sortValue: (v) => v.engaged },
    {
      key: "rate",
      header: "Engagement rate",
      align: "right",
      render: (v) => (v.prospects ? `${Math.round((v.engaged / v.prospects) * 100)}%` : "—"),
      sortValue: (v) => (v.prospects ? v.engaged / v.prospects : 0),
    },
  ];

  const avgReply =
    h?.avg_reply_seconds == null
      ? "—"
      : h.avg_reply_seconds < 3600
        ? `${Math.round(h.avg_reply_seconds / 60)}m`
        : `${(h.avg_reply_seconds / 3600).toFixed(1)}h`;

  return (
    <div>
      <div className="or-analytics-bar">
        <Segmented
          ariaLabel="Analytics date range"
          value={String(days)}
          onChange={(v) => setDays(Number(v))}
          options={[
            { value: "7", label: "7 days" },
            { value: "30", label: "30 days" },
            { value: "90", label: "90 days" },
          ]}
        />
        <Button
          onClick={() =>
            downloadCsv(outreachAuditExportUrl(clientId || undefined), "outreach-audit.csv").catch(
              (e) => toast(e instanceof Error ? e.message : "Export failed", "error"),
            )
          }
        >
          Export audit CSV
        </Button>
      </div>

      <div className="or-kpis">
        <KpiGrid>
          {loading || !h ? (
            <>
              <KpiSkeleton />
              <KpiSkeleton />
              <KpiSkeleton />
              <KpiSkeleton />
              <KpiSkeleton />
            </>
          ) : (
            <>
              <Kpi label="Messages sent" value={h.sent.toLocaleString()} />
              <Kpi label="Replies received" value={h.received.toLocaleString()} />
              <Kpi label="Reply rate" value={`${Math.round(h.reply_rate * 100)}%`} />
              <Kpi label="Active enrollments" value={h.active_enrollments.toLocaleString()} />
              <Kpi label="Avg time to reply" value={avgReply} />
            </>
          )}
        </KpiGrid>
      </div>

      <Section title="Sequence funnel">
        <DataTable
          columns={seqColumns}
          rows={data?.sequences ?? []}
          rowKey={(s) => s.sequence_id}
          loading={loading}
          emptyMessage="No sequence activity in this window."
        />
      </Section>
      <Section title="Trigger rules">
        <DataTable
          columns={ruleColumns}
          rows={data?.rules ?? []}
          rowKey={(r) => r.rule_id}
          loading={loading}
          emptyMessage="No rule activity in this window."
        />
      </Section>
      <Section title="Business verticals">
        <DataTable
          columns={vertColumns}
          rows={data?.verticals ?? []}
          rowKey={(v) => v.vertical}
          loading={loading}
          emptyMessage="Tag prospects with a vertical to see the breakdown."
        />
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="or-section">
      <h3 className="or-section-title">{title}</h3>
      {children}
    </section>
  );
}

// --- Accounts (settings) ---

function AccountsPanel({
  clientId,
  clients,
  accounts,
  onChanged,
}: {
  clientId: string;
  clients: Client[];
  accounts: IgAccount[];
  onChanged: () => void;
}) {
  const toast = useToast();
  const [connectClient, setConnectClient] = useState(clientId);
  const [disconnecting, setDisconnecting] = useState<IgAccount | null>(null);
  const [busy, setBusy] = useState(false);

  // Keep the connect dropdown in sync with the global client filter.
  useEffect(() => setConnectClient(clientId), [clientId]);

  const connect = async () => {
    if (!connectClient) {
      toast("Pick a client to connect the account under", "error");
      return;
    }
    try {
      const { url } = await igConnectStart(connectClient);
      openAuthUrl(url);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Could not start OAuth", "error");
    }
  };

  const confirmDisconnect = async () => {
    if (!disconnecting) return;
    setBusy(true);
    try {
      await disconnectIgAccount(disconnecting.id);
      toast("Disconnected", "ok");
      setDisconnecting(null);
      onChanged();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Disconnect failed", "error");
    } finally {
      setBusy(false);
    }
  };

  const disconnectRows: ReceiptRow[] = disconnecting
    ? [
        {
          field: `@${disconnecting.username || disconnecting.ig_user_id}`,
          oldValue: "connected",
          newValue: "disconnected",
        },
      ]
    : [];

  const columns: Column<IgAccount>[] = [
    {
      key: "account",
      header: "Account",
      render: (a) => `@${a.username || a.ig_user_id}`,
      sortValue: (a) => a.username ?? "",
    },
    {
      key: "client",
      header: "Client",
      render: (a) => clients.find((c) => c.id === a.client_id)?.name || a.client_id,
    },
    {
      key: "status",
      header: "Status",
      render: (a) =>
        a.status === "active" ? (
          <Badge tone="ok">connected</Badge>
        ) : (
          <Badge tone="danger">reconnect needed</Badge>
        ),
      sortValue: (a) => a.status,
    },
    {
      key: "cap",
      header: "Daily send cap",
      render: (a) => (
        <input
          className="input or-cap-input"
          type="number"
          min={1}
          max={1000}
          defaultValue={a.daily_send_cap}
          aria-label={`Daily send cap for @${a.username || a.ig_user_id}`}
          onBlur={(e) => {
            const v = Number(e.target.value);
            if (v && v !== a.daily_send_cap)
              updateIgAccount(a.id, { daily_send_cap: v })
                .then(() => {
                  toast("Cap updated", "ok");
                  onChanged();
                })
                .catch((err) => toast(err instanceof Error ? err.message : "Failed", "error"));
          }}
        />
      ),
    },
    {
      key: "automation",
      header: "Automation",
      render: (a) => (
        <label className="or-automation">
          <input
            type="checkbox"
            checked={!a.automation_paused}
            onChange={(e) =>
              updateIgAccount(a.id, { automation_paused: !e.target.checked })
                .then(onChanged)
                .catch((err) => toast(err instanceof Error ? err.message : "Failed", "error"))
            }
          />
          {a.automation_paused ? "paused" : "running"}
        </label>
      ),
    },
    {
      key: "manage",
      header: "",
      align: "right",
      render: (a) => (
        <Button variant="danger-outline" onClick={() => setDisconnecting(a)}>
          Disconnect
        </Button>
      ),
    },
  ];

  return (
    <div>
      <GlassCard className="or-connect">
        <h3 className="or-connect-title">Connect an Instagram professional account</h3>
        <p className="or-sub">
          OAuth through Meta — the account authorizes Salescale's app; tokens
          stay server-side. Requires an Instagram Business/Creator account
          linked to a Facebook Page the client manages.
        </p>
        <div className="or-connect-row">
          <select
            className="select or-select"
            aria-label="Client to connect under"
            value={connectClient}
            onChange={(e) => setConnectClient(e.target.value)}
          >
            <option value="">Choose client…</option>
            {clients.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <Button variant="primary" onClick={connect}>
            Connect via Meta
          </Button>
        </div>
      </GlassCard>

      <DataTable
        columns={columns}
        rows={accounts}
        rowKey={(a) => a.id}
        emptyMessage="No Instagram accounts connected yet."
      />

      <ConfirmDialog
        open={disconnecting != null}
        onCancel={() => setDisconnecting(null)}
        onConfirm={confirmDisconnect}
        rows={disconnectRows}
        tone="danger"
        title="Disconnect account"
        confirmLabel="Disconnect"
        cancelLabel="Keep connected"
        busy={busy}
      >
        <p className="or-muted">
          Automated sequences on this account stop until it is reconnected.
        </p>
      </ConfirmDialog>
    </div>
  );
}
