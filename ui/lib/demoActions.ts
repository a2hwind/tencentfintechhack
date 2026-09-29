/**
 * Admin quick actions used by the presenter view and the guided demo. Each acts on the mock
 * platforms as the `admin` user and resolves to a one-line result for a toast.
 */

import { STEP4_TEXT } from "@/components/admin/ScenarioCard";
import { editConfluencePage, pauseSync, resetDemo, resumeSync, setSlackMembership } from "./api";

export const ADMIN_ID = "admin";
export const AUDITOR_ID = "compliance";

export type QuickActionKey = "revoke" | "revoke-silent" | "freshness" | "restore" | "reset";

export interface QuickAction {
  key: QuickActionKey;
  label: string;
  hint: string;
  run: (adminId: string) => Promise<string>;
}

export const QUICK_ACTIONS: Record<QuickActionKey, QuickAction> = {
  revoke: {
    key: "revoke",
    label: "Revoke Jane from #db-migration",
    hint: "Slack removes jdoe from C0DBM and the membership webhook is delivered: her entitlements are invalidated.",
    run: async (adminId) => {
      const r = await setSlackMembership(adminId, "C0DBM", { user_id: "jdoe", member: false, notify: true });
      return `Removed jdoe from #${r.name} (webhook delivered)`;
    },
  },
  "revoke-silent": {
    key: "revoke-silent",
    label: "Revoke silently (missed webhook)",
    hint: "Removes jdoe from C0DBM with notify=false: no event, so cached entitlements stay warm and Gate 2 must catch it.",
    run: async (adminId) => {
      const r = await setSlackMembership(adminId, "C0DBM", { user_id: "jdoe", member: false, notify: false });
      return `Removed jdoe from #${r.name} silently (webhook missed)`;
    },
  },
  freshness: {
    key: "freshness",
    label: "Freshness: pause sync + add step 4 to runbook 8812",
    hint: "Pauses the sync worker, then appends the failover step to Confluence page 8812: the index is now stale.",
    run: async (adminId) => {
      await pauseSync(adminId);
      const r = await editConfluencePage(adminId, "8812", { append: STEP4_TEXT, notify: true });
      return `Sync paused · runbook 8812 is v${r.version} at Confluence, index still v${r.indexed_version ?? "?"}`;
    },
  },
  restore: {
    key: "restore",
    label: "Restore Jane + resume sync",
    hint: "Adds jdoe back to C0DBM (webhook delivered) and resumes the sync worker.",
    run: async (adminId) => {
      const r = await setSlackMembership(adminId, "C0DBM", { user_id: "jdoe", member: true, notify: true });
      await resumeSync(adminId);
      return `jdoe is back in #${r.name} · sync resumed`;
    },
  },
  reset: {
    key: "reset",
    label: "Reset demo",
    hint: "Reloads Company A's fixture, rebuilds the index and clears caches. The audit log keeps everything.",
    run: async (adminId) => {
      const r = await resetDemo(adminId);
      return `Demo reset · platforms reloaded, index rebuilt (audit #${r.audit_seq})`;
    },
  },
};

/** The quick actions shown as buttons; Reset lives in the toolbar. */
export const QUICK_ACTION_ROW: QuickActionKey[] = ["revoke", "revoke-silent", "freshness", "restore"];
