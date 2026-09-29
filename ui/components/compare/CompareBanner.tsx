"use client";

import type { AskResult } from "@/lib/api";
import { fmtBytes, fmtMs } from "@/lib/format";

/** Byte offset (UTF-8) of the first difference between two strings, or null when they are equal. */
export function firstDifference(a: string, b: string): number | null {
  if (a === b) return null;
  const n = Math.min(a.length, b.length);
  let i = 0;
  while (i < n && a.charCodeAt(i) === b.charCodeAt(i)) i += 1;
  return new TextEncoder().encode(a.slice(0, i)).length;
}

/** Compares the two raw response bodies byte for byte. */
export function CompareBanner({ a, b, shaA, pending }: { a: AskResult | null; b: AskResult | null; shaA: string | null; pending: boolean }) {
  if (pending) {
    return (
      <div className="compare-banner waiting" role="status">
        <div className="compare-banner-title">Asking both at once…</div>
      </div>
    );
  }
  if (!a || !b) {
    return (
      <div className="compare-banner waiting" role="status">
        <div className="compare-banner-title">Ask both to compare the raw response bodies byte for byte</div>
      </div>
    );
  }
  const offset = firstDifference(a.raw, b.raw);
  if (offset === null) {
    return (
      <div className="compare-banner same" role="status">
        <span className="compare-banner-mark" aria-hidden="true">
          ✓
        </span>
        <div>
          <div className="compare-banner-title">Byte-identical responses ✓</div>
          <div className="compare-banner-sub">
            {fmtBytes(a.bytes)} each{shaA ? <> · SHA-256 <span className="mono">{shaA.slice(0, 16)}…</span></> : null} · {fmtMs(a.elapsed_ms)} vs {fmtMs(b.elapsed_ms)}
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="compare-banner differ" role="status">
      <span className="compare-banner-mark" aria-hidden="true">
        ≠
      </span>
      <div>
        <div className="compare-banner-title">Responses differ</div>
        <div className="compare-banner-sub">
          {fmtBytes(a.bytes)} vs {fmtBytes(b.bytes)} · first difference at byte {offset.toLocaleString("en-US")} · {fmtMs(a.elapsed_ms)} vs {fmtMs(b.elapsed_ms)}
        </div>
      </div>
    </div>
  );
}
