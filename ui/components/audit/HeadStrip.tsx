"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { describeError, getAuditHead, verifyAudit, type AuditHead, type VerifyReport } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { Hash, Notice } from "@/components/ui";

export function HeadStrip({ userId, refreshKey, autoVerify = false }: { userId: string; refreshKey: number; /** Run Verify chain once on mount (the guided demo's audit beat). */ autoVerify?: boolean }) {
  const [head, setHead] = useState<AuditHead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [report, setReport] = useState<VerifyReport | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  const load = useCallback(() => {
    getAuditHead(userId)
      .then((h) => {
        setHead(h);
        setError(null);
      })
      .catch((err: unknown) => setError(describeError(err)));
  }, [userId]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const verify = useCallback(async () => {
    setVerifying(true);
    setVerifyError(null);
    try {
      const r = await verifyAudit(userId);
      setReport(r);
      load();
    } catch (err: unknown) {
      setVerifyError(describeError(err));
    } finally {
      setVerifying(false);
    }
  }, [userId, load]);

  const autoVerified = useRef(false);
  useEffect(() => {
    if (!autoVerify || autoVerified.current) return;
    autoVerified.current = true;
    void verify();
  }, [autoVerify, verify]);

  const latestCheckpoint = head?.checkpoints.length ? head.checkpoints[head.checkpoints.length - 1] : null;

  return (
    <div className="card">
      <div className="row-between">
        <div className="stat-row">
          <div className="stat">
            <span className="stat-label">Entries</span>
            <span className="stat-value">{head ? head.entries : "—"}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Head</span>
            <span className="stat-value mono">
              {head?.head ? (
                <>
                  #{head.head.seq} <Hash value={head.head.entry_hash} />
                </>
              ) : (
                "—"
              )}
            </span>
            {head?.head ? <span className="tiny muted">{fmtDateTime(head.head.ts)}</span> : null}
          </div>
          <div className="stat">
            <span className="stat-label">Checkpoints</span>
            <span className="stat-value">{report ? report.checkpoints : head ? `${head.checkpoints.length}${head.checkpoints.length >= 5 ? "+" : ""}` : "—"}</span>
            {latestCheckpoint ? <span className="tiny muted">latest signed at #{latestCheckpoint.seq}</span> : null}
          </div>
          <div className="stat">
            <span className="stat-label">Signing key</span>
            <span className="stat-value mono" title={head?.public_key ? `Ed25519 public key ${head.public_key}` : undefined}>
              {head?.key_id ?? "—"}
            </span>
          </div>
        </div>
        <div className="row">
          {report && (
            <span className={report.ok ? "verify-ok" : "verify-bad"} title={report.head_hash ?? undefined}>
              {report.ok ? "OK" : "FAILED"} · {report.entries} entries · {report.checkpoints_verified}/{report.checkpoints} checkpoints verified
            </span>
          )}
          <button type="button" className="btn btn-primary" onClick={() => void verify()} disabled={verifying}>
            {verifying ? "Verifying…" : "Verify chain"}
          </button>
          <button type="button" className="btn btn-sm" onClick={load} title="Reload head">
            Refresh
          </button>
        </div>
      </div>
      {error && (
        <div style={{ marginTop: 8 }}>
          <Notice kind="error">{error}</Notice>
        </div>
      )}
      {verifyError && (
        <div style={{ marginTop: 8 }}>
          <Notice kind="error">{verifyError}</Notice>
        </div>
      )}
      {report && !report.ok && (
        <div style={{ marginTop: 8 }}>
          <Notice kind="error">
            <div className="strong">Chain verification failed</div>
            <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
              {report.problems.map((p, i) => (
                <li key={i}>{p}</li>
              ))}
            </ul>
            <div className="small" style={{ marginTop: 4 }}>
              first broken seq: {report.first_broken_seq ?? "—"} · gap after seq: {report.gap_after_seq ?? "—"} · bad checkpoint seq: {report.bad_checkpoint_seq ?? "—"}
            </div>
          </Notice>
        </div>
      )}
      {report && report.ok && (
        <div style={{ marginTop: 8 }}>
          <Notice kind="ok">
            Every entry hash recomputes, every link and sequence number is intact, and all {report.checkpoints_verified} Ed25519 checkpoint signature{report.checkpoints_verified === 1 ? "" : "s"} verify against key <span className="mono">{head?.key_id}</span>. Head hash <Hash value={report.head_hash} length={16} />.
          </Notice>
        </div>
      )}
    </div>
  );
}
