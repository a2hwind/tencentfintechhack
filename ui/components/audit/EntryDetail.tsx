"use client";

import type { Decision, FullEntry } from "@/lib/api";
import { decisionOf, fmtDateTime, platformOf, redactionLabel, severityOf, sumCounts } from "@/lib/format";
import { Collapsible, DecisionBadge, Empty, Hash, JsonBlock, Notice, PlatformBadge, Spinner } from "@/components/ui";
import { KindBadge, OutcomeBadge, SeverityBadge } from "./EntriesTable";
import { StageTimingBar } from "./StageTimingBar";

export function EntryDetail({
  entry,
  loading,
  error,
  readLoggedAs,
  onClose,
  onSelectSeq,
}: {
  entry: FullEntry | null;
  loading: boolean;
  error: string | null;
  readLoggedAs: number | null;
  onClose: () => void;
  /** Open another entry (an alert's evidence). */
  onSelectSeq?: (seq: number) => void;
}) {
  const severity = entry?.kind === "alert" ? severityOf(entry.event?.["severity"]) : undefined;
  return (
    <div className="card detail-panel">
      <div className="card-header">
        <h2>
          Entry {entry ? <span className="mono">#{entry.seq}</span> : null}
          {entry ? <KindBadge kind={entry.kind} severity={severity} /> : null}
        </h2>
        <div className="row">
          {readLoggedAs !== null && (
            <span className="tiny muted" title="Reading an entry is itself an audit entry">
              this read logged as #{readLoggedAs}
            </span>
          )}
          {entry && (
            <button type="button" className="btn btn-sm" onClick={onClose}>
              Close
            </button>
          )}
        </div>
      </div>
      {loading && <Spinner label="Loading entry…" />}
      {error && <Notice kind="error">{error}</Notice>}
      {!loading && !error && !entry && <Empty>Select an entry to inspect its decisions, the chunks sent to the model and its place in the hash chain.</Empty>}
      {entry && !loading && <Body entry={entry} onSelectSeq={onSelectSeq} />}
    </div>
  );
}

function DecisionsTable({ decisions }: { decisions: Decision[] }) {
  return (
    <div className="table-wrap">
      <table className="table table-compact">
        <thead>
          <tr>
            <th>Document</th>
            <th>Gate 1</th>
            <th>Gate 2</th>
            <th>Rule</th>
            <th className="num">Ver.</th>
            <th>Refreshed</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {decisions.map((d, i) => (
            <tr key={`${d.doc}-${i}`} className={decisionOf(d) === "deny" ? "deny-row" : undefined}>
              <td className="nowrap">
                <PlatformBadge platform={d.platform ?? platformOf(d.doc)} />{" "}
                <span className="mono" title={d.container ?? undefined}>
                  {d.doc}
                </span>
              </td>
              <td>
                <DecisionBadge value={d.gate1} />
              </td>
              <td>
                <DecisionBadge value={d.gate2} />
              </td>
              <td>
                <span className="chip-mono">{d.rule}</span>
                {d.gate2 === "deny" && d.gate1_rule ? (
                  <span className="muted small" title="The entitlement Gate 1 used, which the live check at the source no longer honours">
                    {" "}gate 1 via <span className="mono">{d.gate1_rule}</span>
                  </span>
                ) : null}
              </td>
              <td className="num">{d.version ?? "—"}</td>
              <td>{d.refreshed ? <span className="badge badge-amber">✓ refreshed</span> : <span className="muted">—</span>}</td>
              <td>
                <span className="badge badge-muted">{d.source}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function asString(v: unknown): string {
  return typeof v === "string" ? v : v === null || v === undefined ? "" : String(v);
}

function AlertBody({ event, onSelectSeq }: { event: Record<string, unknown>; onSelectSeq?: (seq: number) => void }) {
  const severity = severityOf(event["severity"]);
  const evidence = Array.isArray(event["evidence_seqs"]) ? event["evidence_seqs"].filter((v): v is number => typeof v === "number") : [];
  const subject = event["subject"] && typeof event["subject"] === "object" ? (event["subject"] as Record<string, unknown>) : null;
  const counts = event["counts"] && typeof event["counts"] === "object" ? Object.entries(event["counts"] as Record<string, unknown>) : [];
  const containers = Array.isArray(event["containers"]) ? event["containers"].map(asString) : event["container"] ? [asString(event["container"])] : [];
  return (
    <div className={`alert-item sev-${severity}`}>
      <div className="alert-item-head">
        <SeverityBadge severity={severity} />
        <span className="alert-item-title">{asString(event["title"])}</span>
      </div>
      <p className="alert-item-detail">{asString(event["detail"])}</p>
      <dl className="kv" style={{ marginTop: 6 }}>
        <dt>Rule</dt>
        <dd>
          <span className="chip-mono">{asString(event["rule"])}</span>
        </dd>
        {subject && (
          <>
            <dt>Subject</dt>
            <dd>
              {asString(subject["type"])} <span className="mono">{asString(subject["id"])}</span>
            </dd>
          </>
        )}
        {containers.length > 0 && (
          <>
            <dt>Containers</dt>
            <dd className="chips">
              {containers.map((c) => (
                <span key={c} className="chip-mono">
                  {c}
                </span>
              ))}
            </dd>
          </>
        )}
        {counts.length > 0 && (
          <>
            <dt>Found</dt>
            <dd className="chips">
              {counts.map(([k, n]) => (
                <span key={k} className="chip-mono">
                  {redactionLabel(k)} ×{asString(n)}
                </span>
              ))}
            </dd>
          </>
        )}
        <dt>Evidence</dt>
        <dd>
          {evidence.length === 0 ? (
            <span className="muted">none (raised at ingestion)</span>
          ) : (
            <span className="row" style={{ gap: 4 }}>
              {evidence.map((seq) =>
                onSelectSeq ? (
                  <button key={seq} type="button" className="btn btn-sm mono" onClick={() => onSelectSeq(seq)}>
                    #{seq}
                  </button>
                ) : (
                  <span key={seq} className="chip-mono">
                    #{seq}
                  </span>
                ),
              )}
            </span>
          )}
        </dd>
      </dl>
    </div>
  );
}

function SourceOpenBody({ entry }: { entry: FullEntry }) {
  const ev = entry.event ?? {};
  const available = ev["available"] === true;
  const chunks = Array.isArray(ev["chunks"]) ? ev["chunks"].map(asString) : [];
  return (
    <div className="stack-sm">
      <dl className="kv">
        <dt>Document</dt>
        <dd className="mono">{asString(ev["doc"]) || "—"}</dd>
        <dt>Result</dt>
        <dd>{available ? <span className="badge badge-allow">opened</span> : <span className="badge badge-deny">unavailable</span>}</dd>
        <dt>Reason</dt>
        <dd>
          <span className="chip-mono">{asString(ev["reason"]) || "—"}</span>
          {!available && <span className="muted small"> · the asker saw only the uniform message</span>}
        </dd>
        {chunks.length > 0 && (
          <>
            <dt>Passages</dt>
            <dd className="chips">
              {chunks.map((c) => (
                <span key={c} className="chip-mono">
                  {c}
                </span>
              ))}
            </dd>
          </>
        )}
      </dl>
      <div>
        <h3 style={{ marginBottom: 6 }}>Decision at open</h3>
        {entry.decisions.length === 0 ? <Empty>No such document in the index: nothing to decide.</Empty> : <DecisionsTable decisions={entry.decisions} />}
      </div>
    </div>
  );
}

function Body({ entry, onSelectSeq }: { entry: FullEntry; onSelectSeq?: (seq: number) => void }) {
  const isQuery = entry.kind === "query";
  const redactions = entry.guard?.redactions ?? {};
  const redactionTotal = sumCounts(redactions);
  return (
    <div className="stack-sm">
      <dl className="kv">
        <dt>Time</dt>
        <dd>{fmtDateTime(entry.ts)}</dd>
        <dt>Actor</dt>
        <dd className="mono">{entry.actor.id}</dd>
        {isQuery && (
          <>
            <dt>Query</dt>
            <dd className="strong">{entry.query}</dd>
            <dt>Outcome</dt>
            <dd>
              <OutcomeBadge outcome={entry.outcome} />
            </dd>
            <dt>Latency</dt>
            <dd>{entry.latency_ms !== null ? `${entry.latency_ms} ms` : "—"}</dd>
            <dt>Model</dt>
            <dd className="mono">{entry.model ?? "—"}</dd>
          </>
        )}
      </dl>

      {isQuery && entry.timings_ms && Object.keys(entry.timings_ms).length > 0 && (
        <div>
          <h3 style={{ marginBottom: 6 }}>Stage timings</h3>
          <StageTimingBar timings={entry.timings_ms} total={entry.latency_ms} />
        </div>
      )}

      {entry.kind === "alert" && entry.event && <AlertBody event={entry.event} onSelectSeq={onSelectSeq} />}
      {entry.kind === "source_open" && <SourceOpenBody entry={entry} />}

      {entry.actor.principals.length > 0 && (
        <Collapsible title={`Principals at ${isQuery ? "query" : "request"} time (${entry.actor.principals.length})`}>
          <div className="principals">
            {entry.actor.principals.map((p) => (
              <span key={p} className="chip-mono">
                {p}
              </span>
            ))}
          </div>
        </Collapsible>
      )}

      {entry.plan && (
        <Collapsible title="Plan">
          <JsonBlock value={entry.plan} />
        </Collapsible>
      )}

      {isQuery && (
        <div>
          <h3 style={{ marginBottom: 6 }}>Decisions ({entry.decisions.length})</h3>
          {entry.decisions.length === 0 ? <Empty>No documents were considered.</Empty> : <DecisionsTable decisions={entry.decisions} />}
        </div>
      )}

      {isQuery && (
        <Collapsible title={`Sent to model (${entry.sent_to_model.length} chunks)`}>
          {entry.sent_to_model.length === 0 ? (
            <span className="muted small">Nothing was sent to the model.</span>
          ) : (
            <div className="chips">
              {entry.sent_to_model.map((c) => (
                <span key={c.chunk} className="chip-mono" title={`sha256 ${c.sha256}`}>
                  {c.chunk}
                </span>
              ))}
            </div>
          )}
        </Collapsible>
      )}

      {entry.guard && (
        <Collapsible title={`Guard${redactionTotal > 0 ? ` · ${redactionTotal} DLP redaction${redactionTotal === 1 ? "" : "s"}` : ""}`} defaultOpen={redactionTotal > 0 || entry.guard.citations_rejected > 0}>
          <div className="row" style={{ marginBottom: 6 }}>
            <span className={`badge ${entry.guard.citations_rejected ? "badge-deny" : "badge-muted"}`}>{entry.guard.citations_rejected} citations rejected</span>
            <span className={`badge ${entry.guard.claims_stripped ? "badge-amber" : "badge-muted"}`}>{entry.guard.claims_stripped} claims stripped</span>
            <span className={`badge ${entry.guard.unsupported_claims ? "badge-amber" : "badge-muted"}`}>{entry.guard.unsupported_claims} unsupported claims</span>
            <span className={`badge ${redactionTotal ? "badge-amber" : "badge-muted"}`}>{redactionTotal} DLP redactions</span>
          </div>
          {redactionTotal > 0 && (
            <div className="chips" style={{ marginBottom: 6 }}>
              {Object.entries(redactions)
                .filter(([, n]) => n > 0)
                .map(([kind, n]) => (
                  <span key={kind} className="chip-mono">
                    {redactionLabel(kind)} ×{n}
                  </span>
                ))}
            </div>
          )}
          {entry.guard.events.length > 0 ? (
            <ul className="small mono" style={{ margin: 0, paddingLeft: 18 }}>
              {entry.guard.events.map((ev, i) => (
                <li key={i}>{ev}</li>
              ))}
            </ul>
          ) : (
            <span className="muted small">No guard events. DLP values are masked at ingestion, so the model only sees truncated values; the output pass is defence in depth.</span>
          )}
        </Collapsible>
      )}

      {entry.answer !== null && (
        <Collapsible title="Answer as returned" defaultOpen={false}>
          <p className="answer-text" style={{ fontSize: 12.5 }}>
            {entry.answer}
          </p>
        </Collapsible>
      )}

      {entry.event && (
        <Collapsible title="Event" defaultOpen={entry.kind !== "alert" && entry.kind !== "source_open"}>
          <JsonBlock value={entry.event} />
        </Collapsible>
      )}

      <div className="card-section">
        <h3>Hash chain</h3>
        <div className="hash-chain">
          <span className="muted">prev</span>
          <Hash value={entry.prev_hash} length={14} />
          <span className="muted">→</span>
          <span className="muted">entry</span>
          <Hash value={entry.entry_hash} length={14} />
        </div>
        <p className="tiny muted" style={{ marginTop: 4 }}>
          entry_hash = SHA-256(canonical JSON of this entry ∥ prev_hash). Hover a hash for the full value.
        </p>
      </div>
    </div>
  );
}
