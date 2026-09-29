"use client";

import { useState, type FormEvent } from "react";
import { addJiraComment, setJiraSecurityLevel, type JiraIssue, type JiraProject } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { NotifyCheck, type RunAction } from "./common";

export function JiraCard({ userId, projects, issues, userIds, run, busy }: { userId: string; projects: JiraProject[]; issues: JiraIssue[]; userIds: string[]; run: RunAction; busy: boolean }) {
  const firstIssue = issues[0]?.key ?? "";
  const [levelIssue, setLevelIssue] = useState(firstIssue);
  const [level, setLevel] = useState<string>("");
  const [levelNotify, setLevelNotify] = useState(true);

  const [commentIssue, setCommentIssue] = useState(firstIssue);
  const [author, setAuthor] = useState(userIds[0] ?? "jdoe");
  const [text, setText] = useState("");
  const [commentNotify, setCommentNotify] = useState(true);

  const issueKey = levelIssue || firstIssue;
  const project = projects.find((p) => p.key === issues.find((i) => i.key === issueKey)?.project);
  const levels = project ? Object.keys(project.security_levels) : [];

  const submitLevel = (e: FormEvent) => {
    e.preventDefault();
    if (!issueKey) return;
    void run(`Jira ${issueKey} security level → ${level || "none"}`, () => setJiraSecurityLevel(userId, issueKey, { level: level || null, notify: levelNotify }));
  };

  const submitComment = (e: FormEvent) => {
    e.preventDefault();
    const key = commentIssue || firstIssue;
    if (!key || !text.trim()) return;
    void run(`Jira comment on ${key} by ${author}`, () => addJiraComment(userId, key, { author, text: text.trim(), notify: commentNotify }));
  };

  return (
    <div className="card">
      <div className="card-header">
        <h2>Jira</h2>
        <span className="card-sub">
          {projects.map((p) => (
            <span key={p.key} className="chip-mono" style={{ marginRight: 4 }} title={`roles: ${JSON.stringify(p.roles)}\nsecurity levels: ${JSON.stringify(p.security_levels)}`}>
              {p.key} · {p.name}
            </span>
          ))}
        </span>
      </div>
      <div className="table-wrap">
        <table className="table table-compact">
          <thead>
            <tr>
              <th>Key</th>
              <th>Summary</th>
              <th>Status</th>
              <th>Security level</th>
              <th className="num">Ver.</th>
              <th>Updated</th>
            </tr>
          </thead>
          <tbody>
            {issues.map((i) => (
              <tr key={i.key} className={i.deleted ? "deleted-row" : undefined}>
                <td className="mono">{i.key}</td>
                <td>
                  {i.summary}
                  {i.deleted && (
                    <span className="badge badge-deny" style={{ marginLeft: 6 }}>
                      deleted
                    </span>
                  )}
                </td>
                <td>
                  <span className="badge">{i.status}</span>
                </td>
                <td>{i.security_level ? <span className="badge badge-amber">{i.security_level}</span> : <span className="muted small">none (project roles)</span>}</td>
                <td className="num">{i.version}</td>
                <td className="nowrap">{fmtDateTime(i.updated)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card-section">
        <h3>Set security level</h3>
        <form className="form-row" onSubmit={submitLevel}>
          <div className="field">
            <label htmlFor="jira-level-issue">Issue</label>
            <select id="jira-level-issue" className="select" value={issueKey} onChange={(e) => setLevelIssue(e.target.value)} disabled={busy}>
              {issues.map((i) => (
                <option key={i.key} value={i.key}>
                  {i.key} — {i.summary}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="jira-level">Level</label>
            <select id="jira-level" className="select" value={level} onChange={(e) => setLevel(e.target.value)} disabled={busy}>
              <option value="">none (clear)</option>
              {levels.map((l) => (
                <option key={l} value={l}>
                  {l}
                </option>
              ))}
            </select>
          </div>
          <NotifyCheck value={levelNotify} onChange={setLevelNotify} id="jira-level-notify" />
          <button type="submit" className="btn btn-primary" disabled={busy || issues.length === 0}>
            Set level
          </button>
          {levels.length === 0 && issueKey && <span className="tiny muted">This project defines no security levels; only "none" applies.</span>}
        </form>
      </div>

      <div className="card-section">
        <h3>Add a comment</h3>
        <form className="stack-sm" onSubmit={submitComment}>
          <div className="form-row">
            <div className="field">
              <label htmlFor="jira-comment-issue">Issue</label>
              <select id="jira-comment-issue" className="select" value={commentIssue || firstIssue} onChange={(e) => setCommentIssue(e.target.value)} disabled={busy}>
                {issues.map((i) => (
                  <option key={i.key} value={i.key}>
                    {i.key}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="jira-comment-author">Author</label>
              <select id="jira-comment-author" className="select" value={author} onChange={(e) => setAuthor(e.target.value)} disabled={busy}>
                {userIds.map((u) => (
                  <option key={u} value={u}>
                    {u}
                  </option>
                ))}
              </select>
            </div>
            <NotifyCheck value={commentNotify} onChange={setCommentNotify} id="jira-comment-notify" />
          </div>
          <textarea className="textarea" rows={2} placeholder="Comment text" value={text} onChange={(e) => setText(e.target.value)} disabled={busy} />
          <div>
            <button type="submit" className="btn btn-primary" disabled={busy || !text.trim() || issues.length === 0}>
              Add comment
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
