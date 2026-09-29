"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ask, describeError, getAuditEntry, type AskResult, type FullEntry, type StreamEntry } from "@/lib/api";
import { useUser } from "@/lib/user";
import { useSessionState, useStagedReveal } from "@/lib/hooks";
import { traceOf } from "@/lib/trace";
import { ADMIN_ID, AUDITOR_ID, QUICK_ACTIONS, type QuickActionKey } from "@/lib/demoActions";
import { DEMO_BEATS, EXAMPLES, type BeatStep, type DemoBeat, type ExampleQuestion } from "@/lib/demoBeats";
import { EvidenceDrawer, type EvidenceRequest } from "@/components/EvidenceDrawer";
import { Toast, type ToastMessage } from "@/components/Toast";
import { PresenterToolbar } from "@/components/presenter/PresenterToolbar";
import { AskerPane, type PresenterTurn } from "@/components/presenter/AskerPane";
import { AuditorPane } from "@/components/presenter/AuditorPane";
import { BeatsPanel, type BeatStatus } from "@/components/presenter/BeatsPanel";
import { AlertBanner } from "@/components/presenter/AlertBanner";

const DEFAULT_ASKER = "jdoe";
const STAGE_MS = 260;
const ALERT_WAIT_MS = 3500;
const SEVERITY_RANK: Record<string, number> = { low: 0, medium: 1, high: 2 };
const MAX_TURNS = 20;
const KEY = "internal-brain.presenter";

/** A beat that was running when the page was left is not running any more. */
function reviveBeats(status: Record<number, BeatStatus>): Record<number, BeatStatus> {
  if (!status || typeof status !== "object") return {};
  const out: Record<number, BeatStatus> = {};
  for (const [id, st] of Object.entries(status)) out[Number(id)] = st === "running" ? "idle" : st;
  return out;
}

/** Answers survive a guided detour to /compare or /audit; one that was still pending is marked interrupted. */
function reviveTurns(turns: PresenterTurn[]): PresenterTurn[] {
  if (!Array.isArray(turns)) return [];
  return turns.map((t) => (t.pending ? { ...t, pending: false, error: "The page was left before this answer arrived." } : t));
}

interface Shown {
  entry: FullEntry;
  readLoggedAs: number;
}

export default function PresenterPage() {
  const router = useRouter();
  const { users, setUserId } = useUser();

  const [asker, setAsker] = useState(DEFAULT_ASKER);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useSessionState<PresenterTurn[]>(`${KEY}.turns`, [], reviveTurns);
  const [asking, setAsking] = useState(false);
  const [actionBusy, setActionBusy] = useState<QuickActionKey | null>(null);
  const nextTurn = useRef(1);

  // The trace on the right: the entry shown, the one loading, and a replay counter.
  const [shown, setShown] = useState<Shown | null>(null);
  const [loadingSeq, setLoadingSeq] = useState<number | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [replay, setReplay] = useState(0);
  const loadId = useRef(0);

  const [evidence, setEvidence] = useState<EvidenceRequest | null>(null);
  const [toast, setToast] = useState<ToastMessage | null>(null);
  const [banner, setBanner] = useState<StreamEntry | null>(null);
  const bannerAt = useRef(0);
  const alertsSeen = useRef<{ seq: number; rule: string }[]>([]);
  const toastId = useRef(1);

  const [shownSeq, setShownSeq] = useSessionState<number | null>(`${KEY}.shownSeq`, null);
  const [beatsOpen, setBeatsOpen] = useSessionState<boolean>(`${KEY}.beatsOpen`, false);
  const [beatStatus, setBeatStatus] = useSessionState<Record<number, BeatStatus>>(`${KEY}.beats`, {}, reviveBeats);
  const [focusBeat, setFocusBeat] = useState<number | null>(null);
  const [runningBeat, setRunningBeat] = useState<number | null>(null);

  const trace = useMemo(() => (shown ? traceOf(shown.entry) : null), [shown]);
  const revealKey = `${shown?.entry.seq ?? "none"}:${replay}`;
  const revealed = useStagedReveal(trace?.stages.length ?? 0, revealKey, STAGE_MS);

  const busy = asking || actionBusy !== null || runningBeat !== null;
  const firstOpenBeat = DEMO_BEATS.find((b) => beatStatus[b.id] !== "done")?.id ?? DEMO_BEATS[DEMO_BEATS.length - 1].id;
  const focusId = focusBeat ?? firstOpenBeat;

  const showToast = useCallback((kind: ToastMessage["kind"], text: string) => {
    setToast({ id: toastId.current++, kind, text });
  }, []);
  const dismissToast = useCallback(() => setToast(null), []);
  const dismissBanner = useCallback(() => setBanner(null), []);

  /** A new alert on the live chain: remember it, and banner it unless a more severe one just arrived. */
  const onAlert = useCallback((entry: StreamEntry) => {
    const rule = typeof entry.event?.["rule"] === "string" ? entry.event["rule"] : "";
    alertsSeen.current = [...alertsSeen.current.slice(-49), { seq: entry.seq, rule }];
    const rank = (e: StreamEntry) => SEVERITY_RANK[String(e.event?.["severity"] ?? "low")] ?? 0;
    const now = Date.now();
    setBanner((prev) => {
      if (prev && now - bannerAt.current < 3000 && rank(prev) > rank(entry)) return prev;
      bannerAt.current = now;
      return entry;
    });
  }, []);

  /** Resolve true once an alert with `rule` and a sequence number after `afterSeq` has arrived. */
  const waitForAlert = useCallback(async (rule: string, afterSeq: number, ms: number): Promise<boolean> => {
    const deadline = Date.now() + ms;
    for (;;) {
      if (alertsSeen.current.some((a) => a.seq > afterSeq && a.rule === rule)) return true;
      if (Date.now() >= deadline) return false;
      await new Promise((resolve) => window.setTimeout(resolve, 150));
    }
  }, []);
  const closeEvidence = useCallback(() => setEvidence(null), []);

  /** Load an entry into the diagram and stepper, as the compliance user (the read is logged). */
  const loadEntry = useCallback(async (seq: number) => {
    const id = ++loadId.current;
    setLoadingSeq(seq);
    setTraceError(null);
    try {
      const res = await getAuditEntry(AUDITOR_ID, seq);
      if (id !== loadId.current) return;
      if (res.entry.kind !== "query" && res.entry.kind !== "source_open") {
        setTraceError(`Entry #${seq} is a ${res.entry.kind.replace("_", " ")}; only questions and source opens have a trace.`);
        return;
      }
      setShown({ entry: res.entry, readLoggedAs: res.read_logged_as });
      setShownSeq(seq);
    } catch (err: unknown) {
      if (id === loadId.current) setTraceError(describeError(err));
    } finally {
      if (id === loadId.current) setLoadingSeq(null);
    }
  }, [setShownSeq]);

  // Back from a guided detour: show the trace that was on screen (one more logged read).
  const restored = useRef(false);
  useEffect(() => {
    if (restored.current || shownSeq === null || shown !== null) return;
    restored.current = true;
    void loadEntry(shownSeq);
  }, [shownSeq, shown, loadEntry]);

  /** Ask as a user; the answer goes left, its audit entry right. Resolves null on failure. */
  const askAs = useCallback(
    async (user: string, text: string): Promise<AskResult | null> => {
      const q = text.trim();
      if (!q) return null;
      const id = `${Date.now()}-${nextTurn.current++}`;
      setTurns((prev) => [{ id, asker: user, question: q, pending: true }, ...prev].slice(0, MAX_TURNS));
      setAsking(true);
      try {
        const result = await ask(user, q);
        setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, result, pending: false } : t)));
        if (result.audit_seq !== null) await loadEntry(result.audit_seq);
        return result;
      } catch (err: unknown) {
        setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, error: describeError(err), pending: false } : t)));
        return null;
      } finally {
        setAsking(false);
      }
    },
    [loadEntry, setTurns],
  );

  /** Run an admin quick action as the admin user; rejects on failure (after the error toast). */
  const runAction = useCallback(
    async (key: QuickActionKey) => {
      setActionBusy(key);
      try {
        const text = await QUICK_ACTIONS[key].run(ADMIN_ID);
        showToast("ok", text);
      } catch (err: unknown) {
        showToast("error", `${QUICK_ACTIONS[key].label} failed: ${describeError(err)}`);
        throw err;
      } finally {
        setActionBusy(null);
      }
    },
    [showToast],
  );

  const clearPanes = useCallback(() => {
    setTurns([]);
    setShown(null);
    setShownSeq(null);
    setTraceError(null);
    setAsker(DEFAULT_ASKER);
    setQuestion("");
  }, [setTurns, setShownSeq]);

  const runStep = useCallback(
    async (step: BeatStep, beat: DemoBeat) => {
      switch (step.kind) {
        case "admin":
          await runAction(step.action);
          return;
        case "ask": {
          setAsker(step.as);
          const result = await askAs(step.as, step.question);
          if (!result) throw new Error(`the question as ${step.as} failed`);
          if (step.expectAlert && result.audit_seq !== null) {
            const arrived = await waitForAlert(step.expectAlert.rule, result.audit_seq, ALERT_WAIT_MS);
            if (!arrived) showToast("info", step.expectAlert.missing);
          }
          return;
        }
        case "clear":
          clearPanes();
          return;
        case "replay":
          setReplay((n) => n + 1);
          return;
        case "navigate":
          // Leaving the page: record the beat first (the write is synchronous).
          setBeatStatus((prev) => ({ ...prev, [beat.id]: "done" }));
          if (step.actAs) setUserId(step.actAs);
          router.push(step.href);
          return;
      }
    },
    [runAction, askAs, waitForAlert, showToast, clearPanes, setBeatStatus, setUserId, router],
  );

  const runBeat = useCallback(
    async (beat: DemoBeat) => {
      if (runningBeat !== null) return;
      setRunningBeat(beat.id);
      setFocusBeat(beat.id);
      // Beat 0 starts a fresh run-through.
      setBeatStatus((prev) => ({ ...(beat.id === 0 ? {} : prev), [beat.id]: "running" }));
      try {
        for (const step of beat.steps) await runStep(step, beat);
        setBeatStatus((prev) => ({ ...prev, [beat.id]: "done" }));
        const next = DEMO_BEATS.find((b) => b.id > beat.id);
        if (next && !beat.steps.some((s) => s.kind === "navigate")) setFocusBeat(next.id);
      } catch (err: unknown) {
        setBeatStatus((prev) => ({ ...prev, [beat.id]: "error" }));
        showToast("error", `Beat ${beat.id} stopped: ${describeError(err)}`);
      } finally {
        setRunningBeat(null);
      }
    },
    [runningBeat, runStep, setBeatStatus, showToast],
  );

  const onAsk = () => {
    void askAs(asker, question).then((r) => {
      if (r) setQuestion("");
    });
  };

  const onExample = (ex: ExampleQuestion) => {
    setAsker(ex.asker);
    void askAs(ex.asker, ex.question);
  };

  const onAction = (key: QuickActionKey) => {
    runAction(key).catch(() => undefined);
  };

  // Esc closes the beats overlay on narrow screens (the evidence drawer handles its own Esc).
  useEffect(() => {
    if (!beatsOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && evidence === null && window.matchMedia("(max-width: 1499px)").matches) setBeatsOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [beatsOpen, evidence, setBeatsOpen]);

  return (
    <div className={`presenter${beatsOpen ? " with-beats" : ""}`}>
      <PresenterToolbar
        users={users}
        asker={asker}
        onAskerChange={setAsker}
        question={question}
        onQuestionChange={setQuestion}
        onAsk={onAsk}
        examples={EXAMPLES}
        onExample={onExample}
        busy={busy}
        beatsOpen={beatsOpen}
        onToggleBeats={() => setBeatsOpen((v) => !v)}
        actionBusy={actionBusy}
        onAction={onAction}
      />

      <div className="pv-grid">
        {beatsOpen && (
          <BeatsPanel
            beats={DEMO_BEATS}
            status={beatStatus}
            focusId={focusId}
            running={runningBeat}
            disabled={busy && runningBeat === null}
            onFocus={setFocusBeat}
            onRun={(b) => void runBeat(b)}
            onClose={() => setBeatsOpen(false)}
            onResetProgress={() => {
              setBeatStatus({});
              setFocusBeat(null);
            }}
          />
        )}
        <AskerPane turns={turns} users={users} fallbackAsker={asker} onOpenEvidence={setEvidence} />
        <AuditorPane
          trace={trace}
          revealed={revealed}
          loadingSeq={loadingSeq}
          error={traceError}
          readLoggedAs={shown?.readLoggedAs ?? null}
          onReplay={() => setReplay((n) => n + 1)}
          onSelect={(seq) => void loadEntry(seq)}
          onAlert={onAlert}
          banner={banner ? <AlertBanner alert={banner} onDismiss={dismissBanner} onTrace={(seq) => void loadEntry(seq)} /> : null}
        />
      </div>

      <Toast toast={toast} onDismiss={dismissToast} />
      <EvidenceDrawer request={evidence} onClose={closeEvidence} />
    </div>
  );
}
