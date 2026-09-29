"use client";

import { useId, type ReactNode } from "react";
import { plural, shortHash } from "@/lib/format";
import { isRevealed, type Trace, type TraceStageKey } from "@/lib/trace";

/*
 * The trust boundary, drawn as four zones: untrusted input (left), untrusted compute (top),
 * the trusted control plane (middle) and the sources of truth (bottom). A trace lights the
 * path stage by stage, in step with the pipeline stepper, and labels it with the entry's numbers.
 * All colours come from theme variables through CSS classes (tb-*), so it reads in light and dark.
 */

const W = 860;
const H = 350;

// Zones
const Z1 = { x: 4, y: 4, w: 142, h: 342 };
const LANE_X = 156;
const LANE_W = W - LANE_X - 6;
const Z3 = { x: LANE_X, y: 4, w: LANE_W, h: 76 };
const Z2 = { x: LANE_X, y: 118, w: LANE_W, h: 140 };
const Z4 = { x: LANE_X, y: 266, w: LANE_W, h: 80 };

// Boxes
const BOX_Y = 144;
const BOX_H = 50;
const Q = { x: 16, y: BOX_Y, w: 118, h: BOX_H };
const DOCS = { x: 16, y: 286, w: 118, h: 52 };
const IDENTITY = { x: 168, y: BOX_Y, w: 108, h: BOX_H };
const GATE1 = { x: 306, y: BOX_Y, w: 108, h: BOX_H };
const GATE2 = { x: 444, y: BOX_Y, w: 108, h: BOX_H };
const GUARD = { x: 582, y: BOX_Y, w: 108, h: BOX_H };
const AUDIT = { x: 720, y: BOX_Y, w: 122, h: BOX_H };
const PLANNER = { x: 229, y: 28, w: 124, h: 44 };
const ANSWERER = { x: 505, y: 28, w: 124, h: 44 };
const SOURCES = { x: 306, y: 292, w: 384, h: 44 };

const MID_Y = BOX_Y + BOX_H / 2;
const LABEL_Y = [210, 224, 238];

type BoxState = "idle" | "lit" | "dim";
type Tone = "allow" | "deny" | "refresh" | "muted" | "strong" | "plain";

interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

function Box({ r, title, sub, state, current, zone }: { r: Rect; title: string; sub?: string; state: BoxState; current?: boolean; zone: "trusted" | "untrusted" | "source" | "input" }) {
  const cx = r.x + r.w / 2;
  return (
    <g className={`tb-box tb-box-${zone} ${state}${current ? " current" : ""}`}>
      <rect x={r.x} y={r.y} width={r.w} height={r.h} rx={8} />
      <text x={cx} y={sub ? r.y + r.h / 2 - 2 : r.y + r.h / 2 + 4} textAnchor="middle" className="tb-box-title">
        {title}
      </text>
      {sub ? (
        <text x={cx} y={r.y + r.h / 2 + 13} textAnchor="middle" className="tb-box-sub">
          {sub}
        </text>
      ) : null}
    </g>
  );
}

function Arrow({ x1, y1, x2, y2, lit, dim, uid, dashed, both }: { x1: number; y1: number; x2: number; y2: number; lit: boolean; dim?: boolean; uid: string; dashed?: boolean; both?: boolean }) {
  const head = `url(#${uid}-${lit ? "head-lit" : "head"})`;
  return (
    <line
      x1={x1}
      y1={y1}
      x2={x2}
      y2={y2}
      pathLength={1}
      className={`tb-arrow${lit ? " lit" : ""}${dim ? " dim" : ""}${dashed ? " dashed" : ""}`}
      markerEnd={head}
      markerStart={both ? head : undefined}
    />
  );
}

function Label({ x, y, lit, anchor = "middle", tone = "plain", children, small }: { x: number; y: number; lit: boolean; anchor?: "start" | "middle" | "end"; tone?: Tone; children: ReactNode; small?: boolean }) {
  return (
    <text x={x} y={y} textAnchor={anchor} className={`tb-label tone-${tone}${lit ? " lit" : ""}${small ? " small" : ""}`}>
      {children}
    </text>
  );
}

function Seg({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <tspan className={`tone-${tone}`}>{children}</tspan>;
}

export function TrustBoundaryDiagram({ trace, revealed }: { trace: Trace | null; revealed: number }) {
  const uid = `tb${useId().replace(/[^a-zA-Z0-9]/g, "")}`;
  const on = (stage: TraceStageKey) => (trace ? isRevealed(trace, stage, revealed) : false);
  const total = trace?.stages.length ?? 0;
  const animating = trace !== null && revealed < total;
  const current = animating && revealed > 0 ? trace.stages[revealed - 1] : null;
  const isSource = trace?.kind === "source_open";
  const modelCalled = trace !== null && !isSource && trace.sent.length > 0;

  const idLit = on("identity");
  const planLit = on("plan");
  const g1Lit = on("gate1");
  const g2Lit = on("gate2");
  const ctxLit = isSource ? false : on("context");
  const ansLit = isSource ? false : on("answer");
  const auditLit = on("audit");

  const g1Allowed = trace ? trace.gate1Allowed.length + trace.expansionAllowed.length : 0;
  const g1Denied = trace ? trace.gate1Denied.length + trace.expansionDenied.length : 0;
  const guard = trace?.guard;

  const boxState = (lit: boolean, involved = true): BoxState => (!trace ? "idle" : !involved ? "dim" : lit ? "lit" : "idle");

  const desc = trace ? describe(trace) : "No trace loaded. Ask a question to trace its path through the trust boundary.";

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="tb-svg" role="img" aria-labelledby={`${uid}-title ${uid}-desc`}>
      <title id={`${uid}-title`}>Trust boundary</title>
      <desc id={`${uid}-desc`}>{desc}</desc>
      <defs>
        <marker id={`${uid}-head`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" className="tb-head" />
        </marker>
        <marker id={`${uid}-head-lit`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0,0 L10,5 L0,10 z" className="tb-head-lit" />
        </marker>
      </defs>

      {/* zones */}
      <rect x={Z1.x} y={Z1.y} width={Z1.w} height={Z1.h} rx={12} className="tb-zone tb-zone-untrusted" />
      <rect x={Z3.x} y={Z3.y} width={Z3.w} height={Z3.h} rx={12} className="tb-zone tb-zone-untrusted" />
      <rect x={Z2.x} y={Z2.y} width={Z2.w} height={Z2.h} rx={12} className="tb-zone tb-zone-trusted" />
      <rect x={Z4.x} y={Z4.y} width={Z4.w} height={Z4.h} rx={12} className="tb-zone tb-zone-source" />

      <text x={Z1.x + 12} y={Z1.y + 20} className="tb-zone-label">
        <tspan className="tb-zone-num">ZONE 1</tspan>
      </text>
      <text x={Z1.x + 12} y={Z1.y + 35} className="tb-zone-label">
        UNTRUSTED INPUT
      </text>
      <text x={Z3.x + Z3.w - 12} y={Z3.y + 16} textAnchor="end" className="tb-zone-label">
        <tspan className="tb-zone-num">ZONE 3</tspan> · UNTRUSTED COMPUTE
      </text>
      <text x={Z2.x + Z2.w - 12} y={Z2.y + 16} textAnchor="end" className="tb-zone-label trusted">
        <tspan className="tb-zone-num">ZONE 2</tspan> · TRUSTED CONTROL PLANE
      </text>
      <text x={Z4.x + Z4.w - 12} y={Z4.y + 16} textAnchor="end" className="tb-zone-label">
        <tspan className="tb-zone-num">ZONE 4</tspan> · SOURCES OF TRUTH
      </text>
      <text x={Z3.x + Z3.w - 12} y={52} textAnchor="end" className="tb-note">
        no credentials, no tools,
      </text>
      <text x={Z3.x + Z3.w - 12} y={65} textAnchor="end" className="tb-note">
        no text the asker cannot read
      </text>
      <text x={Z4.x + Z4.w - 12} y={311} textAnchor="end" className="tb-note">
        checked as the asker,
      </text>
      <text x={Z4.x + Z4.w - 12} y={324} textAnchor="end" className="tb-note">
        at the moment of use
      </text>

      {/* static: document text is untrusted input, synced from the platforms */}
      <Arrow x1={SOURCES.x} y1={314} x2={DOCS.x + DOCS.w} y2={314} lit={false} uid={uid} dashed />
      <text x={(SOURCES.x + DOCS.x + DOCS.w) / 2} y={307} textAnchor="middle" className="tb-note">
        synced, DLP-masked
      </text>

      {/* arrows: 1 question -> identity */}
      <Arrow x1={Q.x + Q.w} y1={MID_Y} x2={IDENTITY.x} y2={MID_Y} lit={idLit} uid={uid} />
      {/* 2 planner round trip */}
      <Arrow x1={256} y1={IDENTITY.y} x2={256} y2={PLANNER.y + PLANNER.h} lit={planLit} dim={isSource} uid={uid} />
      <Arrow x1={326} y1={PLANNER.y + PLANNER.h} x2={326} y2={GATE1.y} lit={planLit} dim={isSource} uid={uid} />
      {/* spine */}
      <Arrow x1={IDENTITY.x + IDENTITY.w} y1={MID_Y} x2={GATE1.x} y2={MID_Y} lit={g1Lit} uid={uid} />
      <Arrow x1={GATE1.x + GATE1.w} y1={MID_Y} x2={GATE2.x} y2={MID_Y} lit={g2Lit} uid={uid} />
      {/* Gate 2 <-> platforms, live */}
      <Arrow x1={458} y1={GATE2.y + GATE2.h} x2={458} y2={SOURCES.y} lit={g2Lit} uid={uid} both />
      {/* 5 chunks -> answer model, 6 draft -> guard; or straight to the guard when nothing was sent */}
      <Arrow x1={532} y1={GATE2.y} x2={532} y2={ANSWERER.y + ANSWERER.h} lit={ctxLit && modelCalled} dim={isSource || (trace !== null && !modelCalled)} uid={uid} />
      <Arrow x1={602} y1={ANSWERER.y + ANSWERER.h} x2={602} y2={GUARD.y} lit={ansLit && modelCalled} dim={isSource || (trace !== null && !modelCalled)} uid={uid} />
      <Arrow x1={GATE2.x + GATE2.w} y1={MID_Y} x2={GUARD.x} y2={MID_Y} lit={trace !== null && (isSource ? auditLit : !modelCalled && ansLit)} dim={trace === null || modelCalled} uid={uid} />
      {/* 7 guard -> audit */}
      <Arrow x1={GUARD.x + GUARD.w} y1={MID_Y} x2={AUDIT.x} y2={MID_Y} lit={auditLit} uid={uid} />

      {/* boxes */}
      <Box r={Q} title={isSource ? "Citation click" : "Question"} sub={isSource ? "open a source" : "from the asker"} state={boxState(idLit)} zone="input" />
      <Box r={DOCS} title="Document text" sub="may carry injections" state="idle" zone="input" />
      <Box r={IDENTITY} title="Identity" sub="+ entitlements" state={boxState(idLit)} current={current === "identity"} zone="trusted" />
      <Box r={GATE1} title="Gate 1" sub="index ACL filter" state={boxState(g1Lit)} current={current === "gate1" || current === "expand"} zone="trusted" />
      <Box r={GATE2} title="Gate 2" sub="live re-check" state={boxState(g2Lit)} current={current === "gate2"} zone="trusted" />
      <Box r={GUARD} title="Guard + DLP" sub="citations · masking" state={boxState(ansLit, !isSource)} current={current === "answer"} zone="trusted" />
      <Box r={AUDIT} title="Audit log" sub="hash-chained" state={boxState(auditLit)} current={current === "audit"} zone="trusted" />
      <Box r={PLANNER} title="Planner LLM" sub="sees the question only" state={boxState(planLit, !isSource)} current={current === "plan"} zone="untrusted" />
      <Box r={ANSWERER} title="Answer LLM" sub="sees permitted chunks" state={boxState(ansLit && modelCalled, !isSource && (trace === null || modelCalled))} current={current === "context" && modelCalled} zone="untrusted" />
      <g className={`tb-box tb-box-source ${boxState(g2Lit)}`}>
        <rect x={SOURCES.x} y={SOURCES.y} width={SOURCES.w} height={SOURCES.h} rx={8} />
        {["Confluence", "Jira", "Slack", "Drive"].map((name, i) => (
          <g key={name}>
            <rect x={SOURCES.x + 12 + i * 92} y={SOURCES.y + 10} width={84} height={24} rx={5} className="tb-chip" />
            <text x={SOURCES.x + 12 + i * 92 + 42} y={SOURCES.y + 26} textAnchor="middle" className="tb-chip-text">
              {name}
            </text>
          </g>
        ))}
      </g>

      {/* labels from the audit entry */}
      {trace && (
        <>
          <Label x={IDENTITY.x + IDENTITY.w / 2} y={LABEL_Y[0]} lit={idLit} tone="strong">
            {plural(trace.principals.length, "principal")}
          </Label>
          <Label x={IDENTITY.x + IDENTITY.w / 2} y={LABEL_Y[1]} lit={idLit} tone="muted" small>
            resolved live
          </Label>

          {isSource ? (
            <Label x={(PLANNER.x + ANSWERER.x + ANSWERER.w) / 2} y={106} lit={idLit} tone="muted">
              no model involved in opening a source
            </Label>
          ) : (
            <>
              <Label x={PLANNER.x + PLANNER.w + 8} y={47} lit={planLit} anchor="start">
                <Seg tone="strong">plan:</Seg> {trace.plan ? plural(trace.plan.platforms.length, "platform") : "—"}
              </Label>
              <Label x={PLANNER.x + PLANNER.w + 8} y={61} lit={planLit} anchor="start">
                {trace.plan ? (
                  <>
                    {plural(trace.plan.subqueries.length, "sub-query", "sub-queries")}
                    {trace.plan.window_days ? ` · ${trace.plan.window_days} d` : ""}
                    {trace.plan.fallback ? <Seg tone="refresh"> · fallback</Seg> : null}
                  </>
                ) : null}
              </Label>
            </>
          )}

          <Label x={GATE1.x + GATE1.w / 2} y={LABEL_Y[0]} lit={g1Lit}>
            <Seg tone="allow">{g1Allowed} allowed</Seg>
          </Label>
          <Label x={GATE1.x + GATE1.w / 2} y={LABEL_Y[1]} lit={g1Lit}>
            <Seg tone={g1Denied > 0 ? "deny" : "muted"}>{g1Denied} denied</Seg>
          </Label>
          {g1Denied > 0 && (
            <Label x={GATE1.x + GATE1.w / 2} y={LABEL_Y[2]} lit={g1Lit} tone="deny" small>
              never shown to the model
            </Label>
          )}

          <Label x={468} y={LABEL_Y[0]} lit={g2Lit} anchor="start">
            {plural(trace.gate2.length, "live check")}
          </Label>
          <Label x={468} y={LABEL_Y[1]} lit={g2Lit} anchor="start">
            <Seg tone={trace.gate2Denied.length > 0 ? "deny" : "muted"}>{trace.gate2Denied.length} denied</Seg>
          </Label>
          <Label x={468} y={LABEL_Y[2]} lit={g2Lit} anchor="start">
            <Seg tone={trace.gate2Refreshed.length > 0 ? "refresh" : "muted"}>{trace.gate2Refreshed.length} refreshed</Seg>
          </Label>

          {!isSource && (
            <Label x={526} y={106} lit={ctxLit} anchor="end">
              {modelCalled ? (
                <>
                  <Seg tone="strong">{plural(trace.sent.length, "chunk")}</Seg> · <Seg tone="allow">all permitted</Seg>
                </>
              ) : (
                <Seg tone="muted">nothing permitted: model not called</Seg>
              )}
            </Label>
          )}
          {!isSource && modelCalled && (
            <Label x={608} y={106} lit={ansLit} anchor="start" tone="muted">
              draft answer
            </Label>
          )}

          {isSource ? (
            <Label x={GUARD.x + GUARD.w / 2} y={LABEL_Y[0]} lit={auditLit} tone="muted" small>
              no model output to check
            </Label>
          ) : (
            <>
              <Label x={GUARD.x + GUARD.w / 2} y={LABEL_Y[0]} lit={ansLit}>
                <Seg tone={guard && guard.citations_rejected > 0 ? "deny" : "muted"}>{guard?.citations_rejected ?? 0} citations rejected</Seg>
              </Label>
              <Label x={GUARD.x + GUARD.w / 2} y={LABEL_Y[1]} lit={ansLit}>
                <Seg tone={guard && guard.claims_stripped > 0 ? "refresh" : "muted"}>{guard?.claims_stripped ?? 0} claims stripped</Seg>
              </Label>
              <Label x={GUARD.x + GUARD.w / 2} y={LABEL_Y[2]} lit={ansLit}>
                <Seg tone={trace.redactionTotal > 0 ? "refresh" : "muted"}>{trace.redactionTotal} DLP redactions</Seg>
              </Label>
            </>
          )}

          <Label x={AUDIT.x + AUDIT.w / 2} y={LABEL_Y[0]} lit={auditLit} tone="strong">
            audit #{trace.seq}
          </Label>
          <Label x={AUDIT.x + AUDIT.w / 2} y={LABEL_Y[1]} lit={auditLit} tone="muted">
            <tspan className="tb-mono">{shortHash(trace.entryHash, 10)}</tspan>
          </Label>
          <Label x={AUDIT.x + AUDIT.w / 2} y={LABEL_Y[2]} lit={auditLit} tone="muted" small>
            written before the reply
          </Label>
        </>
      )}

      {!trace && (
        <text x={Z2.x + 14} y={Z2.y + Z2.h - 12} className="tb-note">
          Ask a question to trace its path through the trust boundary.
        </text>
      )}
    </svg>
  );
}

function describe(t: Trace): string {
  const g1a = t.gate1Allowed.length + t.expansionAllowed.length;
  const g1d = t.gate1Denied.length + t.expansionDenied.length;
  const parts = [
    `${t.kind === "query" ? "Query" : "Source open"} #${t.seq} by ${t.actor}`,
    `${t.principals.length} principals`,
    `Gate 1 allowed ${g1a}, denied ${g1d}`,
    `Gate 2 ${t.gate2.length} live checks, ${t.gate2Denied.length} denied, ${t.gate2Refreshed.length} refreshed`,
  ];
  if (t.kind === "query") {
    parts.push(`${t.sent.length} chunks to the model`);
    parts.push(`guard: ${t.guard?.citations_rejected ?? 0} citations rejected, ${t.guard?.claims_stripped ?? 0} claims stripped, ${t.redactionTotal} DLP redactions`);
  }
  parts.push(`audit entry ${t.seq}, hash ${shortHash(t.entryHash, 10)}`);
  return parts.join("; ") + ".";
}
