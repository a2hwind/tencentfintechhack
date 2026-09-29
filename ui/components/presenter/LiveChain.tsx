"use client";

import { useEffect, useRef, useState } from "react";
import { getHealth, streamAudit, type StreamEntry, type StreamStatus } from "@/lib/api";
import { AUDITOR_ID } from "@/lib/demoActions";
import { fmtClock, redactionLabel, severityOf, shortHash, sumCounts, summarizeEntry } from "@/lib/format";
import { KindBadge } from "@/components/audit/EntriesTable";

const MAX_ROWS = 40;
const WATCHDOG_MS = 15_000;
const STORAGE_KEY = "internal-brain.presenter.chain";

function readRows(): StreamEntry[] {
  try {
    const raw = window.sessionStorage.getItem(STORAGE_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? (parsed as StreamEntry[]).filter((r) => r && typeof r.seq === "number") : [];
  } catch {
    return [];
  }
}

function writeRows(rows: StreamEntry[]): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(rows));
  } catch {
    // Storage unavailable: the chain simply starts empty next time.
  }
}

function isTraceable(e: StreamEntry): boolean {
  return e.kind === "query" || e.kind === "source_open";
}

function numberList(value: unknown): number[] {
  return Array.isArray(value) ? value.filter((v): v is number => typeof v === "number") : [];
}

/**
 * The audit chain as it grows, read as the compliance user over server-sent events, newest first.
 * A first visit starts at the current head (from /health, which is not logged) and shows only what
 * happens next; coming back to the page (e.g. from a guided detour) restores the rows and catches up
 * from the last entry seen.
 */
export function LiveChain({ selectedSeq, onSelect, onAlert }: { selectedSeq: number | null; onSelect: (seq: number) => void; onAlert?: (entry: StreamEntry) => void }) {
  const [rows, setRows] = useState<StreamEntry[]>([]);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  const [error, setError] = useState<string | null>(null);
  const [retryIn, setRetryIn] = useState<number | null>(null);
  const [openedAs, setOpenedAs] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const onAlertRef = useRef(onAlert);

  useEffect(() => {
    onAlertRef.current = onAlert;
  }, [onAlert]);

  useEffect(() => {
    const outer = new AbortController();
    let session: AbortController | null = null;
    let serverStartedAt: string | null = null;
    // Entries up to the head at (re)connect time are backlog: listed, but they do not raise a banner.
    let backlogUntil = 0;

    // A restarted API process means a new connection: a server that is shutting down can keep an
    // open stream alive (and sending keep-alives) indefinitely, so the stream alone cannot tell.
    // /health is open and not logged, so polling it costs no audit entries.
    const watchdog = window.setInterval(() => {
      getHealth()
        .then((h) => {
          if (serverStartedAt && h.started_at && h.started_at !== serverStartedAt) session?.abort();
        })
        .catch(() => undefined);
    }, WATCHDOG_MS);

    void (async () => {
      while (!outer.signal.aborted) {
        let head: number | null = null;
        try {
          const health = await getHealth();
          head = health.audit.head?.seq ?? 0;
          serverStartedAt = health.started_at ?? null;
        } catch {
          // The stream reports reachability itself (and retries).
        }
        if (outer.signal.aborted) return;
        const saved = readRows();
        const lastSeen = saved.length ? Math.max(...saved.map((r) => r.seq)) : null;
        // Rows from a log that has since been replaced (a fresh data dir) are dropped.
        const resume = lastSeen !== null && (head === null || lastSeen <= head);
        if (resume) setRows(saved);
        else {
          writeRows([]);
          setRows([]);
        }
        const since = resume && lastSeen !== null ? lastSeen : (head ?? 0);
        const current = new AbortController();
        session = current;
        const stop = () => current.abort();
        outer.signal.addEventListener("abort", stop, { once: true });
        await streamAudit(AUDITOR_ID, {
          since,
          signal: current.signal,
          onHello: (hello) => {
            setOpenedAs(hello.read_logged_as);
            const head = hello.head?.seq ?? 0;
            backlogUntil = head;
            // Rows from a log that has since been replaced would sort above the new chain: drop them.
            setRows((prev) => {
              if (!prev.some((r) => r.seq > head)) return prev;
              writeRows([]);
              return [];
            });
          },
          onEntry: (entry) => {
            setRows((prev) => {
              if (prev.some((r) => r.seq === entry.seq)) return prev;
              const next = [entry, ...prev].sort((a, b) => b.seq - a.seq).slice(0, MAX_ROWS);
              writeRows(next);
              return next;
            });
            if (entry.kind === "alert" && entry.seq > backlogUntil) onAlertRef.current?.(entry);
          },
          onError: (message, retry) => {
            setError(message);
            setRetryIn(retry);
          },
          onStatus: (s) => {
            setStatus(s);
            if (s === "open") {
              setError(null);
              setRetryIn(null);
            }
          },
        });
        outer.signal.removeEventListener("abort", stop);
        // streamAudit returns when its signal aborts (the page left, or the watchdog saw a new
        // server: reconnect) or when the API refuses the identity (stop; never retry in a loop).
        if (outer.signal.aborted || !current.signal.aborted) return;
        setStatus("connecting");
      }
    })();
    return () => {
      outer.abort();
      window.clearInterval(watchdog);
    };
  }, []);

  return (
    <div className="chain">
      <div className="card-header chain-head">
        <h3>Live chain</h3>
        <StreamState status={status} error={error} retryIn={retryIn} />
      </div>
      <div className="tiny muted chain-sub">
        GET /audit/stream as <span className="mono">compliance</span>
        {openedAs !== null ? <> · opening it was logged as #{openedAs}</> : null} · reads of the log are hidden
      </div>
      <div className="pv-scroll chain-scroll">
        {rows.length === 0 ? (
          <div className="empty">{status === "open" ? "Listening. New entries appear here the moment they are chained." : "Waiting for the stream…"}</div>
        ) : (
          <ol className="chain-list" aria-live="polite" aria-relevant="additions">
            {rows.map((e) => (
              <ChainRow key={e.seq} entry={e} selected={e.seq === selectedSeq} expanded={expanded === e.seq} onSelect={onSelect} onToggle={() => setExpanded((cur) => (cur === e.seq ? null : e.seq))} />
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}

function StreamState({ status, error, retryIn }: { status: StreamStatus; error: string | null; retryIn: number | null }) {
  if (status === "open") {
    return (
      <span className="stream-state ok">
        <span className="live-dot" aria-hidden="true" /> live
      </span>
    );
  }
  if (status === "retrying") {
    return (
      <span className="stream-state warn" title={error ?? undefined}>
        reconnecting{retryIn !== null ? ` in ${Math.round(retryIn / 1000)} s` : ""}…
      </span>
    );
  }
  if (status === "closed") {
    return (
      <span className="stream-state bad" title={error ?? undefined}>
        closed{error ? `: ${error}` : ""}
      </span>
    );
  }
  return <span className="stream-state">connecting…</span>;
}

function ChainRow({ entry, selected, expanded, onSelect, onToggle }: { entry: StreamEntry; selected: boolean; expanded: boolean; onSelect: (seq: number) => void; onToggle: () => void }) {
  const isAlert = entry.kind === "alert";
  const ev = entry.event ?? {};
  const severity = isAlert ? severityOf(ev["severity"]) : null;
  const summary = isAlert ? String(ev["title"] ?? ev["rule"] ?? "alert") : summarizeEntry(entry) || "—";
  const traceable = isTraceable(entry);
  const redactions = sumCounts(entry.redactions);
  const available = entry.kind === "source_open" ? ev["available"] === true : null;

  const content = (
    <>
      <span className="chain-seq mono">#{entry.seq}</span>
      <span className="chain-kind">
        <KindBadge kind={entry.kind} severity={severity ?? undefined} />
      </span>
      <span className="chain-actor mono">{entry.actor}</span>
      <span className="chain-meta">
        {fmtClock(entry.ts)} · <span className="mono">{shortHash(entry.entry_hash, 8)}</span>
      </span>
      <span className="chain-summary" title={summary}>
        {summary}
      </span>
      <span className="chain-counts">
        {entry.kind === "query" && (
          <>
            <span className="badge badge-allow" title="documents allowed by both gates">
              {entry.allow} allow
            </span>
            <span className={`badge ${entry.deny ? "badge-deny" : "badge-muted"}`} title="documents denied by Gate 1 or Gate 2">
              {entry.deny} deny
            </span>
            {entry.refreshed && <span className="badge badge-amber">refreshed</span>}
            {entry.revoked && <span className="badge badge-deny">revoked</span>}
            {redactions > 0 && <span className="badge badge-amber">DLP {redactions}</span>}
            {entry.citations_rejected > 0 && <span className="badge badge-deny">{entry.citations_rejected} cite rejected</span>}
          </>
        )}
        {available !== null && (available ? <span className="badge badge-allow">opened</span> : <span className="badge badge-deny">unavailable</span>)}
      </span>
    </>
  );

  const cls = `chain-row${isAlert ? ` alert sev-${severity}` : ""}${selected ? " selected" : ""}`;
  return (
    <li className={cls}>
      {traceable ? (
        <button type="button" className="chain-btn" onClick={() => onSelect(entry.seq)} aria-pressed={selected} title="Trace this entry in the diagram and stepper">
          {content}
        </button>
      ) : isAlert ? (
        <button type="button" className="chain-btn" onClick={onToggle} aria-expanded={expanded} title="Show the alert's detail and evidence">
          {content}
        </button>
      ) : (
        <div className="chain-btn static">{content}</div>
      )}
      {isAlert && expanded && <AlertDetail event={ev} onSelect={onSelect} />}
    </li>
  );
}

function AlertDetail({ event, onSelect }: { event: Record<string, unknown>; onSelect: (seq: number) => void }) {
  const evidence = numberList(event["evidence_seqs"]);
  const counts = event["counts"] && typeof event["counts"] === "object" ? (event["counts"] as Record<string, unknown>) : null;
  const subject = event["subject"] && typeof event["subject"] === "object" ? (event["subject"] as Record<string, unknown>) : null;
  return (
    <div className="chain-detail">
      {typeof event["detail"] === "string" && <p className="small">{event["detail"]}</p>}
      <div className="row small" style={{ gap: 6 }}>
        <span className="chip-mono">{String(event["rule"] ?? "")}</span>
        {subject && (
          <span className="muted">
            {String(subject["type"] ?? "")} <span className="mono">{String(subject["id"] ?? "")}</span>
          </span>
        )}
        {counts &&
          Object.entries(counts).map(([k, v]) => (
            <span key={k} className="chip-mono">
              {redactionLabel(k)} ×{String(v)}
            </span>
          ))}
      </div>
      {evidence.length > 0 && (
        <div className="row small" style={{ gap: 4, marginTop: 6 }}>
          <span className="muted">Evidence:</span>
          {evidence.map((seq) => (
            <button key={seq} type="button" className="btn btn-sm mono" onClick={() => onSelect(seq)} title="Trace this entry">
              #{seq}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
