"use client";

import { useState, type FormEvent } from "react";
import { editConfluencePage, setConfluenceRestrictions, type ConfluencePage, type ConfluenceSpace } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { STEP4_TEXT } from "./ScenarioCard";
import { CheckboxGroup, NotifyCheck, type RunAction } from "./common";

export function ConfluenceCard({ userId, spaces, pages, userIds, groupNames, run, busy }: { userId: string; spaces: ConfluenceSpace[]; pages: ConfluencePage[]; userIds: string[]; groupNames: string[]; run: RunAction; busy: boolean }) {
  const firstPage = pages[0]?.id ?? "";
  const [editPage, setEditPage] = useState(firstPage);
  const [appendText, setAppendText] = useState("");
  const [editNotify, setEditNotify] = useState(true);

  const [restrictPage, setRestrictPage] = useState(firstPage);
  const [restrictUsers, setRestrictUsers] = useState<string[]>([]);
  const [restrictGroups, setRestrictGroups] = useState<string[]>([]);
  const [restrictNotify, setRestrictNotify] = useState(true);

  const submitEdit = (e: FormEvent) => {
    e.preventDefault();
    const pageId = editPage || firstPage;
    if (!pageId || !appendText.trim()) return;
    void run(`Confluence edit page ${pageId}${editNotify ? "" : " (no notify)"}`, () => editConfluencePage(userId, pageId, { append: appendText.trim(), notify: editNotify }));
  };

  const submitRestrictions = (e: FormEvent) => {
    e.preventDefault();
    const pageId = restrictPage || firstPage;
    if (!pageId) return;
    void run(`Confluence restrict page ${pageId} to users=[${restrictUsers.join(",")}] groups=[${restrictGroups.join(",")}]`, () => setConfluenceRestrictions(userId, pageId, { users: restrictUsers, groups: restrictGroups, notify: restrictNotify }));
  };

  const loadCurrent = (pageId: string) => {
    setRestrictPage(pageId);
    const page = pages.find((p) => p.id === pageId);
    setRestrictUsers(page?.restrictions?.users ?? []);
    setRestrictGroups(page?.restrictions?.groups ?? []);
  };

  return (
    <div className="card">
      <div className="card-header">
        <h2>Confluence</h2>
        <span className="card-sub">
          Spaces:{" "}
          {spaces.map((s) => (
            <span key={s.key} title={`read: users=[${s.read.users.join(", ")}] groups=[${s.read.groups.join(", ")}]`} className="chip-mono" style={{ marginRight: 4 }}>
              {s.key} · {s.name}
            </span>
          ))}
        </span>
      </div>
      <div className="table-wrap">
        <table className="table table-compact">
          <thead>
            <tr>
              <th>Id</th>
              <th>Space</th>
              <th>Title</th>
              <th className="num">Ver.</th>
              <th>Modified</th>
              <th>Restrictions</th>
            </tr>
          </thead>
          <tbody>
            {pages.map((p) => (
              <tr key={p.id} className={p.deleted ? "deleted-row" : undefined}>
                <td className="mono">{p.id}</td>
                <td className="mono">{p.space}</td>
                <td>
                  {p.title}
                  {p.deleted && (
                    <span className="badge badge-deny" style={{ marginLeft: 6 }}>
                      deleted
                    </span>
                  )}
                </td>
                <td className="num">{p.version}</td>
                <td className="nowrap">{fmtDateTime(p.last_modified)}</td>
                <td>
                  {p.restrictions ? (
                    <span className="chips">
                      {p.restrictions.users.map((u) => (
                        <span key={`u-${u}`} className="chip-mono" title="user">
                          user:{u}
                        </span>
                      ))}
                      {p.restrictions.groups.map((g) => (
                        <span key={`g-${g}`} className="chip-mono" title="group">
                          group:{g}
                        </span>
                      ))}
                    </span>
                  ) : (
                    <span className="muted small">inherits space</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card-section">
        <h3>Edit a page (append)</h3>
        <form className="stack-sm" onSubmit={submitEdit}>
          <div className="form-row">
            <div className="field">
              <label htmlFor="cf-edit-page">Page</label>
              <select id="cf-edit-page" className="select select-wide" value={editPage || firstPage} onChange={(e) => setEditPage(e.target.value)} disabled={busy}>
                {pages.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.id} — {p.title}
                  </option>
                ))}
              </select>
            </div>
            <NotifyCheck value={editNotify} onChange={setEditNotify} id="cf-edit-notify" />
            <button type="button" className="btn btn-sm" onClick={() => setAppendText(STEP4_TEXT)} disabled={busy} title="Fill in the scenario 2 failover step">
              Use step 4 text
            </button>
          </div>
          <textarea className="textarea" rows={2} placeholder="Text to append to the page body" value={appendText} onChange={(e) => setAppendText(e.target.value)} disabled={busy} />
          <div>
            <button type="submit" className="btn btn-primary" disabled={busy || !appendText.trim() || pages.length === 0}>
              Append to page
            </button>
          </div>
        </form>
      </div>

      <div className="card-section">
        <h3>Page restrictions</h3>
        <form className="stack-sm" onSubmit={submitRestrictions}>
          <div className="form-row">
            <div className="field">
              <label htmlFor="cf-restrict-page">Page</label>
              <select id="cf-restrict-page" className="select select-wide" value={restrictPage || firstPage} onChange={(e) => loadCurrent(e.target.value)} disabled={busy}>
                {pages.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.id} — {p.title}
                  </option>
                ))}
              </select>
            </div>
            <NotifyCheck value={restrictNotify} onChange={setRestrictNotify} id="cf-restrict-notify" />
          </div>
          <div className="field">
            <span className="field-label">Users</span>
            <CheckboxGroup name="cf-users" options={userIds} value={restrictUsers} onChange={setRestrictUsers} />
          </div>
          <div className="field">
            <span className="field-label">Groups</span>
            <CheckboxGroup name="cf-groups" options={groupNames} value={restrictGroups} onChange={setRestrictGroups} />
          </div>
          <div className="row">
            <button type="submit" className="btn btn-primary" disabled={busy || pages.length === 0}>
              Set restrictions
            </button>
            <span className="tiny muted">Leaving both empty clears the restriction (the page inherits the space permission again). Restrictions apply on top of the space permission.</span>
          </div>
        </form>
      </div>
    </div>
  );
}
