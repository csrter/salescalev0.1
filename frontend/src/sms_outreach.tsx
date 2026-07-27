/**
 * SMS Outreach — cold-SMS sequences on a BYO Twilio number.
 *
 * One view, five tabs: Dashboard (default, all team roles), Campaigns (admin),
 * Messages (all team roles — a conversation list keyed by contact; SMS has no
 * threads/subjects), Accounts (admin), Suppression (admin). Admin-only tabs
 * are hidden for the member role, matching the server-side gates.
 *
 * Every send routes through services/sms_consent.sendable() server-side —
 * ONLY contacts with a recorded SMS opt-in are ever textable, regardless of
 * what phone number is on file. The enroll receipt surfaces the skip buckets
 * (no_number / no_consent / suppressed / already) rather than silently
 * dropping anyone, and the dashboard leads with that compliance note.
 */

import { memo, useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  activateSmsCampaign,
  addSmsSuppression,
  archiveSmsCampaign,
  catchUpSmsReplies,
  composeSms,
  resumeCompletedSms,
  createSmsAccount,
  createSmsCampaign,
  deleteSmsAccount,
  deleteSmsSuppression,
  enrollSmsContacts,
  featureEnabled,
  getHouseClient,
  getLeadNotifications,
  getLeadRelay,
  setLeadRelay,
  getMyOrg,
  getSmsCampaign,
  listClients,
  listContactLists,
  listSmsAccounts,
  listSmsCampaigns,
  listSmsCrmContactsForClient,
  listSmsEnrollments,
  listSmsMessages,
  listSmsSuppression,
  markSmsRead,
  pauseSmsCampaign,
  previewSmsStep,
  saveSmsSteps,
  setLeadNotifications,
  setOrgSmsOptInDefault,
  smsAnalytics,
  smsUsage,
  smsWebhookUrls,
  testSmsAccount,
  unenrollSms,
  updateSmsAccount,
  updateSmsCampaign,
  type Client,
  type ContactList,
  type SmsAccount,
  type SmsAccountBody,
  type SmsAnalytics,
  type SmsCampaign,
  type SmsCampaignDetail,
  type SmsEnrollment,
  type SmsEnrollReceipt,
  type SmsMessage,
  type SmsPickContact,
  type SmsProvider,
  type SmsStep,
  type SmsStepBranch,
  type SmsStepStats,
  type SmsSuppression,
  type SmsUsage,
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
  Switch,
  Tabs,
  keepEqual,
} from "./components/ui";
import { Plus, Send } from "./components/icons";
import { useToast } from "./components/Toast";
import "./styles/views/sms_outreach.css";

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
  email?: string | null;
  phone?: string | null;
}): string =>
  [c.first_name, c.last_name].filter(Boolean).join(" ") ||
  c.email ||
  c.phone ||
  "Unnamed contact";

/** Human label for how an enrollment entered the campaign (Audience tab
 * "Source" column). Pre-tracking rows have no source and render "—". */
const enrollmentSourceLabel = (e: {
  source: string | null;
  source_detail: string | null;
}): string => {
  switch (e.source) {
    case "list":
      return e.source_detail ? `List · ${e.source_detail}` : "List";
    case "client":
      return e.source_detail ? `All leads · ${e.source_detail}` : "All client leads";
    case "auto_new_lead":
      return e.source_detail ? `Auto · ${e.source_detail}` : "Auto (new lead)";
    case "manual":
      return "Manual";
    default:
      return "—";
  }
};

/** Opt-out red line — a rate at or above this reads as danger, mirroring the
 * email module's bounce red line. */
const OPT_OUT_RED_LINE = 0.05;

// send_days is a 0–6 array; 0 = Monday (Python date.weekday()).
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const HOURS = Array.from({ length: 24 }, (_, i) => i);
const hourLabel = (h: number) =>
  `${((h + 11) % 12) + 1}:00 ${h < 12 ? "AM" : "PM"}`;

// The SMS token set is narrower than email's — no company_description/
// revenue/employees (too long for texts), but job_title and ai_snippet are
// shared. No unsubscribe_url (STOP is a carrier-level reply, not a link).
const TOKENS_HINT = (
  <>
    Personalization: <code>{"{{first_name}}"}</code>{" "}
    <code>{"{{last_name}}"}</code> <code>{"{{company}}"}</code>{" "}
    <code>{"{{city}}"}</code> <code>{"{{state}}"}</code>{" "}
    <code>{"{{job_title}}"}</code> <code>{"{{custom.<key>}}"}</code>{" "}
    <code>{"{{research.<key>}}"}</code> (AI research fields, from CRM setup).
    Fallbacks like <code>{"{{first_name|there}}"}</code>, plus{" "}
    <code>{"{{ai_snippet}}"}</code>. Conditionals{" "}
    <code>{"{{#if token}}...{{/if}}"}</code> and spintax{" "}
    <code>{"{{spin:a|b|c}}"}</code> also work. Failsafes: a lead with no
    first name greets by its business name (proper-cased), and a missing{" "}
    <code>{"{{city}}"}</code> is AI-inferred from the lead's own details and
    saved to the contact.
  </>
);

const SMS_SEGMENT_LEN = 160;

/** Resolve the org's house-CRM client id once, then load its contacts. Used
 * by the enroll and preview pickers — imports and prospecting land in the
 * house CRM, so that's where a campaign audience comes from. */
function useHouseContacts(active: boolean) {
  const [contacts, setContacts] = useState<SmsPickContact[] | null>(null);
  useEffect(() => {
    if (!active) return;
    let alive = true;
    setContacts(null);
    getHouseClient()
      .then((r) => listSmsCrmContactsForClient(r.client_id))
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

/** House-CRM contact lists for the enroll picker's "Audience" select — lists
 * a `list_id` enroll (the whole list, server-side) instead of picking
 * individual contacts. */
function useHouseContactLists(active: boolean) {
  const [lists, setLists] = useState<ContactList[]>([]);
  useEffect(() => {
    if (!active) return;
    let alive = true;
    getHouseClient()
      .then((r) => listContactLists(r.client_id))
      .then((rows) => {
        if (alive) setLists(rows);
      })
      .catch(() => {
        if (alive) setLists([]);
      });
    return () => {
      alive = false;
    };
  }, [active]);
  return lists;
}

/** Client-side cap on picking individual contacts in the enroll dialog — the
 * backend enrolls a list in slices of 500, but hand-picking past that many
 * checkboxes is neither realistic UI nor honest about the cost; use a list. */
const ENROLL_SELECT_CAP = 500;

// ==========================================================================
// Root view
// ==========================================================================

type Panel = "dashboard" | "campaigns" | "messages" | "accounts" | "suppression";

export function SmsOutreachView({
  isAdmin,
  isOwner,
  active = true,
}: {
  isAdmin: boolean;
  isOwner: boolean;
  /** False while the view is kept mounted but hidden behind another tab —
   * gates the messages poll. */
  active?: boolean;
}) {
  const [panel, setPanel] = useState<Panel>("dashboard");
  const [accounts, setAccounts] = useState<SmsAccount[]>([]);
  const [usage, setUsage] = useState<SmsUsage | null>(null);

  const refreshAccounts = useCallback(() => {
    listSmsAccounts().then(setAccounts).catch(() => {});
  }, []);
  useEffect(refreshAccounts, [refreshAccounts]);
  useEffect(() => {
    smsUsage().then(setUsage).catch(() => {});
  }, [panel]);

  const panels: { key: Panel; label: string; adminOnly: boolean }[] = [
    { key: "dashboard", label: "Dashboard", adminOnly: false },
    { key: "campaigns", label: "Campaigns", adminOnly: true },
    { key: "messages", label: "Messages", adminOnly: false },
    { key: "accounts", label: "Accounts", adminOnly: true },
    { key: "suppression", label: "Suppression", adminOnly: true },
  ];
  const visible = panels.filter((p) => isAdmin || !p.adminOnly);

  const errored = accounts.filter((a) => a.status === "error");

  return (
    <div className="sms">
      {errored.length > 0 && (
        <div className="sms-banner">
          <Alert tone="danger" title="Twilio connection error">
            {errored.map((a) => a.name).join(", ")}{" "}
            {errored.length === 1 ? "is" : "are"} failing Twilio auth — sending
            is paused until reconnected.
            {isAdmin && (
              <Button variant="link" size="sm" onClick={() => setPanel("accounts")}>
                Go to Accounts
              </Button>
            )}
          </Alert>
        </div>
      )}

      <div className="sms-subnav">
        <Tabs
          ariaLabel="SMS outreach sections"
          tabs={visible.map((p) => ({ id: p.key, label: p.label }))}
          active={panel}
          onChange={(id) => setPanel(id as Panel)}
        />
        <UsageChip usage={usage} />
      </div>

      {panel === "dashboard" && (
        <DashboardPanel accounts={accounts} isAdmin={isAdmin} isOwner={isOwner} />
      )}
      {panel === "campaigns" && isAdmin && <CampaignsPanel accounts={accounts} />}
      {panel === "messages" && (
        <MessagesPanel accounts={accounts} active={active} />
      )}
      {panel === "accounts" && isAdmin && (
        <AccountsPanel accounts={accounts} onChanged={refreshAccounts} />
      )}
      {panel === "suppression" && isAdmin && <SuppressionPanel />}
    </div>
  );
}

function UsageChip({ usage }: { usage: SmsUsage | null }) {
  if (!usage) return <span className="sms-usage sms-usage--load" aria-hidden="true" />;
  const { used, limit } = usage.sends;
  const over = limit != null && used >= limit;
  return (
    <span className={`sms-usage ${over ? "sms-usage--over" : ""}`.trim()}>
      <strong>{int(used)}</strong> of{" "}
      {limit == null ? "unlimited" : int(limit)} sends this month
    </span>
  );
}

// ==========================================================================
// 1. Dashboard
// ==========================================================================

function DashboardPanel({
  accounts,
  isAdmin,
  isOwner,
}: {
  accounts: SmsAccount[];
  isAdmin: boolean;
  isOwner: boolean;
}) {
  const [campaignId, setCampaignId] = useState<string>("");
  const [days, setDays] = useState(30);
  const [campaigns, setCampaigns] = useState<SmsCampaign[]>([]);
  const [data, setData] = useState<SmsAnalytics | null>(null);

  useEffect(() => {
    listSmsCampaigns().then(setCampaigns).catch(() => {});
  }, []);
  useEffect(() => {
    setData(null);
    smsAnalytics(campaignId || undefined, days).then(setData).catch(() => {});
  }, [campaignId, days]);

  const loading = data === null;
  const t = data?.totals;
  const optOutOver = (t?.opt_out_rate ?? 0) >= OPT_OUT_RED_LINE;

  const campaignColumns: Column<SmsAnalytics["by_campaign"][number]>[] = [
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
      key: "read",
      header: "Read",
      align: "right",
      render: (c) => pct(c.read_rate),
      sortValue: (c) => c.read_rate ?? -1,
    },
    {
      key: "replies",
      header: "Replies",
      align: "right",
      render: (c) => int(c.replies),
      sortValue: (c) => c.replies,
    },
    {
      key: "reply",
      header: "Reply",
      align: "right",
      render: (c) => pct(c.reply_rate),
      sortValue: (c) => c.reply_rate ?? -1,
    },
    {
      key: "failed",
      header: "Failed",
      align: "right",
      render: (c) => int(c.failed),
      sortValue: (c) => c.failed,
    },
  ];

  const chartLabels = data?.by_day.map((d) => d.date) ?? [];
  const hasChart = chartLabels.length > 1;

  return (
    <div>
      <div className="sms-redline">
        <Alert tone="info" title="Only opted-in contacts are textable">
          Every SMS send is gated server-side on a recorded SMS opt-in — a
          phone number alone is never enough. Contacts without consent are
          automatically excluded from enrollment, and any STOP reply removes
          them everywhere, immediately, org-wide.
        </Alert>
      </div>

      {isAdmin && <OrgOptInDefaultCard isOwner={isOwner} />}
      {isAdmin && <LeadNotificationsCard hasAccount={accounts.length > 0} />}
      {isAdmin && (
        <LeadRelayCard
          hasBlueBubbles={accounts.some((a) => a.provider === "bluebubbles")}
        />
      )}

      <div className="sms-bar">
        <select
          className="select sms-select"
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

      <div className="sms-kpis">
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
              <Kpi label="Read rate (iMessage)" value={pct(t.read_rate)} />
              <Kpi label="Replies" value={int(t.replies)} />
              <Kpi label="Reply rate" value={pct(t.reply_rate)} />
              <Kpi label="Auto-replies" value={int(t.auto_replies)} />
              <Kpi label="Failed" value={int(t.failed)} />
              <Kpi label="Opt-out rate" value={pct(t.opt_out_rate)} />
            </>
          )}
        </KpiGrid>
      </div>

      {!loading && optOutOver && (
        <div className="sms-redline">
          <Alert tone="danger" title="Opt-out rate is elevated">
            {pct(t?.opt_out_rate)} of sends resulted in an opt-out (over{" "}
            {pct(OPT_OUT_RED_LINE)}). Review targeting and message content —
            a high opt-out rate risks carrier filtering.
          </Alert>
        </div>
      )}

      {!loading && data?.ai_configured === false && (
        <div className="sms-redline">
          <Alert tone="warn" title="AI personalization is off">
            No AI provider key is configured, so {"{{ai_snippet}}"} renders as
            empty text in every send. Add a key on the Integrations page (AI
            provider card) to turn it on — sends are never blocked either way.
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
                { name: "Delivered", data: data!.by_day.map((d) => d.delivered) },
                { name: "Read", data: data!.by_day.map((d) => d.read) },
                { name: "Replied", data: data!.by_day.map((d) => d.replied) },
                { name: "Failed", data: data!.by_day.map((d) => d.failed) },
              ]}
              height={220}
              ariaLabel={`Daily SMS volume over ${chartLabels.length} days`}
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

      <Section title="Number health">
        {loading ? (
          <GlassCard>
            <SkeletonText lines={2} />
          </GlassCard>
        ) : (data?.accounts.length ?? 0) === 0 ? (
          <GlassCard>
            <EmptyState title="No Twilio numbers connected">
              Connect a number in the Accounts tab to start outreach.
            </EmptyState>
          </GlassCard>
        ) : (
          <div className="sms-health">
            {(data ?? { accounts: [] }).accounts.map((a) => (
              <GlassCard key={a.account_id} className="sms-health-card">
                <div className="sms-health-top">
                  <span className="sms-health-number">
                    {a.from_number || "messaging service"}
                  </span>
                  <Badge tone={a.status === "active" ? "ok" : "danger"}>
                    {a.status === "active" ? "connected" : "error"}
                  </Badge>
                </div>
                <div className="sms-health-meta">
                  <span>
                    {int(a.sends_today)} of {int(a.daily_send_cap)} today
                  </span>
                </div>
              </GlassCard>
            ))}
          </div>
        )}
      </Section>

      {accounts.length === 0 && (
        <div className="sms-redline">
          <Alert tone="info" title="No numbers yet">
            The dashboard fills in once you connect a Twilio number and
            activate a campaign.
          </Alert>
        </div>
      )}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="sms-section">
      <h3 className="sms-section-title">{title}</h3>
      {children}
    </section>
  );
}

/** Admin-only: the org-wide standing consent attestation for agencies whose
 * own intake funnels already collect SMS consent before leads reach
 * Salescale. STOP/suppression at send time is unaffected either way. */
function OrgOptInDefaultCard({ isOwner }: { isOwner: boolean }) {
  const toast = useToast();
  const [value, setValue] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getMyOrg()
      .then((o) => setValue(o.sms_opt_in_default))
      .catch(() => {});
  }, []);

  const toggle = async (next: boolean) => {
    setBusy(true);
    try {
      const o = await setOrgSmsOptInDefault(next);
      setValue(o.sms_opt_in_default);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Failed to update", "error");
    } finally {
      setBusy(false);
    }
  };

  if (value == null) return null;

  return (
    <div className="sms-redline">
      <GlassCard className="sms-optin-card">
        <Switch
          checked={value}
          onChange={toggle}
          disabled={busy || !isOwner}
          label="New contacts are pre-opted-in"
        />
        <p className="sms-hint">
          Every new contact added to the CRM is stamped with SMS consent,
          source “org_default:pre_opted_funnel”. Only enable this if your
          intake funnels collect SMS consent before leads reach Salescale.
          STOP/suppression always wins.
          {!isOwner && " Only the organization owner can change this."}
        </p>
      </GlassCard>
    </div>
  );
}

/** Admin-only: text-the-team alerts on new leads. Reuses whichever SMS
 * account the org has already connected — no separate sender setup. The
 * message template is shared with per-client recipients too (see
 * ClientLeadNotifications in crm.tsx). */
function LeadNotificationsCard({ hasAccount }: { hasAccount: boolean }) {
  const toast = useToast();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [phones, setPhones] = useState<string[]>([]);
  const [template, setTemplate] = useState("");
  const [defaultTemplate, setDefaultTemplate] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    getLeadNotifications()
      .then((c) => {
        setEnabled(c.enabled);
        setPhones(c.phones.length > 0 ? c.phones : [""]);
        setTemplate(c.message_template ?? c.default_template);
        setDefaultTemplate(c.default_template);
      })
      .catch(() => {});
  }, []);
  useEffect(load, [load]);

  const save = async (next: {
    enabled: boolean;
    phones: string[];
    template: string;
  }) => {
    setBusy(true);
    try {
      const saved = await setLeadNotifications({
        enabled: next.enabled,
        phones: next.phones.map((p) => p.trim()).filter(Boolean),
        message_template: next.template,
      });
      setEnabled(saved.enabled);
      setPhones(saved.phones.length > 0 ? saved.phones : [""]);
      setTemplate(saved.message_template ?? saved.default_template);
      setDefaultTemplate(saved.default_template);
    } catch (e) {
      toast(e instanceof Error ? e.message : "Failed to update", "error");
    } finally {
      setBusy(false);
    }
  };

  if (enabled == null) return null;

  return (
    <div className="sms-redline">
      <GlassCard className="sms-optin-card">
        <Switch
          checked={enabled}
          onChange={(next) => save({ enabled: next, phones, template })}
          disabled={busy}
          label="Text the team when a new lead comes in"
        />
        <p className="sms-hint">
          Sends a short SMS to the numbers below the moment a lead arrives
          (native lead-form webhooks or a landing-page submission — not bulk
          CSV/Lead Finder imports), using the org's own connected number.
          {!hasAccount &&
            " Connect a number in the Accounts tab first — nothing sends without one."}
        </p>
        {enabled && (
          <div className="sms-notify-phones">
            {phones.map((phone, i) => (
              <div key={i} className="sms-form-row">
                <input
                  className="input"
                  placeholder="+1 480 555 0100"
                  value={phone}
                  onChange={(e) =>
                    setPhones(phones.map((p, j) => (j === i ? e.target.value : p)))
                  }
                />
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy}
                  onClick={() => setPhones(phones.filter((_, j) => j !== i))}
                >
                  Remove
                </Button>
              </div>
            ))}
            <div className="sms-form-actions">
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => setPhones([...phones, ""])}
              >
                <Plus size={14} /> Add number
              </Button>
            </div>
            <Field
              label="Message template"
              description="The default for every client's alerts — a client can override it in CRM setup → Lead SMS notifications. Tokens: {{name}} {{first_name}} {{last_name}} {{phone}} {{email}} {{brand}} {{zip}} {{source}}"
            >
              <textarea
                className="input sms-notify-template"
                rows={6}
                value={template}
                onChange={(e) => setTemplate(e.target.value)}
              />
            </Field>
            <div className="sms-form-actions">
              <Button
                variant="ghost"
                size="sm"
                disabled={busy}
                onClick={() => setTemplate(defaultTemplate)}
              >
                Reset to default
              </Button>
              <Button
                variant="primary"
                size="sm"
                busy={busy}
                onClick={() => save({ enabled, phones, template })}
              >
                Save
              </Button>
            </div>
          </div>
        )}
      </GlassCard>
    </div>
  );
}

function LeadRelayCard({ hasBlueBubbles }: { hasBlueBubbles: boolean }) {
  const toast = useToast();
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [phone, setPhone] = useState("");
  const [savedPhone, setSavedPhone] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getLeadRelay()
      .then((c) => {
        setEnabled(c.enabled);
        setPhone(c.phone ?? "");
        setSavedPhone(c.phone ?? "");
      })
      .catch(() => setEnabled(false));
  }, []);

  const save = async (next: { enabled: boolean; phone: string }) => {
    setBusy(true);
    try {
      const saved = await setLeadRelay({
        enabled: next.enabled,
        phone: next.phone.trim() || null,
      });
      setEnabled(saved.enabled);
      setPhone(saved.phone ?? "");
      setSavedPhone(saved.phone ?? "");
      toast("Saved", "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Failed to update", "error");
    } finally {
      setBusy(false);
    }
  };

  if (enabled == null) return null;

  return (
    <div className="sms-redline">
      <GlassCard className="sms-optin-card">
        <Switch
          checked={enabled}
          onChange={(next) => save({ enabled: next, phone })}
          disabled={busy}
          label="Forward lead replies to my phone"
        />
        <p className="sms-hint">
          When a lead replies, it's forwarded to the number below. Reply from
          your phone starting with the lead's code (shown in each forward, e.g.{" "}
          <strong>1234 on my way</strong>) and it's sent back to that lead
          through BlueBubbles. Enter your number, then turn this on. STOP/opt-out
          is always respected.
          {!hasBlueBubbles &&
            " Connect a BlueBubbles account in the Accounts tab first — the relay runs through it."}
        </p>
        <div className="sms-form-row">
          <input
            className="input"
            placeholder="+1 480 720 7351"
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
            onBlur={() => {
              if (phone.trim() !== savedPhone) save({ enabled, phone });
            }}
          />
        </div>
      </GlassCard>
    </div>
  );
}

// ==========================================================================
// 2. Campaigns
// ==========================================================================

function CampaignsPanel({ accounts }: { accounts: SmsAccount[] }) {
  const toast = useToast();
  const [campaigns, setCampaigns] = useState<SmsCampaign[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);

  const refresh = useCallback(() => {
    listSmsCampaigns().then(setCampaigns).catch(() => {});
  }, []);
  useEffect(refresh, [refresh]);

  const columns: Column<SmsCampaign>[] = [
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
      key: "read",
      header: "Read",
      align: "right",
      render: (c) => pct(c.read_rate),
      sortValue: (c) => c.read_rate ?? -1,
    },
    {
      key: "replies",
      header: "Replies",
      align: "right",
      render: (c) => int(c.replies),
      sortValue: (c) => c.replies,
    },
    {
      key: "reply",
      header: "Reply",
      align: "right",
      render: (c) => pct(c.reply_rate),
      sortValue: (c) => c.reply_rate ?? -1,
    },
    {
      key: "failed",
      header: "Failed",
      align: "right",
      render: (c) => int(c.failed),
      sortValue: (c) => c.failed,
    },
    {
      key: "optout",
      header: "Opt-out",
      align: "right",
      render: (c) =>
        c.opt_out_rate != null && c.opt_out_rate >= OPT_OUT_RED_LINE ? (
          <Badge tone="danger">{pct(c.opt_out_rate)}</Badge>
        ) : (
          pct(c.opt_out_rate)
        ),
      sortValue: (c) => c.opt_out_rate ?? -1,
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
      <div className="sms-head">
        <p className="sms-sub">
          Multi-step cold-SMS sequences. Every audience passes the SMS consent
          gate — only opted-in contacts are enrolled — and STOP replies land
          on the suppression list automatically.
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
          <EmptyState title="Connect a Twilio number first">
            Campaigns send from a number — add one in the Accounts tab, then
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
  accounts: SmsAccount[];
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const toast = useToast();
  const [name, setName] = useState("");
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [busy, setBusy] = useState(false);

  const create = async () => {
    if (!name.trim() || !accountId) {
      toast("Name and number are required", "error");
      return;
    }
    setBusy(true);
    try {
      const c = await createSmsCampaign({ name: name.trim(), account_id: accountId });
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
      <div className="sms-form">
        <Field label="Campaign name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Q3 HVAC contractors"
          />
        </Field>
        <Field label="Send from number">
          <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} — {a.from_number || a.messaging_service_sid}
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
  accounts: SmsAccount[];
  onClose: () => void;
  onToast: (msg: string, tone?: "ok" | "error" | "info") => void;
}) {
  const [tab, setTab] = useState<EditorTab>("config");
  const [detail, setDetail] = useState<SmsCampaignDetail | null>(null);
  const [steps, setSteps] = useState<SmsStep[]>([]);
  const [activateError, setActivateError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ position: number } | null>(null);
  const [enrolling, setEnrolling] = useState(false);
  const [archiveConfirm, setArchiveConfirm] = useState(false);

  const load = useCallback(() => {
    getSmsCampaign(campaignId)
      .then((d) => {
        setDetail(d);
        setSteps(d.steps);
      })
      .catch((e) => onToast(e instanceof Error ? e.message : "Load failed", "error"));
  }, [campaignId, onToast]);
  useEffect(load, [load]);

  const patchConfig = async (body: Parameters<typeof updateSmsCampaign>[1]) => {
    if (!detail) return;
    try {
      const d = await updateSmsCampaign(detail.id, body);
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
      const d = await saveSmsSteps(detail.id, steps.map((s, i) => ({ ...s, position: i + 1 })));
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
      const d = await activateSmsCampaign(detail.id);
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
      const d = await pauseSmsCampaign(detail.id);
      setDetail((cur) => (cur ? { ...cur, ...d } : d));
      onToast("Campaign paused", "ok");
    } catch (e) {
      onToast(e instanceof Error ? e.message : "Pause failed", "error");
    } finally {
      setBusy(false);
    }
  };

  const archive = async () => {
    if (!detail) return;
    setBusy(true);
    try {
      const d = await archiveSmsCampaign(detail.id);
      setDetail((cur) => (cur ? { ...cur, ...d } : d));
      setArchiveConfirm(false);
      onToast("Campaign archived", "ok");
    } catch (e) {
      onToast(e instanceof Error ? e.message : "Archive failed", "error");
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
          {detail && (detail.status === "draft" || detail.status === "paused") && (
            archiveConfirm ? (
              <>
                <Button variant="danger-outline" busy={busy} onClick={archive}>
                  Really archive?
                </Button>
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => setArchiveConfirm(false)}
                >
                  Keep it
                </Button>
              </>
            ) : (
              <Button variant="ghost" onClick={() => setArchiveConfirm(true)}>
                Archive
              </Button>
            )
          )}
          {detail && detail.status === "active" ? (
            <Button variant="danger-outline" busy={busy} onClick={pause}>
              Pause campaign
            </Button>
          ) : detail && detail.status !== "archived" ? (
            <Button variant="primary" busy={busy} onClick={activate}>
              Activate campaign
            </Button>
          ) : null}
        </>
      }
    >
      {!detail ? (
        <SkeletonText lines={8} />
      ) : (
        <>
          <div className="sms-editor-head">
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
            <Alert tone="warn" title="Can't activate yet" className="sms-activate-err">
              {activateError}
            </Alert>
          )}

          {tab === "config" && (
            <>
              {detail.failed > 0 && detail.failure_reasons.length > 0 && (
                <Alert tone="warn" title={`${int(detail.failed)} sends failed`}>
                  <ul className="sms-failure-reasons">
                    {detail.failure_reasons.map((f) => (
                      <li key={f.reason}>
                        <span className="sms-fr-count">{int(f.count)}</span>{" "}
                        {f.reason}
                      </li>
                    ))}
                  </ul>
                </Alert>
              )}
              <ConfigForm detail={detail} accounts={accounts} onPatch={patchConfig} />
            </>
          )}

          {tab === "steps" && (
            <>
              {detail.status === "active" && (
                <Alert tone="info">
                  This campaign is live — saved step edits apply to future
                  sends only. Contacts mid-sequence continue from their
                  current position; texts already sent are unchanged.
                </Alert>
              )}
              <StepsEditor
                steps={steps}
                setSteps={setSteps}
                onSave={saveSteps}
                busy={busy}
                onPreview={(position) => setPreview({ position })}
              />
            </>
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
              isReplyStep={steps[preview.position]?.trigger === "reply"}
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
  detail: SmsCampaignDetail;
  accounts: SmsAccount[];
  onPatch: (body: Parameters<typeof updateSmsCampaign>[1]) => void;
}) {
  const [clients, setClients] = useState<Client[]>([]);
  useEffect(() => {
    listClients()
      .then(setClients)
      .catch(() => setClients([]));
  }, []);

  const toggleDay = (day: number) => {
    const set = new Set(detail.send_days);
    if (set.has(day)) set.delete(day);
    else set.add(day);
    onPatch({ send_days: [...set].sort((a, b) => a - b) });
  };

  const clientName = detail.client_id
    ? clients.find((c) => c.id === detail.client_id)?.name ?? "this client"
    : null;

  // "24/7" = full-day window (end=24 through midnight) on all seven days.
  const all247 =
    detail.send_window_start === 0 &&
    detail.send_window_end === 24 &&
    detail.send_days.length === 7;

  return (
    <div className="sms-form">
      <Field label="Send from number">
        <select
          value={detail.account_id}
          onChange={(e) => onPatch({ account_id: e.target.value })}
        >
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name} — {a.from_number || a.messaging_service_sid}
            </option>
          ))}
        </select>
      </Field>

      <Field label="Client">
        <select
          value={detail.client_id ?? ""}
          onChange={(e) => {
            const id = e.target.value;
            const picked = clients.find((c) => c.id === id);
            onPatch({
              client_id: id || null,
              // Clearing the client can't co-exist with auto-enroll (server 422s);
              // turn it off in the same patch so the UI never sends an invalid pair.
              ...(id ? {} : { auto_enroll_new_leads: false }),
              // Apply the client's own timezone (if it has one) so send-window /
              // quiet-hours follow that client's market. Never overwrite with a
              // blank — a client without a timezone leaves the campaign's as-is.
              ...(picked?.timezone ? { timezone: picked.timezone } : {}),
            });
          }}
        >
          <option value="">No client (manual enroll only)</option>
          {clients.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
      </Field>

      <div className="sms-fieldset">
        <Switch
          checked={detail.auto_enroll_new_leads}
          onChange={(v) => onPatch({ auto_enroll_new_leads: v })}
          label="Auto-enroll new leads for this client"
          disabled={!detail.client_id}
        />
        <p className="sms-hint">
          {detail.client_id ? (
            <>
              Every new lead that arrives for <strong>{clientName}</strong> (form
              submission, lead-form webhook, or landing page) is automatically
              enrolled the moment it lands, so it starts getting this sequence
              within a minute. Leads with no recorded SMS consent are skipped,
              never force-texted — STOP and suppression still apply.
            </>
          ) : (
            <>Pick a client above to enable automatic enrollment of that
            client&apos;s incoming leads. Without a client, this campaign is
            manual-enroll only.</>
          )}
        </p>
      </div>

      <div className="sms-form-row">
        <Field label="Daily cap (this campaign)" optional>
          <input
            type="number"
            min={1}
            defaultValue={detail.daily_cap ?? ""}
            placeholder="number default"
            onBlur={(e) => {
              const v = e.target.value ? Number(e.target.value) : null;
              if (v !== detail.daily_cap) onPatch({ daily_cap: v });
            }}
          />
        </Field>
        <Field label="Timezone">
          <input
            // Re-mount when the timezone changes (e.g. applied from the client
            // above) so this uncontrolled input reflects the new value.
            key={detail.timezone}
            defaultValue={detail.timezone}
            placeholder="America/New_York"
            onBlur={(e) => {
              if (e.target.value && e.target.value !== detail.timezone)
                onPatch({ timezone: e.target.value });
            }}
          />
        </Field>
      </div>

      <div className="sms-fieldset">
        <Switch
          checked={all247}
          onChange={(v) =>
            v
              ? onPatch({
                  // Full 24h window (end=24 = through midnight) on every day —
                  // the backend's quiet-hours check reads this as always-open.
                  send_window_start: 0,
                  send_window_end: 24,
                  send_days: [0, 1, 2, 3, 4, 5, 6],
                })
              : onPatch({
                  // Back to a standard, compliant window the operator can adjust.
                  send_window_start: 8,
                  send_window_end: 21,
                  send_days: [0, 1, 2, 3, 4],
                })
          }
          label="Send at all hours (24/7)"
        />
        <p className="sms-hint">
          Leads get a reply the moment they arrive — any hour, any day —
          ignoring the send window below. Consent and STOP/opt-out still apply.
          Note: standard SMS quiet hours are 8am–9pm local; this is intended for
          immediate replies to inbound leads, not cold sends.
        </p>
      </div>

      {!all247 && (
        <>
          <div className="sms-form-row">
            <Field label="Send window start">
              <select
                value={detail.send_window_start}
                onChange={(e) =>
                  onPatch({ send_window_start: Number(e.target.value) })
                }
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
                onChange={(e) =>
                  onPatch({ send_window_end: Number(e.target.value) })
                }
              >
                {HOURS.map((h) => (
                  <option key={h} value={h}>
                    {hourLabel(h)}
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <div className="sms-fieldset">
            <span className="field-label">Send days</span>
            <div className="sms-days">
              {WEEKDAYS.map((label, day) => (
                <label key={day} className="sms-check">
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
        </>
      )}

      <div className="sms-fieldset">
        <Switch
          checked={detail.exit_on_reply}
          onChange={(v) => onPatch({ exit_on_reply: v })}
          label="Stop the drip when they reply"
        />
        <p className="sms-hint">
          When a lead replies, their remaining scheduled steps are cancelled
          (status "replied") — nobody keeps getting cold follow-ups
          mid-conversation. Steps set to send <em>after they reply</em> always
          take precedence: a reply routes to the next reply-triggered step when
          the sequence has one, and this setting only applies when none remain.
        </p>
      </div>

      <div className="sms-fieldset">
        <Switch
          checked={detail.include_compliance_footer}
          onChange={(v) => onPatch({ include_compliance_footer: v })}
          label="Sender ID + opt-out footer on the first message"
        />
        <p className="sms-hint">
          Adds "OrgName: " and "Reply STOP to opt out" to the first text of
          this campaign, when not already present in the template. Turn this
          off only for contacts who already know they'll hear from you (past
          clients, warm follow-ups) — STOP still works exactly the same
          either way, this only controls whether the reminder text is shown.
          Carriers may filter unidentified bulk SMS more aggressively without
          it.
        </p>
      </div>
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
  steps: SmsStep[];
  setSteps: (s: SmsStep[]) => void;
  onSave: () => void;
  busy: boolean;
  onPreview: (position: number) => void;
}) {
  const update = (i: number, next: SmsStep) =>
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
        wait_minutes: 0,
        trigger: "schedule",
        body: "",
        branches: [],
        ai_branching: false,
        ai_instructions: null,
      },
    ]);

  return (
    <div className="sms-steps">
      <p className="sms-tokens">{TOKENS_HINT}</p>
      {steps.length === 0 && (
        <EmptyState title="No steps yet">
          Add a first text, then follow-ups. A step either sends on a schedule
          (a set delay after the previous step) or after the lead replies —
          reply steps can branch on what they said.
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
      <div className="sms-step-add">
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

type DelayUnit = "minutes" | "hours" | "days";

/** Display form of a step's wait_days + wait_minutes: the largest unit that
 * expresses the total exactly. */
function delayUi(step: SmsStep): { value: number; unit: DelayUnit } {
  const totalMinutes = (step.wait_days || 0) * 1440 + (step.wait_minutes || 0);
  if (totalMinutes === 0) return { value: 0, unit: "minutes" };
  if (totalMinutes % 1440 === 0) return { value: totalMinutes / 1440, unit: "days" };
  if (totalMinutes % 60 === 0) return { value: totalMinutes / 60, unit: "hours" };
  return { value: totalMinutes, unit: "minutes" };
}

function delayFields(value: number, unit: DelayUnit): Pick<SmsStep, "wait_days" | "wait_minutes"> {
  const v = Math.max(0, Math.floor(value) || 0);
  if (unit === "days") return { wait_days: v, wait_minutes: 0 };
  if (unit === "hours") return { wait_days: 0, wait_minutes: v * 60 };
  return { wait_days: 0, wait_minutes: v };
}

function StepStatsLine({ stats }: { stats?: SmsStepStats }) {
  if (!stats || (stats.sent === 0 && stats.failed === 0 && stats.replies === 0))
    return null;
  return (
    <span className="sms-step-stats">
      sent {int(stats.sent)} · delivered {int(stats.delivered)} · read{" "}
      {int(stats.read)} · replies {int(stats.replies)} · failed {int(stats.failed)}
    </span>
  );
}

function BranchesEditor({
  step,
  onChange,
}: {
  step: SmsStep;
  onChange: (s: SmsStep) => void;
}) {
  const branches = step.branches ?? [];
  const update = (i: number, next: SmsStepBranch) =>
    onChange({ ...step, branches: branches.map((b, j) => (j === i ? next : b)) });
  const remove = (i: number) =>
    onChange({ ...step, branches: branches.filter((_, j) => j !== i) });
  const add = () =>
    onChange({
      ...step,
      branches: [...branches, { label: "", keywords: [], body: "" }],
    });

  return (
    <div className="sms-branches">
      <span className="field-label">Response branches</span>
      <p className="sms-hint">
        Match what they text back: the first branch with a keyword found in
        their reply (whole word, any casing) sends its response instead of the
        default message above. Branch order is priority.
      </p>
      {branches.map((b, i) => (
        <div className="sms-branch" key={i}>
          <div className="sms-form-row">
            <Field label="Branch name">
              <input
                value={b.label}
                onChange={(e) => update(i, { ...b, label: e.target.value })}
                placeholder="Interested"
              />
            </Field>
            <Field label="Keywords (comma-separated)">
              <input
                // Uncontrolled + commit-on-blur so typing commas doesn't fight
                // the normalized join (same pattern as the daily-cap input).
                key={`${i}-${branches.length}`}
                defaultValue={b.keywords.join(", ")}
                onBlur={(e) =>
                  update(i, {
                    ...b,
                    keywords: e.target.value
                      .split(",")
                      .map((k) => k.trim())
                      .filter(Boolean),
                  })
                }
                placeholder="yes, sure, interested"
              />
            </Field>
          </div>
          <Field label="Response">
            <textarea
              rows={2}
              value={b.body}
              onChange={(e) => update(i, { ...b, body: e.target.value })}
              placeholder={"Great — what's the best time to call you, {{first_name|there}}?"}
            />
          </Field>
          <Button variant="danger-outline" size="sm" onClick={() => remove(i)}>
            Remove branch
          </Button>
        </div>
      ))}
      <Button variant="ghost" size="sm" onClick={add}>
        <Plus size={14} />
        Add branch
      </Button>
      {branches.length > 0 && (
        <div className="sms-fieldset">
          <Switch
            checked={step.ai_branching}
            onChange={(v) => onChange({ ...step, ai_branching: v })}
            label="AI-match replies when no keyword hits"
          />
          <p className="sms-hint">
            When no keyword matches, one AI call classifies the reply into a
            branch by meaning ("yeah go ahead" → Interested). Falls back to the
            default message on any AI failure — a reply is never left hanging.
          </p>
        </div>
      )}
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
  step: SmsStep;
  index: number;
  isFirst: boolean;
  isLast: boolean;
  onChange: (s: SmsStep) => void;
  onRemove: () => void;
  onMove: (dir: -1 | 1) => void;
  onPreview: () => void;
}) {
  const [showAi, setShowAi] = useState(Boolean(step.ai_instructions));
  const isReply = step.trigger === "reply";
  const delay = delayUi(step);
  const len = step.body.length;
  const segments = Math.max(1, Math.ceil(len / SMS_SEGMENT_LEN));

  return (
    <GlassCard className="sms-step">
      <div className="sms-step-head">
        <Badge tone="accent">{index + 1}</Badge>
        <strong className="sms-step-title">
          {isReply ? "Reply handler" : isFirst ? "First text" : `Follow-up ${index}`}
        </strong>
        <StepStatsLine stats={step.stats} />
        <div className="sms-step-actions">
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

      <div className="sms-form">
        <Field label="Send this step">
          <Segmented
            ariaLabel={`Step ${index + 1} trigger`}
            value={step.trigger}
            onChange={(v) =>
              onChange({
                ...step,
                trigger: v as SmsStep["trigger"],
                // Branches only exist on reply steps (the API enforces it).
                ...(v === "schedule" ? { branches: [], ai_branching: false } : {}),
              })
            }
            options={[
              { value: "schedule", label: "On a schedule" },
              { value: "reply", label: "After they reply" },
            ]}
          />
        </Field>

        {isReply ? (
          <>
            <div className="sms-form-row">
              <Field label="Wait after their reply">
                <input
                  type="number"
                  min={0}
                  value={delay.value}
                  onChange={(e) =>
                    onChange({ ...step, ...delayFields(Number(e.target.value), delay.unit) })
                  }
                />
              </Field>
              <Field label="Unit">
                <select
                  value={delay.unit}
                  onChange={(e) =>
                    onChange({
                      ...step,
                      ...delayFields(delay.value, e.target.value as DelayUnit),
                    })
                  }
                >
                  <option value="minutes">minutes</option>
                  <option value="hours">hours</option>
                  <option value="days">days</option>
                </select>
              </Field>
            </div>
            <p className="sms-hint">
              The sequence pauses here ("awaiting reply") until the lead texts
              back, then this sends after the wait above — 0 minutes means
              within about a minute of their reply. Responses are prioritized
              over scheduled sends and skip the campaign's daily cap and
              send pacing, so the wait you set is the wait they get. The
              campaign's send window still applies unless 24/7 sending is on.
            </p>
          </>
        ) : isFirst ? (
          <p className="sms-hint">Sends immediately when a contact is enrolled.</p>
        ) : (
          <div className="sms-form-row">
            <Field label="Wait after previous step">
              <input
                type="number"
                min={0}
                value={delay.value}
                onChange={(e) =>
                  onChange({ ...step, ...delayFields(Number(e.target.value), delay.unit) })
                }
              />
            </Field>
            <Field label="Unit">
              <select
                value={delay.unit}
                onChange={(e) =>
                  onChange({
                    ...step,
                    ...delayFields(delay.value, e.target.value as DelayUnit),
                  })
                }
              >
                <option value="minutes">minutes</option>
                <option value="hours">hours</option>
                <option value="days">days</option>
              </select>
            </Field>
          </div>
        )}

        <Field label={isReply ? "Default response (when no branch matches)" : "Message"}>
          <textarea
            rows={4}
            value={step.body}
            onChange={(e) => onChange({ ...step, body: e.target.value })}
            placeholder={
              isReply
                ? "Thanks for getting back to me, {{first_name|there}} — mind sharing a bit more?"
                : "Hi {{first_name|there}}, quick question about {{company}}…"
            }
          />
        </Field>
        <span className={`sms-step-charcount${segments > 1 ? " sms-step-charcount--over" : ""}`}>
          {len} characters · {segments} segment{segments === 1 ? "" : "s"}
        </span>
        <p className="sms-hint">
          Counted before personalization; sends are capped at 3 segments.
        </p>

        {isReply && <BranchesEditor step={step} onChange={onChange} />}

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
              placeholder="Reference their city and vertical; keep it to one short sentence."
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
  isReplyStep,
  onClose,
}: {
  campaignId: string;
  position: number;
  isReplyStep: boolean;
  onClose: () => void;
}) {
  const toast = useToast();
  const contacts = useHouseContacts(true);
  const [contactId, setContactId] = useState("");
  const [sampleReply, setSampleReply] = useState("");
  const [rendered, setRendered] = useState<Awaited<
    ReturnType<typeof previewSmsStep>
  > | null>(null);
  const [busy, setBusy] = useState(false);

  const run = async (cid: string, reply: string) => {
    if (!cid) return;
    setBusy(true);
    setRendered(null);
    try {
      // `position` here is the 0-based array index within this dialog's own
      // state; the API's step positions are 1-indexed (matching saveSmsSteps).
      const r = await previewSmsStep(campaignId, cid, position + 1, reply);
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
      <div className="sms-form">
        <Field label="Render for contact">
          <select
            value={contactId}
            onChange={(e) => {
              setContactId(e.target.value);
              run(e.target.value, sampleReply);
            }}
          >
            <option value="">Choose a contact…</option>
            {(contacts ?? []).map((c) => (
              <option key={c.id} value={c.id}>
                {contactLabel(c)}
                {c.phone ? ` · ${c.phone}` : ""}
              </option>
            ))}
          </select>
        </Field>
        {isReplyStep && (
          <Field
            label="Sample reply"
            description="What the lead might text back — the preview shows which response branch fires."
          >
            <input
              value={sampleReply}
              onChange={(e) => setSampleReply(e.target.value)}
              onBlur={() => run(contactId, sampleReply)}
              onKeyDown={(e) => {
                if (e.key === "Enter") run(contactId, sampleReply);
              }}
              placeholder="yes sounds good"
            />
          </Field>
        )}
        {contacts !== null && contacts.length === 0 && (
          <Alert tone="info">
            No contacts in the house CRM yet — import leads to preview
            personalization.
          </Alert>
        )}
        {busy && <SkeletonText lines={4} />}
        {rendered && (
          <div className="sms-preview">
            <span className="sms-preview-label">
              Message
              {isReplyStep && (
                <>
                  {" "}
                  <Badge tone={rendered.branch_label ? "accent" : "neutral"}>
                    {rendered.branch_label
                      ? `branch: ${rendered.branch_label}`
                      : "default response"}
                  </Badge>
                </>
              )}
            </span>
            <div className="sms-preview-body">{rendered.body}</div>
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
  campaign: SmsCampaignDetail;
  onEnrollClick: () => void;
  onToast: (msg: string, tone?: "ok" | "error" | "info") => void;
}) {
  const [rows, setRows] = useState<SmsEnrollment[] | null>(null);
  const [catchUp, setCatchUp] = useState<{ queued: number } | null>(null);
  const [catchUpConfirm, setCatchUpConfirm] = useState(false);
  const [catchUpBusy, setCatchUpBusy] = useState(false);
  const hasReplyStep = campaign.steps.some((s) => s.trigger === "reply");

  // Re-fetch whenever the campaign's enrolled count changes (not just on
  // mount/campaign switch) — otherwise a fresh enroll leaves this table
  // showing its stale empty state until some unrelated re-mount.
  const refresh = useCallback(() => {
    listSmsEnrollments(campaign.id).then(setRows).catch(() => {});
  }, [campaign.id, campaign.enrolled]);
  useEffect(refresh, [refresh]);

  // Leads who replied before this campaign had a reply step are terminal
  // (exited "replied" / completed) — surface them so the admin can queue the
  // reply step for them. Dry-run only; nothing sends without the confirm.
  useEffect(() => {
    if (!hasReplyStep) {
      setCatchUp(null);
      return;
    }
    catchUpSmsReplies(campaign.id, true).then(setCatchUp).catch(() => {});
  }, [campaign.id, campaign.enrolled, hasReplyStep]);

  const runCatchUp = async () => {
    setCatchUpBusy(true);
    try {
      const r = await catchUpSmsReplies(campaign.id, false);
      onToast(
        `Queued the reply step for ${r.queued} past ${r.queued === 1 ? "replier" : "repliers"}`,
        "ok",
      );
      setCatchUpConfirm(false);
      setCatchUp(null);
      refresh();
    } catch (e) {
      onToast(e instanceof Error ? e.message : "Catch-up failed", "error");
    } finally {
      setCatchUpBusy(false);
    }
  };

  // Leads who COMPLETED the sequence before its newest steps existed (e.g. a
  // parting-message step written later) — dry-run counts only; explicit
  // confirm before anything queues.
  const [resume, setResume] = useState<{ queued: number; awaiting: number } | null>(null);
  const [resumeConfirm, setResumeConfirm] = useState(false);
  const [resumeBusy, setResumeBusy] = useState(false);
  useEffect(() => {
    resumeCompletedSms(campaign.id, true)
      .then((r) => setResume(r.no_new_steps ? null : r))
      .catch(() => {});
  }, [campaign.id, campaign.enrolled, campaign.steps_count]);

  const runResume = async () => {
    setResumeBusy(true);
    try {
      const r = await resumeCompletedSms(campaign.id, false);
      onToast(
        `Resumed ${r.queued + r.awaiting} completed ${r.queued + r.awaiting === 1 ? "lead" : "leads"} through the new steps`,
        "ok",
      );
      setResumeConfirm(false);
      setResume(null);
      refresh();
    } catch (e) {
      onToast(e instanceof Error ? e.message : "Resume failed", "error");
    } finally {
      setResumeBusy(false);
    }
  };

  const unenroll = (e: SmsEnrollment) => {
    unenrollSms(campaign.id, e.id)
      .then(() => {
        onToast("Removed from campaign", "ok");
        refresh();
      })
      .catch((err) => onToast(err instanceof Error ? err.message : "Failed", "error"));
  };

  const columns: Column<SmsEnrollment>[] = [
    {
      key: "contact",
      header: "Contact",
      render: (e) => contactLabel(e.contact),
      sortValue: (e) => contactLabel(e.contact),
    },
    { key: "company", header: "Company", render: (e) => e.contact.company_name || "—" },
    {
      key: "source",
      header: "Source",
      render: (e) => enrollmentSourceLabel(e),
      sortValue: (e) => enrollmentSourceLabel(e),
    },
    {
      key: "status",
      header: "Status",
      render: (e) =>
        e.status === "active" && e.awaiting_reply ? (
          <Badge tone="info">awaiting reply</Badge>
        ) : (
          <Badge tone={e.status}>
            {e.status}
            {e.exit_reason ? ` (${e.exit_reason})` : ""}
          </Badge>
        ),
      sortValue: (e) => (e.awaiting_reply ? "awaiting" : e.status),
    },
    { key: "step", header: "Step", align: "right", render: (e) => int(e.current_position) },
    {
      key: "next",
      header: "Next send",
      render: (e) =>
        e.status === "active" && e.awaiting_reply
          ? "when they reply"
          : timeAgo(e.next_run_at),
    },
    {
      key: "lastreply",
      header: "Last reply",
      render: (e) =>
        e.last_reply_at ? (
          <span className="sms-last-reply" title={e.last_reply_body ?? undefined}>
            {timeAgo(e.last_reply_at)}
            {e.last_reply_body ? ` · “${e.last_reply_body.slice(0, 40)}${e.last_reply_body.length > 40 ? "…" : ""}”` : ""}
          </span>
        ) : (
          "—"
        ),
      sortValue: (e) => e.last_reply_at ?? "",
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
      <div className="sms-head">
        <p className="sms-sub">
          {int(campaign.enrolled)} enrolled · {int(campaign.active_enrollments)}{" "}
          currently active
          {campaign.awaiting_reply > 0
            ? ` · ${int(campaign.awaiting_reply)} awaiting a reply`
            : ""}
          .
        </p>
        <Button variant="primary" onClick={onEnrollClick}>
          <Plus size={16} />
          Enroll contacts
        </Button>
      </div>
      {resume !== null && resume.queued + resume.awaiting > 0 && (
        <Alert
          tone="info"
          title={`${int(resume.queued + resume.awaiting)} completed ${resume.queued + resume.awaiting === 1 ? "lead" : "leads"} finished before this campaign's newest steps`}
        >
          They went through the sequence as it existed at the time — steps
          added since (like a parting message) never reached them. Resuming
          sends {int(resume.queued)} of them the next scheduled step
          {resume.awaiting > 0
            ? ` and parks ${int(resume.awaiting)} at a reply step (no text sent — they're simply answerable again if they ever reply)`
            : ""}
          . Consent and STOP are re-checked per lead.
          <div className="sms-catchup-actions">
            {resumeConfirm ? (
              <>
                <Button variant="primary" size="sm" busy={resumeBusy} onClick={runResume}>
                  Resume {int(resume.queued + resume.awaiting)} {resume.queued + resume.awaiting === 1 ? "lead" : "leads"}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={resumeBusy}
                  onClick={() => setResumeConfirm(false)}
                >
                  Cancel
                </Button>
              </>
            ) : (
              <Button variant="ghost" size="sm" onClick={() => setResumeConfirm(true)}>
                Resume them…
              </Button>
            )}
          </div>
        </Alert>
      )}
      {catchUp !== null && catchUp.queued > 0 && (
        <Alert
          tone="info"
          title={`${int(catchUp.queued)} ${catchUp.queued === 1 ? "lead" : "leads"} replied before this campaign had reply handling`}
        >
          Their replies ended the sequence without an answer. Queue the reply
          step for them now — each response is matched to what that lead
          actually said (branches apply), consent and STOP are re-checked per
          lead, and sends stay inside the campaign&apos;s send window.
          <div className="sms-catchup-actions">
            {catchUpConfirm ? (
              <>
                <Button variant="primary" size="sm" busy={catchUpBusy} onClick={runCatchUp}>
                  Queue {int(catchUp.queued)} {catchUp.queued === 1 ? "response" : "responses"}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={catchUpBusy}
                  onClick={() => setCatchUpConfirm(false)}
                >
                  Cancel
                </Button>
              </>
            ) : (
              <Button variant="ghost" size="sm" onClick={() => setCatchUpConfirm(true)}>
                Respond to them…
              </Button>
            )}
          </div>
        </Alert>
      )}
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

const SKIP_LABELS: Record<SmsEnrollReceipt["skipped"][number]["reason"], string> = {
  no_number: "no phone number on file",
  no_consent: "no recorded SMS opt-in",
  suppressed: "on the suppression list",
  already: "already enrolled in this campaign",
};

function SKIP_SUMMARY(
  skipped: SmsEnrollReceipt["skipped"],
): [SmsEnrollReceipt["skipped"][number]["reason"], number][] {
  const counts = new Map<SmsEnrollReceipt["skipped"][number]["reason"], number>();
  for (const s of skipped) counts.set(s.reason, (counts.get(s.reason) ?? 0) + 1);
  return [...counts.entries()];
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
  const lists = useHouseContactLists(true);
  const [listId, setListId] = useState("");
  const [search, setSearch] = useState("");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [receipt, setReceipt] = useState<SmsEnrollReceipt | null>(null);

  const filtered = useMemo(() => {
    const list = contacts ?? [];
    const s = search.trim().toLowerCase();
    if (!s) return list;
    return list.filter((c) =>
      [c.first_name, c.last_name, c.phone, c.company_name]
        .filter(Boolean)
        .some((v) => v!.toLowerCase().includes(s)),
    );
  }, [contacts, search]);

  const selectedList = lists.find((l) => l.id === listId) ?? null;
  const allShownSelected =
    filtered.length > 0 && filtered.every((c) => picked.has(c.id));
  const overCap = !listId && picked.size > ENROLL_SELECT_CAP;

  const toggle = (id: string) => {
    setPicked((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleAllShown = (on: boolean) => {
    setPicked((cur) => {
      const next = new Set(cur);
      for (const c of filtered) {
        if (on) next.add(c.id);
        else next.delete(c.id);
      }
      return next;
    });
  };

  const submit = async () => {
    if (listId) {
      setBusy(true);
      try {
        const r = await enrollSmsContacts(campaignId, { list_id: listId });
        setReceipt(r);
        if (r.enrolled > 0) toast(`Enrolled ${r.enrolled}`, "ok");
      } catch (e) {
        toast(e instanceof Error ? e.message : "Enroll failed", "error");
      } finally {
        setBusy(false);
      }
      return;
    }
    if (picked.size === 0) {
      toast("Select at least one contact", "error");
      return;
    }
    if (overCap) {
      toast(`Selection exceeds ${ENROLL_SELECT_CAP} — enroll by list instead`, "error");
      return;
    }
    setBusy(true);
    try {
      const r = await enrollSmsContacts(campaignId, { contact_ids: [...picked] });
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
        <div className="sms-form">
          <p className="sms-hint">
            Enrolled <strong>{receipt.enrolled}</strong> contact
            {receipt.enrolled === 1 ? "" : "s"}.
          </p>
          {receipt.skipped.length > 0 && (
            <Alert tone="info" title={`${receipt.skipped.length} skipped`}>
              <ul className="sms-skip-list">
                {SKIP_SUMMARY(receipt.skipped).map(([reason, n]) => (
                  <li key={reason}>
                    {n} — {SKIP_LABELS[reason]}
                  </li>
                ))}
              </ul>
              {receipt.skipped.some((s) => s.reason === "no_consent") && (
                <p className="sms-hint">
                  SMS requires recorded opt-in (TCPA). Ways to record it:
                  check the attestation box on CSV import, turn on the
                  org-wide pre-opted-funnel default on the SMS Dashboard (only
                  if your intake forms genuinely collect SMS consent), or set
                  opt-in per lead from the contact drawer. STOP and
                  suppression always win regardless.
                </p>
              )}
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
          <Button variant="primary" busy={busy} disabled={overCap} onClick={submit}>
            {listId
              ? `Enroll list${selectedList ? ` (${selectedList.member_count})` : ""}`
              : `Enroll ${picked.size > 0 ? `(${picked.size})` : ""}`}
          </Button>
        </>
      }
    >
      <div className="sms-form">
        <Field label="Audience">
          <select
            className="select"
            aria-label="Audience"
            value={listId}
            onChange={(e) => setListId(e.target.value)}
          >
            <option value="">All contacts (house CRM)</option>
            {lists.map((l) => (
              <option key={l.id} value={l.id}>
                {l.name} ({l.member_count})
              </option>
            ))}
          </select>
        </Field>

        {!listId && (
          <>
            <input
              className="input"
              placeholder="Search house-CRM contacts…"
              aria-label="Search contacts"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
            {overCap && (
              <Alert tone="warn" title="Too many contacts selected">
                Selecting more than {ENROLL_SELECT_CAP} contacts at once isn't
                supported here — add them to a list instead and enroll the
                whole list.
              </Alert>
            )}
            {contacts === null ? (
              <SkeletonText lines={6} />
            ) : filtered.length === 0 ? (
              <EmptyState title="No contacts">
                Import leads into the house CRM (Lead Finder or CSV) to build an
                audience.
              </EmptyState>
            ) : (
              <>
                <label className="sms-pickrow sms-pickrow--all">
                  <input
                    type="checkbox"
                    checked={allShownSelected}
                    onChange={(e) => toggleAllShown(e.target.checked)}
                  />
                  <span className="sms-pickrow-name">
                    Select all ({filtered.length} shown)
                  </span>
                </label>
                <div className="sms-picklist">
                  {filtered.map((c) => (
                    <label key={c.id} className="sms-pickrow">
                      <input
                        type="checkbox"
                        checked={picked.has(c.id)}
                        onChange={() => toggle(c.id)}
                      />
                      <span className="sms-pickrow-name">{contactLabel(c)}</span>
                      <span className="sms-pickrow-phone">{c.phone || "no number"}</span>
                      {c.sms_opt_in === false && <Badge tone="warn">no opt-in</Badge>}
                      {c.sms_opt_in === true && <Badge tone="ok">opted in</Badge>}
                    </label>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </div>
    </Dialog>
  );
}

// ==========================================================================
// 3. Messages — conversation list keyed by contact (no threads)
// ==========================================================================

/** One conversation row — memoized so the 15s poll (which keeps the messages
 * array reference stable when nothing changed) skips unchanged rows. */
const ConversationListItem = memo(function ConversationListItem({
  id,
  contact,
  last,
  unread,
  isActive,
  onOpen,
}: {
  id: string;
  contact: SmsMessage["contact"];
  last: SmsMessage | undefined;
  unread: boolean;
  isActive: boolean;
  onOpen: (contactId: string) => void;
}) {
  return (
    <button
      type="button"
      className={[
        "sms-thread-item",
        isActive ? "sms-thread-item--active" : "",
        unread ? "sms-thread-item--unread" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      onClick={() => onOpen(id)}
    >
      <div className="sms-thread-top">
        <span className="sms-thread-name">
          <span>{contact ? contactLabel(contact) : "Unknown contact"}</span>
          {unread && <Badge tone="info">new</Badge>}
        </span>
        <time className="sms-thread-time" title={last?.sent_at ?? last?.received_at ?? undefined}>
          {timeAgo(last?.sent_at || last?.received_at || null)}
        </time>
      </div>
      <span className="sms-thread-snippet">{last?.body}</span>
    </button>
  );
});

function MessagesPanel({
  accounts,
  active = true,
}: {
  accounts: SmsAccount[];
  active?: boolean;
}) {
  const toast = useToast();
  const [messages, setMessages] = useState<SmsMessage[] | null>(null);
  const [selectedContactId, setSelectedContactId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [composing, setComposing] = useState(false);

  const refresh = useCallback(() => {
    // keepEqual: identical poll payloads keep the previous reference, so the
    // conversations memo (and every memoized row) stays untouched.
    listSmsMessages()
      .then((rows) => setMessages((prev) => keepEqual(prev, rows)))
      .catch(() => {});
  }, []);
  // Poll only while the view is actually visible; re-activating refreshes
  // immediately (the effect re-runs when `active` flips back to true).
  useEffect(() => {
    if (!active) return;
    refresh();
    const t = setInterval(refresh, 15_000);
    return () => clearInterval(t);
  }, [refresh, active]);

  // Group the flat, newest-first message log by contact — SMS has no
  // threads, so a "conversation" here is just every message with a given
  // contact, most recent first.
  const conversations = useMemo(() => {
    const byContact = new Map<string, { contact: SmsMessage["contact"]; messages: SmsMessage[] }>();
    for (const m of messages ?? []) {
      const key = m.contact?.id ?? "unknown";
      if (!byContact.has(key)) byContact.set(key, { contact: m.contact, messages: [] });
      byContact.get(key)!.messages.push(m);
    }
    return [...byContact.entries()].map(([id, v]) => ({ id, ...v }));
  }, [messages]);

  const selected = conversations.find((c) => c.id === selectedContactId) ?? conversations[0] ?? null;

  const hasUnread = (c: (typeof conversations)[number]) =>
    c.messages.some((m) => m.direction === "in" && !m.read_at);

  const open = useCallback(
    (contactId: string) => {
      setSelectedContactId(contactId);
      if (contactId !== "unknown") {
        markSmsRead(contactId)
          .then((r) => {
            if (r.marked > 0) refresh();
          })
          .catch(() => {});
      }
    },
    [refresh],
  );

  // Auto-mark-read the conversation that lands selected by default (the
  // most-recent one), same as opening it explicitly — otherwise it would
  // silently sit unread forever until someone clicks a different thread
  // first.
  useEffect(() => {
    if (selected && selected.id !== "unknown" && hasUnread(selected)) {
      markSmsRead(selected.id)
        .then((r) => {
          if (r.marked > 0) refresh();
        })
        .catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected?.id]);

  // Reply from the same number the conversation has been using so far —
  // falling back to the org's only/first connected number for a thread
  // that's somehow empty of outbound messages yet.
  const replyAccountId =
    selected?.messages.find((m) => m.account_id)?.account_id ?? accounts[0]?.id ?? "";

  const send = async () => {
    if (!selected || !draft.trim() || !replyAccountId) return;
    setSending(true);
    try {
      await composeSms({
        account_id: replyAccountId,
        contact_id: selected.id,
        body: draft.trim(),
      });
      setDraft("");
      refresh();
      toast("Sent", "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : "Send failed", "error");
    } finally {
      setSending(false);
    }
  };

  return (
    <div>
      <div className="sms-inbox-bar">
        <p className="sms-sub">
          Sent and received texts, grouped by contact — SMS has no threads or
          subjects, just a running log per person.
        </p>
        <Button
          variant="primary"
          className="sms-inbox-compose"
          disabled={accounts.length === 0}
          onClick={() => setComposing(true)}
        >
          <Plus size={16} />
          New message
        </Button>
      </div>

      <div className="sms-inbox">
        <GlassCard className="sms-threads">
          {messages === null && <SkeletonText lines={6} />}
          {messages !== null && conversations.length === 0 && (
            <EmptyState title="No conversations">
              Sent and received texts show up here — nothing yet.
            </EmptyState>
          )}
          {conversations.map((c) => (
            <ConversationListItem
              key={c.id}
              id={c.id}
              contact={c.contact}
              last={c.messages[0]}
              unread={hasUnread(c)}
              isActive={selected?.id === c.id}
              onOpen={open}
            />
          ))}
        </GlassCard>

        {selected ? (
          <GlassCard className="sms-thread-pane">
            <div className="sms-thread-head">
              <h3 className="sms-thread-title">
                {selected.contact ? contactLabel(selected.contact) : "Unknown contact"}
              </h3>
              <span className="sms-thread-with">{selected.contact?.phone ?? ""}</span>
            </div>
            <div className="sms-messages" aria-live="polite">
              {[...selected.messages].reverse().map((m) => (
                <div
                  key={m.id}
                  className={[
                    "sms-msg",
                    m.direction === "out" ? "sms-msg--out" : "sms-msg--in",
                  ].join(" ")}
                >
                  <div className="sms-msg-body">{m.body}</div>
                  <small className="sms-msg-meta">
                    {m.status}
                    {" · "}
                    {timeAgo(m.sent_at || m.received_at)}
                    {m.direction === "out" && m.read_at && (
                      <span className="sms-read">
                        {" · "}Read {timeAgo(m.read_at)}
                      </span>
                    )}
                  </small>
                </div>
              ))}
            </div>
            <div className="sms-composer">
              <Field label="Message">
                <textarea
                  rows={3}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="Type a message…"
                />
              </Field>
              <div className="sms-composer-actions">
                <Button
                  variant="primary"
                  busy={sending}
                  disabled={!draft.trim() || !replyAccountId}
                  onClick={send}
                >
                  <Send size={16} />
                  Send
                </Button>
              </div>
            </div>
          </GlassCard>
        ) : (
          <GlassCard className="sms-thread-pane">
            <EmptyState title="Select a conversation">
              Pick a contact to read the message history, or start a new one.
            </EmptyState>
          </GlassCard>
        )}
      </div>

      {accounts.length === 0 && (
        <div className="sms-redline">
          <Alert tone="info" title="No numbers connected">
            Connect a Twilio number to send and receive texts.
          </Alert>
        </div>
      )}

      {composing && (
        <ComposeSmsDialog
          accounts={accounts}
          onClose={() => setComposing(false)}
          onSent={(contactId) => {
            setComposing(false);
            setSelectedContactId(contactId);
            refresh();
          }}
        />
      )}
    </div>
  );
}

function ComposeSmsDialog({
  accounts,
  onClose,
  onSent,
}: {
  accounts: SmsAccount[];
  onClose: () => void;
  onSent: (contactId: string) => void;
}) {
  const toast = useToast();
  const contacts = useHouseContacts(true);
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [contactId, setContactId] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);

  const send = async () => {
    if (!accountId || !contactId || !body.trim()) {
      toast("Number, contact and message are required", "error");
      return;
    }
    setBusy(true);
    try {
      await composeSms({ account_id: accountId, contact_id: contactId, body: body.trim() });
      toast("Sent", "ok");
      onSent(contactId);
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
      title="New message"
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
      <div className="sms-form">
        <Field label="Send from number">
          <select value={accountId} onChange={(e) => setAccountId(e.target.value)}>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} — {a.from_number || a.messaging_service_sid}
              </option>
            ))}
          </select>
        </Field>
        <Field label="To (house-CRM contact)">
          <select value={contactId} onChange={(e) => setContactId(e.target.value)}>
            <option value="">Choose a contact…</option>
            {(contacts ?? []).map((c) => (
              <option key={c.id} value={c.id} disabled={!c.phone}>
                {contactLabel(c)}
                {c.phone ? ` · ${c.phone}` : " · no number"}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Message">
          <textarea rows={5} value={body} onChange={(e) => setBody(e.target.value)} />
        </Field>
        <p className="sms-hint">
          Sends immediately, as a one-off text — no compliance footer, no
          quiet-hours window (same as replying in a live conversation). The
          consent and suppression gates still apply.
        </p>
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
  accounts: SmsAccount[];
  onChanged: () => void;
}) {
  const toast = useToast();
  const [connecting, setConnecting] = useState(false);
  const [editing, setEditing] = useState<SmsAccount | null>(null);
  const [deleting, setDeleting] = useState<SmsAccount | null>(null);
  const [tested, setTested] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const test = async (a: SmsAccount) => {
    setTested((cur) => ({ ...cur, [a.id]: "Testing…" }));
    try {
      const r = await testSmsAccount(a.id);
      setTested((cur) => ({
        ...cur,
        [a.id]: `${r.ok ? "OK" : "FAIL"}${r.detail ? ` — ${r.detail}` : ""}`,
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
      await deleteSmsAccount(deleting.id);
      toast("Number removed", "ok");
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
    ? [{ field: deleting.name, oldValue: "connected", newValue: "removed" }]
    : [];

  return (
    <div>
      <div className="sms-head">
        <p className="sms-sub">
          Connect your own Twilio (SMS) or Sendblue (iMessage/SMS) account.
          Twilio long codes need A2P 10DLC brand + campaign registration —
          unregistered numbers get filtered or blocked by carriers.
        </p>
        <Button variant="primary" onClick={() => setConnecting(true)}>
          <Plus size={16} />
          Connect a number
        </Button>
      </div>

      {accounts.length === 0 ? (
        <GlassCard>
          <EmptyState title="No numbers connected">
            Connect a Twilio or Sendblue account to start sending SMS outreach.
          </EmptyState>
        </GlassCard>
      ) : (
        <div className="sms-account-grid">
          {accounts.map((a) => {
            const webhooks = smsWebhookUrls(a);
            return (
              <GlassCard key={a.id} className="sms-account">
                <div className="sms-account-top">
                  <div>
                    <div className="sms-account-name">
                      {a.name}{" "}
                      <Badge tone="neutral">
                        {a.provider === "bluebubbles"
                          ? "BlueBubbles"
                          : a.provider === "sendblue"
                            ? "Sendblue"
                            : "Twilio"}
                      </Badge>{" "}
                      {a.channel_health && (
                        <Badge
                          tone={
                            a.channel_health.status === "healthy"
                              ? "ok"
                              : a.channel_health.status === "degraded"
                                ? "warn"
                                : "danger"
                          }
                        >
                          {a.channel_health.status}
                        </Badge>
                      )}
                    </div>
                    <div className="sms-account-number">
                      {a.from_number || a.messaging_service_sid || "—"}
                    </div>
                  </div>
                  <Badge tone={a.status === "active" ? "ok" : "danger"}>
                    {a.status === "active" ? "connected" : "error"}
                  </Badge>
                </div>

                {a.status === "error" && a.error_detail && (
                  <Alert tone="danger">{a.error_detail}</Alert>
                )}

                {a.provider === "twilio" && a.status === "active" && (
                  // "connected" only proves the credentials — carriers still
                  // filter unregistered long-code traffic silently, so the
                  // reminder stays on the card, not just the connect dialog.
                  <Alert tone="warn" title="A2P 10DLC">
                    Connected ≠ registered: without an approved A2P 10DLC
                    Brand + Campaign in the Twilio Console, carriers silently
                    filter this number's messages. Verify registration before
                    sending volume.
                  </Alert>
                )}

                <div className="sms-account-stat">
                  <span>
                    {int(a.sends_today)} of {int(a.daily_send_cap)} sent today
                  </span>
                </div>

                <div className="sms-webhooks">
                  <p className="sms-hint">
                    {a.provider === "bluebubbles" ? (
                      <>
                        Point your BlueBubbles VPS relay's webhook at this one
                        URL — it carries both inbound messages and
                        delivery/read updates.
                      </>
                    ) : a.provider === "sendblue" ? (
                      <>
                        Add these as webhooks in your Sendblue dashboard (the
                        URL already carries this account's secret token).
                      </>
                    ) : (
                      <>
                        Paste these into this number's (or Messaging Service's)
                        Twilio configuration.
                      </>
                    )}{" "}
                    <strong>Inbound is required</strong> — without it, STOP/HELP
                    replies never reach us and opt-outs can't be honored.
                  </p>
                  {a.provider === "bluebubbles" ? (
                    <>
                      <WebhookRow label="Inbound webhook" url={webhooks.inbound} />
                      <WebhookRow label="Secret" url={a.webhook_token ?? ""} />
                      <p className="sms-hint">
                        Send it as header{" "}
                        <code>X-Salescale-Webhook-Secret: &lt;secret&gt;</code>{" "}
                        (the VPS relay injects it) — or append{" "}
                        <code>?secret=&lt;secret&gt;</code> to the URL.
                      </p>
                    </>
                  ) : (
                    <>
                      <WebhookRow label="Inbound" url={webhooks.inbound} />
                      <WebhookRow label="Status" url={webhooks.status} />
                    </>
                  )}
                </div>

                {tested[a.id] && <div className="sms-test-result">{tested[a.id]}</div>}

                <div className="sms-account-actions">
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
            );
          })}
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
        title="Remove number"
        confirmLabel="Remove number"
        cancelLabel="Keep it"
        busy={busy}
      >
        <p className="sms-hint">
          Campaigns still using this number will block removal. Reassign them
          first.
        </p>
      </ConfirmDialog>
    </div>
  );
}

function WebhookRow({ label, url }: { label: string; url: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="sms-webhook-row">
      <span className="sms-webhook-label">{label}</span>
      <code className="sms-webhook-uri">{url}</code>
      <Button
        size="sm"
        variant="ghost"
        onClick={() => {
          void navigator.clipboard.writeText(url);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
      >
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}

function AccountDialog({
  existing,
  onClose,
  onSaved,
}: {
  existing?: SmsAccount;
  onClose: () => void;
  onSaved: () => void;
}) {
  const toast = useToast();
  const [provider, setProvider] = useState<SmsProvider>(
    existing?.provider ?? "twilio",
  );
  const [name, setName] = useState(existing?.name ?? "");
  const [accountSid, setAccountSid] = useState(existing?.account_sid ?? "");
  const [authToken, setAuthToken] = useState("");
  const [fromNumber, setFromNumber] = useState(existing?.from_number ?? "");
  const [messagingServiceSid, setMessagingServiceSid] = useState(
    existing?.messaging_service_sid ?? "",
  );
  const [relayUrl, setRelayUrl] = useState(existing?.relay_url ?? "");
  const [forceSms, setForceSms] = useState(
    existing?.bluebubbles_force_sms ?? false,
  );
  const [minSendSpacing, setMinSendSpacing] = useState(
    existing?.min_send_spacing_seconds != null
      ? String(existing.min_send_spacing_seconds)
      : "",
  );
  const [maxSendSpacing, setMaxSendSpacing] = useState(
    existing?.max_send_spacing_seconds != null
      ? String(existing.max_send_spacing_seconds)
      : "",
  );
  const [dailyCap, setDailyCap] = useState(
    existing ? String(existing.daily_send_cap) : "200",
  );
  const [busy, setBusy] = useState(false);

  const isEdit = Boolean(existing);
  const isSendblue = provider === "sendblue";
  const isBluebubbles = provider === "bluebubbles";
  // Provider changes only at create time; editing keeps the stored provider.
  const sidLabel = isSendblue ? "API Key ID" : "Account SID";
  const secretLabel = isBluebubbles
    ? "Server password"
    : isSendblue
      ? "API Secret Key"
      : "Auth token";

  const save = async () => {
    if (!name.trim()) {
      toast("A label is required", "error");
      return;
    }
    if (isBluebubbles) {
      if (!relayUrl.trim()) {
        toast("A BlueBubbles relay URL is required", "error");
        return;
      }
      if (!fromNumber.trim()) {
        toast("An iMessage sending number is required", "error");
        return;
      }
    } else {
      if (!accountSid.trim()) {
        toast(`${sidLabel} is required`, "error");
        return;
      }
      if (isSendblue) {
        if (!fromNumber.trim()) {
          toast("A Sendblue sending number is required", "error");
          return;
        }
      } else if (!fromNumber.trim() && !messagingServiceSid.trim()) {
        toast("Provide a from number or a Messaging Service SID", "error");
        return;
      }
    }
    if (!isEdit && !authToken) {
      toast(`A ${secretLabel} is required to connect`, "error");
      return;
    }
    const base: SmsAccountBody = {
      name: name.trim(),
      // BlueBubbles has no meaningful SID — the backend fills a placeholder.
      account_sid: isBluebubbles ? null : accountSid.trim(),
      from_number: fromNumber.trim() || null,
      messaging_service_sid:
        isBluebubbles || isSendblue ? null : messagingServiceSid.trim() || null,
      relay_url: isBluebubbles ? relayUrl.trim() : null,
      bluebubbles_force_sms: isBluebubbles ? forceSms : false,
      min_send_spacing_seconds:
        isBluebubbles && minSendSpacing.trim() ? Number(minSendSpacing) : null,
      max_send_spacing_seconds:
        isBluebubbles && maxSendSpacing.trim() ? Number(maxSendSpacing) : null,
      daily_send_cap: Number(dailyCap),
    };
    if (
      isBluebubbles &&
      minSendSpacing.trim() &&
      maxSendSpacing.trim() &&
      Number(maxSendSpacing) < Number(minSendSpacing)
    ) {
      toast("Max seconds between sends must be ≥ the minimum", "error");
      setBusy(false);
      return;
    }
    if (authToken) base.auth_token = authToken;
    setBusy(true);
    try {
      if (existing) await updateSmsAccount(existing.id, base);
      else
        await createSmsAccount({
          ...base,
          provider,
          name: name.trim(),
          auth_token: authToken,
        } as SmsAccountBody & {
          name: string;
          account_sid?: string | null;
          auth_token: string;
        });
      toast(isEdit ? "Number updated" : "Number connected", "ok");
      onSaved();
    } catch (e) {
      // 400 detail on a failed provider auth probe — surface verbatim.
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
      title={
        isEdit
          ? `Edit ${existing!.name}`
          : `Connect ${isBluebubbles ? "BlueBubbles" : isSendblue ? "Sendblue" : "Twilio"}`
      }
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
      <div className="sms-form">
        {!isEdit && (
          <Field label="Provider">
            <Segmented
              ariaLabel="SMS provider"
              options={[
                { value: "twilio", label: "Twilio (SMS)" },
                { value: "sendblue", label: "Sendblue (iMessage/SMS)" },
                // Self-hosted dev path — operator-allowlisted orgs only
                // (server enforces the same gate on account creation).
                ...(featureEnabled("bluebubbles")
                  ? [{ value: "bluebubbles", label: "BlueBubbles (dev)" }]
                  : []),
              ]}
              value={provider}
              onChange={(v) => setProvider(v as SmsProvider)}
            />
          </Field>
        )}
        <Field label="Label">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Atlas primary" />
        </Field>
        {isBluebubbles ? (
          <Field
            label="Relay URL"
            description="Self-hosted BlueBubbles via your VPS relay — dev/prototype path."
          >
            <input
              value={relayUrl}
              onChange={(e) => setRelayUrl(e.target.value)}
              placeholder="https://relay.example.com"
            />
          </Field>
        ) : (
          <Field label={sidLabel}>
            <input
              value={accountSid}
              onChange={(e) => setAccountSid(e.target.value)}
              placeholder={isSendblue ? "your Sendblue API Key ID" : "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"}
            />
          </Field>
        )}
        <Field
          label={secretLabel}
          description={isEdit ? "Leave blank to keep the current secret." : undefined}
        >
          <input
            type="password"
            value={authToken}
            onChange={(e) => setAuthToken(e.target.value)}
            autoComplete="new-password"
          />
        </Field>

        <div className="sms-fieldset">
          <span className="field-label">Send from</span>
          <div className="sms-form-row">
            <Field
              label={
                isBluebubbles
                  ? "iMessage number"
                  : isSendblue
                    ? "Sendblue number"
                    : "From number"
              }
              description="A sending number in E.164 format."
              optional={!isSendblue && !isBluebubbles}
            >
              <input
                value={fromNumber}
                onChange={(e) => setFromNumber(e.target.value)}
                placeholder="+15555550123"
              />
            </Field>
            {!isSendblue && !isBluebubbles && (
              <Field label="Messaging Service SID" optional>
                <input
                  value={messagingServiceSid}
                  onChange={(e) => setMessagingServiceSid(e.target.value)}
                  placeholder="MGxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
                />
              </Field>
            )}
          </div>
          <p className="sms-hint">
            {isBluebubbles
              ? "The iMessage-registered number this relay sends from."
              : isSendblue
                ? "Sendblue assigns you a dedicated number — enter it here."
                : "Provide either a from number or a Messaging Service SID."}
          </p>
        </div>

        {isBluebubbles && (
          <div className="sms-form-row">
            <Field
              label="Min seconds between sends"
              optional
              description="Paces automated campaign sends so this Mac/Apple ID isn't flagged for machine-gun texting — each gap is a random point between Min and Max (live 1:1 replies are never throttled). Leave both blank for the recommended 20–45s range; set both to 0 to disable."
            >
              <input
                type="number"
                min={0}
                value={minSendSpacing}
                onChange={(e) => setMinSendSpacing(e.target.value)}
                placeholder="20"
              />
            </Field>
            <Field label="Max seconds between sends" optional>
              <input
                type="number"
                min={0}
                value={maxSendSpacing}
                onChange={(e) => setMaxSendSpacing(e.target.value)}
                placeholder="45"
              />
            </Field>
          </div>
        )}

        {isBluebubbles && (
          <div className="sms-fieldset">
            <Switch
              checked={forceSms}
              onChange={setForceSms}
              label="Send as SMS only (no iMessage)"
            />
            <p className="sms-hint">
              Turn this on for a host that can't send iMessage — notably an AWS
              EC2 Mac, where the Private API is unavailable and Apple blocks
              datacenter iMessage, so an iMessage send is accepted but silently
              never delivers. With this on, every message goes out as
              green-bubble SMS through the Mac's Text Message Forwarding (which
              still reaches iMessage users). Leave off for a normal Mac that
              sends iMessage.
            </p>
          </div>
        )}

        <Field label="Daily send cap" optional>
          <input
            type="number"
            min={1}
            value={dailyCap}
            onChange={(e) => setDailyCap(e.target.value)}
          />
        </Field>

        {isBluebubbles ? (
          <Alert tone="warn" title="Consent still required">
            BlueBubbles sends over iMessage and does not auto-handle STOP —
            Salescale records opt-outs from inbound replies, so the inbound
            webhook below is mandatory. TCPA consent rules apply exactly as
            they do for SMS: only opted-in contacts are ever messaged.
          </Alert>
        ) : isSendblue ? (
          <Alert tone="warn" title="Consent still required">
            Sendblue sends over iMessage/SMS and does not auto-handle STOP —
            Salescale records opt-outs from inbound replies, so the inbound
            webhook below is mandatory. TCPA consent rules apply exactly as
            they do for SMS: only opted-in contacts are ever messaged.
          </Alert>
        ) : (
          <Alert tone="warn" title="A2P 10DLC registration required">
            Carriers filter or block unregistered traffic on long-code numbers.
            Register a Brand and Campaign for this number in the Twilio Console
            (Messaging → Regulatory Compliance → A2P 10DLC) before sending real
            outreach volume — this can take a few business days for approval.
          </Alert>
        )}
      </div>
    </Dialog>
  );
}

// ==========================================================================
// 5. Suppression
// ==========================================================================

function SuppressionPanel() {
  const toast = useToast();
  const [rows, setRows] = useState<SmsSuppression[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [phone, setPhone] = useState("");
  const [detail, setDetail] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    listSmsSuppression().then(setRows).catch(() => {});
  }, []);
  useEffect(refresh, [refresh]);

  const add = async () => {
    if (!phone.trim()) {
      toast("A phone number is required", "error");
      return;
    }
    setBusy(true);
    try {
      await addSmsSuppression(phone.trim(), detail.trim() || undefined);
      toast("Added to suppression list", "ok");
      setAdding(false);
      setPhone("");
      setDetail("");
      refresh();
    } catch (e) {
      toast(e instanceof Error ? e.message : "Failed", "error");
    } finally {
      setBusy(false);
    }
  };

  const remove = (r: SmsSuppression) => {
    deleteSmsSuppression(r.id)
      .then(() => {
        toast("Removed from suppression", "ok");
        refresh();
      })
      .catch((e) => toast(e instanceof Error ? e.message : "Failed", "error"));
  };

  const columns: Column<SmsSuppression>[] = [
    { key: "phone", header: "Phone", render: (r) => r.phone_e164, sortValue: (r) => r.phone_e164 },
    {
      key: "reason",
      header: "Reason",
      render: (r) => <Badge tone="neutral">{r.reason}</Badge>,
      sortValue: (r) => r.reason,
    },
    { key: "detail", header: "Detail", render: (r) => r.detail || "—" },
    {
      key: "created",
      header: "Added",
      render: (r) => <time title={r.created_at}>{timeAgo(r.created_at)}</time>,
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
      <div className="sms-head">
        <p className="sms-sub">
          Numbers here are never texted again. STOP replies land on this list
          automatically and exit every active campaign org-wide — add numbers
          manually to pre-empt contact you already know is off-limits.
        </p>
        <Button variant="primary" onClick={() => setAdding(true)}>
          <Plus size={16} />
          Add number
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
        <div className="sms-form">
          <Field label="Phone number">
            <input
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="+15555550123"
            />
          </Field>
          <Field label="Note" optional>
            <input
              value={detail}
              onChange={(e) => setDetail(e.target.value)}
              placeholder="Requested no contact via email"
            />
          </Field>
        </div>
      </Dialog>
    </div>
  );
}
