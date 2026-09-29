/**
 * The glass-box model of one audit entry: what the trust-boundary diagram and the pipeline
 * stepper draw. Derived only from the full entry (GET /audit/entries/{seq}, compliance view).
 */

import type { Decision, FullEntry, GuardStats, SentChunk, StageTimings } from "./api";
import { sumCounts } from "./format";

export type TraceStageKey = "identity" | "plan" | "gate1" | "expand" | "gate2" | "context" | "answer" | "audit";

export const QUERY_STAGES: TraceStageKey[] = ["identity", "plan", "gate1", "expand", "gate2", "context", "answer", "audit"];
export const SOURCE_OPEN_STAGES: TraceStageKey[] = ["identity", "gate1", "gate2", "audit"];

export interface PlanSubquery {
  platform: string;
  query: string;
  window_days: number | null;
  container: string | null;
}

export interface PlanView {
  platforms: string[];
  subqueries: PlanSubquery[];
  intent: string | null;
  window_days: number | null;
  /** The planner output failed validation and the raw question was used. */
  fallback: boolean;
}

export interface SourceOpenEvent {
  doc: string;
  available: boolean;
  reason: string;
  chunks: string[];
}

export interface Trace {
  kind: "query" | "source_open";
  seq: number;
  ts: string;
  actor: string;
  principals: string[];
  query: string | null;
  outcome: "answered" | "no_result" | null;
  plan: PlanView | null;
  /** Gate 1 on the retrieval candidates. Denied ones were found by the index but never shown to the model. */
  gate1Allowed: Decision[];
  gate1Denied: Decision[];
  /** Explicit cross-references followed from the kept candidates (each passes Gate 1 too). */
  expansionAllowed: Decision[];
  expansionDenied: Decision[];
  /** Every document that reached Gate 2 (allowed by Gate 1). */
  gate2: Decision[];
  gate2Denied: Decision[];
  gate2Refreshed: Decision[];
  sent: SentChunk[];
  model: string | null;
  guard: GuardStats | null;
  redactions: Record<string, number>;
  redactionTotal: number;
  timings: StageTimings;
  latencyMs: number | null;
  prevHash: string;
  entryHash: string;
  stages: TraceStageKey[];
  source: SourceOpenEvent | null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export function planOf(raw: Record<string, unknown> | null): PlanView | null {
  if (!raw) return null;
  const platforms = Array.isArray(raw["platforms"]) ? raw["platforms"].filter((p): p is string => typeof p === "string") : [];
  const subqueries: PlanSubquery[] = [];
  if (Array.isArray(raw["subqueries"])) {
    for (const item of raw["subqueries"]) {
      const sq = asRecord(item);
      if (!sq) continue;
      subqueries.push({
        platform: asString(sq["platform"]) ?? "?",
        query: asString(sq["query"]) ?? "",
        window_days: asNumber(sq["window_days"]),
        container: asString(sq["container"]),
      });
    }
  }
  return { platforms, subqueries, intent: asString(raw["intent"]), window_days: asNumber(raw["window_days"]), fallback: raw["fallback"] === true };
}

function sourceEventOf(event: Record<string, unknown> | null): SourceOpenEvent | null {
  if (!event) return null;
  const chunks = Array.isArray(event["chunks"]) ? event["chunks"].filter((c): c is string => typeof c === "string") : [];
  return { doc: asString(event["doc"]) ?? "", available: event["available"] === true, reason: asString(event["reason"]) ?? "", chunks };
}

/** The trace of a query or source-open entry; null for other kinds. */
export function traceOf(entry: FullEntry): Trace | null {
  if (entry.kind !== "query" && entry.kind !== "source_open") return null;
  const decisions = entry.decisions ?? [];
  const retrieval = decisions.filter((d) => d.source !== "expansion");
  const expansion = decisions.filter((d) => d.source === "expansion");
  const gate2 = decisions.filter((d) => d.gate1 === "allow" && d.gate2 !== "skipped");
  const redactions = entry.guard?.redactions ?? {};
  return {
    kind: entry.kind,
    seq: entry.seq,
    ts: entry.ts,
    actor: entry.actor.id,
    principals: entry.actor.principals ?? [],
    query: entry.query,
    outcome: entry.outcome,
    plan: planOf(entry.plan),
    gate1Allowed: retrieval.filter((d) => d.gate1 === "allow"),
    gate1Denied: retrieval.filter((d) => d.gate1 === "deny"),
    expansionAllowed: expansion.filter((d) => d.gate1 === "allow"),
    expansionDenied: expansion.filter((d) => d.gate1 === "deny"),
    gate2,
    gate2Denied: gate2.filter((d) => d.gate2 === "deny"),
    gate2Refreshed: gate2.filter((d) => d.gate2 === "allow" && d.refreshed),
    sent: entry.sent_to_model ?? [],
    model: entry.model,
    guard: entry.guard,
    redactions,
    redactionTotal: sumCounts(redactions),
    timings: entry.timings_ms ?? {},
    latencyMs: entry.latency_ms,
    prevHash: entry.prev_hash,
    entryHash: entry.entry_hash,
    stages: entry.kind === "query" ? QUERY_STAGES : SOURCE_OPEN_STAGES,
    source: entry.kind === "source_open" ? sourceEventOf(entry.event) : null,
  };
}

/** True once `stage` is among the first `revealed` stages of the trace (the stepper and the diagram share the clock). */
export function isRevealed(trace: Trace, stage: TraceStageKey, revealed: number): boolean {
  const idx = trace.stages.indexOf(stage);
  return idx !== -1 && idx < revealed;
}
