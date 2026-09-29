"use client";

import { QUICK_ACTIONS } from "@/lib/demoActions";
import type { BeatStep, DemoBeat } from "@/lib/demoBeats";

export type BeatStatus = "idle" | "running" | "done" | "error";

function stepLabel(step: BeatStep): string {
  switch (step.kind) {
    case "admin":
      return QUICK_ACTIONS[step.action].label;
    case "ask":
      return `ask as ${step.as}`;
    case "navigate":
      return step.href.startsWith("/compare") ? "open Compare" : step.href.startsWith("/audit") ? "open the compliance console" : `open ${step.href.split("?")[0]}`;
    case "clear":
      return "clear the panes";
    case "replay":
      return "replay the last trace";
  }
}

/** The guided demo: one row per beat, the focused beat expanded with its narration and cue. */
export function BeatsPanel({
  beats,
  status,
  focusId,
  running,
  disabled,
  onFocus,
  onRun,
  onClose,
  onResetProgress,
}: {
  beats: DemoBeat[];
  status: Record<number, BeatStatus>;
  focusId: number;
  running: number | null;
  disabled: boolean;
  onFocus: (id: number) => void;
  onRun: (beat: DemoBeat) => void;
  onClose: () => void;
  onResetProgress: () => void;
}) {
  const done = beats.filter((b) => status[b.id] === "done").length;
  return (
    <aside className="beats" aria-label="Guided demo">
      <div className="beats-head">
        <div>
          <h2>Guided demo</h2>
          <div className="tiny muted">
            {done}/{beats.length} beats done · narration in lib/demoBeats.ts
          </div>
        </div>
        <button type="button" className="icon-btn" onClick={onClose} aria-label="Close the guided demo">
          <span aria-hidden="true">×</span>
        </button>
      </div>
      <ol className="beats-list">
        {beats.map((b) => {
          const st = status[b.id] ?? "idle";
          const focused = b.id === focusId;
          return (
            <li key={b.id} className={`beat ${st}${focused ? " focused" : ""}`}>
              <div className="beat-row">
                <button type="button" className="beat-title" onClick={() => onFocus(b.id)} aria-expanded={focused}>
                  <span className="beat-num" aria-hidden="true">
                    {st === "done" ? "✓" : st === "error" ? "!" : b.id}
                  </span>
                  <span className="beat-name">{b.title}</span>
                  <span className="sr-only">{st === "done" ? "(done)" : st === "error" ? "(failed)" : ""}</span>
                </button>
                <button type="button" className={`btn btn-sm${focused ? " btn-primary" : ""}`} onClick={() => onRun(b)} disabled={disabled || running !== null} aria-label={`Run beat ${b.id}: ${b.title}`}>
                  {running === b.id ? "Running…" : st === "done" ? "Run again" : "Run"}
                </button>
              </div>
              {focused && (
                <div className="beat-body">
                  <p className="beat-narration">{b.narration}</p>
                  {b.cue && (
                    <p className="beat-cue">
                      <span className="beat-cue-label">Point at</span> {b.cue}
                    </p>
                  )}
                  {b.steps.length > 0 && <div className="beat-steps tiny muted">Runs: {b.steps.map(stepLabel).join(" → ")}</div>}
                </div>
              )}
            </li>
          );
        })}
      </ol>
      <div className="beats-foot">
        <button type="button" className="btn-link small" onClick={onResetProgress} disabled={running !== null}>
          Reset progress
        </button>
      </div>
    </aside>
  );
}
