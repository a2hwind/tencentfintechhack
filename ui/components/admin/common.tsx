"use client";

import { fmtDateTime } from "@/lib/format";
import { Collapsible, Empty, JsonBlock } from "@/components/ui";

/** Runs one admin mutation: records its result (or error), then the page refreshes its state. */
export type RunAction = <T>(label: string, fn: () => Promise<T>) => Promise<T | undefined>;

export interface ActionRecord {
  id: number;
  label: string;
  ts: string;
  ok: boolean;
  result: unknown;
}

export function ActionLog({ records }: { records: ActionRecord[] }) {
  if (records.length === 0) return <Empty>No actions yet. Every mutation's JSON result appears here, newest first.</Empty>;
  const [latest, ...rest] = records;
  return (
    <div className="stack-sm">
      <div className={`result-box ${latest.ok ? "ok" : "err"}`}>
        <div className="row-between" style={{ marginBottom: 6 }}>
          <span className="strong">{latest.label}</span>
          <span className="tiny muted">
            {latest.ok ? <span className="badge badge-allow">ok</span> : <span className="badge badge-deny">error</span>} {fmtDateTime(latest.ts)}
          </span>
        </div>
        <JsonBlock value={latest.result} />
      </div>
      {rest.length > 0 && (
        <Collapsible title={`Earlier actions (${rest.length})`}>
          <div className="stack-sm">
            {rest.map((r) => (
              <div key={r.id} className={`result-box ${r.ok ? "ok" : "err"}`}>
                <div className="row-between" style={{ marginBottom: 4 }}>
                  <span className="strong small">{r.label}</span>
                  <span className="tiny muted">{fmtDateTime(r.ts)}</span>
                </div>
                <pre className="small">{JSON.stringify(r.result)}</pre>
              </div>
            ))}
          </div>
        </Collapsible>
      )}
    </div>
  );
}

export function CheckboxGroup({ options, value, onChange, name }: { options: string[]; value: string[]; onChange: (next: string[]) => void; name: string }) {
  const toggle = (opt: string) => {
    onChange(value.includes(opt) ? value.filter((v) => v !== opt) : [...value, opt]);
  };
  if (options.length === 0) return <span className="muted small">none</span>;
  return (
    <div className="check-group">
      {options.map((opt) => {
        const on = value.includes(opt);
        return (
          <label key={opt} className={`check-chip${on ? " on" : ""}`}>
            <input type="checkbox" name={name} checked={on} onChange={() => toggle(opt)} />
            {opt}
          </label>
        );
      })}
    </div>
  );
}

export function NotifyCheck({ value, onChange, id }: { value: boolean; onChange: (v: boolean) => void; id?: string }) {
  return (
    <label className="check" title="Unchecked simulates a missed webhook: the index and entitlement cache stay stale until Gate 2 or the next sync catches it">
      <input id={id} type="checkbox" checked={value} onChange={(e) => onChange(e.target.checked)} />
      notify (deliver webhook)
    </label>
  );
}

export function MemberChips({ members }: { members: string[] }) {
  if (members.length === 0) return <span className="muted small">none</span>;
  return (
    <span className="chips">
      {members.map((m) => (
        <span key={m} className="chip-mono">
          {m}
        </span>
      ))}
    </span>
  );
}
