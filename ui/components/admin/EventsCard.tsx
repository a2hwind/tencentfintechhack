"use client";

import type { PlatformEvent } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { Empty, PlatformBadge } from "@/components/ui";

export function EventsCard({ events }: { events: PlatformEvent[] }) {
  const newestFirst = [...events].reverse();
  return (
    <div className="card">
      <div className="card-header">
        <h2>
          Recent events <span className="muted small" style={{ fontWeight: 400 }}>· last {events.length}</span>
        </h2>
        <span className="card-sub">What the mock platforms emitted as webhooks (mutations with notify=false do not appear here).</span>
      </div>
      {newestFirst.length === 0 ? (
        <Empty>No events yet.</Empty>
      ) : (
        <div className="events-list">
          {newestFirst.map((e, i) => (
            <div key={`${e.ts}-${i}`} className="event-row">
              <span className="nowrap muted">{fmtDateTime(e.ts)}</span>
              <span>
                <PlatformBadge platform={e.platform} />
              </span>
              <span className={`badge ${e.kind === "membership_changed" ? "badge-amber" : e.kind === "item_deleted" ? "badge-deny" : "badge-accent"}`}>{e.kind}</span>
              <span className="mono small break">{JSON.stringify(e.payload)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
