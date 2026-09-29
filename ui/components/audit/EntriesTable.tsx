"use client";

import type { Entry } from "@/lib/api";
import { countDecisions, fmtDateTime, kindLabel, severityOf, summarizeEntry, type SeverityLevel } from "@/lib/format";
import { Empty } from "@/components/ui";

function kindClass(kind: string, severity?: SeverityLevel): string {
  switch (kind) {
    case "query":
      return "badge badge-accent";
    case "permission_event":
      return "badge badge-amber";
    case "audit_read":
      return "badge badge-muted";
    case "source_open":
      return "badge badge-outline";
    case "alert":
      return `badge badge-sev-${severity ?? "low"}`;
    default:
      return "badge";
  }
}

/** Entry kind badge; alerts are coloured by severity and (unless compact) labelled with it. */
export function KindBadge({ kind, severity, compact = false }: { kind: string; severity?: SeverityLevel; compact?: boolean }) {
  const label = kind === "alert" && severity && !compact ? `${severity} alert` : kindLabel(kind);
  return (
    <span className={kindClass(kind, severity)} title={kind === "alert" && severity ? `${severity} severity alert` : undefined}>
      {label}
    </span>
  );
}

export function SeverityBadge({ severity }: { severity: SeverityLevel }) {
  return <span className={`badge badge-sev-${severity}`}>{severity}</span>;
}

export function OutcomeBadge({ outcome }: { outcome: Entry["outcome"] }) {
  if (outcome === "answered") return <span className="badge badge-allow">answered</span>;
  if (outcome === "no_result") return <span className="badge badge-muted">no result</span>;
  return <span className="muted">—</span>;
}

export function EntriesTable({ entries, selectedSeq, onSelect, emptyText = "No entries match." }: { entries: Entry[]; selectedSeq: number | null; onSelect: (seq: number) => void; emptyText?: string }) {
  if (entries.length === 0) return <Empty>{emptyText}</Empty>;
  return (
    <div className="table-wrap table-scroll">
      <table className="table table-compact clickable entries-table">
        <colgroup>
          <col style={{ width: 40 }} />
          <col style={{ width: 134 }} />
          <col style={{ width: 90 }} />
          <col style={{ width: 88 }} />
          <col />
          <col style={{ width: 86 }} />
          <col style={{ width: 130 }} />
        </colgroup>
        <thead>
          <tr>
            <th className="num">Seq</th>
            <th>Time</th>
            <th>Kind</th>
            <th>Actor</th>
            <th>Summary</th>
            <th>Outcome</th>
            <th>Decisions</th>
          </tr>
        </thead>
        <tbody>
          {entries.map((e) => {
            const summary = summarizeEntry(e);
            const counts = countDecisions(e.decisions);
            const severity = e.kind === "alert" ? severityOf(e.event?.["severity"]) : undefined;
            return (
              <tr key={e.seq} className={`${e.seq === selectedSeq ? "selected" : ""}${e.kind === "alert" ? ` alert-row sev-${severity}` : ""}` || undefined} onClick={() => onSelect(e.seq)}>
                <td className="num mono">{e.seq}</td>
                <td className="nowrap time">{fmtDateTime(e.ts)}</td>
                <td>
                  <KindBadge kind={e.kind} severity={severity} compact />
                </td>
                <td className="mono" title={e.actor}>
                  {e.actor}
                </td>
                <td>
                  <div className="query-cell" title={summary}>
                    {summary || <span className="muted">—</span>}
                  </div>
                </td>
                <td>
                  <OutcomeBadge outcome={e.outcome} />
                </td>
                <td>
                  {e.kind === "query" || e.kind === "source_open" ? (
                    <span className="counts">
                      <span className="badge badge-allow" title="documents allowed by both gates">
                        {counts.allow} allow
                      </span>
                      <span className="badge badge-deny" title="documents denied by Gate 1 or Gate 2">
                        {counts.deny} deny
                      </span>
                    </span>
                  ) : (
                    <span className="muted">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
