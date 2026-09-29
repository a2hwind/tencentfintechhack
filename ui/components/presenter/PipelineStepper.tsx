"use client";

import type { ReactNode } from "react";
import type { Decision } from "@/lib/api";
import { fmtMs, plural, redactionLabel, shortHash } from "@/lib/format";
import type { Trace, TraceStageKey } from "@/lib/trace";
import { PlatformBadge } from "@/components/ui";

const STAGE_NAMES: Record<TraceStageKey, string> = {
  identity: "Identity",
  plan: "Plan",
  gate1: "Gate 1",
  expand: "Link expansion",
  gate2: "Gate 2",
  context: "Context",
  answer: "Answer + guard",
  audit: "Audit",
};

type RowState = "pending" | "current" | "done";

/** One row per pipeline stage, revealed in step with the diagram; times are the entry's measured timings. */
export function PipelineStepper({ trace, revealed }: { trace: Trace; revealed: number }) {
  const total = trace.stages.length;
  return (
    <ol className="steps" aria-label="Pipeline stages">
      {trace.stages.map((stage, i) => {
        const state: RowState = i >= revealed ? "pending" : i === revealed - 1 && revealed < total ? "current" : "done";
        return <StepRow key={stage} index={i} stage={stage} trace={trace} state={state} last={i === total - 1} />;
      })}
    </ol>
  );
}

function stageTime(trace: Trace, stage: TraceStageKey): { value: number | null; title?: string } {
  const t = trace.timings;
  switch (stage) {
    case "identity":
      return { value: t.entitlements ?? null };
    case "plan":
      return { value: t.plan ?? null };
    case "gate1":
      return { value: t.gate1 ?? null };
    case "expand":
      return { value: t.expand ?? null };
    case "gate2":
      return { value: t.gate2 ?? null };
    case "context":
      return { value: t.assemble ?? null };
    case "answer": {
      if (t.answer === undefined && t.guard === undefined) return { value: null };
      return { value: (t.answer ?? 0) + (t.guard ?? 0), title: `answer ${fmtMs(t.answer)} + guard ${fmtMs(t.guard)}` };
    }
    case "audit":
      return { value: trace.latencyMs, title: "total latency of the query" };
  }
}

function StepRow({ index, stage, trace, state, last }: { index: number; stage: TraceStageKey; trace: Trace; state: RowState; last: boolean }) {
  const time = stageTime(trace, stage);
  const { summary, body, tone } = stageContent(stage, trace);
  return (
    <li className={`step ${state}${tone ? ` tone-${tone}` : ""}`} aria-current={state === "current" ? "step" : undefined}>
      <div className="step-rail" aria-hidden="true">
        <span className="step-dot">{index + 1}</span>
        {!last && <span className="step-line" />}
      </div>
      <div className="step-main">
        <div className="step-head">
          <span className="step-name">{STAGE_NAMES[stage]}</span>
          <span className="step-summary">{state === "pending" ? "" : summary}</span>
          <span className="step-time mono" title={time.title}>
            {state === "pending" || time.value === null ? "" : stage === "audit" ? `${fmtMs(time.value)} total` : fmtMs(time.value)}
          </span>
        </div>
        {state !== "pending" && body ? <div className="step-body">{body}</div> : null}
      </div>
    </li>
  );
}

function DocLine({ d, tone, right }: { d: Decision; tone: "allow" | "deny" | "refresh"; right: ReactNode }) {
  return (
    <li className={`doc-line tone-${tone}`}>
      <PlatformBadge platform={d.platform} />
      <span className="doc-id mono" title={d.container ?? d.doc}>
        {d.doc}
      </span>
      <span className="doc-right">{right}</span>
    </li>
  );
}

function DocList({ children }: { children: ReactNode }) {
  return <ul className="doc-list">{children}</ul>;
}

/**
 * The granting token of a document Gate 1 allowed. When Gate 2 then denied it, `rule` holds Gate 2's
 * reason and `gate1_rule` the (stale) token Gate 1 used; older audit entries have no `gate1_rule`.
 */
function GrantChip({ d }: { d: Decision }) {
  if (d.gate2 === "deny") {
    return d.gate1_rule ? (
      <span className="chip-mono" title={`Gate 1 passed on ${d.gate1_rule} (a stale entitlement); Gate 2 then denied it (${d.rule})`}>
        {d.gate1_rule} · stale
      </span>
    ) : (
      <span className="chip-mono" title={`Gate 2 denied this document (${d.rule})`}>
        passed Gate 1
      </span>
    );
  }
  return (
    <span className="chip-mono chip-allow" title="granting principal token">
      {d.rule}
    </span>
  );
}

function stageContent(stage: TraceStageKey, t: Trace): { summary: ReactNode; body: ReactNode; tone?: "deny" | "refresh" } {
  switch (stage) {
    case "identity":
      return {
        summary: (
          <>
            {plural(t.principals.length, "principal")} for <span className="mono">{t.actor}</span>
          </>
        ),
        body: (
          <details className="tokens">
            <summary>Show principal tokens</summary>
            <div className="principals">
              {t.principals.map((p) => (
                <span key={p} className="chip-mono">
                  {p}
                </span>
              ))}
            </div>
          </details>
        ),
      };

    case "plan": {
      const plan = t.plan;
      if (!plan) return { summary: "no plan recorded", body: null };
      return {
        summary: (
          <>
            {plural(plan.platforms.length, "platform")} · {plural(plan.subqueries.length, "sub-query", "sub-queries")}
            {plan.window_days ? ` · last ${plan.window_days} d` : ""}
            {plan.fallback ? <span className="badge badge-amber" style={{ marginLeft: 6 }}>fallback</span> : null}
          </>
        ),
        tone: plan.fallback ? "refresh" : undefined,
        body: (
          <div className="stack-xs">
            {plan.fallback && <div className="small tone-text-refresh">The planner output failed validation; the raw question was used.</div>}
            <ul className="subqueries">
              {plan.subqueries.map((sq, i) => (
                <li key={i}>
                  <PlatformBadge platform={sq.platform} />
                  <span className="subquery-text" title={sq.query}>
                    {sq.query}
                  </span>
                  {sq.window_days ? <span className="chip-mono">{sq.window_days} d</span> : null}
                  {sq.container ? <span className="chip-mono">{sq.container}</span> : null}
                </li>
              ))}
            </ul>
            <div className="tiny muted">The planner is a model: it saw only the question, and its output was schema-validated before use.</div>
          </div>
        ),
      };
    }

    case "gate1": {
      if (t.kind === "source_open") {
        const d = t.gate1Allowed[0] ?? t.gate1Denied[0];
        if (!d) {
          return {
            summary: <span className="muted">no such document in the index</span>,
            body: <div className="small muted">The asker received the uniform unavailable response.</div>,
          };
        }
        const allowed = d.gate1 === "allow";
        return {
          summary: allowed ? <span className="tone-text-allow">allowed</span> : <span className="tone-text-deny">denied</span>,
          tone: allowed ? undefined : "deny",
          body: (
            <DocList>
              <DocLine d={d} tone={allowed ? "allow" : "deny"} right={allowed ? <GrantChip d={d} /> : <span className="chip-mono chip-deny">{d.rule}</span>} />
            </DocList>
          ),
        };
      }
      return {
        summary: (
          <>
            <span className="tone-text-allow">{t.gate1Allowed.length} allowed</span> · <span className={t.gate1Denied.length ? "tone-text-deny" : "muted"}>{t.gate1Denied.length} denied</span>
          </>
        ),
        tone: t.gate1Denied.length ? "deny" : undefined,
        body:
          t.gate1Allowed.length + t.gate1Denied.length === 0 ? (
            <div className="small muted">The index returned no candidates for this question.</div>
          ) : (
            <div className="stack-xs">
              {t.gate1Allowed.length > 0 && (
                <DocList>
                  {t.gate1Allowed.map((d) => (
                    <DocLine key={d.doc} d={d} tone="allow" right={<GrantChip d={d} />} />
                  ))}
                </DocList>
              )}
              {t.gate1Denied.length > 0 && (
                <div className="denied-block">
                  <DocList>
                    {t.gate1Denied.map((d) => (
                      <DocLine key={d.doc} d={d} tone="deny" right={<span className="chip-mono chip-deny">{d.rule}</span>} />
                    ))}
                  </DocList>
                  <div className="denied-caption">found, never shown to the model</div>
                </div>
              )}
            </div>
          ),
      };
    }

    case "expand": {
      const n = t.expansionAllowed.length + t.expansionDenied.length;
      return {
        summary: n === 0 ? <span className="muted">no cross-references followed</span> : <>{plural(n, "linked document")}</>,
        tone: t.expansionDenied.length ? "deny" : undefined,
        body:
          n === 0 ? null : (
            <DocList>
              {t.expansionAllowed.map((d) => (
                <DocLine key={d.doc} d={d} tone="allow" right={<GrantChip d={d} />} />
              ))}
              {t.expansionDenied.map((d) => (
                <DocLine key={d.doc} d={d} tone="deny" right={<span className="chip-mono chip-deny">{d.rule}</span>} />
              ))}
            </DocList>
          ),
      };
    }

    case "gate2": {
      if (t.gate2.length === 0) {
        return { summary: <span className="muted">nothing to check</span>, body: t.kind === "source_open" ? <div className="small muted">Gate 1 denied, so the platform was not asked.</div> : null };
      }
      return {
        summary: (
          <>
            {plural(t.gate2.length, "live check")} · <span className={t.gate2Denied.length ? "tone-text-deny" : "muted"}>{t.gate2Denied.length} denied</span> ·{" "}
            <span className={t.gate2Refreshed.length ? "tone-text-refresh" : "muted"}>{t.gate2Refreshed.length} refreshed</span>
          </>
        ),
        tone: t.gate2Denied.length ? "deny" : t.gate2Refreshed.length ? "refresh" : undefined,
        body: (
          <DocList>
            {t.gate2.map((d) => {
              const denied = d.gate2 === "deny";
              const right = denied ? (
                <>
                  <span className="badge badge-deny">deny</span>
                  <span className="chip-mono chip-deny">{d.rule}</span>
                </>
              ) : (
                <>
                  <span className="badge badge-allow">allow</span>
                  {d.refreshed ? <span className="badge badge-amber">refreshed to v{d.version ?? "?"}</span> : <span className="chip-mono">v{d.version ?? "?"}</span>}
                </>
              );
              return <DocLine key={d.doc} d={d} tone={denied ? "deny" : d.refreshed ? "refresh" : "allow"} right={right} />;
            })}
          </DocList>
        ),
      };
    }

    case "context":
      return {
        summary: t.sent.length ? <>{plural(t.sent.length, "chunk")} sent · exactly what the model saw</> : <span className="muted">nothing permitted: the model was not called</span>,
        body: t.sent.length ? (
          <ul className="chunk-list">
            {t.sent.map((c) => (
              <li key={c.chunk}>
                <span className="mono chunk-id" title={c.chunk}>
                  {c.chunk}
                </span>
                <span className="mono muted" title={`sha256 ${c.sha256}`}>
                  sha256 {c.sha256.slice(0, 10)}
                </span>
              </li>
            ))}
          </ul>
        ) : null,
      };

    case "answer": {
      const g = t.guard;
      const redactions = Object.entries(t.redactions).filter(([, n]) => n > 0);
      return {
        summary: (
          <>
            <span className="mono">{t.model ?? "—"}</span> · {t.outcome === "no_result" ? "uniform no-result" : "answered"}
          </>
        ),
        tone: g && g.citations_rejected > 0 ? "deny" : undefined,
        body: (
          <div className="stack-xs">
            <div className="row" style={{ gap: 6 }}>
              <span className={`badge ${g && g.citations_rejected ? "badge-deny" : "badge-muted"}`}>{g?.citations_rejected ?? 0} citations rejected</span>
              <span className={`badge ${g && g.claims_stripped ? "badge-amber" : "badge-muted"}`}>{g?.claims_stripped ?? 0} claims stripped</span>
              <span className={`badge ${g && g.unsupported_claims ? "badge-amber" : "badge-muted"}`}>{g?.unsupported_claims ?? 0} unsupported</span>
              <span className={`badge ${t.redactionTotal ? "badge-amber" : "badge-muted"}`}>{t.redactionTotal} DLP redactions</span>
            </div>
            {redactions.length > 0 && (
              <div className="small">
                DLP masked in the output:{" "}
                {redactions.map(([kind, n]) => (
                  <span key={kind} className="chip-mono" style={{ marginRight: 4 }}>
                    {redactionLabel(kind)} ×{n}
                  </span>
                ))}
              </div>
            )}
            {g && g.events.length > 0 && (
              <ul className="guard-events mono">
                {g.events.map((ev, i) => (
                  <li key={i}>{ev}</li>
                ))}
              </ul>
            )}
            <div className="tiny muted">Values are masked at ingestion, so the model only ever sees truncated card numbers and IDs; the output pass is defence in depth.</div>
          </div>
        ),
      };
    }

    case "audit": {
      const src = t.source;
      return {
        summary: (
          <>
            <span className="strong mono">#{t.seq}</span> · written before the {t.kind === "source_open" ? "passages were" : "answer was"} returned
          </>
        ),
        body: (
          <div className="stack-xs">
            <div className="hash-chain">
              <span className="muted">prev</span>
              <span className="mono" title={t.prevHash}>
                {shortHash(t.prevHash, 12)}
              </span>
              <span className="muted">→</span>
              <span className="muted">entry</span>
              <span className="mono strong" title={t.entryHash}>
                {shortHash(t.entryHash, 12)}
              </span>
            </div>
            {src && (
              <div className="small">
                {src.available ? (
                  <>
                    Opened <span className="mono">{src.doc}</span>: {plural(src.chunks.length, "passage")} returned.
                  </>
                ) : (
                  <>
                    <span className="mono">{src.doc || "unknown document"}</span>: uniform unavailable response (reason <span className="chip-mono chip-deny">{src.reason || "—"}</span>, audit only).
                  </>
                )}
              </div>
            )}
          </div>
        ),
      };
    }
  }
}
