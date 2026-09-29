"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type RefObject } from "react";
import { describeError, openSource, type SourceOpenResult, type SourceView } from "@/lib/api";
import { fmtClock, fmtDate, platformName } from "@/lib/format";
import { usePrefersReducedMotion } from "@/lib/hooks";
import { Notice, PlatformBadge, Spinner } from "./ui";

/** What a citation chip asks the drawer to open. */
export interface EvidenceRequest {
  /** The identity that received the answer: the source is opened (and re-checked) as this user. */
  asker: string;
  doc: string;
  /** The chip's number in its answer. */
  n?: number;
  /** The sentence's supporting passage in this document, when opened from a sentence chip. */
  chunk?: string | null;
  quote?: string | null;
}

type LoadState = { status: "loading" } | { status: "ok"; result: SourceOpenResult } | { status: "error"; message: string };

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Right-side drawer that opens a cited source as the asker. Every open is a new access: the API
 * re-checks both gates live and logs it. An unavailable source shows only the uniform message.
 */
export function EvidenceDrawer({ request, onClose }: { request: EvidenceRequest | null; onClose: () => void }) {
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const panelRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  const titleId = useId();
  const open = request !== null;

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  // Fetch on every open (a new request object), so each click is one audited access.
  useEffect(() => {
    if (!request) return;
    let cancelled = false;
    setState({ status: "loading" });
    openSource(request.asker, request.doc)
      .then((result) => {
        if (!cancelled) setState({ status: "ok", result });
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ status: "error", message: describeError(err) });
      });
    return () => {
      cancelled = true;
    };
  }, [request]);

  // Focus in on open, back to the chip on close; Esc closes.
  useEffect(() => {
    if (!open) return;
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCloseRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      returnFocus.current?.focus();
    };
  }, [open]);

  if (!request) return null;

  const trapTab = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key !== "Tab" || !panelRef.current) return;
    const items = Array.from(panelRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  };

  const view = state.status === "ok" ? state.result.view : null;
  const auditSeq = state.status === "ok" ? state.result.audit_seq : null;

  return (
    <div className="drawer-root">
      <div className="drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <div ref={panelRef} className="drawer" role="dialog" aria-modal="true" aria-labelledby={titleId} onKeyDown={trapTab}>
        <div className="drawer-head">
          <div className="drawer-kicker">
            Source{request.n !== undefined ? <span className="source-num">{request.n}</span> : null}
            <span className="muted">· opened as <span className="mono">{request.asker}</span></span>
          </div>
          <button ref={closeRef} type="button" className="icon-btn" onClick={onClose} aria-label="Close source">
            <span aria-hidden="true">×</span>
          </button>
        </div>

        <div className="drawer-body">
          {state.status === "loading" && <Spinner label="Re-checking access at the source…" />}
          {state.status === "error" && <Notice kind="error">{state.message}</Notice>}
          {view && !view.available && (
            <>
              <h2 id={titleId} className="sr-only">
                Source unavailable
              </h2>
              <Notice kind="plain">{view.message ?? "This source isn't available to you right now."}</Notice>
            </>
          )}
          {view && view.available && <AvailableSource view={view} request={request} titleId={titleId} />}
          {state.status !== "ok" && (
            <h2 id={titleId} className="sr-only">
              Source
            </h2>
          )}
        </div>

        <div className="drawer-foot">
          Opening a source is re-checked at the platform and logged{auditSeq !== null ? <> · <span className="mono">audit #{auditSeq}</span></> : null}
        </div>
      </div>
    </div>
  );
}

function AvailableSource({ view, request, titleId }: { view: SourceView; request: EvidenceRequest; titleId: string }) {
  const reduced = usePrefersReducedMotion();
  const markRef = useRef<HTMLElement | null>(null);
  const quote = request.quote ?? null;

  // The quote is an exact substring of one chunk: prefer the chunk the evidence named.
  const target = useMemo(() => {
    if (!quote) return null;
    const preferred = view.chunks.find((c) => c.chunk === request.chunk && c.text.includes(quote));
    return (preferred ?? view.chunks.find((c) => c.text.includes(quote)))?.chunk ?? null;
  }, [quote, request.chunk, view.chunks]);

  useEffect(() => {
    markRef.current?.scrollIntoView({ block: "center", behavior: reduced ? "auto" : "smooth" });
  }, [target, reduced]);

  return (
    <div className="stack-sm">
      <div className="drawer-title-row">
        <PlatformBadge platform={view.platform} />
        <h2 id={titleId} className="drawer-title">
          {view.title ?? view.doc}
        </h2>
      </div>
      <div className="tiny muted mono break">
        {view.doc}
        {view.version !== null ? ` · v${view.version}` : ""}
        {view.updated ? ` · updated ${fmtDate(view.updated)}` : ""}
      </div>

      <div className="why-line">
        <span className="why-label">Why you can see this</span>
        <span className="chip-mono">{view.rule ?? "—"}</span>
        <span className="muted small">verified live {fmtClock(view.verified_at)}</span>
        {view.refreshed && <span className="badge badge-amber">refreshed to v{view.version ?? "?"}</span>}
      </div>

      {view.url && (
        <div>
          <a className="btn btn-sm" href={view.url} target="_blank" rel="noreferrer noopener">
            Open in {platformName(view.platform)} <span aria-hidden="true">↗</span>
          </a>
        </div>
      )}

      <div className="sources-label" style={{ marginTop: 8, marginBottom: 0 }}>
        Passages ({view.chunks.length})
      </div>
      {quote && target === null && <p className="small muted">The quoted passage is not in the current version of this source.</p>}
      <div className="stack-sm">
        {view.chunks.map((c) => (
          <div key={c.chunk} className={`passage${c.chunk === target ? " passage-hit" : ""}`}>
            <div className="passage-id mono">{c.chunk}</div>
            <p className="passage-text">{c.chunk === target && quote ? <Highlighted text={c.text} quote={quote} markRef={markRef} /> : c.text}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

function Highlighted({ text, quote, markRef }: { text: string; quote: string; markRef: RefObject<HTMLElement | null> }) {
  const idx = text.indexOf(quote);
  if (idx === -1) return <>{text}</>;
  return (
    <>
      {text.slice(0, idx)}
      <mark ref={markRef} className="evidence-mark">
        {quote}
      </mark>
      {text.slice(idx + quote.length)}
    </>
  );
}
