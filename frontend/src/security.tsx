import { useEffect, useState } from "react";
import QRCode from "qrcode";

import {
  disableMfa,
  emailMfaEnable,
  emailMfaSetup,
  getMfaStatus,
  getMyOrg,
  getSessions,
  logoutEverywhere,
  revokeSession,
  setRequireMfa,
  setSession,
  smsMfaEnable,
  smsMfaSetup,
  totpEnable,
  totpSetup,
  type MfaStatus,
  type Session,
  type SessionInfo,
} from "./api";
import { SkeletonText } from "./components/ui";

type Flow = "idle" | "totp" | "email" | "sms";

export function TwoFactorSettings({ session }: { session: Session }) {
  const [status, setStatus] = useState<MfaStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [flow, setFlow] = useState<Flow>("idle");
  const [qr, setQr] = useState("");
  const [secret, setSecret] = useState("");
  const [code, setCode] = useState("");
  const [phone, setPhone] = useState("");
  const [backupCodes, setBackupCodes] = useState<string[] | null>(null);
  const [disablePw, setDisablePw] = useState("");

  const refresh = () => getMfaStatus().then(setStatus).catch((e) => setError(e.message));
  useEffect(() => {
    refresh();
  }, []);

  const reset = () => {
    setFlow("idle");
    setCode("");
    setError(null);
  };
  const guard = (fn: () => Promise<void>) => async () => {
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const startTotp = guard(async () => {
    setBackupCodes(null);
    const s = await totpSetup();
    setSecret(s.secret);
    setQr(await QRCode.toDataURL(s.otpauth_uri));
    setCode("");
    setFlow("totp");
  });
  const startEmail = guard(async () => {
    setBackupCodes(null);
    await emailMfaSetup();
    setCode("");
    setFlow("email");
  });
  const startSms = guard(async () => {
    setBackupCodes(null);
    await smsMfaSetup(phone);
    setCode("");
    setFlow("sms");
  });
  const enable = guard(async () => {
    const r =
      flow === "totp"
        ? await totpEnable(code)
        : flow === "email"
        ? await emailMfaEnable(code)
        : await smsMfaEnable(code);
    setBackupCodes(r.backup_codes);
    reset();
    refresh();
  });
  const disable = guard(async () => {
    await disableMfa(disablePw);
    setDisablePw("");
    refresh();
  });

  if (!status)
    return (
      <div className="settings mfa-settings">
        <SkeletonText lines={4} />
      </div>
    );

  return (
    <div className="settings mfa-settings">
      <h2>Two-factor authentication</h2>
      <p className="muted">
        Add a second step at login so a password alone isn't enough to get in.
      </p>
      {error && <p className="error">{error}</p>}

      {backupCodes && (
        <div className="card notice">
          <h3>Save your backup codes</h3>
          <p>
            Each code works once if you lose access to your device. Store them
            somewhere safe — they won't be shown again.
          </p>
          <ul className="backup-codes">
            {backupCodes.map((c) => (
              <li key={c}>
                <code>{c}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {status.method ? (
        <div className="card">
          <p>
            <strong>Two-factor is on</strong> — method: <strong>{status.method}</strong>
            {status.phone_hint ? ` (${status.phone_hint})` : ""}.
          </p>
          <p className="muted">{status.backup_codes_remaining} backup codes remaining.</p>
          <label>
            Confirm your password to turn it off
            <input
              type="password"
              value={disablePw}
              onChange={(e) => setDisablePw(e.target.value)}
            />
          </label>
          <button className="danger" onClick={disable} disabled={!disablePw}>
            Disable two-factor
          </button>
        </div>
      ) : flow === "idle" ? (
        <div className="mfa-methods">
          <div className="card">
            <h3>Authenticator app</h3>
            <p className="muted">Google Authenticator, Authy, 1Password, and similar.</p>
            <button onClick={startTotp}>Set up</button>
          </div>
          <div className="card">
            <h3>Email code</h3>
            <p className="muted">We email a code to your account address at login.</p>
            <button onClick={startEmail}>Set up</button>
          </div>
          <div className="card">
            <h3>Text message (SMS)</h3>
            <p className="muted">We text a code to your phone at login.</p>
            <input
              placeholder="+1 555 555 0123"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
            />
            <button onClick={startSms} disabled={!phone}>
              Send code
            </button>
          </div>
        </div>
      ) : flow === "totp" ? (
        <div className="card">
          <h3>Scan with your authenticator app</h3>
          {qr && <img src={qr} alt="Authenticator QR code" width={180} height={180} />}
          <p className="muted">
            Or enter this key manually: <code>{secret}</code>
          </p>
          <label>
            Enter the 6-digit code to confirm
            <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} />
          </label>
          <button onClick={enable} disabled={!code}>
            Enable
          </button>
          <button type="button" className="link" onClick={reset}>
            Cancel
          </button>
        </div>
      ) : (
        <div className="card">
          <h3>Enter the code we sent</h3>
          <label>
            Verification code
            <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} />
          </label>
          <button onClick={enable} disabled={!code}>
            Enable
          </button>
          <button type="button" className="link" onClick={reset}>
            Cancel
          </button>
        </div>
      )}

      <SessionsPanel />
      <OrgPolicyPanel session={session} />
    </div>
  );
}

function _device(ua: string | null): string {
  if (!ua) return "Unknown device";
  const os = /Mac/.test(ua) ? "macOS" : /Windows/.test(ua) ? "Windows"
    : /Android/.test(ua) ? "Android" : /iPhone|iPad|iOS/.test(ua) ? "iOS"
    : /Linux/.test(ua) ? "Linux" : "";
  const br = /Edg/.test(ua) ? "Edge" : /Chrome/.test(ua) ? "Chrome"
    : /Firefox/.test(ua) ? "Firefox" : /Safari/.test(ua) ? "Safari" : "Browser";
  return [br, os].filter(Boolean).join(" · ");
}

function SessionsPanel() {
  const [list, setList] = useState<SessionInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => getSessions().then(setList).catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);
  const revoke = async (id: string) => {
    setError(null);
    try {
      await revokeSession(id);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const logoutAll = async () => {
    try {
      await logoutEverywhere();
      // This session is now revoked too — drop it and return to login.
      setSession(null);
      window.location.reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <section className="mfa-block">
      <h2>Active sessions</h2>
      <p className="muted">Devices currently signed in to your account.</p>
      {error && <p className="error">{error}</p>}
      <ul className="sessions">
        {(list ?? []).map((s) => (
          <li key={s.id} className="card session-row">
            <div>
              <strong>{_device(s.user_agent)}</strong>
              {s.current && <span className="badge current">This device</span>}
              <div className="muted">
                {s.ip ?? "—"} · last active {new Date(s.last_seen_at).toLocaleString()}
              </div>
            </div>
            {!s.current && (
              <button className="link danger" onClick={() => revoke(s.id)}>
                Revoke
              </button>
            )}
          </li>
        ))}
      </ul>
      <button className="danger" onClick={logoutAll}>
        Log out everywhere
      </button>
    </section>
  );
}

function OrgPolicyPanel({ session }: { session: Session }) {
  const [required, setRequired] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isOwner = session.role === "owner";
  useEffect(() => {
    getMyOrg()
      .then((o) => setRequired(o.require_mfa))
      .catch((e) => setError(e.message));
  }, []);
  if (!isOwner || required === null) return null;
  const toggle = async () => {
    setError(null);
    try {
      const o = await setRequireMfa(!required);
      setRequired(o.require_mfa);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <section className="mfa-block">
      <h2>Organization policy</h2>
      <div className="card policy-row">
        <div>
          <strong>Require two-factor for all team members</strong>
          <div className="muted">
            When on, team members without 2FA must set it up before they can use
            the app.
          </div>
        </div>
        <button className={required ? "danger" : "primary"} onClick={toggle}>
          {required ? "Turn off" : "Turn on"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
