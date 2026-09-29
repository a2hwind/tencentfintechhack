"use client";

import { useState, type FormEvent } from "react";
import { describeError, getDenials, getDocAccess, getUserAccess, type ContainerInfo, type DenialsResponse, type DocAccessResponse, type User, type UserAccessResponse } from "@/lib/api";
import { fmtDateTime, platformLabel } from "@/lib/format";
import { Empty, Notice, PlatformBadge } from "@/components/ui";
import { AccessRowsTable } from "./AccessRowsTable";

export type CannedKind = "user-access" | "doc-access" | "denials";

export type CannedResult = { kind: "user-access"; data: UserAccessResponse } | { kind: "doc-access"; data: DocAccessResponse } | { kind: "denials"; data: DenialsResponse };

const VIEWS: { kind: CannedKind; label: string; hint: string }[] = [
  { kind: "user-access", label: "What did user X access", hint: "Every document a user's queries touched, with allow/deny counts" },
  { kind: "doc-access", label: "Who retrieved document Y", hint: "Every decision recorded for one document" },
  { kind: "denials", label: "All denials for user X", hint: "Every document a user was refused, and why" },
];

export function CannedViews({ userId, users, containers, docSuggestions, onResult, onSelectEntry, onRead }: { userId: string; users: User[]; containers: ContainerInfo[]; docSuggestions: string[]; onResult: (r: CannedResult) => void; onSelectEntry: (seq: number) => void; onRead: (seq: number) => void }) {
  const [active, setActive] = useState<CannedKind | null>(null);
  const [actor, setActor] = useState<string>("jdoe");
  const [days, setDays] = useState<string>("30");
  const [container, setContainer] = useState<string>("");
  const [doc, setDoc] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<CannedResult | null>(null);

  const run = async (e: FormEvent) => {
    e.preventDefault();
    if (!active) return;
    setBusy(true);
    setError(null);
    try {
      let next: CannedResult;
      if (active === "user-access") {
        const d = Number(days);
        next = { kind: "user-access", data: await getUserAccess(userId, actor, Number.isFinite(d) && d >= 1 ? Math.floor(d) : 30, container || undefined) };
      } else if (active === "doc-access") {
        if (!doc.trim()) throw new Error("Enter a document id such as jira:DBM-42");
        next = { kind: "doc-access", data: await getDocAccess(userId, doc.trim()) };
      } else {
        next = { kind: "denials", data: await getDenials(userId, actor) };
      }
      setResult(next);
      onResult(next);
      onRead(next.data.read_logged_as);
    } catch (err: unknown) {
      setError(describeError(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack-sm">
      <div className="row">
        {VIEWS.map((v) => (
          <button key={v.kind} type="button" className={`btn${active === v.kind ? " btn-primary" : ""}`} title={v.hint} onClick={() => setActive(active === v.kind ? null : v.kind)}>
            {v.label}
          </button>
        ))}
      </div>

      {active && (
        <form className="form-row" onSubmit={run}>
          {(active === "user-access" || active === "denials") && (
            <div className="field">
              <label htmlFor="cv-actor">User</label>
              <select id="cv-actor" className="select" value={actor} onChange={(e) => setActor(e.target.value)}>
                {users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.id} — {u.name}
                  </option>
                ))}
              </select>
            </div>
          )}
          {active === "user-access" && (
            <>
              <div className="field">
                <label htmlFor="cv-days">Days</label>
                <input id="cv-days" className="input input-sm" type="number" min={1} max={3650} value={days} onChange={(e) => setDays(e.target.value)} />
              </div>
              <div className="field">
                <label htmlFor="cv-container">Container</label>
                <select id="cv-container" className="select" value={container} onChange={(e) => setContainer(e.target.value)}>
                  <option value="">any</option>
                  {containers.map((c) => (
                    <option key={c.container} value={c.container}>
                      {platformLabel(c.platform)} · {c.label ?? c.container}
                    </option>
                  ))}
                </select>
              </div>
            </>
          )}
          {active === "doc-access" && (
            <div className="field" style={{ minWidth: 260 }}>
              <label htmlFor="cv-doc">Document id</label>
              <input id="cv-doc" className="input mono" list="cv-doc-list" placeholder="jira:DBM-42" value={doc} onChange={(e) => setDoc(e.target.value)} />
              <datalist id="cv-doc-list">
                {docSuggestions.map((d) => (
                  <option key={d} value={d} />
                ))}
              </datalist>
            </div>
          )}
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Running…" : "Run"}
          </button>
        </form>
      )}

      {error && <Notice kind="error">{error}</Notice>}

      {result && <CannedResultView result={result} onSelectEntry={onSelectEntry} />}
    </div>
  );
}

function CannedResultView({ result, onSelectEntry }: { result: CannedResult; onSelectEntry: (seq: number) => void }) {
  if (result.kind === "user-access") {
    const { data } = result;
    return (
      <div className="stack-sm">
        <div className="row-between">
          <h3>
            Documents touched by <span className="mono">{data.actor}</span> in the last {data.days} days
            {data.container ? (
              <>
                {" "}
                in <span className="mono">{data.container}</span>
              </>
            ) : null}
          </h3>
          <span className="tiny muted">
            {data.queries.length} queries · read logged as #{data.read_logged_as}
          </span>
        </div>
        {data.documents.length === 0 ? (
          <Empty>No documents in that window. The matching queries (if any) are listed in the entries table.</Empty>
        ) : (
          <div className="table-wrap table-scroll">
            <table className="table table-compact">
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Container</th>
                  <th className="num">Allowed</th>
                  <th className="num">Denied</th>
                  <th>Last seen</th>
                </tr>
              </thead>
              <tbody>
                {data.documents.map((d) => (
                  <tr key={d.doc} className={d.allowed === 0 && d.denied > 0 ? "deny-row" : undefined}>
                    <td className="nowrap">
                      <PlatformBadge platform={d.platform} /> <span className="mono">{d.doc}</span>
                    </td>
                    <td className="mono small">{d.container ?? "—"}</td>
                    <td className="num">{d.allowed > 0 ? <span className="badge badge-allow">{d.allowed}</span> : <span className="muted">0</span>}</td>
                    <td className="num">{d.denied > 0 ? <span className="badge badge-deny">{d.denied}</span> : <span className="muted">0</span>}</td>
                    <td className="nowrap">{fmtDateTime(d.last_seen)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    );
  }
  if (result.kind === "doc-access") {
    const { data } = result;
    return (
      <div className="stack-sm">
        <div className="row-between">
          <h3>
            Accesses of <span className="mono">{data.doc}</span>
          </h3>
          <span className="tiny muted">
            {data.accesses.length} decisions · read logged as #{data.read_logged_as}
          </span>
        </div>
        <AccessRowsTable rows={data.accesses} onSelect={onSelectEntry} emptyText="No query has ever considered that document." />
      </div>
    );
  }
  const { data } = result;
  return (
    <div className="stack-sm">
      <div className="row-between">
        <h3>
          Denials for <span className="mono">{data.actor}</span>
        </h3>
        <span className="tiny muted">
          {data.denials.length} denials · read logged as #{data.read_logged_as}
        </span>
      </div>
      <AccessRowsTable rows={data.denials} onSelect={onSelectEntry} emptyText="No denials recorded for that user." />
    </div>
  );
}
