import { useEffect, useState } from "react";
import {
  ORG_PLANS,
  addMember,
  adminOrg,
  adminOrgs,
  adminSignups,
  adminStats,
  listMembers,
  resetUserPassword,
  updateMember,
  updateOrg,
  type AdminOrgDetail,
  type AdminOrgRow,
  type AdminSignupPoint,
  type AdminStats,
  type OrgPlan,
  type Role,
  type Session,
  type TeamMember,
} from "./api";
import { DataTable } from "./components/DataTable";

/* ---------------- Platform super-admin (cross-tenant) ---------------- */

export function SuperAdmin() {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [orgs, setOrgs] = useState<AdminOrgRow[]>([]);
  const [signups, setSignups] = useState<AdminSignupPoint[]>([]);
  const [selected, setSelected] = useState<AdminOrgRow | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    adminStats().then(setStats).catch((e) => setError(e.message));
    adminOrgs().then(setOrgs).catch((e) => setError(e.message));
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

  return (
    <div>
      <h2>Platform admin</h2>
      {error && <p className="error">{error}</p>}
      <div className="admin-stats">
        {cards.map(([label, value]) => (
          <div key={label} className="stat">
            <div className="stat-value">{value ?? "—"}</div>
            <div className="stat-label">{label}</div>
          </div>
        ))}
      </div>

      <h3>Signups — last 30 days</h3>
      <SignupChart points={signups} />

      <h3>Organizations</h3>
      <DataTable<AdminOrgRow>
        rows={orgs}
        rowKey={(o) => o.id}
        onRowClick={(o) => setSelected(o)}
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
            render: (o) => <span className="badge">{o.plan}</span>,
            sortValue: (o) => o.plan,
          },
          {
            key: "status",
            header: "Status",
            render: (o) => (
              <span className={`badge ${o.status === "active" ? "active" : "failed"}`}>
                {o.status}
              </span>
            ),
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
  );
}

function SignupChart({ points }: { points: AdminSignupPoint[] }) {
  if (points.length === 0) return <p className="muted">No data yet.</p>;
  const W = 720;
  const H = 140;
  const pad = 20;
  const max = Math.max(1, ...points.map((p) => p.count));
  const bw = (W - pad * 2) / points.length;
  const total = points.reduce((s, p) => s + p.count, 0);
  return (
    <div className="signup-chart">
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" preserveAspectRatio="none">
        {points.map((p, i) => {
          const h = (p.count / max) * (H - pad * 2);
          return (
            <rect
              key={p.date}
              x={pad + i * bw + 1}
              y={H - pad - h}
              width={Math.max(1, bw - 2)}
              height={h}
              rx={2}
              className="bar"
            >
              <title>
                {p.date}: {p.count}
              </title>
            </rect>
          );
        })}
        <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} className="axis" />
      </svg>
      <div className="chart-caption">
        {points[0].date} → {points[points.length - 1].date} · {total} total
      </div>
    </div>
  );
}

function OrgDetail({
  org,
  onBack,
}: {
  org: AdminOrgRow;
  onBack: () => void;
}) {
  const [detail, setDetail] = useState<AdminOrgDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = () =>
    adminOrg(org.id).then(setDetail).catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, [org.id]);

  const act = async (fn: () => Promise<unknown>) => {
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const resetPw = async (userId: string) => {
    setError(null);
    setNotice(null);
    try {
      const r = await resetUserPassword(userId);
      setNotice(
        `Temporary password for ${r.email}: ${r.temporary_password} — share it securely; it won't be shown again.`
      );
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const suspended = detail?.status === "suspended";

  return (
    <div>
      <button className="link" onClick={onBack}>
        ← All organizations
      </button>
      <div className="org-head">
        <h2>{org.name}</h2>
        {detail && (
          <>
            <span className={`badge ${suspended ? "failed" : "active"}`}>
              {detail.status}
            </span>
            <label className="plan-select">
              Plan:{" "}
              <select
                value={detail.plan}
                onChange={(e) =>
                  act(() => updateOrg(org.id, { plan: e.target.value as OrgPlan }))
                }
              >
                {ORG_PLANS.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            <button
              className={suspended ? "" : "danger"}
              onClick={() =>
                act(() =>
                  updateOrg(org.id, { status: suspended ? "active" : "suspended" })
                )
              }
            >
              {suspended ? "Reactivate org" : "Suspend org"}
            </button>
          </>
        )}
      </div>
      {error && <p className="error">{error}</p>}
      {notice && <p className="notice">{notice}</p>}

      <h3>Users</h3>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Email</th>
            <th>Role</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {detail?.users.map((u) => (
            <tr key={u.id}>
              <td>{u.full_name}</td>
              <td>{u.email}</td>
              <td>
                <span className="badge">{u.role}</span>
              </td>
              <td>{u.is_active ? "active" : "inactive"}</td>
              <td>
                <button onClick={() => resetPw(u.id)}>Reset password</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3>Clients</h3>
      <table className="admin-table">
        <thead>
          <tr>
            <th>Client</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {detail?.clients.map((c) => (
            <tr key={c.id}>
              <td>{c.name}</td>
              <td>
                <span className={`badge ${c.status}`}>{c.status}</span>
              </td>
            </tr>
          ))}
          {detail && detail.clients.length === 0 && (
            <tr>
              <td colSpan={2} className="muted">
                No clients.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

/* ---------------- Org admin console: team management ---------------- */

export function TeamAdmin({ session }: { session: Session }) {
  const [members, setMembers] = useState<TeamMember[]>([]);
  const [error, setError] = useState<string | null>(null);
  const isOwner = session.role === "owner";

  const load = () =>
    listMembers().then(setMembers).catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);

  const patch = async (
    id: string,
    body: { role?: "admin" | "member"; is_active?: boolean }
  ) => {
    setError(null);
    try {
      await updateMember(id, body);
      await load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div>
      <h2>Team</h2>
      {error && <p className="error">{error}</p>}
      <table className="admin-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Email</th>
            <th>Role</th>
            <th>Status</th>
            {isOwner && <th>Actions</th>}
          </tr>
        </thead>
        <tbody>
          {members.map((m) => {
            // The Owner is the only "owner" row and can't be edited here, which
            // also prevents the acting Owner from editing themselves.
            const editable = isOwner && m.role !== "owner";
            return (
              <tr key={m.id} className={m.is_active ? "" : "muted"}>
                <td>{m.full_name}</td>
                <td>{m.email}</td>
                <td>
                  <span className="badge">{m.role}</span>
                </td>
                <td>{m.is_active ? "active" : "inactive"}</td>
                {isOwner && (
                  <td>
                    {editable ? (
                      <>
                        {m.role === "member" ? (
                          <button onClick={() => patch(m.id, { role: "admin" })}>
                            Make admin
                          </button>
                        ) : (
                          <button onClick={() => patch(m.id, { role: "member" })}>
                            Make member
                          </button>
                        )}
                        {m.is_active ? (
                          <button onClick={() => patch(m.id, { is_active: false })}>
                            Deactivate
                          </button>
                        ) : (
                          <button onClick={() => patch(m.id, { is_active: true })}>
                            Reactivate
                          </button>
                        )}
                      </>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
      <AddMemberForm session={session} onAdded={load} />
    </div>
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
      className="add-member"
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
      <h3>Invite a team member</h3>
      <input
        placeholder="Full name"
        value={fullName}
        onChange={(e) => setFullName(e.target.value)}
        required
      />
      <input
        placeholder="Email"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        required
      />
      <input
        placeholder="Temporary password (min 8 chars)"
        type="password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        required
      />
      <select value={role} onChange={(e) => setRole(e.target.value as "admin" | "member")}>
        {roles.map((r) => (
          <option key={r} value={r}>
            {r}
          </option>
        ))}
      </select>
      <button type="submit">Add member</button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}
