"use client";

import { PLATFORMS, type Health } from "@/lib/api";
import { platformName } from "@/lib/format";
import { PlatformBadge } from "@/components/ui";

/** Mock or real, per platform, as GET /health reports it. */
export function ConnectorsCard({ health, error }: { health: Health | null; error: string | null }) {
  const connectors = health?.connectors;
  return (
    <div className="card">
      <div className="card-header">
        <h2>Connectors</h2>
        <span className="card-sub">Every platform sits behind the same adapter interface; a real connector replaces its mock without touching the gates.</span>
      </div>
      {error && <p className="small muted">Health unavailable: {error}</p>}
      {!error && !health && <p className="small muted">Loading…</p>}
      {health && !connectors && <p className="small muted">This API does not report connector modes (all platforms are the in-process mocks).</p>}
      {connectors && (
        <div className="connector-grid">
          {PLATFORMS.map((p) => {
            const mode = connectors[p] ?? "mock";
            return (
              <div key={p} className="connector">
                <PlatformBadge platform={p} />
                <span className="connector-name">{platformName(p)}</span>
                <span className={`badge ${mode === "real" ? "badge-allow" : "badge-muted"}`}>{mode}</span>
              </div>
            );
          })}
        </div>
      )}
      {health?.dlp && (
        <p className="tiny muted" style={{ marginTop: 8 }}>
          DLP ingest version {health.dlp.ingest_version}
          {health.vector_index ? ` · vector index ${health.vector_index.rows} rows over ${health.vector_index.items} items, ${health.vector_index.principals} principals, dim ${health.vector_index.dim}` : ""}
        </p>
      )}
    </div>
  );
}
