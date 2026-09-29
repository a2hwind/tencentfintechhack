"use client";

import { useEffect } from "react";
import type { StreamEntry } from "@/lib/api";
import { severityOf } from "@/lib/format";

/** How long a banner stays, by severity. */
const SHOW_MS: Record<string, number> = { high: 10000, medium: 8000, low: 5000 };

/** Transient banner for an alert that just landed in the chain (compliance view). */
export function AlertBanner({ alert, onDismiss, onTrace }: { alert: StreamEntry | null; onDismiss: () => void; onTrace: (seq: number) => void }) {
  useEffect(() => {
    if (!alert) return;
    const timer = window.setTimeout(onDismiss, SHOW_MS[severityOf(alert.event?.["severity"])] ?? 8000);
    return () => window.clearTimeout(timer);
  }, [alert, onDismiss]);

  if (!alert) return null;
  const ev = alert.event ?? {};
  const severity = severityOf(ev["severity"]);
  const title = typeof ev["title"] === "string" ? ev["title"] : String(ev["rule"] ?? "alert");
  const evidence = Array.isArray(ev["evidence_seqs"]) ? ev["evidence_seqs"].filter((v): v is number => typeof v === "number") : [];
  const latest = evidence.length ? evidence[evidence.length - 1] : null;

  return (
    <div key={alert.seq} className={`alert-banner sev-${severity}`} role="alert">
      <div className="alert-banner-main">
        <div className="alert-banner-kicker">
          Compliance view · alert engine · audit #{alert.seq}
        </div>
        <div className="alert-banner-title">
          New {severity} alert: {title}
        </div>
      </div>
      <div className="alert-banner-actions">
        {latest !== null && (
          <button type="button" className="btn btn-sm" onClick={() => onTrace(latest)}>
            Trace #{latest}
          </button>
        )}
        <button type="button" className="icon-btn" aria-label="Dismiss alert" onClick={onDismiss}>
          <span aria-hidden="true">×</span>
        </button>
      </div>
    </div>
  );
}
