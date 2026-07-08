import { useEffect, useState } from "react";
import {
  ORG_PLANS,
  getSubscription,
  openBillingPortal,
  resetPassword,
  startCheckout,
  verifyEmail,
  type OrgPlan,
  type Session,
  type Subscription,
} from "./api";

function Brand() {
  return (
    <div className="brand auth-brand">
      <span className="brand-mark">◈</span>
      <span className="brand-name">Salescale</span>
    </div>
  );
}

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
            <button className="primary block" onClick={onDone}>
              Continue
            </button>
          </>
        )}
        {state === "error" && (
          <>
            <h1>Link expired</h1>
            <p className="auth-sub">
              This verification link is invalid or has expired. Log in and resend it.
            </p>
            <button className="primary block" onClick={onDone}>
              Back to login
            </button>
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
          <button className="primary block" onClick={onDone}>
            Continue
          </button>
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
        <label className="field">
          <span>New password</span>
          <input
            type="password"
            placeholder="At least 8 characters"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            required
          />
        </label>
        <button type="submit" className="primary block" disabled={password.length < 8}>
          Update password
        </button>
        {error && <p className="error">{error}</p>}
      </form>
    </div>
  );
}

/* ---- in-app: billing / subscription ---- */

export function Billing({ session }: { session: Session }) {
  const [sub, setSub] = useState<Subscription | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const isOwner = session.role === "owner";

  useEffect(() => {
    getSubscription().then(setSub).catch((e) => setError(e.message));
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
      <div className="page-head">
        <div>
          <h2>Billing</h2>
          <p className="page-sub">Manage your subscription and plan.</p>
        </div>
      </div>
      {error && <p className="error">{error}</p>}

      <div className="admin-stats">
        <div className="stat">
          <div className="stat-value" style={{ textTransform: "capitalize" }}>
            {sub?.plan ?? "—"}
          </div>
          <div className="stat-label">Current plan</div>
        </div>
        <div className="stat">
          <div className="stat-value">{sub?.status ?? "active"}</div>
          <div className="stat-label">Status</div>
        </div>
      </div>

      {sub && !sub.billing_enabled && (
        <p className="notice">
          Billing isn't configured on this deployment yet. Plans are managed
          manually until Stripe keys are set.
        </p>
      )}

      {sub && sub.billing_enabled && isOwner && (
        <>
          <h3>Change plan</h3>
          <div className="client-grid">
            {(ORG_PLANS.filter((p) => p !== "starter") as OrgPlan[]).map((plan) => (
              <div key={plan} className="client-card" style={{ cursor: "default" }}>
                <div className="client-info">
                  <strong style={{ textTransform: "capitalize" }}>{plan}</strong>
                  <span className="page-sub">
                    {plan === "pro" ? "25 clients · 15 seats" : "Unlimited"}
                  </span>
                </div>
                <button
                  className="primary"
                  disabled={busy || sub.plan === plan}
                  onClick={() => go(() => startCheckout(plan))}
                >
                  {sub.plan === plan ? "Current" : "Upgrade"}
                </button>
              </div>
            ))}
          </div>
          <p style={{ marginTop: "1.2rem" }}>
            <button className="ghost" disabled={busy} onClick={() => go(openBillingPortal)}>
              Manage billing &amp; invoices
            </button>
          </p>
        </>
      )}

      {sub && sub.billing_enabled && !isOwner && (
        <p className="page-sub">Only the organization owner can change the plan.</p>
      )}
    </div>
  );
}
