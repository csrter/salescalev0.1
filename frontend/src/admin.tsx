import { useEffect, useState } from "react";
import {
  ORG_PLANS,
  addMember,
  adminOrg,
  adminOrgs,
  adminSignups,
  adminStats,
  getSeatUsage,
  listInvites,
  listMembers,
  listMembershipAudit,
  removeMember,
  resendInvite,
  resetUserPassword,
  revokeInvite,
  sendInvite,
  transferOwnership,
  updateMember,
  updateOrg,
  type AdminOrgDetail,
  type AdminOrgRow,
  type AdminSignupPoint,
  type AdminStats,
  type Invite,
  type MembershipAuditEntry,
  type OrgPlan,
  type Role,
  type SeatUsage,
  type Session,
  type TeamMember,
} from "./api";
import { DataTable, type Column } from "./components/DataTable";
import { ConfirmDialog, type ReceiptRow } from "./components/Dialog";
import { LineChart } from "./components/charts";
import { ChevronLeft } from "./components/icons";
import {
  Alert,
  Badge,
  Button,
  Field,
  Kpi,
  KpiGrid,
  KpiSkeleton,
} from "./components/ui";
import "./styles/views/admin.css";

/* ---------------- shared helpers ---------------- */

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [done, setDone] = useState(false);
  return (
    <Button
      size="sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setDone(true);
          setTimeout(() => setDone(false), 1500);
        } catch {
          /* clipboard unavailable — the value stays selectable inline */
        }
      }}
    >
      {done ? "Copied" : label}
    </Button>
  );
}

/** Staged confirmation for a single admin action (rendered via ConfirmDialog). */
interface AdminConfirm {
  title: string;
  tone: "warn" | "danger";
  confirmLabel: string;
  rows: ReceiptRow[];
  run: () => Promise<unknown>;
}

/* ---------------- Platform super-admin (cross-tenant) ---------------- */

export function SuperAdmin() {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [orgs, setOrgs] = useState<AdminOrgRow[]>([]);
  const [signups, setSignups] = useState<AdminSignupPoint[]>([]);
  const [selected, setSelected] = useState<AdminOrgRow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    adminStats().then(setStats).catch((e) => setError(e.message));
    adminOrgs()
      .then(setOrgs)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
    adminSignups(30).then(setSignups).catch((e) => setError(e.message));
  };
  useEffect(load, []);

  if (selected)
    return (
      <OrgDetail
        org={selected}
        onBack={() => {
          setSelected(null);
          load();
        }}
      />
    );

  const cards: [string, number | undefined][] = [
    ["Organizations", stats?.organizations],
    ["Users", stats?.users],
    ["Clients", stats?.clients],
    ["Active connections", stats?.active_connections],
    ["Signups (30d)", stats?.signups_last_30d],
  ];

  const total = signups.reduce((s, p) => s + p.count, 0);

  return (
    <div className="adm-view">
      <div className="adm-head">
        <div>
          <h2>Platform admin</h2>
          <p className="adm-sub">Cross-tenant overview of every organization.</p>
        </div>
      </div>

      {error && <Alert tone="danger">{error}</Alert>}

      {stats ? (
        <KpiGrid>
          {cards.map(([label, value]) => (
            <Kpi key={label} label={label} value={value ?? 0} />
          ))}
        </KpiGrid>
      ) : (
        <KpiGrid>
          {cards.map(([label]) => (
            <KpiSkeleton key={label} />
          ))}
        </KpiGrid>
      )}

      <div>
        <h3 className="adm-section-title">Signups — last 30 days</h3>
        <div className="card">
          {signups.length > 0 ? (
            <>
              <LineChart
                labels={signups.map((p) => p.date)}
                series={[{ name: "Signups", data: signups.map((p) => p.count) }]}
                height={160}
                ariaLabel={`Daily signups over the last ${signups.length} days`}
              />
              <p className="adm-chart-cap">
                {signups[0].date} → {signups[signups.length - 1].date} · {total} total
              </p>
            </>
          ) : (
            <p className="adm-sub">No data yet.</p>
          )}
        </div>
      </div>

      <div>
        <h3 className="adm-section-title">Organizations</h3>
        <DataTable<AdminOrgRow>
          rows={orgs}
          rowKey={(o) => o.id}
          onRowClick={(o) => setSelected(o)}
          loading={loading}
          initialSort="-created"
          emptyMessage="No organizations yet."
          columns={[
            {
              key: "name",
              header: "Organization",
              render: (o) => o.name,
              sortValue: (o) => o.name,
            },
            {
              key: "plan",
              header: "Plan",
              render: (o) => <Badge tone="neutral">{o.plan}</Badge>,
              sortValue: (o) => o.plan,
            },
            {
              key: "status",
              header: "Status",
              render: (o) => <Badge tone={o.status}>{o.status}</Badge>,
              sortValue: (o) => o.status,
            },
            {
              key: "users",
              header: "Users",
              align: "right",
              render: (o) => o.user_count,
              sortValue: (o) => o.user_count,
            },
            {
              key: "clients",
              header: "Clients",
              align: "right",
              render: (o) => o.client_count,
              sortValue: (o) => o.client_count,
            },
            {
              key: "conns",
              header: "Conns",
              align: "right",
              render: (o) => o.connection_count,
              sortValue: (o) => o.connection_count,
            },
            {
              key: "contacts",
              header: "Contacts",
              align: "right",
              render: (o) => o.contact_count,
              sortValue: (o) => o.contact_count,
            },
            {
              key: "created",
              header: "Created",
              render: (o) => new Date(o.created_at).toLocaleDateString(),
              sortValue: (o) => o.created_at,
            },
          ]}
        />
      </div>
    </div>
  );
}

function OrgDetail({ org, onBack }: { org: AdminOrgRow; onBack: () => void }) {
  const [detail, setDetail] = useState<AdminOrgDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [secret, setSecret] = useState<{ email: string; password: string } | null>(
    null,
  );
  const [confirm, setConfirm] = useState<AdminConfirm | null>(null);
  const [busy, setBusy] = useState(false);

  const load = () =>
    adminOrg(org.id).then(setDetail).catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, [org.id]);

  const runConfirm = async () => {
    if (!confirm) return;
    setError(null);
    setBusy(true);
    try {
      await confirm.run();
      setConfirm(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const suspended = detail?.status === "suspended";

  const askSuspend = () => {
    if (!detail) return;
    setConfirm({
      title: suspended ? "Reactivate organization" : "Suspend organization",
      tone: suspended ? "warn" : "danger",
      confirmLabel: suspended ? "Reactivate org" : "Suspend org",
      rows: [
        {
          field: `${org.name} · status`,
          oldValue: detail.status,
          newValue: suspended ? "active" : "suspended",
        },
      ],
      run: async () => {
        await updateOrg(org.id, { status: suspended ? "active" : "suspended" });
        await load();
      },
    });
  };

  const askPlan = (next: OrgPlan) => {
    if (!detail || next === detail.plan) return;
    setConfirm({
      title: "Change plan",
      tone: "warn",
      confirmLabel: `Change to ${next}`,
      rows: [{ field: `${org.name} · plan`, oldValue: detail.plan, newValue: next }],
      run: async () => {
        await updateOrg(org.id, { plan: next });
        await load();
      },
    });
  };

  const askReset = (user: { id: string; email: string }) => {
    setSecret(null);
    setConfirm({
      title: "Reset password",
      tone: "warn",
      confirmLabel: "Reset password",
      rows: [
        {
          field: `${user.email} · password`,
          oldValue: "current",
          newValue: "new temporary",
        },
      ],
      run: async () => {
        const r = await resetUserPassword(user.id);
        setSecret({ email: r.email, password: r.temporary_password });
      },
    });
  };

  const userCols: Column<AdminOrgDetail["users"][number]>[] = [
    { key: "name", header: "Name", render: (u) => u.full_name, sortValue: (u) => u.full_name },
    { key: "email", header: "Email", render: (u) => u.email, sortValue: (u) => u.email },
    { key: "role", header: "Role", render: (u) => <Badge tone="neutral">{u.role}</Badge> },
    {
      key: "status",
      header: "Status",
      render: (u) => (
        <Badge tone={u.is_active ? "ok" : "neutral"}>
          {u.is_active ? "active" : "inactive"}
        </Badge>
      ),
    },
    {
      key: "actions",
      header: "Actions",
      render: (u) => (
        <Button size="sm" onClick={() => askReset(u)}>
          Reset password
        </Button>
      ),
    },
  ];

  return (
    <div className="adm-view">
      <div className="adm-back">
        <Button variant="link" onClick={onBack}>
          <ChevronLeft size={16} /> All organizations
        </Button>
      </div>

      <div className="adm-org-head">
        <h2>{org.name}</h2>
        {detail && (
          <>
            <Badge tone={suspended ? "danger" : "ok"}>{detail.status}</Badge>
            <div className="adm-plan-field">
              <Field label="Plan">
                <select
                  className="select"
                  value={detail.plan}
                  onChange={(e) => askPlan(e.target.value as OrgPlan)}
                >
                  {ORG_PLANS.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            <Button
              variant={suspended ? "default" : "danger-outline"}
              onClick={askSuspend}
            >
              {suspended ? "Reactivate org" : "Suspend org"}
            </Button>
          </>
        )}
      </div>

      {error && <Alert tone="danger">{error}</Alert>}

      {secret && (
        <Alert tone="warn" title="Temporary password">
          <p>
            Share it securely with <strong>{secret.email}</strong> — it won't be
            shown again.
          </p>
          <div className="adm-secret">
            <code>{secret.password}</code>
            <CopyButton text={secret.password} />
            <Button variant="ghost" size="sm" onClick={() => setSecret(null)}>
              Dismiss
            </Button>
          </div>
        </Alert>
      )}

      <div>
        <h3 className="adm-section-title">Users</h3>
        <DataTable<AdminOrgDetail["users"][number]>
          rows={detail?.users ?? []}
          rowKey={(u) => u.id}
          loading={!detail}
          emptyMessage="No users."
          columns={userCols}
        />
      </div>

      <div>
        <h3 className="adm-section-title">Clients</h3>
        <DataTable<AdminOrgDetail["clients"][number]>
          rows={detail?.clients ?? []}
          rowKey={(c) => c.id}
          loading={!detail}
          emptyMessage="No clients."
          columns={[
            { key: "name", header: "Client", render: (c) => c.name, sortValue: (c) => c.name },
            {
              key: "status",
              header: "Status",
              render: (c) => <Badge tone={c.status}>{c.status}</Badge>,
            },
          ]}
        />
      </div>

      <ConfirmDialog
        open={confirm != null}
        onCancel={() => setConfirm(null)}
        onConfirm={runConfirm}
        rows={confirm?.rows ?? []}
        title={confirm?.title ?? ""}
        tone={confirm?.tone ?? "warn"}
        confirmLabel={confirm?.confirmLabel}
        cancelLabel="Cancel"
        busy={busy}
      />
    </div>
  );
}

/* ---------------- Org admin console: team management ---------------- */

const INVITE_TONE: Record<Invite["status"], "info" | "ok" | "neutral" | "warn"> = {
  pending: "info",
  accepted: "ok",
  revoked: "neutral",
  expired: "warn",
};

export function TeamAdmin({
  session,
  onGoToBilling,
}: {
  session: Session;
  onGoToBilling?: () => void;
}) {
  const [members, setMembers] = useState<TeamMember[] | null>(null);
  const [invites, setInvites] = useState<Invite[] | null>(null);
  const [seats, setSeats] = useState<SeatUsage | null>(null);
  const [audit, setAudit] = useState<MembershipAuditEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<{
    title: string;
    rows: ReceiptRow[];
    confirmLabel: string;
    run: () => Promise<void>;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const isOwner = session.role === "owner";

  const load = () =>
    Promise.all([
      listMembers().then(setMembers),
      listInvites().then(setInvites),
      getSeatUsage().then(setSeats),
      listMembershipAudit().then(setAudit),
    ]).catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const patch = (id: string, body: { role?: "admin" | "member"; is_active?: boolean }) =>
    act(() => updateMember(id, body));

  const atLimit =
    seats?.limit != null && seats.used + seats.pending_invites >= seats.limit;

  const columns: Column<TeamMember>[] = [
    { key: "name", header: "Name", render: (m) => m.full_name, sortValue: (m) => m.full_name },
    { key: "email", header: "Email", render: (m) => m.email, sortValue: (m) => m.email },
    { key: "role", header: "Role", render: (m) => <Badge tone="neutral">{m.role}</Badge> },
    {
      key: "status",
      header: "Status",
      render: (m) => (
        <Badge tone={m.is_active ? "ok" : "neutral"}>
          {m.is_active ? "active" : "inactive"}
        </Badge>
      ),
    },
  ];
  columns.push({
    key: "actions",
    header: "Actions",
    render: (m) => {
      // Owner rows change only via explicit ownership transfer; client
      // portal users are managed from the client surface.
      if (m.role === "owner" || m.role === "client")
        return <span className="adm-sub">—</span>;
      return (
        <div className="adm-actions">
          {isOwner &&
            (m.role === "member" ? (
              <Button size="sm" onClick={() => patch(m.id, { role: "admin" })}>
                Make admin
              </Button>
            ) : (
              <Button size="sm" onClick={() => patch(m.id, { role: "member" })}>
                Make member
              </Button>
            ))}
          {isOwner &&
            (m.is_active ? (
              <Button size="sm" onClick={() => patch(m.id, { is_active: false })}>
                Deactivate
              </Button>
            ) : (
              <Button size="sm" onClick={() => patch(m.id, { is_active: true })}>
                Reactivate
              </Button>
            ))}
          {isOwner && m.is_active && (
            <Button
              size="sm"
              onClick={() =>
                setConfirm({
                  title: `Transfer ownership to ${m.full_name}?`,
                  confirmLabel: "Transfer ownership",
                  rows: [
                    {
                      field: "Owner",
                      oldValue: session.full_name,
                      newValue: `${m.full_name} (${m.email})`,
                    },
                    { field: "Your role", oldValue: "owner", newValue: "admin" },
                  ],
                  run: () => transferOwnership(m.id).then(() => {}),
                })
              }
            >
              Make owner
            </Button>
          )}
          {(isOwner || m.role === "member") && (
            <Button
              size="sm"
              variant="danger-outline"
              onClick={() =>
                setConfirm({
                  title: `Remove ${m.full_name} from the team?`,
                  confirmLabel: "Remove member",
                  rows: [
                    {
                      field: "Membership",
                      oldValue: `${m.full_name} (${m.email})`,
                      newValue: "removed",
                    },
                    {
                      field: "Their sessions",
                      oldValue: "active",
                      newValue: "ended immediately",
                    },
                    {
                      field: "Their open tasks",
                      oldValue: m.full_name,
                      newValue: "reassigned to you",
                    },
                  ],
                  run: () => removeMember(m.id).then(() => {}),
                })
              }
            >
              Remove
            </Button>
          )}
        </div>
      );
    },
  });

  const inviteColumns: Column<Invite>[] = [
    { key: "email", header: "Email", render: (i) => i.email, sortValue: (i) => i.email },
    { key: "role", header: "Role", render: (i) => <Badge tone="neutral">{i.role}</Badge> },
    {
      key: "status",
      header: "Status",
      render: (i) => <Badge tone={INVITE_TONE[i.status]}>{i.status}</Badge>,
    },
    {
      key: "expires",
      header: "Expires",
      render: (i) =>
        i.status === "pending" ? new Date(i.expires_at).toLocaleDateString() : "—",
      sortValue: (i) => i.expires_at,
    },
    {
      key: "actions",
      header: "Actions",
      render: (i) => (
        <div className="adm-actions">
          {(i.status === "pending" || i.status === "expired") && (
            <Button size="sm" onClick={() => act(() => resendInvite(i.id))}>
              Resend
            </Button>
          )}
          {i.status === "pending" && (
            <Button
              size="sm"
              variant="danger-outline"
              onClick={() => act(() => revokeInvite(i.id))}
            >
              Revoke
            </Button>
          )}
        </div>
      ),
    },
  ];

  return (
    <div className="adm-view">
      <div className="adm-head">
        <div>
          <h2>Team</h2>
          <p className="adm-sub">Manage who can access your organization.</p>
        </div>
      </div>

      {error && <Alert tone="danger">{error}</Alert>}

      <KpiGrid>
        {seats ? (
          <>
            <Kpi
              label="Seats used"
              value={`${seats.used} / ${seats.limit ?? "∞"}`}
            />
            <Kpi label="Pending invites" value={String(seats.pending_invites)} />
          </>
        ) : (
          <>
            <KpiSkeleton />
            <KpiSkeleton />
          </>
        )}
      </KpiGrid>

      {atLimit && (
        <Alert tone="info" title={`All ${seats!.limit} seats on the ${seats!.plan} plan are taken`}>
          Pending invites reserve seats too. Free a seat, or upgrade to invite
          more people.{" "}
          {onGoToBilling && (
            <Button variant="link" onClick={onGoToBilling}>
              View plans
            </Button>
          )}
        </Alert>
      )}

      <DataTable<TeamMember>
        rows={members ?? []}
        rowKey={(m) => m.id}
        loading={members == null}
        emptyMessage="No team members yet."
        columns={columns}
      />

      <InviteForm session={session} disabled={!!atLimit} onSent={load} />

      {invites != null && invites.length > 0 && (
        <>
          <h3 className="adm-section-title">Invites</h3>
          <DataTable<Invite>
            rows={invites}
            rowKey={(i) => i.id}
            columns={inviteColumns}
            initialSort="-expires"
          />
        </>
      )}

      <AddMemberForm session={session} onAdded={load} />

      {audit != null && audit.length > 0 && (
        <>
          <h3 className="adm-section-title">Membership activity</h3>
          <ul className="adm-audit">
            {audit.map((a) => (
              <li key={a.id}>
                <span className="adm-audit-time">
                  {new Date(a.created_at).toLocaleString()}
                </span>{" "}
                <strong>{a.actor_name}</strong> —{" "}
                {a.action.replace(/_/g, " ")}
                {a.target_email ? ` · ${a.target_email}` : ""}
              </li>
            ))}
          </ul>
        </>
      )}

      <ConfirmDialog
        open={confirm != null}
        onCancel={() => setConfirm(null)}
        onConfirm={async () => {
          if (!confirm) return;
          setBusy(true);
          try {
            await confirm.run();
            setConfirm(null);
            await load();
          } catch (e) {
            setError((e as Error).message);
            setConfirm(null);
          } finally {
            setBusy(false);
          }
        }}
        rows={confirm?.rows ?? []}
        title={confirm?.title ?? ""}
        tone="warn"
        confirmLabel={confirm?.confirmLabel}
        cancelLabel="Cancel"
        busy={busy}
      />
    </div>
  );
}

/** Primary path: email an invite; the recipient sets their own password. */
function InviteForm({
  session,
  disabled,
  onSent,
}: {
  session: Session;
  disabled: boolean;
  onSent: () => void;
}) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const roles: Role[] = session.role === "owner" ? ["member", "admin"] : ["member"];

  return (
    <form
      className="card adm-invite"
      onSubmit={async (e) => {
        e.preventDefault();
        setError(null);
        setSent(null);
        setBusy(true);
        try {
          await sendInvite({ email, role });
          setSent(email);
          setEmail("");
          setRole("member");
          onSent();
        } catch (err) {
          setError((err as Error).message);
        } finally {
          setBusy(false);
        }
      }}
    >
      <h3 className="adm-invite-title">Invite by email</h3>
      <p className="adm-sub">
        Sends an invite link — they choose their own password. Invites expire
        after 7 days and reserve a seat until accepted or revoked.
      </p>
      <div className="adm-invite-grid">
        <Field label="Email">
          <input
            className="input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </Field>
        <Field label="Role">
          <select
            className="select"
            value={role}
            onChange={(e) => setRole(e.target.value as "admin" | "member")}
          >
            {roles.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="adm-invite-foot">
        <Button type="submit" variant="primary" busy={busy} disabled={disabled}>
          Send invite
        </Button>
        {sent && <Alert tone="ok">Invite sent to {sent}.</Alert>}
        {error && <Alert tone="danger">{error}</Alert>}
      </div>
    </form>
  );
}

function AddMemberForm({
  session,
  onAdded,
}: {
  session: Session;
  onAdded: () => void;
}) {
  const [email, setEmail] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  // Only the Owner may create admins (mirrors the API gate).
  const [role, setRole] = useState<"admin" | "member">("member");
  const [error, setError] = useState<string | null>(null);
  const roles: Role[] = session.role === "owner" ? ["member", "admin"] : ["member"];

  return (
    <form
      className="card adm-invite"
      onSubmit={async (e) => {
        e.preventDefault();
        setError(null);
        try {
          await addMember({ email, password, full_name: fullName, role });
          setEmail("");
          setFullName("");
          setPassword("");
          setRole("member");
          onAdded();
        } catch (err) {
          setError((err as Error).message);
        }
      }}
    >
      <h3 className="adm-invite-title">Add member directly</h3>
      <p className="adm-sub">
        Alternative to the email invite: creates the account now, with a
        temporary password you set and share.
      </p>
      <div className="adm-invite-grid">
        <Field label="Full name">
          <input
            className="input"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            required
          />
        </Field>
        <Field label="Email">
          <input
            className="input"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
        </Field>
        <Field label="Temporary password" description="Minimum 8 characters.">
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            required
          />
        </Field>
        <Field label="Role">
          <select
            className="select"
            value={role}
            onChange={(e) => setRole(e.target.value as "admin" | "member")}
          >
            {roles.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </Field>
      </div>
      <div className="adm-invite-foot">
        <Button type="submit" variant="primary">
          Add member
        </Button>
        {error && <Alert tone="danger">{error}</Alert>}
      </div>
    </form>
  );
}
