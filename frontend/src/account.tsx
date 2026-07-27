import { useEffect, useState, type ReactNode } from "react";
import {
  ORG_PLANS,
  acceptInvite,
  acceptInviteSignup,
  getBillingUsage,
  getSubscription,
  type BillingUsage,
  isMfaChallenge,
  login,
  loginMfa,
  lookupInvite,
  openBillingPortal,
  resetPassword,
  startCheckout,
  verifyEmail,
  type InviteLookup,
  type LoginChallenge,
  type OrgPlan,
  type Session,
  type Subscription,
} from "./api";
import { Logo } from "./logo";
import { Alert, Button, Field, Kpi, KpiGrid, KpiSkeleton } from "./components/ui";
import "./styles/views/settings.css";

function Brand() {
  return <Logo auth />;
}

const cap = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : s);

/* ---- pre-auth: email verification (opened from the emailed link) ---- */

export function VerifyEmail({ token, onDone }: { token: string; onDone: () => void }) {
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");
  useEffect(() => {
    verifyEmail(token)
      .then(() => setState("ok"))
      .catch(() => setState("error"));
  }, [token]);

  return (
    <div className="auth-center">
      <div className="auth-card">
        <Brand />
        {state === "loading" && <p className="auth-sub">Verifying your email…</p>}
        {state === "ok" && (
          <>
            <h1>Email verified</h1>
            <p className="auth-sub">You're all set — you can log in now.</p>
            <Button variant="primary" block onClick={onDone}>
              Continue
            </Button>
          </>
        )}
        {state === "error" && (
          <>
            <h1>Link expired</h1>
            <p className="auth-sub">
              This verification link is invalid or has expired. Log in and resend it.
            </p>
            <Button variant="primary" block onClick={onDone}>
              Back to login
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

/* ---- pre-auth: password reset (opened from the emailed link) ---- */

export function ResetPassword({ token, onDone }: { token: string; onDone: () => void }) {
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (done)
    return (
      <div className="auth-center">
        <div className="auth-card">
          <Brand />
          <h1>Password updated</h1>
          <p className="auth-sub">Log in with your new password.</p>
          <Button variant="primary" block onClick={onDone}>
            Continue
          </Button>
        </div>
      </div>
    );

  return (
    <div className="auth-center">
      <form
        className="auth-card"
        onSubmit={async (e) => {
          e.preventDefault();
          setError(null);
          try {
            await resetPassword(token, password);
            setDone(true);
          } catch (err) {
            setError((err as Error).message);
          }
        }}
      >
        <Brand />
        <h1>Set a new password</h1>
        <Field label="New password">
          <input
            type="password"
            placeholder="At least 8 characters"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            required
          />
        </Field>
        <Button type="submit" variant="primary" block disabled={password.length < 8}>
          Update password
        </Button>
        {error && <Alert tone="danger">{error}</Alert>}
      </form>
    </div>
  );
}

/* ---- pre-auth: team invite (opened from the emailed link) ---- */

export function AcceptInvite({
  token,
  session,
  onJoined,
  onDone,
}: {
  token: string;
  session: Session | null;
  onJoined: (s: Session) => void;
  onDone: () => void;
}) {
  const [invite, setInvite] = useState<InviteLookup | null>(null);
  const [lookupFailed, setLookupFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Signup path
  const [fullName, setFullName] = useState("");
  // Both paths
  const [password, setPassword] = useState("");
  // Login path may hit a 2FA challenge
  const [challenge, setChallenge] = useState<LoginChallenge | null>(null);
  const [code, setCode] = useState("");

  useEffect(() => {
    lookupInvite(token)
      .then(setInvite)
      .catch(() => setLookupFailed(true));
  }, [token]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const card = (children: ReactNode) => (
    <div className="auth-center">
      <div className="auth-card">
        <Brand />
        {children}
      </div>
    </div>
  );

  if (lookupFailed)
    return card(
      <>
        <h1>Invalid invite</h1>
        <p className="auth-sub">
          This invite link is invalid — ask your organization admin to send a
          new one.
        </p>
        <Button variant="primary" block onClick={onDone}>
          Back
        </Button>
      </>
    );

  if (!invite) return card(<p className="auth-sub">Checking your invite…</p>);

  if (invite.status !== "pending")
    return card(
      <>
        <h1>
          {invite.status === "accepted"
            ? "Invite already used"
            : invite.status === "expired"
              ? "Invite expired"
              : "Invite revoked"}
        </h1>
        <p className="auth-sub">
          {invite.status === "expired"
            ? "Ask your organization admin to resend it."
            : "Ask your organization admin for a new invite if you still need access."}
        </p>
        <Button variant="primary" block onClick={onDone}>
          Back
        </Button>
      </>
    );

  const intro = (
    <>
      <h1>Join {invite.organization_name}</h1>
      <p className="auth-sub">
        You've been invited as {invite.role === "admin" ? "an" : "a"}{" "}
        <strong>{invite.role}</strong> ({invite.email}).
      </p>
    </>
  );

  // Already logged in: one click. The server enforces that the logged-in
  // account's email matches the invite.
  if (session)
    return card(
      <>
        {intro}
        <Button
          variant="primary"
          block
          busy={busy}
          onClick={() => run(async () => onJoined(await acceptInvite(token)))}
        >
          Accept invite
        </Button>
        <Button variant="ghost" block onClick={onDone}>
          Not now
        </Button>
        {error && <Alert tone="danger">{error}</Alert>}
      </>
    );

  // Existing account, not logged in: log in as the invited address, then join.
  if (invite.account_exists) {
    if (challenge)
      return card(
        <form
          onSubmit={(e) => {
            e.preventDefault();
            run(async () => {
              await loginMfa(challenge.challenge_token, code);
              onJoined(await acceptInvite(token));
            });
          }}
        >
          {intro}
          <Field label="Two-factor code">
            <input
              className="input"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              autoFocus
              required
            />
          </Field>
          <Button type="submit" variant="primary" block busy={busy}>
            Verify &amp; join
          </Button>
          {error && <Alert tone="danger">{error}</Alert>}
        </form>
      );
    return card(
      <form
        onSubmit={(e) => {
          e.preventDefault();
          run(async () => {
            const r = await login(invite.email, password);
            if (isMfaChallenge(r)) {
              setChallenge(r);
              return;
            }
            onJoined(await acceptInvite(token));
          });
        }}
      >
        {intro}
        <p className="auth-sub">Log in to your existing account to accept.</p>
        <Field label="Password">
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </Field>
        <Button type="submit" variant="primary" block busy={busy}>
          Log in &amp; join
        </Button>
        {error && <Alert tone="danger">{error}</Alert>}
      </form>
    );
  }

  // New user: the invite doubles as signup — the address is already proven
  // by the token, so no separate verification email round-trip.
  return card(
    <form
      onSubmit={(e) => {
        e.preventDefault();
        run(async () =>
          onJoined(await acceptInviteSignup(token, fullName, password))
        );
      }}
    >
      {intro}
      <Field label="Full name">
        <input
          className="input"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          required
        />
      </Field>
      <Field label="Password" description="Minimum 8 characters.">
        <input
          className="input"
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          minLength={8}
          required
        />
      </Field>
      <Button
        type="submit"
        variant="primary"
        block
        busy={busy}
        disabled={password.length < 8}
      >
        Create account &amp; join
      </Button>
      {error && <Alert tone="danger">{error}</Alert>}
    </form>
  );
}

/* ---- in-app: billing / subscription ---- */

export function Billing({ session }: { session: Session }) {
  const [sub, setSub] = useState<Subscription | null>(null);
  const [usage, setUsage] = useState<BillingUsage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isOwner = session.role === "owner";

  useEffect(() => {
    getSubscription().then(setSub).catch((e) => setError(e.message));
    getBillingUsage().then(setUsage).catch(() => undefined); // informative
  }, []);

  const go = async (fn: () => Promise<{ url: string }>) => {
    setBusy(true);
    setError(null);
    try {
      const { url } = await fn();
      window.location.href = url;
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="set-page-head">
        <div>
          <h2>Billing</h2>
          <p className="set-page-sub">Manage your subscription and plan.</p>
        </div>
      </div>
      {error && <Alert tone="danger">{error}</Alert>}

      <div className="set-billing-kpis">
        <KpiGrid>
          {sub ? (
            <>
              <Kpi label="Current plan" value={cap(sub.plan)} />
              <Kpi label="Status" value={sub.status ? cap(sub.status) : "—"} />
            </>
          ) : (
            <>
              <KpiSkeleton />
              <KpiSkeleton />
            </>
          )}
        </KpiGrid>
      </div>

      {sub && !sub.billing_enabled && (
        <Alert tone="info">
          Billing isn't configured on this deployment yet. Plans are managed
          manually until Stripe keys are set.
        </Alert>
      )}

      {usage && (
        <>
          <h3>Usage</h3>
          <div className="set-usage">
            {usage.meters.map((m) => {
              const pctUsed =
                m.limit == null ? 0 : Math.min(1, m.used / Math.max(1, m.limit));
              const nearCap = m.limit != null && pctUsed >= 0.8;
              return (
                <div key={m.key} className="set-usage-row">
                  <span className="set-usage-label">{m.label}</span>
                  <span
                    className={
                      nearCap ? "set-usage-count set-usage-count--warn" : "set-usage-count"
                    }
                  >
                    {m.used} of {m.limit == null ? "∞" : m.limit} used
                  </span>
                </div>
              );
            })}
          </div>
        </>
      )}

      {sub && sub.billing_enabled && isOwner && (
        <>
          <h3>Change plan</h3>
          <div className="set-plans">
            {(ORG_PLANS.filter((p) => p !== "starter") as OrgPlan[]).map((plan) => {
              const current = sub.plan === plan;
              return (
                <div
                  key={plan}
                  className={current ? "set-plan set-plan--current" : "set-plan"}
                >
                  <div className="set-plan-label">
                    Plan {current && <span className="set-plan-tag">current</span>}
                  </div>
                  <div className="set-plan-price">{plan}</div>
                  <span className="set-plan-meta">
                    {plan === "pro" ? "25 clients · 15 seats" : "Unlimited"}
                  </span>
                  <Button
                    className="set-plan-cta"
                    variant="primary"
                    disabled={busy || current}
                    onClick={() => go(() => startCheckout(plan))}
                  >
                    {current ? "Current plan" : "Upgrade"}
                  </Button>
                </div>
              );
            })}
          </div>
          <div className="set-billing-portal">
            <Button variant="ghost" disabled={busy} onClick={() => go(openBillingPortal)}>
              Manage billing &amp; invoices
            </Button>
          </div>
        </>
      )}

      {sub && sub.billing_enabled && !isOwner && (
        <p className="set-note">Only the organization owner can change the plan.</p>
      )}
    </div>
  );
}
