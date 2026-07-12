/**
 * Email Outreach — cold-email sequences on a self-hosted mailbox.
 *
 * One view, five tabs: Dashboard (default, all team roles), Campaigns (admin),
 * Inbox (all team roles), Accounts (admin), Suppression (admin). Admin-only
 * tabs are hidden for the member role, matching the server-side gates.
 *
 * Every send routes through the Phase-12 verification gate server-side, so the
 * enroll receipt surfaces risky/invalid/suppressed splits rather than silently
 * dropping addresses. Bounce rate over 5% is the deliverability red line and is
 * flagged in danger tone on the dashboard and the account health strip.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  activateEmailCampaign,
  addEmailSuppression,
  composeEmail,
  createEmailAccount,
  createEmailCampaign,
  deleteEmailAccount,
  deleteEmailSuppression,
  emailAnalytics,
  emailUsage,
  enrollEmailContacts,
  getEmailCampaign,
  getHouseClient,
  listCrmContactsForClient,
  listEmailAccounts,
  listEmailCampaigns,
  listEmailEnrollments,
  listEmailSuppression,
  listEmailThreadMessages,
  listEmailThreads,
  markEmailThreadRead,
  pauseEmailCampaign,
  previewEmailStep,
  replyEmailThread,
  saveEmailSteps,
  testEmailAccount,
  unenrollEmail,
  updateEmailAccount,
  updateEmailCampaign,
  type EmailAccount,
  type EmailAccountBody,
  type EmailAnalytics,
  type EmailCampaign,
  type EmailCampaignDetail,
  type EmailEnrollment,
  type EmailMessage,
  type EmailPickContact,
  type EmailSmtpSecurity,
  type EmailStep,
  type EmailSuppression,
  type EmailThread,
  type EmailUsage,
  type EnrollReceipt,
} from "./api";
import { LineChart } from "./components/charts";
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
import "./styles/views/email_outreach.css";

// --- formatting helpers ---

function timeAgo(iso: string | null): string {
  if (!iso) return "—";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** Rate 0–1 float → "42.3%"; null → "—". */
const pct = (v: number | null | undefined): string =>
  v == null ? "—" : `${(v * 100).toFixed(1)}%`;

const int = (v: number | null | undefined): string =>
  v == null ? "—" : v.toLocaleString();

const contactLabel = (c: {
  first_name: string | null;
  last_name: string | null;
  email: string | null;
}): string =>
  [c.first_name, c.last_name].filter(Boolean).join(" ") ||
  c.email ||
  "Unnamed contact";

/** Deliverability red line — a bounce rate at or above this reads as danger. */
const BOUNCE_RED_LINE = 0.05;

// send_days is a 0–6 array; 0 = Monday (Python date.weekday()). See notes.
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const HOURS = Array.from({ length: 24 }, (_, i) => i);
const hourLabel = (h: number) =>
  `${((h + 11) % 12) + 1}:00 ${h < 12 ? "AM" : "PM"}`;

const TOKENS_HINT = (
  <>
    Personalization: <code>{"{{first_name}}"}</code>{" "}
    <code>{"{{last_name}}"}</code> <code>{"{{company}}"}</code>{" "}
    <code>{"{{city}}"}</code> <code>{"{{state}}"}</code>{" "}
    <code>{"{{email}}"}</code> <code>{"{{custom.<key>}}"}</code>. Fallbacks like{" "}
    <code>{"{{first_name|there}}"}</code>, plus <code>{"{{ai_snippet}}"}</code>{" "}
    and <code>{"{{unsubscribe_url}}"}</code>.
  </>
);

/** Resolve the org's house-CRM client id once, then load its contacts. Used by
 * the enroll and compose pickers — imports and prospecting land in the house
 * CRM, so that's where a campaign audience comes from. */
function useHouseContacts(active: boolean) {
  const [contacts, setContacts] = useState<EmailPickContact[] | null>(null);
  useEffect(() => {
    if (!active) return;
    let alive = true;
    setContacts(null);
    getHouseClient()
      .then((r) => listCrmContactsForClient(r.client_id))
      .then((rows) => {
        if (alive) setContacts(rows);
      })
      .catch(() => {
        if (alive) setContacts([]);
      });
    return () => {
      alive = false;
    };
  }, [active]);
  return contacts;
}

// ==========================================================================
// Root view
// ==========================================================================

type Panel = "dashboard" | "campaigns" | "inbox" | "accounts" | "suppression";

export function EmailOutreachView({ isAdmin }: { isAdmin: boolean }) {
  const [panel, setPanel] = useState<Panel>("dashboard");
  const [accounts, setAccounts] = useState<EmailAccount[]>([]);
  const [usage, setUsage] = useState<EmailUsage | null>(null);

  const refreshAccounts = useCallback(() => {
    listEmailAccounts().then(setAccounts).catch(() => {});
  }, []);
  useEffect(refreshAccounts, [refreshAccounts]);
  useEffect(() => {
    emailUsage().then(setUsage).catch(() => {});
  }, [panel]);

  const panels: { key: Panel; label: string; adminOnly: boolean }[] = [
    { key: "dashboard", label: "Dashboard", adminOnly: false },
    { key: "campaigns", label: "Campaigns", adminOnly: true },
    { key: "inbox", label: "Inbox", adminOnly: false },
    { key: "accounts", label: "Accounts", adminOnly: true },
    { key: "suppression", label: "Suppression", adminOnly: true },
  ];
  const visible = panels.filter((p) => isAdmin || !p.adminOnly);

  const errored = accounts.filter((a) => a.status === "error");

  return (
    <div className="eml">
      {errored.length > 0 && (
        <div className="eml-banner">
          <Alert tone="danger" title="Mailbox connection error">
            {errored.map((a) => a.from_email).join(", ")}{" "}
            {errored.length === 1 ? "is" : "are"} failing SMTP/IMAP — sending is
            paused until reconnected.
            {isAdmin && (
              <Button variant="link" size="sm" onClick={() => setPanel("accounts")}>
                Go to Accounts
              </Button>
            )}
          </Alert>
        </div>
      )}

      <div className="eml-subnav">
        <Tabs
          ariaLabel="Email outreach sections"
          tabs={visible.map((p) => ({ id: p.key, label: p.label }))}
          active={panel}
          onChange={(id) => setPanel(id as Panel)}
        />
        <UsageChip usage={usage} />
      </div>

      {panel === "dashboard" && <DashboardPanel accounts={accounts} />}
      {panel === "campaigns" && isAdmin && (
        <CampaignsPanel accounts={accounts} />
      )}
      {panel === "inbox" && <InboxPanel accounts={accounts} />}
      {panel === "accounts" && isAdmin && (
        <AccountsPanel accounts={accounts} onChanged={refreshAccounts} />
      )}
      {panel === "suppression" && isAdmin && <SuppressionPanel />}
    </div>
  );
}

function UsageChip({ usage }: { usage: EmailUsage | null }) {
  if (!usage) return <span className="eml-usage eml-usage--load" aria-hidden="true" />;
  const { used, limit } = usage.sends;
  const over = limit != null && used >= limit;
  return (
    <span className={`eml-usage ${over ? "eml-usage--over" : ""}`.trim()}>
      <strong>{int(used)}</strong> of{" "}
      {limit == null ? "unlimited" : int(limit)} sends this month
    </span>
  );
}

// ==========================================================================
// 1. Dashboard
// ==========================================================================

function DashboardPanel({ accounts }: { accounts: EmailAccount[] }) {
  const [campaignId, setCampaignId] = useState<string>("");
  const [days, setDays] = useState(30);
  const [campaigns, setCampaigns] = useState<EmailCampaign[]>([]);
  const [data, setData] = useState<EmailAnalytics | null>(null);

  useEffect(() => {
    listEmailCampaigns().then(setCampaigns).catch(() => {});
  }, []);
  useEffect(() => {
    setData(null);
    emailAnalytics(campaignId || undefined, days).then(setData).catch(() => {});
  }, [campaignId, days]);

  const loading = data === null;
  const t = data?.totals;
  const bounceOver = (t?.bounce_rate ?? 0) >= BOUNCE_RED_LINE;

  const campaignColumns: Column<EmailAnalytics["by_campaign"][number]>[] = [
    { key: "name", header: "Campaign", render: (c) => c.name, sortValue: (c) => c.name },
    { key: "sent", header: "Sent", align: "right", render: (c) => int(c.sent), sortValue: (c) => c.sent },
    {
      key: "delivery",
      header: "Delivery",
      align: "right",
      render: (c) => pct(c.delivery_rate),
      sortValue: (c) => c.delivery_rate ?? -1,
    },
    {
      key: "open",
      header: "Open",
      align: "right",
      render: (c) => pct(c.open_rate),
      sortValue: (c) => c.open_rate ?? -1,
    },
    {
      key: "reply",
      header: "Reply",
      align: "right",
      render: (c) => pct(c.reply_rate),
      sortValue: (c) => c.reply_rate ?? -1,
    },
  ];

  const chartLabels = data?.by_day.map((d) => d.date) ?? [];
  const hasChart = chartLabels.length > 1;

  return (
    <div>
      <div className="eml-bar">
        <select
          className="select eml-select"
          aria-label="Filter analytics by campaign"
          value={campaignId}
          onChange={(e) => setCampaignId(e.target.value)}
        >
          <option value="">All campaigns</option>
          {campaigns.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
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
      </div>

      <div className="eml-kpis">
        <KpiGrid>
          {loading || !t ? (
            <>
              <KpiSkeleton />
              <KpiSkeleton />
              <KpiSkeleton />
              <KpiSkeleton />
              <KpiSkeleton />
              <KpiSkeleton />
            </>
          ) : (
            <>
              <Kpi label="Sent" value={int(t.sent)} />
              <Kpi label="Delivery rate" value={pct(t.delivery_rate)} />
              <Kpi label="Open rate" value={pct(t.open_rate)} />
              <Kpi label="Reply rate" value={pct(t.reply_rate)} />
              <Kpi label="Bounce rate" value={pct(t.bounce_rate)} />
              <Kpi label="Unsubscribe rate" value={pct(t.unsubscribe_rate)} />
            </>
          )}
        </KpiGrid>
      </div>

      {!loading && bounceOver && (
        <div className="eml-redline">
          <Alert tone="danger" title="Bounce rate above the deliverability red line">
            {pct(t?.bounce_rate)} of sends bounced (over{" "}
            {pct(BOUNCE_RED_LINE)}). Pause sending, clean the list, and let
            warmup rebuild reputation before continuing.
          </Alert>
        </div>
      )}

      <Section title="Volume over time">
        {loading ? (
          <GlassCard>
            <SkeletonText lines={5} />
          </GlassCard>
        ) : hasChart ? (
          <GlassCard>
            <LineChart
              labels={chartLabels}
              series={[
                { name: "Sent", data: data!.by_day.map((d) => d.sent) },
                { name: "Opened", data: data!.by_day.map((d) => d.opened) },
                { name: "Replied", data: data!.by_day.map((d) => d.replied) },
                { name: "Bounced", data: data!.by_day.map((d) => d.bounced) },
              ]}
              height={220}
              ariaLabel={`Daily email volume over ${chartLabels.length} days`}
            />
          </GlassCard>
        ) : (
          <GlassCard>
            <EmptyState title="Not enough data yet">
              Send activity charts here once campaigns have run for a couple of
              days.
            </EmptyState>
          </GlassCard>
        )}
      </Section>

      <Section title="By campaign">
        <DataTable
          columns={campaignColumns}
          rows={data?.by_campaign ?? []}
          rowKey={(c) => c.campaign_id}
          loading={loading}
          initialSort="-sent"
          emptyMessage="No campaign activity in this window — activate a campaign to start sending."
        />
      </Section>

      <Section title="Mailbox health">
        {loading ? (
          <GlassCard>
            <SkeletonText lines={2} />
          </GlassCard>
        ) : (data?.accounts.length ?? 0) === 0 ? (
          <GlassCard>
            <EmptyState title="No mailboxes connected">
              Connect a sending mailbox in the Accounts tab to start outreach.
            </EmptyState>
          </GlassCard>
        ) : (
          <div className="eml-health">
            {(data ?? { accounts: [] }).accounts.map((a) => {
              const bounceHot =
                a.bounce_rate_7d != null && a.bounce_rate_7d >= BOUNCE_RED_LINE;
              return (
                <GlassCard key={a.account_id} className="eml-health-card">
                  <div className="eml-health-top">
                    <span className="eml-health-email">{a.from_email}</span>
                    <Badge tone={a.status === "active" ? "ok" : "danger"}>
                      {a.status === "active" ? "connected" : "error"}
                    </Badge>
                  </div>
                  <div className="eml-health-meta">
                    <span>
                      {int(a.sends_today)} of {int(a.effective_daily_cap)} today
                    </span>
                    {a.warmup_stage && (
                      <Badge tone="info">warmup: {a.warmup_stage}</Badge>
                    )}
                    <Badge tone={bounceHot ? "danger" : "neutral"}>
                      7d bounce {pct(a.bounce_rate_7d)}
                    </Badge>
                  </div>
                </GlassCard>
              );
            })}
          </div>
        )}
      </Section>

      {accounts.length === 0 && (
        <div className="eml-redline">
          <Alert tone="info" title="No mailboxes yet">
            The dashboard fills in once you connect a mailbox and activate a
            campaign.
          </Alert>
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="eml-section">
      <h3 className="eml-section-title">{title}</h3>
      {children}
    </section>
  );
}

// ==========================================================================
// 2. Campaigns
// ==========================================================================

function CampaignsPanel({ accounts }: { accounts: EmailAccount[] }) {
  const toast = useToast();
  const [campaigns, setCampaigns] = useState<EmailCampaign[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listEmailCampaigns().then(setCampaigns).catch(() => {});
  }, []);
  useEffect(refresh, [refresh]);

  const columns: Column<EmailCampaign>[] = [
    { key: "name", header: "Campaign", render: (c) => c.name, sortValue: (c) => c.name },
    {
      key: "status",
      header: "Status",
      render: (c) => <Badge tone={c.status}>{c.status}</Badge>,
      sortValue: (c) => c.status,
    },
    { key: "steps", header: "Steps", align: "right", render: (c) => int(c.steps_count), sortValue: (c) => c.steps_count },
    {
      key: "active",
      header: "Active",
      align: "right",
      render: (c) => int(c.active_enrollments),
      sortValue: (c) => c.active_enrollments,
    },
    { key: "sent", header: "Sent", align: "right", render: (c) => int(c.sent), sortValue: (c) => c.sent },
    {
      key: "open",
      header: "Open",
      align: "right",
      render: (c) => pct(c.open_rate),
      sortValue: (c) => c.open_rate ?? -1,
    },
    {
      key: "reply",
      header: "Reply",
      align: "right",
      render: (c) => pct(c.reply_rate),
      sortValue: (c) => c.reply_rate ?? -1,
    },
    {
      key: "bounce",
      header: "Bounce",
      align: "right",
      render: (c) =>
        c.bounce_rate != null && c.bounce_rate >= BOUNCE_RED_LINE ? (
          <Badge tone="danger">{pct(c.bounce_rate)}</Badge>
        ) : (
          pct(c.bounce_rate)
        ),
      sortValue: (c) => c.bounce_rate ?? -1,
    },
    {
      key: "manage",
      header: "",
      align: "right",
      render: (c) => (
        <Button variant="ghost" onClick={() => setEditingId(c.id)}>
          Open
        </Button>
      ),
    },
  ];

  return (
    <div>
      <div className="eml-head">
        <p className="eml-sub">
          Multi-step cold-email sequences. Every audience passes the email
          verification gate — invalid addresses are excluded, risky ones warned
          — and bounces and unsubscribes land on the suppression list
          automatically.
        </p>
        <Button
          variant="primary"
          disabled={accounts.length === 0}
          onClick={() => setCreating(true)}
        >
          <Plus size={16} />
          New campaign
        </Button>
      </div>

      {accounts.length === 0 ? (
        <GlassCard>
          <EmptyState title="Connect a mailbox first">
            Campaigns send from a mailbox — add one in the Accounts tab, then
            come back to build a sequence.
          </EmptyState>
        </GlassCard>
      ) : (
        <DataTable
          columns={columns}
          rows={campaigns ?? []}
          rowKey={(c) => c.id}
          loading={campaigns === null}
          initialSort="-sent"
          emptyMessage="No campaigns yet — create one to start reaching prospects."
        />
      )}

      {creating && (
        <CreateCampaignDialog
          accounts={accounts}
          onClose={() => setCreating(false)}
          onCreated={(id) => {
            setCreating(false);
            refresh();
            setEditingId(id);
          }}
        />
      )}

      {editingId && (
        <CampaignEditor
          campaignId={editingId}
          accounts={accounts}
          onClose={() => {
            setEditingId(null);
            refresh();
          }}
          onToast={toast}
        />
      )}
    </div>
  );
}

function CreateCampaignDialog({
  accounts,
  onClose,
  onCreated,
}: {
  accounts: EmailAccount[];
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [busy, setBusy] = useState(false);

  const create = async () => {
    if (!name.trim() || !accountId) {
      toast("Name and mailbox are required", "error");
      return;
    }
    setBusy(true);
    try {
      const c = await createEmailCampaign({ name: name.trim(), account_id: accountId });
      toast("Campaign created", "ok");
      onCreated(c.id);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Create failed", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="New campaign"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} onClick={create}>
            Create
          </Button>
        </>
      }
    >
      <div className="eml-form">
        <Field label="Campaign name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Q3 HVAC contractors"
          />
        </Field>
        <Field label="Send from mailbox">
          <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.from_name} — {a.from_email}
              </option>
            ))}
          </select>
        </Field>
      </div>
    </Dialog>
  );
}

// --- campaign editor (config + steps + enroll + enrollments + preview) ---

type EditorTab = "config" | "steps" | "audience";

function CampaignEditor({
  campaignId,
  accounts,
  onClose,
  onToast,
}: {
  campaignId: string;
  accounts: EmailAccount[];
  onClose: () => void;
  onToast: (msg: string, tone?: "ok" | "error" | "info") => void;
}) {
  const [tab, setTab] = useState<EditorTab>("config");
  const [detail, setDetail] = useState<EmailCampaignDetail | null>(null);
  const [steps, setSteps] = useState<EmailStep[]>([]);
  const [activateError, setActivateError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ position: number } | null>(null);
  const [enrolling, setEnrolling] = useState(false);

  const load = useCallback(() => {
    getEmailCampaign(campaignId)
      .then((d) => {
        setDetail(d);
        setSteps(d.steps);
      })
      .catch((e) => onToast(e instanceof Error ? e.message : "Load failed", "error"));
  }, [campaignId, onToast]);
  useEffect(load, [load]);

  const patchConfig = async (body: Parameters<typeof updateEmailCampaign>[1]) => {
    if (!detail) return;
    try {
      const d = await updateEmailCampaign(detail.id, body);
      setDetail((cur) => (cur ? { ...cur, ...d } : d));
      onToast("Saved", "ok");
    } catch (e) {
      onToast(e instanceof Error ? e.message : "Save failed", "error");
    }
  };

  const saveSteps = async () => {
    if (!detail) return;
    setBusy(true);
    try {
      const d = await saveEmailSteps(detail.id, steps.map((s, i) => ({ ...s, position: i + 1 })));
      setDetail(d);
      setSteps(d.steps);
      onToast("Steps saved", "ok");
    } catch (e) {
      onToast(e instanceof Error ? e.message : "Save failed", "error");
    } finally {
      setBusy(false);
    }
  };

  const activate = async () => {
    if (!detail) return;
    setActivateError(null);
    setBusy(true);
    try {
      const d = await activateEmailCampaign(detail.id);
      setDetail((cur) => (cur ? { ...cur, ...d } : d));
      onToast("Campaign activated", "ok");
    } catch (e) {
      // 422 detail explains why it can't activate — surface it inline.
      setActivateError(e instanceof Error ? e.message : "Could not activate");
    } finally {
      setBusy(false);
    }
  };

  const pause = async () => {
    if (!detail) return;
    setBusy(true);
    try {
      const d = await pauseEmailCampaign(detail.id);
      setDetail((cur) => (cur ? { ...cur, ...d } : d));
      onToast("Campaign paused", "ok");
    } catch (e) {
      onToast(e instanceof Error ? e.message : "Pause failed", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      closeOnScrim={false}
      size="lg"
      title={detail ? detail.name : "Campaign"}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          {detail && detail.status === "active" ? (
            <Button variant="danger-outline" busy={busy} onClick={pause}>
              Pause campaign
            </Button>
          ) : (
            <Button variant="primary" busy={busy} onClick={activate}>
              Activate campaign
            </Button>
          )}
        </>
      }
    >
      {!detail ? (
        <SkeletonText lines={8} />
      ) : (
        <>
          <div className="eml-editor-head">
            <Badge tone={detail.status}>{detail.status}</Badge>
            <Tabs
              ariaLabel="Campaign editor sections"
              tabs={[
                { id: "config", label: "Config" },
                { id: "steps", label: `Steps (${steps.length})` },
                { id: "audience", label: `Audience (${detail.enrolled})` },
              ]}
              active={tab}
              onChange={(id) => setTab(id as EditorTab)}
            />
          </div>

          {activateError && (
            <Alert tone="warn" title="Can't activate yet" className="eml-activate-err">
              {activateError}
            </Alert>
          )}

          {tab === "config" && (
            <ConfigForm detail={detail} accounts={accounts} onPatch={patchConfig} />
          )}

          {tab === "steps" && (
            <StepsEditor
              steps={steps}
              setSteps={setSteps}
              onSave={saveSteps}
              busy={busy}
              onPreview={(position) => setPreview({ position })}
            />
          )}

          {tab === "audience" && (
            <AudienceTab
              campaign={detail}
              onEnrollClick={() => setEnrolling(true)}
              onToast={onToast}
            />
          )}

          {preview && (
            <PreviewDialog
              campaignId={detail.id}
              position={preview.position}
              onClose={() => setPreview(null)}
            />
          )}

          {enrolling && (
            <EnrollDialog
              campaignId={detail.id}
              onClose={() => setEnrolling(false)}
              onDone={() => {
                setEnrolling(false);
                load();
              }}
            />
          )}
        </>
      )}
    </Dialog>
  );
}

function ConfigForm({
  detail,
  accounts,
  onPatch,
}: {
  detail: EmailCampaignDetail;
  accounts: EmailAccount[];
  onPatch: (body: Parameters<typeof updateEmailCampaign>[1]) => void;
}) {
  const toggleDay = (day: number) => {
    const set = new Set(detail.send_days);
    if (set.has(day)) set.delete(day);
    else set.add(day);
    onPatch({ send_days: [...set].sort((a, b) => a - b) });
  };

  return (
    <div className="eml-form">
      <Field label="Send from mailbox">
        <select
          value={detail.account_id}
          onChange={(e) => onPatch({ account_id: e.target.value })}
        >
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.from_name} — {a.from_email}
            </option>
          ))}
        </select>
      </Field>

      <div className="eml-form-row">
        <Field label="Daily cap (this campaign)" optional>
          <input
            type="number"
            min={1}
            defaultValue={detail.daily_cap ?? ""}
            placeholder="mailbox default"
            onBlur={(e) => {
              const v = e.target.value ? Number(e.target.value) : null;
              if (v !== detail.daily_cap) onPatch({ daily_cap: v });
            }}
          />
        </Field>
        <Field label="Timezone">
          <input
            defaultValue={detail.timezone}
            placeholder="America/New_York"
            onBlur={(e) => {
              if (e.target.value && e.target.value !== detail.timezone)
                onPatch({ timezone: e.target.value });
            }}
          />
        </Field>
      </div>

      <div className="eml-form-row">
        <Field label="Send window start">
          <select
            value={detail.send_window_start}
            onChange={(e) => onPatch({ send_window_start: Number(e.target.value) })}
          >
            {HOURS.map((h) => (
              <option key={h} value={h}>
                {hourLabel(h)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Send window end">
          <select
            value={detail.send_window_end}
            onChange={(e) => onPatch({ send_window_end: Number(e.target.value) })}
          >
            {HOURS.map((h) => (
              <option key={h} value={h}>
                {hourLabel(h)}
              </option>
            ))}
          </select>
        </Field>
      </div>

      <div className="eml-fieldset">
        <span className="field-label">Send days</span>
        <div className="eml-days">
          {WEEKDAYS.map((label, day) => (
            <label key={day} className="eml-check">
              <input
                type="checkbox"
                checked={detail.send_days.includes(day)}
                onChange={() => toggleDay(day)}
              />
              {label}
            </label>
          ))}
        </div>
      </div>

      <label className="eml-check">
        <input
          type="checkbox"
          checked={detail.open_tracking}
          onChange={(e) => onPatch({ open_tracking: e.target.checked })}
        />
        Open tracking (embeds a tracking pixel)
      </label>
    </div>
  );
}

function StepsEditor({
  steps,
  setSteps,
  onSave,
  busy,
  onPreview,
}: {
  steps: EmailStep[];
  setSteps: (s: EmailStep[]) => void;
  onSave: () => void;
  busy: boolean;
  onPreview: (position: number) => void;
}) {
  const update = (i: number, next: EmailStep) =>
    setSteps(steps.map((s, j) => (j === i ? next : s)));
  const remove = (i: number) => setSteps(steps.filter((_, j) => j !== i));
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= steps.length) return;
    const copy = [...steps];
    [copy[i], copy[j]] = [copy[j], copy[i]];
    setSteps(copy);
  };
  const add = () =>
    setSteps([
      ...steps,
      {
        position: steps.length,
        wait_days: steps.length === 0 ? 0 : 3,
        subject: steps.length === 0 ? "" : null,
        body: "",
        ai_instructions: null,
      },
    ]);

  return (
    <div>
      <p className="eml-tokens">{TOKENS_HINT}</p>
      {steps.length === 0 && (
        <EmptyState title="No steps yet">
          Add a first email, then follow-ups. Each step waits a set number of
          days after the previous one.
        </EmptyState>
      )}
      {steps.map((step, i) => (
        <StepRow
          key={i}
          step={step}
          index={i}
          isFirst={i === 0}
          isLast={i === steps.length - 1}
          onChange={(next) => update(i, next)}
          onRemove={() => remove(i)}
          onMove={(dir) => move(i, dir)}
          onPreview={() => onPreview(i)}
        />
      ))}
      <div className="eml-step-add">
        <Button onClick={add}>
          <Plus size={16} />
          Add step
        </Button>
        <Button variant="primary" busy={busy} onClick={onSave}>
          Save steps
        </Button>
      </div>
    </div>
  );
}

function StepRow({
  step,
  index,
  isFirst,
  isLast,
  onChange,
  onRemove,
  onMove,
  onPreview,
}: {
  step: EmailStep;
  index: number;
  isFirst: boolean;
  isLast: boolean;
  onChange: (s: EmailStep) => void;
  onRemove: () => void;
  onMove: (dir: -1 | 1) => void;
  onPreview: () => void;
}) {
  const [showAi, setShowAi] = useState(Boolean(step.ai_instructions));

  return (
    <GlassCard className="eml-step">
      <div className="eml-step-head">
        <Badge tone="accent">{index + 1}</Badge>
        <strong className="eml-step-title">
          {isFirst ? "First email" : `Follow-up ${index}`}
        </strong>
        <div className="eml-step-actions">
          <Button variant="ghost" size="sm" onClick={onPreview}>
            Preview
          </Button>
          <Button variant="ghost" size="sm" disabled={isFirst} onClick={() => onMove(-1)}>
            ↑
          </Button>
          <Button variant="ghost" size="sm" disabled={isLast} onClick={() => onMove(1)}>
            ↓
          </Button>
          <Button variant="danger-outline" size="sm" onClick={onRemove}>
            Remove
          </Button>
        </div>
      </div>

      <div className="eml-form">
        {isFirst ? (
          <p className="eml-hint">Sends immediately when a contact is enrolled.</p>
        ) : (
          <Field label="Wait (days after previous step)">
            <input
              type="number"
              min={0}
              value={step.wait_days}
              onChange={(e) => onChange({ ...step, wait_days: Number(e.target.value) })}
            />
          </Field>
        )}

        <Field
          label="Subject"
          description="Leave blank to reply in the same thread as the previous step."
        >
          <input
            value={step.subject ?? ""}
            onChange={(e) => onChange({ ...step, subject: e.target.value || null })}
            placeholder="Quick question, {{first_name|there}}"
          />
        </Field>

        <Field label="Body">
          <textarea
            rows={5}
            value={step.body}
            onChange={(e) => onChange({ ...step, body: e.target.value })}
            placeholder={"Hi {{first_name|there}},\n\n{{ai_snippet}}\n\n{{unsubscribe_url}}"}
          />
        </Field>

        {showAi ? (
          <Field
            label="AI personalization instructions"
            description="Guides the {{ai_snippet}} the model writes per contact."
            optional
          >
            <textarea
              rows={3}
              value={step.ai_instructions ?? ""}
              onChange={(e) =>
                onChange({ ...step, ai_instructions: e.target.value || null })
              }
              placeholder="Reference their city and vertical; keep it to one sentence."
            />
          </Field>
        ) : (
          <Button variant="link" size="sm" onClick={() => setShowAi(true)}>
            + AI personalization instructions
          </Button>
        )}
      </div>
    </GlassCard>
  );
}

function PreviewDialog({
  campaignId,
  position,
  onClose,
}: {
  campaignId: string;
  position: number;
  onClose: () => void;
}) {
  const toast = useToast();
  const contacts = useHouseContacts(true);
  const [contactId, setContactId] = useState("");
  const [rendered, setRendered] = useState<{ subject: string; body: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (cid: string) => {
    if (!cid) return;
    setBusy(true);
    setRendered(null);
    try {
      // `position` here is the 0-based array index within this dialog's own
      // state; the API's step positions are 1-indexed (matching saveSteps).
      const r = await previewEmailStep(campaignId, cid, position + 1);
      setRendered(r);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Preview failed", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Preview step ${position + 1}`}
      footer={
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      }
    >
      <div className="eml-form">
        <Field label="Render for contact">
          <select
            value={contactId}
            onChange={(e) => {
              setContactId(e.target.value);
              run(e.target.value);
            }}
          >
            <option value="">Choose a contact…</option>
            {(contacts ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {contactLabel(c)}
                {c.email ? ` · ${c.email}` : ""}
              </option>
            ))}
          </select>
        </Field>
        {contacts !== null && contacts.length === 0 && (
          <Alert tone="info">
            No contacts in the house CRM yet — import leads to preview
            personalization.
          </Alert>
        )}
        {busy && <SkeletonText lines={4} />}
        {rendered && (
          <div className="eml-preview">
            <div className="eml-preview-subj">
              <span className="eml-preview-label">Subject</span>
              {rendered.subject || <em>(reply in thread — no subject)</em>}
            </div>
            <div className="eml-preview-body">{rendered.body}</div>
          </div>
        )}
      </div>
    </Dialog>
  );
}

function AudienceTab({
  campaign,
  onEnrollClick,
  onToast,
}: {
  campaign: EmailCampaignDetail;
  onEnrollClick: () => void;
  onToast: (msg: string, tone?: "ok" | "error" | "info") => void;
}) {
  const [rows, setRows] = useState<EmailEnrollment[] | null>(null);

  // Re-fetch whenever the campaign's enrolled count changes (not just on
  // mount/campaign switch) — otherwise a fresh enroll updates the summary
  // numbers (from the campaign detail refetch) but leaves this table showing
  // its stale empty state until some unrelated re-mount.
  const refresh = useCallback(() => {
    listEmailEnrollments(campaign.id).then(setRows).catch(() => {});
  }, [campaign.id, campaign.enrolled]);
  useEffect(refresh, [refresh]);

  const unenroll = (e: EmailEnrollment) => {
    unenrollEmail(campaign.id, e.id)
      .then(() => {
        onToast("Removed from campaign", "ok");
        refresh();
      })
      .catch((err) => onToast(err instanceof Error ? err.message : "Failed", "error"));
  };

  const columns: Column<EmailEnrollment>[] = [
    {
      key: "contact",
      header: "Contact",
      render: (e) => contactLabel(e.contact),
      sortValue: (e) => contactLabel(e.contact),
    },
    { key: "email", header: "Email", render: (e) => e.contact.email || "—" },
    { key: "company", header: "Company", render: (e) => e.contact.company_name || "—" },
    {
      key: "status",
      header: "Status",
      render: (e) => (
        <Badge tone={e.status}>
          {e.status}
          {e.exit_reason ? ` (${e.exit_reason})` : ""}
        </Badge>
      ),
      sortValue: (e) => e.status,
    },
    { key: "step", header: "Step", align: "right", render: (e) => int(e.current_position) },
    {
      key: "next",
      header: "Next send",
      render: (e) => (e.replied_at ? "replied" : timeAgo(e.next_run_at)),
    },
    {
      key: "manage",
      header: "",
      align: "right",
      render: (e) =>
        e.status === "active" ? (
          <Button variant="danger-outline" size="sm" onClick={() => unenroll(e)}>
            Remove
          </Button>
        ) : null,
    },
  ];

  return (
    <div>
      <div className="eml-head">
        <p className="eml-sub">
          {int(campaign.enrolled)} enrolled · {int(campaign.active_enrollments)}{" "}
          currently active.
        </p>
        <Button variant="primary" onClick={onEnrollClick}>
          <Plus size={16} />
          Enroll contacts
        </Button>
      </div>
      <DataTable
        columns={columns}
        rows={rows ?? []}
        rowKey={(e) => e.id}
        loading={rows === null}
        initialSort="contact"
        emptyMessage="No one enrolled yet — enroll house-CRM contacts to start the sequence."
      />
    </div>
  );
}

function EnrollDialog({
  campaignId,
  onClose,
  onDone,
}: {
  campaignId: string;
  onClose: () => void;
  onDone: () => void;
}) {
  const toast = useToast();
  const contacts = useHouseContacts(true);
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<EnrollReceipt | null>(null);

  const filtered = useMemo(() => {
    const list = contacts ?? [];
    const s = search.trim().toLowerCase();
    if (!s) return list;
    return list.filter((c) =>
      [c.first_name, c.last_name, c.email, c.company_name]
        .filter(Boolean)
        .some((v) => v!.toLowerCase().includes(s)),
    );
  }, [contacts, search]);

  const toggle = (id: string) => {
    setPicked((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const submit = async () => {
    if (picked.size === 0) {
      toast("Select at least one contact", "error");
      return;
    }
    setBusy(true);
    try {
      const r = await enrollEmailContacts(campaignId, [...picked]);
      setReceipt(r);
      if (r.enrolled > 0) toast(`Enrolled ${r.enrolled}`, "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Enroll failed", "error");
    } finally {
      setBusy(false);
    }
  };

  if (receipt) {
    return (
      <Dialog
        open
        onClose={() => {
          onDone();
          onClose();
        }}
        title="Enrollment receipt"
        footer={
          <Button
            variant="primary"
            onClick={() => {
              onDone();
              onClose();
            }}
          >
            Done
          </Button>
        }
      >
        <div className="eml-form">
          <p className="eml-hint">
            Enrolled <strong>{receipt.enrolled}</strong> contact
            {receipt.enrolled === 1 ? "" : "s"}.
          </p>
          {receipt.risky.length > 0 && (
            <Alert tone="warn" title={`${receipt.risky.length} risky addresses included`}>
              These are deliverable but reputation-hazardous — they may bounce:
              <div className="eml-receipt-list">
                {receipt.risky.map((r) => (
                  <span key={r.contact_id}>{r.email}</span>
                ))}
              </div>
            </Alert>
          )}
          {receipt.skipped.length > 0 && (
            <Alert tone="info" title={`${receipt.skipped.length} skipped`}>
              <ul className="eml-skip-list">
                {SKIP_SUMMARY(receipt.skipped).map(([reason, n]) => (
                  <li key={reason}>
                    {n} — {SKIP_LABELS[reason]}
                  </li>
                ))}
              </ul>
            </Alert>
          )}
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog
      open
      onClose={onClose}
      closeOnScrim={false}
      title="Enroll contacts"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} onClick={submit}>
            Enroll {picked.size > 0 ? `(${picked.size})` : ""}
          </Button>
        </>
      }
    >
      <div className="eml-form">
        <input
          className="input"
          placeholder="Search house-CRM contacts…"
          aria-label="Search contacts"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {contacts === null ? (
          <SkeletonText lines={6} />
        ) : filtered.length === 0 ? (
          <EmptyState title="No contacts">
            Import leads into the house CRM (Lead Finder or CSV) to build an
            audience.
          </EmptyState>
        ) : (
          <div className="eml-picklist">
            {filtered.map((c) => (
              <label key={c.id} className="eml-pickrow">
                <input
                  type="checkbox"
                  checked={picked.has(c.id)}
                  onChange={() => toggle(c.id)}
                />
                <span className="eml-pickrow-name">{contactLabel(c)}</span>
                <span className="eml-pickrow-email">{c.email || "no email"}</span>
                {c.verification_status && (
                  <Badge tone={c.verification_status}>{c.verification_status}</Badge>
                )}
              </label>
            ))}
          </div>
        )}
      </div>
    </Dialog>
  );
}

const SKIP_LABELS: Record<EnrollReceipt["skipped"][number]["reason"], string> = {
  invalid_email: "invalid email (verified undeliverable)",
  suppressed: "on the suppression list",
  no_email: "no email address on file",
  already_enrolled: "already enrolled in this campaign",
};

function SKIP_SUMMARY(
  skipped: EnrollReceipt["skipped"],
): [EnrollReceipt["skipped"][number]["reason"], number][] {
  const counts = new Map<EnrollReceipt["skipped"][number]["reason"], number>();
  for (const s of skipped) counts.set(s.reason, (counts.get(s.reason) ?? 0) + 1);
  return [...counts.entries()];
}

// ==========================================================================
// 3. Inbox
// ==========================================================================

function InboxPanel({ accounts }: { accounts: EmailAccount[] }) {
  const toast = useToast();
  const [threads, setThreads] = useState<EmailThread[] | null>(null);
  const [accountId, setAccountId] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [selected, setSelected] = useState<EmailThread | null>(null);
  const [messages, setMessages] = useState<EmailMessage[] | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [composing, setComposing] = useState(false);
  const selectedRef = useRef<string | null>(null);

  const refresh = useCallback(() => {
    listEmailThreads(accountId || undefined, unreadOnly || undefined)
      .then((rows) => {
        setThreads(rows);
        const cur = selectedRef.current;
        if (cur) {
          const updated = rows.find((r) => r.id === cur);
          if (updated) setSelected(updated);
        }
      })
      .catch(() => {});
  }, [accountId, unreadOnly]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 15_000);
    return () => clearInterval(t);
  }, [refresh]);

  const open = useCallback((th: EmailThread) => {
    setSelected(th);
    selectedRef.current = th.id;
    setMessages(null);
    setDraft("");
    listEmailThreadMessages(th.id).then(setMessages).catch(() => {});
    if (th.unread) markEmailThreadRead(th.id).catch(() => {});
  }, []);

  const send = async () => {
    if (!selected || !draft.trim()) return;
    setSending(true);
    try {
      await replyEmailThread(selected.id, draft.trim());
      setDraft("");
      listEmailThreadMessages(selected.id).then(setMessages).catch(() => {});
      toast("Reply sent", "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Send failed", "error");
    } finally {
      setSending(false);
    }
  };

  return (
    <div>
      <div className="eml-inbox-bar">
        <select
          className="select eml-select"
          aria-label="Filter by mailbox"
          value={accountId}
          onChange={(e) => setAccountId(e.target.value)}
        >
          <option value="">All mailboxes</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.from_email}
            </option>
          ))}
        </select>
        <label className="eml-check">
          <input
            type="checkbox"
            checked={unreadOnly}
            onChange={(e) => setUnreadOnly(e.target.checked)}
          />
          Unread only
        </label>
        <Button
          variant="primary"
          className="eml-inbox-compose"
          disabled={accounts.length === 0}
          onClick={() => setComposing(true)}
        >
          <Plus size={16} />
          Compose
        </Button>
      </div>

      <div className="eml-inbox">
        <GlassCard className="eml-threads">
          {threads === null && <SkeletonText lines={6} />}
          {threads !== null && threads.length === 0 && (
            <EmptyState title="No conversations">
              Replies to your campaigns show up here — nothing yet.
            </EmptyState>
          )}
          {threads?.map((th) => (
            <button
              key={th.id}
              type="button"
              className={[
                "eml-thread-item",
                selected?.id === th.id ? "eml-thread-item--active" : "",
                th.unread ? "eml-thread-item--unread" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              onClick={() => open(th)}
            >
              <div className="eml-thread-top">
                <span className="eml-thread-name">
                  <span>{th.contact ? contactLabel(th.contact) : th.subject}</span>
                  {th.unread && <Badge tone="info">new</Badge>}
                </span>
                <time className="eml-thread-time" title={th.last_message_at ?? undefined}>
                  {timeAgo(th.last_message_at)}
                </time>
              </div>
              <span className="eml-thread-subj">{th.subject}</span>
              <span className="eml-thread-snippet">{th.snippet}</span>
            </button>
          ))}
        </GlassCard>

        {selected ? (
          <GlassCard className="eml-thread-pane">
            <div className="eml-thread-head">
              <h3 className="eml-thread-title">{selected.subject}</h3>
              <span className="eml-thread-with">
                {selected.contact ? contactLabel(selected.contact) : ""}
                {selected.contact?.email ? ` · ${selected.contact.email}` : ""}
              </span>
            </div>
            <div className="eml-messages" aria-live="polite">
              {messages === null && <SkeletonText lines={4} />}
              {messages?.map((m) => (
                <div
                  key={m.id}
                  className={[
                    "eml-msg",
                    m.direction === "out" ? "eml-msg--out" : "eml-msg--in",
                  ].join(" ")}
                >
                  {m.subject && <div className="eml-msg-subj">{m.subject}</div>}
                  <div className="eml-msg-body">{m.body_text}</div>
                  <small className="eml-msg-meta">
                    {m.status}
                    {" · "}
                    {timeAgo(m.sent_at || m.received_at)}
                    {m.direction === "out" && m.opened_at && (
                      <span className="eml-opened">
                        {" · "}Opened {timeAgo(m.opened_at)}
                        {m.open_count > 1 ? ` (${m.open_count}×)` : ""}
                      </span>
                    )}
                  </small>
                </div>
              ))}
            </div>
            <div className="eml-composer">
              <Field label="Reply">
                <textarea
                  rows={3}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="Type your reply…"
                />
              </Field>
              <div className="eml-composer-actions">
                <Button
                  variant="primary"
                  busy={sending}
                  disabled={!draft.trim()}
                  onClick={send}
                >
                  <Send size={16} />
                  Send
                </Button>
              </div>
            </div>
          </GlassCard>
        ) : (
          <GlassCard className="eml-thread-pane">
            <EmptyState title="Select a conversation">
              Pick a thread to read the history and reply.
            </EmptyState>
          </GlassCard>
        )}
      </div>

      {composing && (
        <ComposeDialog
          accounts={accounts}
          onClose={() => setComposing(false)}
          onSent={() => {
            setComposing(false);
            refresh();
          }}
        />
      )}
    </div>
  );
}

function ComposeDialog({
  accounts,
  onClose,
  onSent,
}: {
  accounts: EmailAccount[];
  onClose: () => void;
  onSent: () => void;
}) {
  const toast = useToast();
  const contacts = useHouseContacts(true);
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [contactId, setContactId] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);

  const send = async () => {
    if (!accountId || !contactId || !subject.trim() || !body.trim()) {
      toast("Mailbox, contact, subject and body are required", "error");
      return;
    }
    setBusy(true);
    try {
      await composeEmail({
        account_id: accountId,
        contact_id: contactId,
        subject: subject.trim(),
        body,
      });
      toast("Email sent", "ok");
      onSent();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Send failed", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      closeOnScrim={false}
      title="Compose email"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} onClick={send}>
            <Send size={16} />
            Send
          </Button>
        </>
      }
    >
      <div className="eml-form">
        <Field label="From mailbox">
          <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.from_name} — {a.from_email}
              </option>
            ))}
          </select>
        </Field>
        <Field label="To (house-CRM contact)">
          <select value={contactId} onChange={(e) => setContactId(e.target.value)}>
            <option value="">Choose a contact…</option>
            {(contacts ?? []).map((c) => (
              <option key={c.id} value={c.id} disabled={!c.email}>
                {contactLabel(c)}
                {c.email ? ` · ${c.email}` : " · no email"}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Subject">
          <input value={subject} onChange={(e) => setSubject(e.target.value)} />
        </Field>
        <Field label="Body">
          <textarea rows={7} value={body} onChange={(e) => setBody(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  );
}

// ==========================================================================
// 4. Accounts
// ==========================================================================

function AccountsPanel({
  accounts,
  onChanged,
}: {
  accounts: EmailAccount[];
  onChanged: () => void;
}) {
  const toast = useToast();
  const [connecting, setConnecting] = useState(false);
  const [editing, setEditing] = useState<EmailAccount | null>(null);
  const [deleting, setDeleting] = useState<EmailAccount | null>(null);
  const [tested, setTested] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const test = async (a: EmailAccount) => {
    setTested((cur) => ({ ...cur, [a.id]: "Testing…" }));
    try {
      const r = await testEmailAccount(a.id);
      setTested((cur) => ({
        ...cur,
        [a.id]: `SMTP ${r.smtp_ok ? "OK" : "FAIL"} · IMAP ${
          r.imap_ok ? "OK" : "FAIL"
        }${r.detail ? ` — ${r.detail}` : ""}`,
      }));
      onChanged();
    } catch (e) {
      setTested((cur) => ({
        ...cur,
        [a.id]: e instanceof Error ? e.message : "Test failed",
      }));
    }
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    setBusy(true);
    try {
      await deleteEmailAccount(deleting.id);
      toast("Mailbox removed", "ok");
      setDeleting(null);
      onChanged();
    } catch (e) {
      // 409 when a campaign still uses it.
      toast(e instanceof Error ? e.message : "Delete failed", "error");
    } finally {
      setBusy(false);
    }
  };

  const deleteRows: ReceiptRow[] = deleting
    ? [{ field: deleting.from_email, oldValue: "connected", newValue: "removed" }]
    : [];

  return (
    <div>
      <div className="eml-head">
        <p className="eml-sub">
          Sending mailboxes. Use a dedicated subdomain address (e.g.{" "}
          mail.atlasreach.io — see deploy/MAILSERVER.md) and let warmup ramp the
          daily volume gradually.
        </p>
        <Button variant="primary" onClick={() => setConnecting(true)}>
          <Plus size={16} />
          Connect mailbox
        </Button>
      </div>

      {accounts.length === 0 ? (
        <GlassCard>
          <EmptyState title="No mailboxes connected">
            Connect an SMTP/IMAP mailbox to start sending outreach.
          </EmptyState>
        </GlassCard>
      ) : (
        <div className="eml-account-grid">
          {accounts.map((a) => (
            <GlassCard key={a.id} className="eml-account">
              <div className="eml-account-top">
                <div>
                  <div className="eml-account-name">{a.from_name}</div>
                  <div className="eml-account-email">{a.from_email}</div>
                </div>
                <Badge tone={a.status === "active" ? "ok" : "danger"}>
                  {a.status === "active" ? "connected" : "error"}
                </Badge>
              </div>

              {a.status === "error" && a.error_detail && (
                <Alert tone="danger">{a.error_detail}</Alert>
              )}

              <div className="eml-account-stat">
                <span>
                  {int(a.sends_today)} of {int(a.effective_daily_cap)} sent today
                </span>
                <span className="eml-account-sync">
                  synced {timeAgo(a.last_synced_at)}
                </span>
              </div>

              <label className="eml-check">
                <input
                  type="checkbox"
                  checked={a.warmup_enabled}
                  onChange={(e) =>
                    updateEmailAccount(a.id, { warmup_enabled: e.target.checked })
                      .then(() => {
                        toast("Warmup updated", "ok");
                        onChanged();
                      })
                      .catch((err) =>
                        toast(err instanceof Error ? err.message : "Failed", "error"),
                      )
                  }
                />
                Warmup{" "}
                {a.warmup_enabled
                  ? `→ ${int(a.warmup_target_daily)}/day${
                      a.warmup_started_at
                        ? ` (started ${timeAgo(a.warmup_started_at)})`
                        : ""
                    }`
                  : "off"}
              </label>

              {tested[a.id] && <div className="eml-test-result">{tested[a.id]}</div>}

              <div className="eml-account-actions">
                <Button variant="ghost" size="sm" onClick={() => test(a)}>
                  Test connection
                </Button>
                <Button variant="ghost" size="sm" onClick={() => setEditing(a)}>
                  Edit
                </Button>
                <Button
                  variant="danger-outline"
                  size="sm"
                  onClick={() => setDeleting(a)}
                >
                  Delete
                </Button>
              </div>
            </GlassCard>
          ))}
        </div>
      )}

      {connecting && (
        <AccountDialog
          onClose={() => setConnecting(false)}
          onSaved={() => {
            setConnecting(false);
            onChanged();
          }}
        />
      )}

      {editing && (
        <AccountDialog
          existing={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            onChanged();
          }}
        />
      )}

      <ConfirmDialog
        open={deleting != null}
        onCancel={() => setDeleting(null)}
        onConfirm={confirmDelete}
        rows={deleteRows}
        tone="danger"
        title="Remove mailbox"
        confirmLabel="Remove mailbox"
        cancelLabel="Keep it"
        busy={busy}
      >
        <p className="eml-hint">
          Campaigns still using this mailbox will block removal. Reassign them
          first.
        </p>
      </ConfirmDialog>
    </div>
  );
}

const SECURITY_OPTS: { value: EmailSmtpSecurity; label: string }[] = [
  { value: "ssl", label: "SSL" },
  { value: "starttls", label: "STARTTLS" },
];

function AccountDialog({
  existing,
  onClose,
  onSaved,
}: {
  existing?: EmailAccount;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [name, setName] = useState(existing?.name ?? "");
  const [fromName, setFromName] = useState(existing?.from_name ?? "");
  const [fromEmail, setFromEmail] = useState(existing?.from_email ?? "");
  const [smtpUsername, setSmtpUsername] = useState(existing?.smtp_username ?? "");
  const [smtpPassword, setSmtpPassword] = useState("");
  // Most orgs send and receive through the same mailbox login. Split
  // credentials matter when sending goes through a separate provider (e.g.
  // Amazon SES SMTP credentials) while replies still land in a real IMAP
  // inbox — uncheck to enter a distinct IMAP login for that case.
  const [sameImapLogin, setSameImapLogin] = useState(
    !existing || existing.imap_username === existing.smtp_username,
  );
  const [imapUsername, setImapUsername] = useState(existing?.imap_username ?? "");
  const [imapPassword, setImapPassword] = useState("");
  const [smtpHost, setSmtpHost] = useState(existing?.smtp_host ?? "");
  const [smtpPort, setSmtpPort] = useState(existing ? String(existing.smtp_port) : "465");
  const [smtpSecurity, setSmtpSecurity] = useState<EmailSmtpSecurity>(
    existing?.smtp_security ?? "ssl",
  );
  const [imapHost, setImapHost] = useState(existing?.imap_host ?? "");
  const [imapPort, setImapPort] = useState(existing ? String(existing.imap_port) : "993");
  const [imapSecurity, setImapSecurity] = useState<EmailSmtpSecurity>(
    existing?.imap_security ?? "ssl",
  );
  const [dailyCap, setDailyCap] = useState(
    existing ? String(existing.daily_send_cap) : "50",
  );
  const [signature, setSignature] = useState(existing?.signature ?? "");
  const [busy, setBusy] = useState(false);

  const isEdit = Boolean(existing);

  const save = async () => {
    const effectiveImapUsername = sameImapLogin ? smtpUsername : imapUsername;
    if (
      !name.trim() || !fromEmail.trim() || !smtpHost.trim() || !imapHost.trim()
      || !smtpUsername.trim() || !effectiveImapUsername.trim()
    ) {
      toast("Name, from email, hosts and usernames are required", "error");
      return;
    }
    if (!isEdit && (!smtpPassword || (!sameImapLogin && !imapPassword))) {
      toast("A password is required for both SMTP and IMAP to connect", "error");
      return;
    }
    const base: EmailAccountBody = {
      name: name.trim(),
      from_name: fromName.trim(),
      from_email: fromEmail.trim(),
      smtp_username: smtpUsername.trim(),
      smtp_host: smtpHost.trim(),
      smtp_port: Number(smtpPort),
      smtp_security: smtpSecurity,
      imap_username: effectiveImapUsername.trim(),
      imap_host: imapHost.trim(),
      imap_port: Number(imapPort),
      imap_security: imapSecurity,
      daily_send_cap: Number(dailyCap),
      signature: signature.trim() || null,
    };
    if (smtpPassword) base.smtp_password = smtpPassword;
    const effectiveImapPassword = sameImapLogin ? smtpPassword : imapPassword;
    if (effectiveImapPassword) base.imap_password = effectiveImapPassword;
    setBusy(true);
    try {
      if (existing) await updateEmailAccount(existing.id, base);
      else
        await createEmailAccount({
          ...base,
          smtp_password: smtpPassword,
          imap_password: effectiveImapPassword,
        } as EmailAccountBody & { smtp_password: string; imap_password: string });
      toast(isEdit ? "Mailbox updated" : "Mailbox connected", "ok");
      onSaved();
    } catch (e) {
      // 400 detail on a failed SMTP/IMAP probe — surface verbatim.
      toast(e instanceof Error ? e.message : "Could not connect", "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      closeOnScrim={false}
      size="lg"
      title={isEdit ? `Edit ${existing!.from_email}` : "Connect a mailbox"}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" busy={busy} onClick={save}>
            {isEdit ? "Save" : "Connect"}
          </Button>
        </>
      }
    >
      <div className="eml-form">
        <div className="eml-form-row">
          <Field label="Label">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Atlas primary" />
          </Field>
          <Field label="Display name">
            <input
              value={fromName}
              onChange={(e) => setFromName(e.target.value)}
              placeholder="Carter at Atlas Reach"
            />
          </Field>
        </div>
        <Field label="From email">
          <input
            type="email"
            value={fromEmail}
            onChange={(e) => setFromEmail(e.target.value)}
            placeholder="carter@mail.atlasreach.io"
          />
        </Field>

        <div className="eml-fieldset">
          <span className="field-label">Outgoing (SMTP)</span>
          <div className="eml-form-row">
            <Field label="Host">
              <input
                value={smtpHost}
                onChange={(e) => setSmtpHost(e.target.value)}
                placeholder="mail.atlasreach.io, or email-smtp.us-east-1.amazonaws.com for SES"
              />
            </Field>
            <Field label="Port">
              <input
                type="number"
                value={smtpPort}
                onChange={(e) => setSmtpPort(e.target.value)}
              />
            </Field>
          </div>
          <div className="eml-fieldset">
            <span className="field-label">Security</span>
            <Segmented
              ariaLabel="SMTP security"
              value={smtpSecurity}
              onChange={setSmtpSecurity}
              options={SECURITY_OPTS}
            />
          </div>
          <div className="eml-form-row">
            <Field label="Username">
              <input
                value={smtpUsername}
                onChange={(e) => setSmtpUsername(e.target.value)}
                placeholder="carter@mail.atlasreach.io, or an SES SMTP username"
              />
            </Field>
            <Field
              label="Password"
              description={isEdit ? "Leave blank to keep the current password." : undefined}
            >
              <input
                type="password"
                value={smtpPassword}
                onChange={(e) => setSmtpPassword(e.target.value)}
                autoComplete="new-password"
              />
            </Field>
          </div>
        </div>

        <div className="eml-fieldset">
          <span className="field-label">Incoming (IMAP)</span>
          <div className="eml-form-row">
            <Field label="Host">
              <input
                value={imapHost}
                onChange={(e) => setImapHost(e.target.value)}
                placeholder="mail.atlasreach.io"
              />
            </Field>
            <Field label="Port">
              <input
                type="number"
                value={imapPort}
                onChange={(e) => setImapPort(e.target.value)}
              />
            </Field>
          </div>
          <div className="eml-fieldset">
            <span className="field-label">Security</span>
            <Segmented
              ariaLabel="IMAP security"
              value={imapSecurity}
              onChange={setImapSecurity}
              options={SECURITY_OPTS}
            />
          </div>
          <label className="eml-check">
            <input
              type="checkbox"
              checked={sameImapLogin}
              onChange={(e) => setSameImapLogin(e.target.checked)}
            />
            Same login as SMTP
          </label>
          {!sameImapLogin && (
            <div className="eml-form-row">
              <Field label="Username">
                <input
                  value={imapUsername}
                  onChange={(e) => setImapUsername(e.target.value)}
                  placeholder="carter@mail.atlasreach.io"
                />
              </Field>
              <Field
                label="Password"
                description={isEdit ? "Leave blank to keep the current password." : undefined}
              >
                <input
                  type="password"
                  value={imapPassword}
                  onChange={(e) => setImapPassword(e.target.value)}
                  autoComplete="new-password"
                />
              </Field>
            </div>
          )}
        </div>

        <Field label="Daily send cap" optional>
          <input
            type="number"
            min={1}
            value={dailyCap}
            onChange={(e) => setDailyCap(e.target.value)}
          />
        </Field>
        <Field label="Signature" optional>
          <textarea
            rows={3}
            value={signature}
            onChange={(e) => setSignature(e.target.value)}
            placeholder="— Carter, Atlas Reach"
          />
        </Field>
      </div>
    </Dialog>
  );
}

// ==========================================================================
// 5. Suppression
// ==========================================================================

function SuppressionPanel() {
  const toast = useToast();
  const [rows, setRows] = useState<EmailSuppression[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [emails, setEmails] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    listEmailSuppression().then(setRows).catch(() => {});
  }, []);
  useEffect(refresh, [refresh]);

  const add = async () => {
    const list = emails
      .split(/[\s,\n]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (list.length === 0) {
      toast("Paste at least one email", "error");
      return;
    }
    setBusy(true);
    try {
      const r = await addEmailSuppression(list);
      toast(`Added ${r.added}`, "ok");
      setAdding(false);
      setEmails("");
      refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Failed", "error");
    } finally {
      setBusy(false);
    }
  };

  const remove = (r: EmailSuppression) => {
    deleteEmailSuppression(r.id)
      .then(() => {
        toast("Removed from suppression", "ok");
        refresh();
      })
      .catch((e) => toast(e instanceof Error ? e.message : "Failed", "error"));
  };

  const columns: Column<EmailSuppression>[] = [
    { key: "email", header: "Email", render: (r) => r.email, sortValue: (r) => r.email },
    {
      key: "reason",
      header: "Reason",
      render: (r) => <Badge tone="neutral">{r.reason}</Badge>,
      sortValue: (r) => r.reason,
    },
    {
      key: "created",
      header: "Added",
      render: (r) => (
        <time title={r.created_at}>{timeAgo(r.created_at)}</time>
      ),
      sortValue: (r) => r.created_at,
    },
    {
      key: "manage",
      header: "",
      align: "right",
      render: (r) => (
        <Button variant="danger-outline" size="sm" onClick={() => remove(r)}>
          Remove
        </Button>
      ),
    },
  ];

  return (
    <div>
      <div className="eml-head">
        <p className="eml-sub">
          Addresses here are never emailed again. Bounces and unsubscribes land
          on this list automatically — add addresses manually to pre-empt
          contact you already know is off-limits.
        </p>
        <Button variant="primary" onClick={() => setAdding(true)}>
          <Plus size={16} />
          Add addresses
        </Button>
      </div>

      <DataTable
        columns={columns}
        rows={rows ?? []}
        rowKey={(r) => r.id}
        loading={rows === null}
        initialSort="-created"
        emptyMessage="Suppression list is empty."
      />

      <Dialog
        open={adding}
        onClose={() => setAdding(false)}
        title="Add to suppression list"
        footer={
          <>
            <Button variant="ghost" onClick={() => setAdding(false)}>
              Cancel
            </Button>
            <Button variant="primary" busy={busy} onClick={add}>
              Add
            </Button>
          </>
        }
      >
        <div className="eml-form">
          <Field label="Emails (one per line or comma-separated)">
            <textarea
              rows={6}
              value={emails}
              onChange={(e) => setEmails(e.target.value)}
              placeholder={"do-not-contact@example.com\nunsub@example.com"}
            />
          </Field>
        </div>
      </Dialog>
    </div>
  );
}
