"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { describeError, getAdminIndex, getAdminState, getHealth, type AdminState, type Health, type IndexItem } from "@/lib/api";
import { useUser } from "@/lib/user";
import { Notice, Spinner } from "@/components/ui";
import { ActionLog, type ActionRecord, type RunAction } from "@/components/admin/common";
import { SyncCard } from "@/components/admin/SyncCard";
import { ScenarioCard } from "@/components/admin/ScenarioCard";
import { SlackCard } from "@/components/admin/SlackCard";
import { ConfluenceCard } from "@/components/admin/ConfluenceCard";
import { JiraCard } from "@/components/admin/JiraCard";
import { DriveCard } from "@/components/admin/DriveCard";
import { GroupsCard } from "@/components/admin/GroupsCard";
import { IndexCard } from "@/components/admin/IndexCard";
import { EventsCard } from "@/components/admin/EventsCard";
import { ConnectorsCard } from "@/components/admin/ConnectorsCard";

const MAX_LOG = 20;

export default function AdminPage() {
  const { userId, hasRole, usersLoaded, ready } = useUser();
  const isAdmin = hasRole("admin");

  const [state, setState] = useState<AdminState | null>(null);
  const [index, setIndex] = useState<IndexItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<ActionRecord[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const nextId = useRef(1);

  const refresh = useCallback(async () => {
    setLoading(true);
    getHealth()
      .then((h) => {
        setHealth(h);
        setHealthError(null);
      })
      .catch((err: unknown) => setHealthError(describeError(err)));
    try {
      const [s, i] = await Promise.all([getAdminState(userId), getAdminIndex(userId)]);
      setState(s);
      setIndex(i);
      setError(null);
    } catch (err: unknown) {
      setError(describeError(err));
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    if (!ready || !isAdmin) return;
    void refresh();
  }, [ready, isAdmin, refresh]);

  const run: RunAction = useCallback(
    async (label, fn) => {
      setBusy(true);
      const id = nextId.current++;
      const ts = new Date().toISOString();
      try {
        const result = await fn();
        setLog((prev) => [{ id, label, ts, ok: true, result }, ...prev].slice(0, MAX_LOG));
        return result;
      } catch (err: unknown) {
        setLog((prev) => [{ id, label, ts, ok: false, result: { error: describeError(err) } }, ...prev].slice(0, MAX_LOG));
        return undefined;
      } finally {
        setBusy(false);
        void refresh();
      }
    },
    [refresh],
  );

  if (!ready || !usersLoaded) {
    return <Spinner label="Loading identity…" />;
  }

  if (!isAdmin) {
    return (
      <div className="stack">
        <div className="page-title">
          <h1>Admin panel</h1>
          <span className="badge badge-amber">demo only</span>
        </div>
        <Notice kind="warn">
          <b>Switch to the admin user to open the admin panel.</b> It mutates the mock platforms (Confluence, Jira, Slack, Drive) and the sync worker, and requires the <span className="mono">admin</span> role.
        </Notice>
      </div>
    );
  }

  const company = state?.company;
  const userIds = company ? company.users.map((u) => u.id) : [];
  const groupNames = company ? Object.keys(company.groups) : [];

  return (
    <div className="stack">
      <div className="page-title">
        <h1>Admin panel</h1>
        <span className="badge badge-amber">demo only</span>
        <span className="muted small">Mutates the mock platforms exactly as their real webhooks would. State refreshes after every action.</span>
        <span style={{ marginLeft: "auto" }} className="row">
          {loading && <Spinner label="Refreshing…" />}
          <button type="button" className="btn btn-sm" onClick={() => void refresh()} disabled={loading}>
            Refresh
          </button>
        </span>
      </div>

      {error && <Notice kind="error">{error}</Notice>}

      {state && company ? (
        <>
          <div className="grid-2">
            <SyncCard userId={userId} sync={state.sync} cache={state.entitlement_cache} run={run} busy={busy} />
            <ScenarioCard userId={userId} run={run} busy={busy} />
          </div>

          <ConnectorsCard health={health} error={healthError} />

          <div className="card">
            <div className="card-header">
              <h2>Last action</h2>
              {busy && <Spinner label="Running…" />}
            </div>
            <ActionLog records={log} />
          </div>

          <SlackCard userId={userId} channels={company.slack.channels} threads={company.slack.threads} users={company.users} run={run} busy={busy} mode={health?.connectors?.slack ?? "mock"} status={health?.slack} />
          <ConfluenceCard userId={userId} spaces={company.confluence.spaces} pages={company.confluence.pages} userIds={userIds} groupNames={groupNames} run={run} busy={busy} />
          <JiraCard userId={userId} projects={company.jira.projects} issues={company.jira.issues} userIds={userIds} run={run} busy={busy} />
          <DriveCard userId={userId} drives={company.gdrive.drives} folders={company.gdrive.folders} files={company.gdrive.files} userIds={userIds} run={run} busy={busy} />
          <GroupsCard userId={userId} groups={company.groups} userIds={userIds} run={run} busy={busy} />
          <IndexCard userId={userId} items={index} run={run} busy={busy} />
          <EventsCard events={state.events} />
        </>
      ) : (
        !error && <Spinner label="Loading platform state…" />
      )}
    </div>
  );
}
