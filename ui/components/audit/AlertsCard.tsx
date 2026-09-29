"use client";

import { useCallback, useEffect, useState } from "react";
import { describeError, getAlerts, type AuditAlert, type Severity } from "@/lib/api";
import { fmtDateTime, redactionLabel } from "@/lib/format";
import { Empty, Notice, Spinner } from "@/components/ui";
import { SeverityBadge } from "./EntriesTable";

const COLLAPSED = 5;

/** Insider-threat and data-hygiene alerts, newest first; evidence links open the entry detail. */
export function AlertsCard({ userId, onSelectSeq, onRead }: { userId: string; onSelectSeq: (seq: number) => void; onRead: (seq: number) => void }) {
  const [alerts, setAlerts] = useState<AuditAlert[] | null>(null);
  const [minSeverity, setMinSeverity] = useState<Severity | "">("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getAlerts(userId, { limit: 100, min_severity: minSeverity || undefined });
      setAlerts(res.alerts);
      onRead(res.read_logged_as);
    } catch (err: unknown) {
      setError(describeError(err));
    } finally {
      setLoading(false);
    }
  }, [userId, minSeverity, onRead]);

  useEffect(() => {
    void load();
  }, [load]);

  const counts = { high: 0, medium: 0, low: 0 };
  for (const a of alerts ?? []) counts[a.severity] = (counts[a.severity] ?? 0) + 1;
  const visible = expanded ? (alerts ?? []) : (alerts ?? []).slice(0, COLLAPSED);
  const hidden = (alerts?.length ?? 0) - visible.length;

  return (
    <div className="card alerts-card">
      <div className="card-header">
        <h2>
          Alerts
          {alerts && (
            <span className="row" style={{ gap: 4, fontWeight: 400 }}>
              {counts.high > 0 && <span className="badge badge-sev-high">{counts.high} high</span>}
              {counts.medium > 0 && <span className="badge badge-sev-medium">{counts.medium} medium</span>}
              {counts.low > 0 && <span className="badge badge-sev-low">{counts.low} low</span>}
            </span>
          )}
        </h2>
        <div className="row">
          <span className="card-sub" style={{ marginTop: 0 }}>
            Deterministic rules over the trail; each alert is itself a chained entry.
          </span>
          <select className="select" value={minSeverity} onChange={(e) => setMinSeverity(e.target.value as Severity | "")} aria-label="Minimum severity">
            <option value="">all severities</option>
            <option value="medium">medium and high</option>
            <option value="high">high only</option>
          </select>
          {loading && <Spinner label="Loading…" />}
          <button type="button" className="btn btn-sm" onClick={() => void load()} disabled={loading}>
            Refresh
          </button>
        </div>
      </div>
      {error && <Notice kind="error">{error}</Notice>}
      {alerts && alerts.length === 0 && <Empty>No alerts{minSeverity ? ` at ${minSeverity} severity or above` : ""}.</Empty>}
      {visible.length > 0 && (
        <ul className="alert-list">
          {visible.map((a) => (
            <AlertRow key={a.seq} alert={a} onSelectSeq={onSelectSeq} />
          ))}
        </ul>
      )}
      {(hidden > 0 || expanded) && (alerts?.length ?? 0) > COLLAPSED && (
        <button type="button" className="btn-link small" onClick={() => setExpanded((v) => !v)} style={{ marginTop: 8 }}>
          {expanded ? "Show fewer" : `Show all ${alerts?.length ?? 0} alerts`}
        </button>
      )}
    </div>
  );
}

function AlertRow({ alert, onSelectSeq }: { alert: AuditAlert; onSelectSeq: (seq: number) => void }) {
  const counts = alert.counts ? Object.entries(alert.counts) : [];
  const containers = alert.containers ?? (alert.container ? [alert.container] : []);
  return (
    <li className={`alert-item sev-${alert.severity}`}>
      <div className="alert-item-head">
        <SeverityBadge severity={alert.severity} />
        <span className="alert-item-title">{alert.title}</span>
        <span className="alert-item-meta">
          {fmtDateTime(alert.ts)} ·{" "}
          <button type="button" className="btn-link mono" onClick={() => onSelectSeq(alert.seq)} title="Open the alert entry">
            alert #{alert.seq}
          </button>
        </span>
      </div>
      <p className="alert-item-detail">{alert.detail}</p>
      <div className="alert-item-foot">
        <span>
          <span className="muted">{alert.subject.type}</span> <span className="mono">{alert.subject.id}</span>
        </span>
        <span className="chip-mono">{alert.rule}</span>
        {containers.map((c) => (
          <span key={c} className="chip-mono" title="container">
            {c}
          </span>
        ))}
        {counts.map(([k, n]) => (
          <span key={k} className="chip-mono">
            {redactionLabel(k)} ×{n}
          </span>
        ))}
        {alert.evidence_seqs.length > 0 && (
          <span className="row" style={{ gap: 4 }}>
            <span className="muted">evidence</span>
            {alert.evidence_seqs.map((seq) => (
              <button key={seq} type="button" className="btn btn-sm mono" onClick={() => onSelectSeq(seq)} title="Open this entry">
                #{seq}
              </button>
            ))}
          </span>
        )}
      </div>
    </li>
  );
}
