"use client";

import type { ReactNode } from "react";
import type { StreamEntry } from "@/lib/api";
import { fmtClock } from "@/lib/format";
import type { Trace } from "@/lib/trace";
import { Notice, Spinner } from "@/components/ui";
import { KindBadge } from "@/components/audit/EntriesTable";
import { TrustBoundaryDiagram } from "./TrustBoundaryDiagram";
import { PipelineStepper } from "./PipelineStepper";
import { LiveChain } from "./LiveChain";

/**
 * "What the auditor sees": the compliance view. Everything here is read as the compliance user from
 * the audit log (GET /audit/entries/{seq} and the live stream), and each read is itself logged.
 */
export function AuditorPane({
  trace,
  revealed,
  loadingSeq,
  error,
  readLoggedAs,
  onReplay,
  onSelect,
  onAlert,
  banner,
}: {
  /** Transient alert banner, shown over the top of this pane (it is compliance data). */
  banner?: ReactNode;
  trace: Trace | null;
  revealed: number;
  loadingSeq: number | null;
  error: string | null;
  readLoggedAs: number | null;
  onReplay: () => void;
  onSelect: (seq: number) => void;
  onAlert: (entry: StreamEntry) => void;
}) {
  return (
    <section className="pv-pane pv-auditor" aria-labelledby="pv-auditor-title">
      <header className="pv-pane-head">
        <h2 id="pv-auditor-title">What the auditor sees</h2>
        <span className="badge badge-compliance">compliance view</span>
        <span className="muted small">
          read as <span className="mono">compliance</span>; every read is logged
        </span>
      </header>
      {banner ? <div className="pv-banner-slot">{banner}</div> : null}

      <div className="pv-auditor-body">
        <div className="card pv-diagram-card">
          <div className="card-header">
            <h3>Trust boundary</h3>
            {trace ? (
              <span className="row small" style={{ gap: 6 }}>
                <KindBadge kind={trace.kind} />
                <span className="mono">#{trace.seq}</span>
                <span className="mono muted">{trace.actor}</span>
                <span className="muted">{fmtClock(trace.ts)}</span>
              </span>
            ) : (
              <span className="muted small">no trace yet</span>
            )}
          </div>
          <TrustBoundaryDiagram trace={trace} revealed={revealed} />
          <div className="tb-legend small muted" aria-hidden="true">
            <span>
              <i className="lg lg-trusted" /> trusted, deterministic
            </span>
            <span>
              <i className="lg lg-untrusted" /> untrusted
            </span>
            <span className="tone-text-deny">denied</span>
            <span className="tone-text-refresh">refreshed</span>
          </div>
        </div>

        <div className="pv-auditor-split">
          <div className="card pv-stepper-card">
            <div className="card-header">
              <h3>Pipeline{trace ? <span className="mono muted small"> · #{trace.seq}</span> : null}</h3>
              <span className="row" style={{ gap: 6 }}>
                {loadingSeq !== null && <Spinner label={`Loading #${loadingSeq}…`} />}
                {readLoggedAs !== null && loadingSeq === null && <span className="tiny muted">this read logged as #{readLoggedAs}</span>}
                <button type="button" className="btn btn-sm" onClick={onReplay} disabled={!trace}>
                  Replay
                </button>
              </span>
            </div>
            {trace && (
              <div className="pv-trace-q" title={trace.query ?? trace.source?.doc ?? undefined}>
                {trace.kind === "query" ? `“${trace.query ?? ""}”` : `Source open: ${trace.source?.doc || "unknown document"}`}
              </div>
            )}
            {error && (
              <div style={{ marginBottom: 8 }}>
                <Notice kind="error">{error}</Notice>
              </div>
            )}
            <div className="pv-scroll">
              {trace ? (
                <PipelineStepper trace={trace} revealed={revealed} />
              ) : (
                <div className="empty">Each stage of the next question appears here with its measured time: identity, plan, both gates, the exact chunks the model saw, the guard and the audit write.</div>
              )}
            </div>
          </div>

          <div className="card pv-chain-card">
            <LiveChain selectedSeq={trace?.seq ?? null} onSelect={onSelect} onAlert={onAlert} />
          </div>
        </div>
      </div>
    </section>
  );
}
