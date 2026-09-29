"use client";

import { getSyncStatus, invalidateEntitlements, pauseSync, resumeSync, runSync, type EntitlementCacheStats, type SyncStatus } from "@/lib/api";
import { fmtDateTime, platformLabel } from "@/lib/format";
import { PlatformBadge } from "@/components/ui";
import type { RunAction } from "./common";

export function SyncCard({ userId, sync, cache, run, busy }: { userId: string; sync: SyncStatus; cache: EntitlementCacheStats; run: RunAction; busy: boolean }) {
  const totalItems = Object.values(sync.index.items).reduce((a, b) => a + b, 0);
  return (
    <div className="card">
      <div className="card-header">
        <h2>
          Sync
          {sync.paused ? <span className="badge badge-amber">paused</span> : <span className="badge badge-allow">running</span>}
        </h2>
        <div className="row">
          <button type="button" className="btn btn-sm" disabled={busy || sync.paused} onClick={() => void run("Pause sync", () => pauseSync(userId))}>
            Pause
          </button>
          <button type="button" className="btn btn-sm" disabled={busy || !sync.paused} onClick={() => void run("Resume sync", () => resumeSync(userId))}>
            Resume
          </button>
          <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={() => void run("Run sync now", () => runSync(userId))}>
            Run now
          </button>
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void run("Invalidate entitlement cache", () => invalidateEntitlements(userId))}>
            Invalidate entitlement cache
          </button>
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void run("Sync status", () => getSyncStatus(userId))} title="GET /admin/sync/status">
            Status
          </button>
        </div>
      </div>
      <div className="stat-row" style={{ marginBottom: 12 }}>
        <div className="stat">
          <span className="stat-label">Runs</span>
          <span className="stat-value">{sync.runs}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Last run</span>
          <span className="stat-value" style={{ fontSize: 12.5 }}>
            {fmtDateTime(sync.last_run)}
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Interval</span>
          <span className="stat-value">{sync.interval_s}s</span>
        </div>
        <div className="stat">
          <span className="stat-label">Pending events</span>
          <span className="stat-value">{sync.pending_events}</span>
        </div>
        <div className="stat">
          <span className="stat-label">Index</span>
          <span className="stat-value">
            {totalItems} items · {sync.index.chunks} chunks
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">Entitlement cache</span>
          <span className="stat-value" style={{ fontSize: 12.5 }}>
            {cache.hits} hits · {cache.misses} misses · {cache.invalidations} invalidations
          </span>
        </div>
      </div>
      <div className="table-wrap">
        <table className="table table-compact">
          <thead>
            <tr>
              <th>Platform</th>
              <th>Cursor</th>
              <th>Last run</th>
              <th className="num">Items synced</th>
              <th className="num">Indexed</th>
            </tr>
          </thead>
          <tbody>
            {sync.platforms.map((p) => (
              <tr key={p.platform}>
                <td>
                  <PlatformBadge platform={p.platform} /> <span className="muted small">{platformLabel(p.platform).toLowerCase()}</span>
                </td>
                <td className="mono small nowrap">{p.cursor ?? "—"}</td>
                <td className="nowrap">{fmtDateTime(p.last_run)}</td>
                <td className="num">{p.items_synced}</td>
                <td className="num">{sync.index.items[p.platform] ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
