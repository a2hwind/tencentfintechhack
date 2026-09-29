"use client";

import { TIMING_STAGES, type StageTimings, type TimingStage } from "@/lib/api";
import { fmtMs } from "@/lib/format";

const LABELS: Record<TimingStage, string> = {
  entitlements: "identity",
  plan: "plan",
  gate1: "gate 1",
  expand: "expand",
  gate2: "gate 2",
  assemble: "assemble",
  answer: "answer",
  guard: "guard",
};

/** Model calls (untrusted compute) are drawn apart from the deterministic control-plane stages. */
const MODEL_STAGES: TimingStage[] = ["plan", "answer"];

/** Horizontal stacked bar of the measured stage timings of one query, labelled where a segment is wide enough. */
export function StageTimingBar({ timings, total }: { timings: StageTimings; total?: number | null }) {
  const parts = TIMING_STAGES.map((stage) => ({ stage, ms: timings[stage] ?? 0 })).filter((p) => p.ms > 0);
  const sum = parts.reduce((acc, p) => acc + p.ms, 0);
  if (parts.length === 0 || sum <= 0) return <span className="muted small">No stage timings recorded.</span>;
  const summary = parts.map((p) => `${LABELS[p.stage]} ${fmtMs(p.ms)}`).join(", ");
  return (
    <div className="timing">
      <div className="timing-bar" role="img" aria-label={`Stage timings: ${summary}`}>
        {parts.map((p) => {
          const pct = (p.ms / sum) * 100;
          return (
            <div key={p.stage} className={`timing-seg seg-${p.stage}${MODEL_STAGES.includes(p.stage) ? " model" : ""}`} style={{ flexGrow: p.ms, flexBasis: 0 }} title={`${LABELS[p.stage]}: ${fmtMs(p.ms)} (${pct.toFixed(0)}%)`}>
              {pct >= 11 ? <span className="timing-seg-label">{LABELS[p.stage]}</span> : null}
            </div>
          );
        })}
      </div>
      <div className="timing-legend">
        {parts.map((p) => (
          <span key={p.stage}>
            <i className={`timing-swatch seg-${p.stage}${MODEL_STAGES.includes(p.stage) ? " model" : ""}`} aria-hidden="true" />
            {LABELS[p.stage]} <span className="mono">{fmtMs(p.ms)}</span>
          </span>
        ))}
        <span className="muted">
          sum <span className="mono">{fmtMs(sum)}</span>
          {total !== null && total !== undefined ? (
            <>
              {" "}
              · end to end <span className="mono">{fmtMs(total)}</span>
            </>
          ) : null}
        </span>
      </div>
    </div>
  );
}
