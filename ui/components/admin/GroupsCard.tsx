"use client";

import { useState, type FormEvent } from "react";
import { setGroupMembership } from "@/lib/api";
import { MemberChips, NotifyCheck, type RunAction } from "./common";

export function GroupsCard({ userId, groups, userIds, run, busy }: { userId: string; groups: Record<string, string[]>; userIds: string[]; run: RunAction; busy: boolean }) {
  const names = Object.keys(groups);
  const [group, setGroup] = useState(names[0] ?? "");
  const [customGroup, setCustomGroup] = useState("");
  const [user, setUser] = useState(userIds[0] ?? "jdoe");
  const [member, setMember] = useState(true);
  const [notify, setNotify] = useState(true);

  const target = group === "__new__" ? customGroup.trim() : group || names[0] || "";

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!target || !user) return;
    void run(`Group ${member ? "add" : "remove"} ${user} ${member ? "to" : "from"} ${target}${notify ? "" : " (no notify)"}`, () => setGroupMembership(userId, target, { user_id: user, member, notify }));
  };

  return (
    <div className="card">
      <div className="card-header">
        <h2>Directory groups</h2>
        <span className="card-sub">Groups grant Confluence spaces, Drive membership and Jira roles; a change fans out as a membership event per platform.</span>
      </div>
      <div className="table-wrap">
        <table className="table table-compact">
          <thead>
            <tr>
              <th>Group</th>
              <th>Members</th>
            </tr>
          </thead>
          <tbody>
            {names.map((g) => (
              <tr key={g}>
                <td className="mono">{g}</td>
                <td>
                  <MemberChips members={groups[g]} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card-section">
        <h3>Change membership</h3>
        <form className="form-row" onSubmit={submit}>
          <div className="field">
            <label htmlFor="grp-group">Group</label>
            <select id="grp-group" className="select" value={group || names[0] || ""} onChange={(e) => setGroup(e.target.value)} disabled={busy}>
              {names.map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
              <option value="__new__">new group…</option>
            </select>
          </div>
          {group === "__new__" && (
            <div className="field">
              <label htmlFor="grp-new">Name</label>
              <input id="grp-new" className="input mono" placeholder="group-name" value={customGroup} onChange={(e) => setCustomGroup(e.target.value)} disabled={busy} />
            </div>
          )}
          <div className="field">
            <label htmlFor="grp-user">User</label>
            <select id="grp-user" className="select" value={user} onChange={(e) => setUser(e.target.value)} disabled={busy}>
              {userIds.map((u) => (
                <option key={u} value={u}>
                  {u}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="grp-action">Action</label>
            <select id="grp-action" className="select" value={member ? "add" : "remove"} onChange={(e) => setMember(e.target.value === "add")} disabled={busy}>
              <option value="add">add</option>
              <option value="remove">remove</option>
            </select>
          </div>
          <NotifyCheck value={notify} onChange={setNotify} id="grp-notify" />
          <button type="submit" className="btn btn-primary" disabled={busy || !target}>
            Apply
          </button>
        </form>
      </div>
    </div>
  );
}
