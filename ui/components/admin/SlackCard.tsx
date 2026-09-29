"use client";

import { useState } from "react";
import { setSlackMembership, type SlackChannel, type SlackStatus, type SlackThread, type SnapshotUser } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { Collapsible, Notice } from "@/components/ui";
import { MemberChips, type RunAction } from "./common";

export function SlackCard({ userId, channels, threads, users, run, busy, mode = "mock", status }: { userId: string; channels: SlackChannel[]; threads: SlackThread[]; users: SnapshotUser[]; run: RunAction; busy: boolean; /** "real" when the API is connected to a real Slack workspace. */ mode?: string; status?: SlackStatus }) {
  if (mode === "real") {
    const team = status?.team;
    return (
      <div className="card">
        <div className="card-header">
          <h2>
            Slack <span className="badge badge-allow">real</span>
          </h2>
          <span className="card-sub">Membership is the ACL: private channels are readable only by their members, public channels by every full member.</span>
        </div>
        <Notice kind="info">
          Connected to a real Slack workspace: change membership in Slack; events arrive at <span className="mono">/webhooks/slack</span>.
        </Notice>
        {status && (
          <dl className="kv" style={{ marginTop: 12 }}>
            <dt>Workspace</dt>
            <dd>{team ? `${team.name ?? team.id} (${team.id})` : <span className="muted">not connected: check SLACK_BOT_TOKEN</span>}</dd>
            <dt>Threads tracked</dt>
            <dd className="num">{status.threads_tracked ?? 0}</dd>
            <dt>Events webhook</dt>
            <dd>{status.webhook ? (status.last_event ? `on, last event ${fmtDateTime(status.last_event)}` : "on, no events yet") : "off (set SLACK_SIGNING_SECRET)"}</dd>
            <dt>Web API calls</dt>
            <dd className="num">
              {status.calls ?? 0}
              {status.rate_limited ? ` · ${status.rate_limited} rate-limited, waited ${Math.round(status.waited_s ?? 0)} s` : ""}
            </dd>
            <dt>Mapped users</dt>
            <dd>{status.mapped_users?.length ? status.mapped_users.join(", ") : <span className="muted">none (set SLACK_USER_MAP)</span>}</dd>
          </dl>
        )}
      </div>
    );
  }
  return (
    <div className="card">
      <div className="card-header">
        <h2>Slack channels</h2>
        <span className="card-sub">Membership is the ACL: private channels and DMs are readable only by their members.</span>
      </div>
      <div className="table-wrap">
        <table className="table table-compact">
          <thead>
            <tr>
              <th>Channel</th>
              <th>Members</th>
              <th>Change membership</th>
            </tr>
          </thead>
          <tbody>
            {channels.map((c) => (
              <tr key={c.id}>
                <td>
                  <div className="strong">{c.is_dm ? c.name : `#${c.name}`}</div>
                  <div className="row" style={{ gap: 4 }}>
                    <span className="mono small muted">{c.id}</span>
                    {c.private && <span className="badge badge-muted">private</span>}
                    {c.is_dm && <span className="badge badge-muted">dm</span>}
                  </div>
                </td>
                <td>
                  <MemberChips members={c.members} />
                </td>
                <td>
                  <MembershipControl channel={c} users={users} disabled={busy} onApply={(user, member, notify) => void run(`Slack ${member ? "add" : "remove"} ${user} ${member ? "to" : "from"} ${c.id}${notify ? "" : " (no notify)"}`, () => setSlackMembership(userId, c.id, { user_id: user, member, notify }))} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ marginTop: 12 }}>
        <Collapsible title={`Indexed threads (${threads.length})`}>
          <div className="table-wrap">
            <table className="table table-compact">
              <thead>
                <tr>
                  <th>Item</th>
                  <th>Channel</th>
                  <th>Title</th>
                  <th className="num">Ver.</th>
                  <th>Modified</th>
                </tr>
              </thead>
              <tbody>
                {threads.map((t) => (
                  <tr key={t.item_id} className={t.deleted ? "deleted-row" : undefined}>
                    <td className="mono small">{t.item_id}</td>
                    <td className="mono small">{t.channel}</td>
                    <td className="truncate" style={{ maxWidth: 360 }} title={t.title}>
                      {t.title}
                    </td>
                    <td className="num">{t.version}</td>
                    <td className="nowrap">{fmtDateTime(t.last_modified)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Collapsible>
      </div>
    </div>
  );
}

function MembershipControl({ channel, users, disabled, onApply }: { channel: SlackChannel; users: SnapshotUser[]; disabled: boolean; onApply: (user: string, member: boolean, notify: boolean) => void }) {
  const [user, setUser] = useState<string>(users[0]?.id ?? "jdoe");
  const [member, setMember] = useState<boolean>(!channel.members.includes(users[0]?.id ?? ""));
  const [notify, setNotify] = useState(true);
  return (
    <div className="member-control">
      <select className="select" value={user} onChange={(e) => setUser(e.target.value)} disabled={disabled} aria-label="User">
        {users.map((u) => (
          <option key={u.id} value={u.id}>
            {u.id}
            {channel.members.includes(u.id) ? " (member)" : ""}
          </option>
        ))}
      </select>
      <select className="select" value={member ? "add" : "remove"} onChange={(e) => setMember(e.target.value === "add")} disabled={disabled} aria-label="Action">
        <option value="add">add</option>
        <option value="remove">remove</option>
      </select>
      <label className="check" title="Unchecked simulates a missed webhook">
        <input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} disabled={disabled} />
        notify
      </label>
      <button type="button" className="btn btn-sm btn-primary" disabled={disabled} onClick={() => onApply(user, member, notify)}>
        Apply
      </button>
    </div>
  );
}
