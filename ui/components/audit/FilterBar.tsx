"use client";

import type { FormEvent } from "react";
import { ENTRY_KINDS, PLATFORMS, type ContainerInfo, type EntriesFilter, type User } from "@/lib/api";
import { kindLabel, platformLabel } from "@/lib/format";

export interface FilterForm {
  actor: string;
  kind: string;
  platform: string;
  container: string;
  doc: string;
  decision: string;
  days: string;
  limit: string;
}

export const EMPTY_FILTER: FilterForm = { actor: "", kind: "", platform: "", container: "", doc: "", decision: "", days: "", limit: "50" };

/** Convert the form into the query the API accepts (blank fields omitted). */
export function toEntriesFilter(f: FilterForm): EntriesFilter {
  const out: EntriesFilter = {};
  if (f.actor) out.actor = f.actor;
  if (f.kind) out.kind = f.kind;
  if (f.platform) out.platform = f.platform;
  if (f.container) out.container = f.container;
  if (f.doc.trim()) out.doc = f.doc.trim();
  if (f.decision === "allow" || f.decision === "deny") out.decision = f.decision;
  const days = Number(f.days);
  if (f.days && Number.isFinite(days) && days >= 1) out.days = Math.floor(days);
  const limit = Number(f.limit);
  if (f.limit && Number.isFinite(limit) && limit >= 1) out.limit = Math.min(500, Math.floor(limit));
  return out;
}

export function FilterBar({ value, onChange, onApply, onReset, users, containers, busy }: { value: FilterForm; onChange: (next: FilterForm) => void; onApply: () => void; onReset: () => void; users: User[]; containers: ContainerInfo[]; busy: boolean }) {
  const set = (patch: Partial<FilterForm>) => onChange({ ...value, ...patch });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onApply();
  };
  return (
    <form className="filter-bar" onSubmit={submit}>
      <div className="field">
        <label htmlFor="f-actor">Actor</label>
        <select id="f-actor" className="select" value={value.actor} onChange={(e) => set({ actor: e.target.value })}>
          <option value="">any</option>
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.id} — {u.name}
            </option>
          ))}
          <option value="sync-worker">sync-worker</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor="f-kind">Kind</label>
        <select id="f-kind" className="select" value={value.kind} onChange={(e) => set({ kind: e.target.value })}>
          <option value="">any</option>
          {ENTRY_KINDS.map((k) => (
            <option key={k} value={k}>
              {kindLabel(k)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="f-platform">Platform</label>
        <select id="f-platform" className="select" value={value.platform} onChange={(e) => set({ platform: e.target.value })}>
          <option value="">any</option>
          {PLATFORMS.map((p) => (
            <option key={p} value={p}>
              {platformLabel(p)}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="f-container">Container</label>
        <select id="f-container" className="select" value={value.container} onChange={(e) => set({ container: e.target.value })}>
          <option value="">any</option>
          {containers.map((c) => (
            <option key={c.container} value={c.container}>
              {platformLabel(c.platform)} · {c.label ?? c.container}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="f-doc">Document</label>
        <input id="f-doc" className="input mono" placeholder="jira:DBM-42" value={value.doc} onChange={(e) => set({ doc: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor="f-decision">Decision</label>
        <select id="f-decision" className="select" value={value.decision} onChange={(e) => set({ decision: e.target.value })}>
          <option value="">any</option>
          <option value="allow">allow</option>
          <option value="deny">deny</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor="f-days">Days</label>
        <input id="f-days" className="input" type="number" min={1} max={3650} placeholder="all" value={value.days} onChange={(e) => set({ days: e.target.value })} />
      </div>
      <div className="field">
        <label htmlFor="f-limit">Limit</label>
        <input id="f-limit" className="input" type="number" min={1} max={500} value={value.limit} onChange={(e) => set({ limit: e.target.value })} />
      </div>
      <div className="row" style={{ alignSelf: "end" }}>
        <button type="submit" className="btn btn-primary" disabled={busy}>
          Apply
        </button>
        <button type="button" className="btn" onClick={onReset} disabled={busy}>
          Reset
        </button>
      </div>
    </form>
  );
}
