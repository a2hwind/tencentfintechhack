"use client";

import { editConfluencePage, pauseSync, resetDemo, resumeSync, setSlackMembership } from "@/lib/api";
import type { RunAction } from "./common";

export const STEP4_TEXT = "Step 4 (failover): promote the standby gateway in ap-southeast-1b and shift the DNS weight to 100% on the standby; roll back when the primary passes health checks.";

interface Scenario {
  key: string;
  title: string;
  detail: string;
  label: string;
  run: (userId: string) => Promise<unknown>;
}

const SCENARIOS: Scenario[] = [
  {
    key: "s2",
    title: "Scenario 2 — pause sync, then add failover step 4 to runbook 8812",
    detail: "The index is now stale; the next question about the runbook must show the live version.",
    label: "Scenario 2: pause sync + edit runbook 8812",
    run: async (userId) => {
      const pause = await pauseSync(userId);
      const edit = await editConfluencePage(userId, "8812", { append: STEP4_TEXT, notify: true });
      return { pause, edit };
    },
  },
  {
    key: "s4",
    title: "Scenario 4 — remove jdoe from #db-migration (C0DBM), event delivered",
    detail: "The membership webhook lands: the entitlement cache is invalidated and Gate 1 stops returning the channel.",
    label: "Scenario 4: remove jdoe from C0DBM (notify)",
    run: (userId) => setSlackMembership(userId, "C0DBM", { user_id: "jdoe", member: false, notify: true }),
  },
  {
    key: "missed",
    title: "Missed-webhook variant — remove jdoe from C0DBM with notify=false",
    detail: "No event arrives, so the cache stays warm and Gate 1 still returns the channel. Gate 2 must catch it live.",
    label: "Missed webhook: remove jdoe from C0DBM (no notify)",
    run: (userId) => setSlackMembership(userId, "C0DBM", { user_id: "jdoe", member: false, notify: false }),
  },
  {
    key: "restore",
    title: "Restore — add jdoe back to C0DBM and resume sync",
    detail: "Undoes the scenario 4 beats only.",
    label: "Restore: add jdoe to C0DBM + resume sync",
    run: async (userId) => {
      const membership = await setSlackMembership(userId, "C0DBM", { user_id: "jdoe", member: true, notify: true });
      const sync = await resumeSync(userId);
      return { membership, sync };
    },
  },
  {
    key: "reset",
    title: "Reset demo — reload the fixture and rebuild the index",
    detail: "Every platform back to Company A's starting state, caches cleared, sync resumed. The audit log keeps everything (the reset is an entry too).",
    label: "Reset demo to the fixture",
    run: (userId) => resetDemo(userId),
  },
];

export function ScenarioCard({ userId, run, busy }: { userId: string; run: RunAction; busy: boolean }) {
  return (
    <div className="card">
      <div className="card-header">
        <h2>Scenario shortcuts</h2>
        <span className="card-sub">Each button runs one demo beat and shows the JSON result below.</span>
      </div>
      <div className="scenario-grid">
        {SCENARIOS.map((s) => (
          <button key={s.key} type="button" className="btn scenario-btn" disabled={busy} onClick={() => void run(s.label, () => s.run(userId))}>
            <span className="strong">{s.title}</span>
            <span className="muted">{s.detail}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
