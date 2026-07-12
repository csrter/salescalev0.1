import { useEffect, useState } from "react";
import QRCode from "qrcode";

import {
  disableMfa,
  emailMfaEnable,
  emailMfaSetup,
  getMfaStatus,
  getMyOrg,
  getSessions,
  getTrustedDevices,
  logoutEverywhere,
  revokeSession,
  revokeTrustedDevice,
  setAllowRememberDevice,
  setRequireMfa,
  setSession,
  smsMfaEnable,
  smsMfaSetup,
  totpEnable,
  totpSetup,
  type MfaStatus,
  type Session,
  type SessionInfo,
  type TrustedDeviceInfo,
} from "./api";
import { DataTable, type Column } from "./components/DataTable";
import { ConfirmDialog } from "./components/Dialog";
import {
  Alert,
  Badge,
  Button,
  Field,
  Kpi,
  KpiGrid,
  SkeletonText,
} from "./components/ui";
import "./styles/views/security.css";

type Flow = "idle" | "totp" | "email" | "sms";

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
      <div className="sec-page">
        <SkeletonText lines={4} />
      </div>
    );

  return (
    <div className="sec-page">
      <div className="sec-head">
        <h2>Two-factor authentication</h2>
        <p className="sec-sub">
          Add a second step at login so a password alone isn't enough to get in.
        </p>
      </div>

      {error && <Alert tone="danger">{error}</Alert>}

      {backupCodes && (
        <Alert tone="warn" title="Save your backup codes">
          <p>
            Each code works once if you lose access to your device. Store them
            somewhere safe — <strong>they won't be shown again</strong>.
          </p>
          <ul className="sec-backup-grid">
            {backupCodes.map((c) => (
              <li key={c}>
                <code>{c}</code>
              </li>
            ))}
          </ul>
          <CopyButton text={backupCodes.join("\n")} label="Copy all codes" />
        </Alert>
      )}

      {status.method ? (
        <div className="card sec-method">
          <p>
            <strong>Two-factor is on</strong> — method: <strong>{status.method}</strong>
            {status.phone_hint ? ` (${status.phone_hint})` : ""}.
          </p>
          <KpiGrid>
            <Kpi label="Backup codes remaining" value={status.backup_codes_remaining} />
          </KpiGrid>
          <Field label="Confirm your password to turn it off">
            <input
              className="input"
              type="password"
              value={disablePw}
              onChange={(e) => setDisablePw(e.target.value)}
            />
          </Field>
          <Button variant="danger" onClick={disable} disabled={!disablePw}>
            Disable two-factor
          </Button>
        </div>
      ) : flow === "idle" ? (
        <div className="sec-methods">
          <div className="card sec-method">
            <h3>Authenticator app</h3>
            <p className="sec-sub">Google Authenticator, Authy, 1Password, and similar.</p>
            <Button onClick={startTotp}>Set up</Button>
          </div>
          <div className="card sec-method">
            <h3>Email code</h3>
            <p className="sec-sub">We email a code to your account address at login.</p>
            <Button onClick={startEmail}>Set up</Button>
          </div>
          <div className="card sec-method">
            <h3>Text message (SMS)</h3>
            <p className="sec-sub">We text a code to your phone at login.</p>
            <Field label="Phone number">
              <input
                className="input"
                placeholder="+1 555 555 0123"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
              />
            </Field>
            <Button onClick={startSms} disabled={!phone}>
              Send code
            </Button>
          </div>
        </div>
      ) : flow === "totp" ? (
        <div className="card sec-method">
          <h3>Scan with your authenticator app</h3>
          {qr && (
            <img className="sec-qr" src={qr} alt="Authenticator QR code" width={180} height={180} />
          )}
          <div className="sec-secret">
            Or enter this key manually: <code>{secret}</code>
            <CopyButton text={secret} label="Copy key" />
          </div>
          <Field label="Enter the 6-digit code to confirm">
            <input
              className="input"
              inputMode="numeric"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </Field>
          <div className="sec-method-actions">
            <Button variant="primary" onClick={enable} disabled={!code}>
              Enable
            </Button>
            <Button variant="link" onClick={reset}>
              Cancel
            </Button>
          </div>
        </div>
      ) : (
        <div className="card sec-method">
          <h3>Enter the code we sent</h3>
          <Field label="Verification code">
            <input
              className="input"
              inputMode="numeric"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </Field>
          <div className="sec-method-actions">
            <Button variant="primary" onClick={enable} disabled={!code}>
              Enable
            </Button>
            <Button variant="link" onClick={reset}>
              Cancel
            </Button>
          </div>
        </div>
      )}

      <SessionsPanel />
      <TrustedDevicesPanel />
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
  const [confirmAll, setConfirmAll] = useState(false);
  const [busy, setBusy] = useState(false);

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
    setBusy(true);
    try {
      await logoutEverywhere();
      // This session is now revoked too — drop it and return to login.
      setSession(null);
      window.location.reload();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
      setConfirmAll(false);
    }
  };

  const columns: Column<SessionInfo>[] = [
    {
      key: "device",
      header: "Device",
      render: (s) => (
        <span className="sec-session-device">
          <strong>{_device(s.user_agent)}</strong>
          {s.current && <Badge tone="ok">This device</Badge>}
        </span>
      ),
    },
    { key: "ip", header: "IP", render: (s) => s.ip ?? "—" },
    {
      key: "last",
      header: "Last active",
      render: (s) => new Date(s.last_seen_at).toLocaleString(),
      sortValue: (s) => s.last_seen_at,
    },
    {
      key: "actions",
      header: "Actions",
      render: (s) =>
        s.current ? (
          <span className="sec-sub">—</span>
        ) : (
          <Button size="sm" variant="danger-outline" onClick={() => revoke(s.id)}>
            Revoke
          </Button>
        ),
    },
  ];

  return (
    <section className="sec-block">
      <h3>Active sessions</h3>
      <p className="sec-sub">Devices currently signed in to your account.</p>
      {error && <Alert tone="danger">{error}</Alert>}
      {list && list.length > 0 && (
        <KpiGrid>
          <Kpi label="Active sessions" value={list.length} />
        </KpiGrid>
      )}
      <DataTable<SessionInfo>
        rows={list ?? []}
        rowKey={(s) => s.id}
        loading={list == null}
        initialSort="-last"
        emptyMessage="No active sessions."
        columns={columns}
      />
      <div>
        <Button variant="danger" onClick={() => setConfirmAll(true)}>
          Log out everywhere
        </Button>
      </div>
      <ConfirmDialog
        open={confirmAll}
        onCancel={() => setConfirmAll(false)}
        onConfirm={logoutAll}
        rows={[
          {
            field: "All other sessions",
            oldValue: "signed in",
            newValue: "signed out",
          },
        ]}
        title="Log out everywhere"
        tone="danger"
        confirmLabel="Log out everywhere"
        cancelLabel="Cancel"
        busy={busy}
      >
        <p className="sec-sub">
          This signs out every device, including this one. You'll need to sign in
          again.
        </p>
      </ConfirmDialog>
    </section>
  );
}

function TrustedDevicesPanel() {
  const [list, setList] = useState<TrustedDeviceInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => getTrustedDevices().then(setList).catch((e) => setError(e.message));
  useEffect(() => {
    load();
  }, []);
  const revoke = async (id: string) => {
    setError(null);
    try {
      await revokeTrustedDevice(id);
      load();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const columns: Column<TrustedDeviceInfo>[] = [
    {
      key: "device",
      header: "Device",
      render: (d) => <strong>{_device(d.user_agent)}</strong>,
    },
    { key: "ip", header: "IP", render: (d) => d.ip ?? "—" },
    {
      key: "last",
      header: "Last used",
      render: (d) => new Date(d.last_used_at).toLocaleString(),
      sortValue: (d) => d.last_used_at,
    },
    {
      key: "expires",
      header: "Expires",
      render: (d) => new Date(d.expires_at).toLocaleDateString(),
      sortValue: (d) => d.expires_at,
    },
    {
      key: "actions",
      header: "Actions",
      render: (d) => (
        <Button size="sm" variant="danger-outline" onClick={() => revoke(d.id)}>
          Forget
        </Button>
      ),
    },
  ];

  return (
    <section className="sec-block">
      <h3>Remembered devices</h3>
      <p className="sec-sub">
        Devices you've told Salescale to skip the two-factor prompt on. Forget
        a device to require a code there again.
      </p>
      {error && <Alert tone="danger">{error}</Alert>}
      <DataTable<TrustedDeviceInfo>
        rows={list ?? []}
        rowKey={(d) => d.id}
        loading={list == null}
        initialSort="-last"
        emptyMessage="No remembered devices."
        columns={columns}
      />
    </section>
  );
}

function OrgPolicyPanel({ session }: { session: Session }) {
  const [required, setRequired] = useState<boolean | null>(null);
  const [allowRemember, setAllowRemember] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const isOwner = session.role === "owner";
  useEffect(() => {
    getMyOrg()
      .then((o) => {
        setRequired(o.require_mfa);
        setAllowRemember(o.allow_remember_device);
      })
      .catch((e) => setError(e.message));
  }, []);
  if (!isOwner) return null;
  const toggle = async () => {
    if (required == null) return;
    setError(null);
    try {
      const o = await setRequireMfa(!required);
      setRequired(o.require_mfa);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const toggleRemember = async () => {
    if (allowRemember == null) return;
    setError(null);
    try {
      const o = await setAllowRememberDevice(!allowRemember);
      setAllowRemember(o.allow_remember_device);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  return (
    <section className="sec-block">
      <h3>Organization policy</h3>
      {error && <Alert tone="danger">{error}</Alert>}
      <div className="card sec-policy">
        <div className="sec-policy-copy">
          <strong>Require two-factor for all team members</strong>
          <p className="sec-policy-desc">
            When on, team members without 2FA must set it up before they can use
            the app.
          </p>
        </div>
        {required != null && (
          <Button
            variant={required ? "danger-outline" : "primary"}
            onClick={toggle}
          >
            {required ? "Turn off" : "Turn on"}
          </Button>
        )}
      </div>
      <div className="card sec-policy">
        <div className="sec-policy-copy">
          <strong>Allow "remember this device"</strong>
          <p className="sec-policy-desc">
            When on, team members can skip the 2FA prompt on devices they've
            marked as trusted. Turn off to require a code every login,
            regardless of device.
          </p>
        </div>
        {allowRemember != null && (
          <Button
            variant={allowRemember ? "danger-outline" : "primary"}
            onClick={toggleRemember}
          >
            {allowRemember ? "Turn off" : "Turn on"}
          </Button>
        )}
      </div>
    </section>
  );
}
