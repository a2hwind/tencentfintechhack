"use client";

import { useState, type FormEvent } from "react";
import { editDriveFile, shareDriveFile, type Drive, type DriveFile, type DriveFolder } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { MemberChips, NotifyCheck, type RunAction } from "./common";

export function DriveCard({ userId, drives, folders, files, userIds, run, busy }: { userId: string; drives: Drive[]; folders: DriveFolder[]; files: DriveFile[]; userIds: string[]; run: RunAction; busy: boolean }) {
  const firstFile = files[0]?.id ?? "";
  const [shareFile, setShareFile] = useState(firstFile);
  const [shareUser, setShareUser] = useState(userIds[0] ?? "jdoe");
  const [share, setShare] = useState(true);
  const [shareNotify, setShareNotify] = useState(true);

  const [editFile, setEditFile] = useState(firstFile);
  const [appendText, setAppendText] = useState("");
  const [editNotify, setEditNotify] = useState(true);

  const submitShare = (e: FormEvent) => {
    e.preventDefault();
    const fileId = shareFile || firstFile;
    if (!fileId) return;
    void run(`Drive ${share ? "share" : "unshare"} ${fileId} ${share ? "with" : "from"} ${shareUser}${shareNotify ? "" : " (no notify)"}`, () => shareDriveFile(userId, fileId, { user_id: shareUser, share, notify: shareNotify }));
  };

  const submitEdit = (e: FormEvent) => {
    e.preventDefault();
    const fileId = editFile || firstFile;
    if (!fileId || !appendText.trim()) return;
    void run(`Drive edit ${fileId}${editNotify ? "" : " (no notify)"}`, () => editDriveFile(userId, fileId, { append: appendText.trim(), notify: editNotify }));
  };

  const driveName = (id: string) => drives.find((d) => d.id === id)?.name ?? id;
  const folderName = (id: string | null) => (id ? (folders.find((f) => f.id === id)?.name ?? id) : null);

  return (
    <div className="card">
      <div className="card-header">
        <h2>Google Drive</h2>
        <span className="card-sub">
          {drives.map((d) => (
            <span key={d.id} className="chip-mono" style={{ marginRight: 4 }} title={`members: users=[${d.members.users.join(", ")}] groups=[${d.members.groups.join(", ")}]`}>
              {d.id} · {d.name}
            </span>
          ))}
        </span>
      </div>
      <div className="table-wrap">
        <table className="table table-compact">
          <thead>
            <tr>
              <th>Id</th>
              <th>Name</th>
              <th>Drive / folder</th>
              <th className="num">Ver.</th>
              <th>Modified</th>
              <th>Shared with</th>
            </tr>
          </thead>
          <tbody>
            {files.map((f) => (
              <tr key={f.id} className={f.deleted ? "deleted-row" : undefined}>
                <td className="mono">{f.id}</td>
                <td>
                  {f.name}
                  {f.deleted && (
                    <span className="badge badge-deny" style={{ marginLeft: 6 }}>
                      deleted
                    </span>
                  )}
                </td>
                <td className="small">
                  {driveName(f.drive)}
                  {folderName(f.folder) ? <span className="muted"> / {folderName(f.folder)}</span> : null}
                </td>
                <td className="num">{f.version}</td>
                <td className="nowrap">{fmtDateTime(f.modified)}</td>
                <td>
                  <span className="row" style={{ gap: 4 }}>
                    <MemberChips members={f.shared_with} />
                    {f.anyone_with_link && <span className="badge badge-amber">anyone with link</span>}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card-section">
        <h3>Share / unshare a file</h3>
        <form className="form-row" onSubmit={submitShare}>
          <div className="field">
            <label htmlFor="gd-share-file">File</label>
            <select id="gd-share-file" className="select" value={shareFile || firstFile} onChange={(e) => setShareFile(e.target.value)} disabled={busy}>
              {files.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.id} — {f.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="gd-share-user">User</label>
            <select id="gd-share-user" className="select" value={shareUser} onChange={(e) => setShareUser(e.target.value)} disabled={busy}>
              {userIds.map((u) => (
                <option key={u} value={u}>
                  {u}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="gd-share-action">Action</label>
            <select id="gd-share-action" className="select" value={share ? "share" : "unshare"} onChange={(e) => setShare(e.target.value === "share")} disabled={busy}>
              <option value="share">share</option>
              <option value="unshare">unshare</option>
            </select>
          </div>
          <NotifyCheck value={shareNotify} onChange={setShareNotify} id="gd-share-notify" />
          <button type="submit" className="btn btn-primary" disabled={busy || files.length === 0}>
            Apply
          </button>
        </form>
      </div>

      <div className="card-section">
        <h3>Edit a file (append)</h3>
        <form className="stack-sm" onSubmit={submitEdit}>
          <div className="form-row">
            <div className="field">
              <label htmlFor="gd-edit-file">File</label>
              <select id="gd-edit-file" className="select" value={editFile || firstFile} onChange={(e) => setEditFile(e.target.value)} disabled={busy}>
                {files.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.id} — {f.name}
                  </option>
                ))}
              </select>
            </div>
            <NotifyCheck value={editNotify} onChange={setEditNotify} id="gd-edit-notify" />
          </div>
          <textarea className="textarea" rows={2} placeholder="Text to append to the file" value={appendText} onChange={(e) => setAppendText(e.target.value)} disabled={busy} />
          <div>
            <button type="submit" className="btn btn-primary" disabled={busy || !appendText.trim() || files.length === 0}>
              Append to file
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
