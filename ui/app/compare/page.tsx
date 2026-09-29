"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { ask, describeError, type AskResult } from "@/lib/api";
import { useUser } from "@/lib/user";
import { COMPARE_PRESETS, comparePreset, type ComparePreset } from "@/lib/demoBeats";
import { ComparePane, emptyPane, type PaneState } from "@/components/compare/ComparePane";
import { CompareBanner } from "@/components/compare/CompareBanner";
import { EvidenceDrawer, type EvidenceRequest } from "@/components/EvidenceDrawer";
import { Spinner } from "@/components/ui";

export default function ComparePage() {
  return (
    <Suspense fallback={<Spinner label="Loading compare…" />}>
      <Compare />
    </Suspense>
  );
}

async function sha256Hex(text: string): Promise<string | null> {
  try {
    if (typeof crypto === "undefined" || !crypto.subtle) return null; // insecure context (plain http, not localhost)
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
  } catch {
    return null;
  }
}

async function settle(user: string, question: string): Promise<Pick<PaneState, "result" | "error" | "sha256" | "askedAs">> {
  try {
    const result: AskResult = await ask(user, question);
    return { result, error: null, sha256: await sha256Hex(result.raw), askedAs: user };
  } catch (err: unknown) {
    return { result: null, error: describeError(err), sha256: null, askedAs: null };
  }
}

function Compare() {
  const params = useSearchParams();
  const { users } = useUser();
  const initialPreset = comparePreset(params.get("preset"));
  const start: ComparePreset = initialPreset ?? COMPARE_PRESETS[0];
  const autoRun = params.get("run") === "1";
  const fromPresenter = params.get("from") === "presenter";

  const [presetKey, setPresetKey] = useState<string | null>(initialPreset?.key ?? null);
  const [a, setA] = useState<PaneState>(() => emptyPane(start.a.user, start.a.question));
  const [b, setB] = useState<PaneState>(() => emptyPane(start.b.user, start.b.question));
  const [running, setRunning] = useState(false);
  const [evidence, setEvidence] = useState<EvidenceRequest | null>(null);
  const closeEvidence = useCallback(() => setEvidence(null), []);
  const autoRan = useRef(false);

  const askBoth = useCallback(async (pa: { user: string; question: string }, pb: { user: string; question: string }) => {
    if (!pa.question.trim() || !pb.question.trim()) return;
    setRunning(true);
    setA((s) => ({ ...s, pending: true, result: null, error: null, sha256: null, askedAs: null }));
    setB((s) => ({ ...s, pending: true, result: null, error: null, sha256: null, askedAs: null }));
    // Concurrent: both requests are in flight at the same time.
    const [ra, rb] = await Promise.all([settle(pa.user, pa.question.trim()), settle(pb.user, pb.question.trim())]);
    setA((s) => ({ ...s, ...ra, pending: false }));
    setB((s) => ({ ...s, ...rb, pending: false }));
    setRunning(false);
  }, []);

  useEffect(() => {
    if (!autoRun || autoRan.current) return;
    autoRan.current = true;
    void askBoth(start.a, start.b);
    // Runs once for the preset in the URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const choosePreset = (p: ComparePreset) => {
    setPresetKey(p.key);
    setA(emptyPane(p.a.user, p.a.question));
    setB(emptyPane(p.b.user, p.b.question));
    try {
      const url = new URL(window.location.href);
      url.searchParams.set("preset", p.key);
      url.searchParams.delete("run");
      window.history.replaceState(null, "", url.toString());
    } catch {
      // URL updates are a convenience only.
    }
  };

  // Editing a pane invalidates its result (and so the comparison).
  const edit = (set: typeof setA, patch: Partial<Pick<PaneState, "user" | "question">>) => {
    setPresetKey(null);
    set((s) => ({ ...s, ...patch, result: null, error: null, sha256: null, askedAs: null }));
  };

  const preset = presetKey ? comparePreset(presetKey) : undefined;

  return (
    <div className="stack compare">
      <div className="page-title">
        <h1>Compare responses</h1>
        <span className="muted small">Two identities or two questions, asked at the same time. The banner compares the raw response bodies byte for byte.</span>
        {fromPresenter && (
          <Link href="/presenter" className="btn btn-sm" style={{ marginLeft: "auto" }}>
            ← Back to presenter
          </Link>
        )}
      </div>

      <div className="card compare-controls">
        <div className="row">
          <span className="pv-row-label">Presets</span>
          {COMPARE_PRESETS.map((p) => (
            <button key={p.key} type="button" className={`btn${presetKey === p.key ? " btn-active" : ""}`} onClick={() => choosePreset(p)} disabled={running} aria-pressed={presetKey === p.key}>
              {p.label}
            </button>
          ))}
          <span style={{ flex: 1 }} />
          <button type="button" className="btn btn-primary compare-ask" onClick={() => void askBoth(a, b)} disabled={running || !a.question.trim() || !b.question.trim()}>
            {running ? "Asking both…" : "Ask both"}
          </button>
        </div>
        {preset && <p className="small muted" style={{ marginTop: 8 }}>{preset.lookFor}</p>}
      </div>

      <div className="stack-sm">
        <CompareBanner a={a.result} b={b.result} shaA={a.sha256} pending={running} />
        <p className="small muted compare-why">
          Why it matters: a denied query must be indistinguishable from an empty one, with the same body, the same size and a time floor, so nobody can learn that a restricted document exists by asking. The audit number travels in a response header, which is why the bodies can match exactly.
        </p>
      </div>

      <div className="grid-2 compare-grid">
        <ComparePane tag="A" state={a} users={users} disabled={running} onUser={(user) => edit(setA, { user })} onQuestion={(question) => edit(setA, { question })} onOpenEvidence={setEvidence} />
        <ComparePane tag="B" state={b} users={users} disabled={running} onUser={(user) => edit(setB, { user })} onQuestion={(question) => edit(setB, { question })} onOpenEvidence={setEvidence} />
      </div>

      <EvidenceDrawer request={evidence} onClose={closeEvidence} />
    </div>
  );
}
