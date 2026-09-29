"use client";

import Link from "next/link";
import { useCallback, useMemo, useRef, useState, type ReactNode } from "react";
import type { AnswerSentence, AskResult, Citation, EvidenceQuote } from "@/lib/api";
import { fmtDate, fmtDateTime, platformOf } from "@/lib/format";
import type { EvidenceRequest } from "./EvidenceDrawer";
import { Collapsible, PlatformBadge } from "./ui";

const TAG_RE = /\[doc:([^\]]+)\]/g;

type Part = { kind: "text"; text: string } | { kind: "cite"; n: number; doc: string };

interface NumberedSource extends Citation {
  n: number;
  /** True when the answer text (or a sentence) cites the document. */
  cited: boolean;
}

function placeholderCitation(doc: string): Citation {
  return { doc, platform: platformOf(doc) as Citation["platform"], title: doc, url: null, updated: null, chunks: [] };
}

/** Number `order` 1..n, then append citations the answer never referenced. */
function numberSources(order: string[], citations: Citation[]): { numbers: Map<string, number>; sources: NumberedSource[] } {
  const numbers = new Map<string, number>();
  const byDoc = new Map(citations.map((c) => [c.doc, c]));
  const sources: NumberedSource[] = [];
  for (const doc of order) {
    if (numbers.has(doc)) continue;
    const n = numbers.size + 1;
    numbers.set(doc, n);
    sources.push({ ...(byDoc.get(doc) ?? placeholderCitation(doc)), n, cited: true });
  }
  for (const c of citations) {
    if (numbers.has(c.doc)) continue;
    const n = numbers.size + 1;
    numbers.set(c.doc, n);
    sources.push({ ...c, n, cited: false });
  }
  return { numbers, sources };
}

/**
 * Split the answer on `[doc:<id>]` tags, numbering documents in order of first appearance.
 * Citations the model returned but never tagged are appended after the tagged ones.
 * (Fallback for answers without structured sentences.)
 */
export function parseAnswer(answer: string, citations: Citation[]): { parts: Part[]; sources: NumberedSource[] } {
  const order: string[] = [];
  const parts: Part[] = [];
  const text = answer.replace(/[ \t]+\[doc:/g, "[doc:"); // let the chip hug the sentence
  let last = 0;
  const seen = new Map<string, number>();
  for (const match of text.matchAll(TAG_RE)) {
    const idx = match.index ?? 0;
    if (idx > last) parts.push({ kind: "text", text: text.slice(last, idx) });
    const doc = match[1].trim();
    let n = seen.get(doc);
    if (n === undefined) {
      n = seen.size + 1;
      seen.set(doc, n);
      order.push(doc);
    }
    parts.push({ kind: "cite", n, doc });
    last = idx + match[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", text: text.slice(last) });
  return { parts, sources: numberSources(order, citations).sources };
}

/** Number documents in order of first appearance across the sentences, consistent with the Sources row. */
export function numberSentences(sentences: AnswerSentence[], citations: Citation[]): { numbers: Map<string, number>; sources: NumberedSource[] } {
  const order: string[] = [];
  for (const s of sentences) for (const doc of s.citations) order.push(doc);
  return numberSources(order, citations);
}

const END_PUNCT = /[.!?…:;]["')\]]?$/;

export function AnswerCard({
  result,
  canOpenAudit,
  asker,
  onOpenEvidence,
}: {
  result: AskResult;
  canOpenAudit: boolean;
  /** Identity that received this answer; required to open sources in the evidence drawer. */
  asker?: string;
  /** When set, citation chips open the evidence drawer instead of jumping to the Sources row. */
  onOpenEvidence?: (req: EvidenceRequest) => void;
}) {
  const structured = result.sentences.length > 0;
  const fallback = useMemo(() => (structured ? null : parseAnswer(result.answer, result.citations)), [structured, result.answer, result.citations]);
  const numbered = useMemo(() => (structured ? numberSentences(result.sentences, result.citations) : null), [structured, result.sentences, result.citations]);
  const sources = numbered?.sources ?? fallback?.sources ?? [];
  const sourceRefs = useRef<Map<number, HTMLElement>>(new Map());
  const [flash, setFlash] = useState<number | null>(null);
  const drawerEnabled = Boolean(onOpenEvidence && asker);

  const jumpTo = useCallback((n: number) => {
    const el = sourceRefs.current.get(n);
    if (!el) return;
    el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    setFlash(n);
    window.setTimeout(() => setFlash((cur) => (cur === n ? null : cur)), 1200);
  }, []);

  const openEvidence = useCallback(
    (doc: string, n: number, evidence?: EvidenceQuote) => {
      if (onOpenEvidence && asker) onOpenEvidence({ asker, doc, n, chunk: evidence?.chunk ?? null, quote: evidence?.quote ?? null });
      else jumpTo(n);
    },
    [onOpenEvidence, asker, jumpTo],
  );

  const auditTag: ReactNode =
    result.audit_seq === null ? null : canOpenAudit ? (
      <Link className="audit-tag" href={`/audit?seq=${result.audit_seq}`} title="Open this entry in the compliance console">
        audit #{result.audit_seq}
      </Link>
    ) : (
      <span className="audit-tag" title="Audit log sequence number of this query">
        audit #{result.audit_seq}
      </span>
    );

  if (result.no_result) {
    return (
      <div className="card answer">
        <p className="answer-text no-result">{result.answer}</p>
        <div className="answer-footer">
          <span>No accessible sources.</span>
          {auditTag}
        </div>
      </div>
    );
  }

  return (
    <div className="card answer">
      {numbered ? (
        <p className="answer-text">
          {result.sentences.map((s, i) => (
            <span key={i} className="answer-sentence">
              {s.text}
              {s.citations.map((doc) => {
                const n = numbered.numbers.get(doc) ?? 0;
                return (
                  <button
                    key={doc}
                    type="button"
                    className="cite"
                    title={drawerEnabled ? `${doc}\nOpen the passage behind this sentence (re-checked live)` : doc}
                    aria-label={drawerEnabled ? `Source ${n}: open the passage behind this sentence` : `Source ${n}`}
                    onClick={() => openEvidence(doc, n, s.evidence.find((e) => e.doc === doc))}
                  >
                    {n}
                  </button>
                );
              })}
              {END_PUNCT.test(s.text) ? "" : "."}
              {i < result.sentences.length - 1 ? " " : ""}
            </span>
          ))}
        </p>
      ) : (
        <p className="answer-text">
          {(fallback?.parts ?? []).map((p, i) =>
            p.kind === "text" ? (
              <span key={i}>{p.text}</span>
            ) : (
              <button key={i} type="button" className="cite" title={p.doc} aria-label={`Source ${p.n}`} onClick={() => openEvidence(p.doc, p.n)}>
                {p.n}
              </button>
            ),
          )}
        </p>
      )}

      {sources.length > 0 && (
        <div>
          <div className="sources-label">Sources</div>
          <div className="chips">
            {sources.map((s) => (
              <SourceChip
                key={s.doc}
                source={s}
                flash={flash === s.n}
                onOpen={drawerEnabled ? () => openEvidence(s.doc, s.n) : undefined}
                register={(el) => {
                  if (el) sourceRefs.current.set(s.n, el);
                  else sourceRefs.current.delete(s.n);
                }}
              />
            ))}
          </div>
        </div>
      )}

      {result.provenance.length > 0 && (
        <Collapsible title="Why you can see this">
          <div className="table-wrap">
            <table className="table table-compact">
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Granting rule</th>
                  <th className="num">Version</th>
                  <th>Verified at</th>
                </tr>
              </thead>
              <tbody>
                {result.provenance.map((p) => (
                  <tr key={p.doc}>
                    <td>
                      <span className="row" style={{ gap: 6 }}>
                        <PlatformBadge platform={platformOf(p.doc)} />
                        <span className="mono">{p.doc}</span>
                      </span>
                    </td>
                    <td>
                      <span className="chip-mono">{p.rule}</span>
                    </td>
                    <td className="num">{p.version ?? "—"}</td>
                    <td className="nowrap">{fmtDateTime(p.verified_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small muted" style={{ marginTop: 6 }}>
            Gate 1 filtered the index by your principals; Gate 2 re-checked each document at the source platform just before answering.
          </p>
        </Collapsible>
      )}

      <div className="answer-footer">
        <span>
          {sources.length} source{sources.length === 1 ? "" : "s"}
        </span>
        {drawerEnabled && (
          <>
            <span>·</span>
            <span>click a number to see the passage</span>
          </>
        )}
        <span>·</span>
        {auditTag}
      </div>
    </div>
  );
}

function SourceChip({ source, flash, register, onOpen }: { source: NumberedSource; flash: boolean; register: (el: HTMLElement | null) => void; onOpen?: () => void }) {
  const inner = (
    <>
      <span className="source-num">{source.n}</span>
      <PlatformBadge platform={source.platform} />
      <span className="source-title" title={source.title}>
        {source.title}
      </span>
      {source.updated ? <span className="source-meta">updated {fmtDate(source.updated)}</span> : null}
      {source.url && !onOpen ? <span className="source-meta">↗</span> : null}
    </>
  );
  const cls = `source-chip${flash ? " flash" : ""}`;
  if (onOpen) {
    return (
      <button ref={register} type="button" className={`${cls} source-chip-btn`} onClick={onOpen} title={`${source.doc}\nOpen this source (re-checked live)`}>
        {inner}
      </button>
    );
  }
  if (source.url) {
    return (
      <a ref={register} className={cls} href={source.url} target="_blank" rel="noreferrer noopener" title={`${source.doc}\n${source.url}`}>
        {inner}
      </a>
    );
  }
  return (
    <span ref={register} className={cls} title={source.doc}>
      {inner}
    </span>
  );
}
