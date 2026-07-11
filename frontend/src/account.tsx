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
