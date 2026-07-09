import { useEffect, useState } from "react";
import QRCode from "qrcode";

import {
  disableMfa,
  emailMfaEnable,
  emailMfaSetup,
  getMfaStatus,
  smsMfaEnable,
  smsMfaSetup,
  totpEnable,
  totpSetup,
  type MfaStatus,
} from "./api";

type Flow = "idle" | "totp" | "email" | "sms";

export function TwoFactorSettings() {
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

  if (!status) return <p className="muted">Loading…</p>;

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
    </div>
  );
}
