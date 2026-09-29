"use client";

import type { AccessRow } from "@/lib/api";
import { fmtDateTime, isRefreshed } from "@/lib/format";
import { DecisionBadge, Empty, PlatformBadge } from "@/components/ui";

/** Rows of the audit_decisions index (who retrieved document Y, all denials for user X). */
export function AccessRowsTable({ rows, onSelect, emptyText = "No rows." }: { rows: AccessRow[]; onSelect: (seq: number) => void; emptyText?: string }) {
  if (rows.length === 0) return <Empty>{emptyText}</Empty>;
  // Newer APIs say which kind of entry made each decision: a query, or opening the source from a citation.
  const showKind = rows.some((r) => r.kind !== undefined);
  return (
    <div className="table-wrap table-scroll">
      <table className="table table-compact clickable">
        <thead>
          <tr>
            <th className="num">Seq</th>
            <th>Time</th>
            {showKind && <th>Via</th>}
            <th>Actor</th>
            <th>Document</th>
            <th>Container</th>
            <th>Gate 1</th>
            <th>Gate 2</th>
            <th>Rule</th>
            <th>Decision</th>
            <th>Refreshed</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.seq}-${r.doc}-${i}`} className={r.decision === "deny" ? "deny-row" : undefined} onClick={() => onSelect(r.seq)} title={r.kind === "source_open" ? "Open the source-open entry" : "Open the query entry"}>
              <td className="num mono">{r.seq}</td>
              <td className="nowrap">{fmtDateTime(r.ts)}</td>
              {showKind && <td>{r.kind === "source_open" ? <span className="badge badge-outline">source open</span> : <span className="badge badge-accent">query</span>}</td>}
              <td className="mono">{r.actor_id}</td>
              <td className="nowrap">
                <PlatformBadge platform={r.platform} /> <span className="mono">{r.doc}</span>
              </td>
              <td className="mono small">{r.container ?? "—"}</td>
              <td>
                <DecisionBadge value={r.gate1} />
              </td>
              <td>
                <DecisionBadge value={r.gate2} />
              </td>
              <td>
                <span className="chip-mono">{r.rule}</span>
              </td>
              <td>
                <DecisionBadge value={r.decision} />
              </td>
              <td>{isRefreshed(r) ? <span className="badge badge-amber">✓</span> : <span className="muted">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
