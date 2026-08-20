/**
 * iMessage checker — a dedicated view for segmenting an audience by
 * reachability before it gets enrolled in anything.
 *
 * The question it answers: will a BlueBubbles send to this lead land as a
 * blue iMessage, or does it have to go out as green-bubble SMS through the
 * host Mac's Text Message Forwarding? Those are materially different
 * channels, and knowing which one you're on BEFORE a campaign beats finding
 * out from a wall of failed sends.
 *
 * Checking sends nothing to the lead — it's Apple's availability lookup.
 * Lookups are paced server-side at one per second, so every run is a
 * background job; this view polls the shared enrichment-job record for
 * progress.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { MessageSquare, RefreshCw } from "./components/icons";

import {
  cancelEnrichmentJob,
  getEnrichmentJobs,
  imessageCheck,
  imessageLists,
  imessageSummary,
  listClients,
  type Client,
  type EnrichmentJob,
  type ImessageListCoverage,
  type ImessageSummary,
} from "./api";
import { Alert, Badge, Button, EmptyState, SkeletonText } from "./components/ui";
import { useToast } from "./components/Toast";

const HOUSE = "__house__";

function CoverageBar({ checked, total }: { checked: number; total: number }) {
  const pct = total > 0 ? Math.round((checked / total) * 100) : 0;
  return (
    <div
      className="crm-enrich-bar"
      role="progressbar"
      aria-valuenow={pct}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div
        className="crm-enrich-bar-fill"
        style={{ transform: `scaleX(${pct / 100})` }}
      />
    </div>
  );
}

export function ImessageView({
  active = true,
  houseClientId,
}: {
  active?: boolean;
  houseClientId?: string;
}) {
  const toast = useToast();
  const [clients, setClients] = useState<Client[]>([]);
  const [scope, setScope] = useState<string>(HOUSE);
  const [summary, setSummary] = useState<ImessageSummary | null>(null);
  const [lists, setLists] = useState<ImessageListCoverage[]>([]);
  const [job, setJob] = useState<EnrichmentJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const clientId = scope === HOUSE ? houseClientId : scope;

  useEffect(() => {
    listClients()
      .then(setClients)
      .catch(() => {});
  }, []);

  const load = useCallback(() => {
    if (!clientId) return;
    Promise.all([imessageSummary(clientId), imessageLists(clientId)])
      .then(([s, l]) => {
        setSummary(s);
        setLists(l.lists);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [clientId]);

  useEffect(load, [load]);

  // Poll the shared job record so a paced run shows live progress. Only
  // while this view is actually visible — a kept-mounted hidden tab must
  // not keep hitting the API.
  useEffect(() => {
    if (!active) return;
    let stop = false;
    const tick = () => {
      getEnrichmentJobs()
        .then((r) => {
          if (stop) return;
          const running =
            r.jobs.find((j) => j.status === "running" && j.phase === "imessage") ??
            null;
          setJob(running);
          if (running) load();
        })
        .catch(() => {});
    };
    tick();
    const t = setInterval(tick, 4000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [active, load]);

  const run = (opts: { listId?: string; label: string; count: number }) => {
    setBusy(opts.listId ?? "all");
    imessageCheck(
      opts.listId ? { listId: opts.listId } : { clientId },
    )
      .then(() => {
        toast(
          `Checking ${opts.count} lead${opts.count === 1 ? "" : "s"} in ${opts.label}. Lookups are paced at one per second, so this runs in the background.`,
          "ok",
        );
        load();
      })
      .catch((e) => toast((e as Error).message, "error"))
      .finally(() => setBusy(null));
  };

  const eta = useMemo(() => {
    if (!job) return null;
    const left = Math.max(0, job.total - job.processed);
    const m = Math.floor(left / 60);
    return m > 0 ? `about ${m}m left` : `about ${left}s left`;
  }, [job]);

  const scopeLabel =
    scope === HOUSE
      ? "my agency (house CRM)"
      : (clients.find((c) => c.id === scope)?.name ?? "this client");

  if (loading) return <SkeletonText lines={6} />;

  return (
    <section className="view">
      <header className="view-head">
        <h2 className="view-title">
          <MessageSquare size={18} /> iMessage checker
        </h2>
        <p className="view-sub">
          Which leads are reachable on iMessage, and which can only be texted
          as green-bubble SMS. Checking sends nothing to the lead.
        </p>
      </header>

      <div className="im-scope">
        <label htmlFor="im-scope-select">Contacts from</label>
        <select
          id="im-scope-select"
          value={scope}
          onChange={(e) => setScope(e.target.value)}
        >
          <option value={HOUSE}>My agency (house CRM)</option>
          {clients.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name}
            </option>
          ))}
        </select>
        <Button variant="ghost" size="sm" onClick={load}>
          <RefreshCw size={14} /> Refresh
        </Button>
      </div>

      {job && (
        <Alert tone="ok" title="Checking now">
          <p>
            {job.processed} of {job.total} looked up — {eta}. Paced so the
            relay's Apple ID doesn't get rate-limited; you can leave this page.
          </p>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              cancelEnrichmentJob(job.id)
                .then((r) => {
                  toast(
                    `Stopped. ${r.processed} leads were checked and kept — run it again any time to pick up the rest.`,
                    "ok",
                  );
                  setJob(null);
                  load();
                })
                .catch((e) => toast((e as Error).message, "error"));
            }}
          >
            Stop this run
          </Button>
        </Alert>
      )}

      {summary && (
        <div className="glass-card crm-enrich-card">
          <div className="crm-enrich-head">
            <h4 className="crm-subhead crm-subhead--sm">
              Whole CRM — {scopeLabel}
            </h4>
            <span className="crm-enrich-pct">
              {summary.with_number > 0
                ? Math.round((summary.checked / summary.with_number) * 100)
                : 0}
              %
            </span>
            <Badge tone={summary.unchecked === 0 ? "ok" : "neutral"}>
              {summary.checked} of {summary.with_number} checked
            </Badge>
          </div>
          <CoverageBar checked={summary.checked} total={summary.with_number} />
          <p className="crm-enrich-line">
            {summary.imessage} on iMessage · {summary.sms_only} SMS only
            {summary.unchecked > 0 && ` · ${summary.unchecked} not checked`}
          </p>
          {summary.unchecked > 0 && (
            <Button
              variant="ghost"
              size="sm"
              disabled={busy !== null || !!job}
              onClick={() =>
                run({ label: scopeLabel, count: summary.unchecked })
              }
            >
              Check all {summary.unchecked} unchecked
            </Button>
          )}
        </div>
      )}

      <h3 className="crm-subhead">Lists</h3>
      {lists.length === 0 ? (
        <EmptyState title="No lists yet">
          Create a contact list in the CRM (or import leads straight into one)
          and it&rsquo;ll show up here so you can check just that audience.
        </EmptyState>
      ) : (
        <div className="im-lists">
          {lists.map((l) => (
            <div key={l.id} className="glass-card im-list-row">
              <div className="im-list-main">
                <div className="im-list-head">
                  <strong>{l.name}</strong>
                  <Badge tone={l.unchecked === 0 && l.with_number > 0 ? "ok" : "neutral"}>
                    {l.checked} of {l.with_number} checked
                  </Badge>
                </div>
                <CoverageBar checked={l.checked} total={l.with_number} />
                <p className="crm-enrich-line">
                  {l.imessage} on iMessage · {l.sms_only} SMS only
                  {l.unchecked > 0 && ` · ${l.unchecked} not checked`}
                </p>
              </div>
              <Button
                variant="ghost"
                size="sm"
                disabled={busy !== null || !!job || l.unchecked === 0}
                onClick={() =>
                  run({ listId: l.id, label: l.name, count: l.unchecked })
                }
              >
                {l.unchecked === 0 ? "All checked" : `Check ${l.unchecked}`}
              </Button>
            </div>
          ))}
        </div>
      )}

      <Alert tone="info" title="How this is used">
        A lead on iMessage can be sent a blue message with read receipts. An
        SMS-only lead has to go out through the sending Mac's Text Message
        Forwarding, which needs a paired iPhone online — the weaker path. Split
        your audience on this before pointing a campaign at it.
      </Alert>
    </section>
  );
}
