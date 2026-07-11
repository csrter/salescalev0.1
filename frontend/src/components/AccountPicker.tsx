/**
 * Account picker for agency-scale connects (Google MCC / Meta Business
 * Manager). The OAuth callback attaches nothing when the login can see more
 * than one new ad account — this dialog lists everything the connection's
 * token can reach (live from the platform) so an Admin can attach the right
 * accounts to this client, or pull in one that landed on another client.
 */
import { useCallback, useEffect, useState } from "react";
import {
  attachAccounts,
  listConnectableAccounts,
  reassignAdAccount,
  type ConnectableAccount,
} from "../api";
import { Dialog } from "./Dialog";
import { useToast } from "./Toast";
import { Alert, Badge, Button, EmptyState, SkeletonText } from "./ui";

export function AccountPickerDialog({
  open,
  onClose,
  platform,
  platformLabel,
  clientId,
  clientName,
  onChanged,
}: {
  open: boolean;
  onClose: () => void;
  platform: string;
  platformLabel: string;
  clientId: string;
  clientName: string;
  /** Called after any attach/move so the caller can refresh its account tree. */
  onChanged: () => void;
}) {
  const toast = useToast();
  const [accounts, setAccounts] = useState<ConnectableAccount[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [movingId, setMovingId] = useState<string | null>(null);

  const load = useCallback(() => {
    setAccounts(null);
    setError(null);
    setChecked(new Set());
    listConnectableAccounts(platform, clientId)
      .then(setAccounts)
      .catch((e) => setError((e as Error).message));
  }, [platform, clientId]);

  useEffect(() => {
    if (open) load();
  }, [open, load]);

  const toggle = (ext: string) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(ext)) next.delete(ext);
      else next.add(ext);
      return next;
    });
  };

  const attach = async () => {
    if (checked.size === 0 || busy) return;
    setBusy(true);
    try {
      const r = await attachAccounts(platform, clientId, [...checked]);
      toast(
        `Connected ${r.attached} account${r.attached === 1 ? "" : "s"} to ${clientName}`,
        "ok"
      );
      onChanged();
      load();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const moveHere = async (acct: ConnectableAccount) => {
    if (!acct.attached || movingId) return;
    setMovingId(acct.attached.account_id);
    try {
      await reassignAdAccount(acct.attached.account_id, clientId);
      toast(`Moved ${acct.name} to ${clientName}`, "ok");
      onChanged();
      load();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setMovingId(null);
    }
  };

  const selectable = (accounts ?? []).filter((a) => a.available && !a.attached);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`${platformLabel} ad accounts`}
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Close
          </Button>
          <Button busy={busy} disabled={checked.size === 0} onClick={() => void attach()}>
            Connect {checked.size > 0 ? `${checked.size} ` : ""}selected
          </Button>
        </>
      }
    >
      <p className="acctpick-sub">
        Everything this connection can see on {platformLabel}. Pick the
        accounts that belong to <strong>{clientName}</strong> — accounts on
        other clients stay put unless you move them here.
      </p>
      {error && <Alert tone="danger">{error}</Alert>}
      {!error && accounts === null && <SkeletonText lines={4} />}
      {accounts !== null && accounts.length === 0 && (
        <EmptyState title="No ad accounts visible">
          The connected {platformLabel} login can't see any ad accounts.
        </EmptyState>
      )}
      {accounts !== null && accounts.length > 0 && (
        <ul className="acctpick-list">
          {accounts.map((a) => {
            const mine = a.attached?.client_id === clientId;
            const elsewhere = a.attached && !mine;
            return (
              <li key={a.external_id} className="acctpick-row">
                {a.available && !a.attached ? (
                  <input
                    type="checkbox"
                    aria-label={`Select ${a.name}`}
                    checked={checked.has(a.external_id)}
                    onChange={() => toggle(a.external_id)}
                  />
                ) : (
                  <span className="acctpick-nocheck" aria-hidden="true" />
                )}
                <div className="acctpick-main">
                  <span className="acctpick-name">{a.name}</span>
                  <span className="acctpick-meta">
                    {a.external_id}
                    {a.currency ? ` · ${a.currency}` : ""}
                  </span>
                </div>
                {mine && <Badge tone="ok">connected</Badge>}
                {elsewhere && (
                  <>
                    <Badge tone="info">with {a.attached!.client_name}</Badge>
                    <Button
                      size="sm"
                      variant="ghost"
                      busy={movingId === a.attached!.account_id}
                      onClick={() => void moveHere(a)}
                    >
                      Move here
                    </Button>
                  </>
                )}
                {!a.available && <Badge tone="neutral">unavailable</Badge>}
              </li>
            );
          })}
        </ul>
      )}
      {accounts !== null && selectable.length > 1 && (
        <p className="acctpick-hint">
          Tip: connect only this client's accounts here, then open each other
          client and do the same — that keeps every account on the right
          profile.
        </p>
      )}
    </Dialog>
  );
}
