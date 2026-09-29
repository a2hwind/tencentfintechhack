/**
 * The guided demo: questions, compare presets and the beats the presenter view runs.
 * Edit narration here; every beat's steps run in order when its Run button is pressed.
 */

import type { QuickActionKey } from "./demoActions";

export const QUESTIONS = {
  migration: "What's the status of the database migration and were there blockers raised in Slack last week?",
  runbook: "What's the latest runbook for the payment-service incident?",
  breach: "Where is the Q3 breach report?",
  control: "Where is the Q9 llama report?",
  probe: "Summarise the Q3 breach incident report.",
  stitch: "Root cause of the payment outage last quarter and the follow-up tickets",
  dlp: "What card was charged twice in the payments channel?",
  auditInquiry: "Everything jdoe accessed in the payment gateway space in the last 30 days",
} as const;

export interface ExampleQuestion {
  key: string;
  /** Short chip label; the full question is the chip's tooltip. */
  label: string;
  asker: string;
  question: string;
}

/** One-click examples in the presenter toolbar: each sets the asker and asks. */
export const EXAMPLES: ExampleQuestion[] = [
  { key: "migration", label: "DB migration status", asker: "jdoe", question: QUESTIONS.migration },
  { key: "runbook", label: "Latest payment runbook", asker: "jdoe", question: QUESTIONS.runbook },
  { key: "breach", label: "Q3 breach report", asker: "ctr-lee", question: QUESTIONS.breach },
  { key: "control", label: "Q9 llama report (control)", asker: "ctr-lee", question: QUESTIONS.control },
  { key: "probe", label: "Summarise the Q3 breach", asker: "ctr-lee", question: QUESTIONS.probe },
  { key: "stitch", label: "Payment outage + tickets", asker: "jdoe", question: QUESTIONS.stitch },
  { key: "dlp", label: "Card charged twice", asker: "jdoe", question: QUESTIONS.dlp },
];

// ---------------------------------------------------------------------------
// Compare presets (/compare?preset=<key>)
// ---------------------------------------------------------------------------
export interface ComparePreset {
  key: string;
  label: string;
  /** What the audience should notice. */
  lookFor: string;
  a: { user: string; question: string };
  b: { user: string; question: string };
}

export const COMPARE_PRESETS: ComparePreset[] = [
  {
    key: "restricted-vs-missing",
    label: "Restricted vs non-existent",
    lookFor: "A contractor asks for a report that exists but is restricted, and for one that does not exist. The bodies must match byte for byte.",
    a: { user: "ctr-lee", question: QUESTIONS.breach },
    b: { user: "ctr-lee", question: QUESTIONS.control },
  },
  {
    key: "same-question-different-access",
    label: "Same question, different access",
    lookFor: "The security lead can read the breach report; the contractor gets the uniform no-result, with no hint that it exists.",
    a: { user: "sec-ho", question: QUESTIONS.breach },
    b: { user: "ctr-lee", question: QUESTIONS.breach },
  },
  {
    key: "engineer-vs-contractor",
    label: "Engineer vs contractor",
    lookFor: "Same question, answered only from what each person can read right now.",
    a: { user: "jdoe", question: QUESTIONS.migration },
    b: { user: "ctr-lee", question: QUESTIONS.migration },
  },
];

export function comparePreset(key: string | null | undefined): ComparePreset | undefined {
  return COMPARE_PRESETS.find((p) => p.key === key);
}

// ---------------------------------------------------------------------------
// Beats
// ---------------------------------------------------------------------------
export type BeatStep =
  /** An admin quick action (runs as the admin user). */
  | { kind: "admin"; action: QuickActionKey }
  /**
   * Ask a question as a user; the answer shows on the left and its trace on the right.
   * `expectAlert` waits briefly for that alert rule on the live chain and explains if it does not come.
   */
  | { kind: "ask"; as: string; question: string; expectAlert?: { rule: string; missing: string } }
  /** Go to another page; `actAs` switches the app-wide identity first (e.g. compliance for /audit). */
  | { kind: "navigate"; href: string; actAs?: string }
  /** Clear the presenter's answers and trace. */
  | { kind: "clear" }
  /** Replay the last trace in the diagram and stepper. */
  | { kind: "replay" };

export interface DemoBeat {
  id: number;
  title: string;
  /** What the presenter says: one to three sentences. */
  narration: string;
  /** Where to point on screen once the beat has run. */
  cue?: string;
  steps: BeatStep[];
}

const PRESENTER = "from=presenter";

export const DEMO_BEATS: DemoBeat[] = [
  {
    id: 0,
    title: "Reset and frame it",
    narration:
      "Internal Brain answers questions across Confluence, Jira, Slack and Drive, but only from what the asker can read right now. On the left is what the employee sees; on the right is what compliance sees, live from the hash-chained audit log.",
    cue: "The two panes and the live chain. The reset reloads the fixture (same document ids, so earlier citations still open) and starts a fresh session for the insider-threat rules; the sensitive-data alerts from ingestion stay on record for beat 8.",
    steps: [{ kind: "admin", action: "reset" }, { kind: "clear" }],
  },
  {
    id: 1,
    title: "Scenario 1: one answer across four tools",
    narration:
      "Jane asks about the database migration. The answer stitches Jira, Drive and Slack, and every sentence cites its source. Click a number and you land on the exact passage, re-checked live.",
    cue: "Gate 1 in red: documents the index found that Jane cannot read. They never reached the model.",
    steps: [{ kind: "ask", as: "jdoe", question: QUESTIONS.migration }],
  },
  {
    id: 2,
    title: "Scenario 3: restricted looks exactly like missing",
    narration:
      "A contractor asks for the Q3 breach report, which exists but is restricted, and for a report that does not exist at all. The two responses are byte-identical: a denial reveals nothing, not even that the document exists.",
    cue: "The green banner: same bytes, same size. The reason lives only in the audit log.",
    steps: [{ kind: "navigate", href: `/compare?preset=restricted-vs-missing&run=1&${PRESENTER}` }],
  },
  {
    id: 3,
    title: "Probing raises an alert",
    narration:
      "The contractor keeps digging. They still get the same polite no-result, but the alert engine sees repeated blocked questions aimed at the security space and raises a high-severity alert, itself a chained audit entry.",
    cue: "The red banner and the alert row in the live chain.",
    steps: [
      {
        kind: "ask",
        as: "ctr-lee",
        question: QUESTIONS.probe,
        expectAlert: {
          rule: "container_probe",
          missing:
            "No new probing alert: the rule needs two blocked questions within 30 minutes (run beat 2 first), and it raises one probing alert per asker per 30 minutes within a demo session. Reset (beat 0) starts a new session; earlier alerts are in the compliance console.",
        },
      },
    ],
  },
  {
    id: 4,
    title: "Scenario 4: revocation takes effect immediately",
    narration:
      "An admin removes Jane from #db-migration in Slack. The webhook invalidates her entitlements, so when she asks again the Slack thread is simply gone. The audit says why: rule revoked.",
    cue: "Open an earlier answer and click its #db-migration citation: now unavailable, and that open is logged too.",
    steps: [
      { kind: "admin", action: "revoke" },
      { kind: "ask", as: "jdoe", question: QUESTIONS.migration },
    ],
  },
  {
    id: 5,
    title: "Missed webhook: Gate 2 catches it",
    narration:
      "Now the worst case. Jane is restored and asks once, so her entitlements are cached. Then she is removed again, but the webhook is lost: Gate 1 still passes the thread on the warm cache, and Gate 2, checking Slack live just before answering, drops it. Fail closed.",
    cue: "Gate 2 row: allowed by Gate 1, denied live, rule revoked.",
    steps: [
      { kind: "admin", action: "restore" },
      { kind: "ask", as: "jdoe", question: QUESTIONS.migration },
      { kind: "admin", action: "revoke-silent" },
      { kind: "ask", as: "jdoe", question: QUESTIONS.migration },
    ],
  },
  {
    id: 6,
    title: "Scenario 2: fresh, even when the index is stale",
    narration:
      "Sync is paused and someone adds a failover step to the incident runbook, so the index is out of date. Gate 2 sees the newer version at Confluence, refreshes it on the spot, and the answer includes step 4.",
    cue: "Gate 2: refreshed to v8 in amber.",
    steps: [
      { kind: "admin", action: "freshness" },
      { kind: "ask", as: "jdoe", question: QUESTIONS.runbook },
    ],
  },
  {
    id: 7,
    title: "Cross-platform stitch",
    narration:
      "One question, three tools: the postmortem in Drive, the follow-up tickets in Jira and the gateway overview in Confluence. Each document is re-checked live at its own platform before the model sees a word of it.",
    cue: "Gate 2: one live check per document, at each platform. (Scenario 1 shows link expansion: the cutover plan arrives through DBM-42's link and still passes both gates.)",
    steps: [{ kind: "ask", as: "jdoe", question: QUESTIONS.stitch }],
  },
  {
    id: 8,
    title: "Sensitive data stays masked",
    narration:
      "Someone pasted a card number and an NRIC into Slack. The Brain masked them at ingestion, so the model never saw the full values and the answer shows only the last digits. Compliance was alerted the moment that data was indexed.",
    cue: "Masked values in the answer; sensitive-data alerts in the compliance console.",
    steps: [{ kind: "ask", as: "jdoe", question: QUESTIONS.dlp }],
  },
  {
    id: 9,
    title: "Scenario 5: the audit inquiry",
    narration:
      "A regulator asks what Jane accessed in the payment gateway space in the last 30 days. The question becomes a validated filter, never a model reading the log, and Verify chain proves that no entry was altered or removed.",
    cue: "Derived filter chips, the entries, and the green verification result.",
    steps: [{ kind: "navigate", href: `/audit?nl=${encodeURIComponent(QUESTIONS.auditInquiry)}&verify=1&${PRESENTER}`, actAs: "compliance" }],
  },
  {
    id: 10,
    title: "Close: the trust boundary",
    narration:
      "Models plan and write, but they sit outside the trust boundary. Identity, both gates, the guard and the audit log are deterministic code, which is why every answer is permission-correct, fresh, cited, and provable after the fact.",
    cue: "Replay the last trace on the diagram.",
    steps: [{ kind: "replay" }],
  },
];
