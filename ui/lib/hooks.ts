"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** True when the user asked the OS for reduced motion; animations then show their end state at once. */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return reduced;
}

/**
 * Reveal `total` stages one after another, `stepMs` apart, restarting whenever `runKey` changes
 * (a new entry, or Replay). The first stage shows immediately. With reduced motion every stage
 * shows at once.
 */
export function useStagedReveal(total: number, runKey: string, stepMs = 280): number {
  const reduced = usePrefersReducedMotion();
  const [state, setState] = useState<{ key: string; count: number }>({ key: runKey, count: total > 0 ? 1 : 0 });

  // A new run starts from the first stage in the same render, so the new content never flashes in full.
  let count = state.count;
  if (state.key !== runKey) {
    count = total > 0 ? 1 : 0;
    setState({ key: runKey, count });
  }

  useEffect(() => {
    if (reduced || total <= 1) {
      setState({ key: runKey, count: total });
      return;
    }
    let n = 1;
    setState({ key: runKey, count: n });
    const timer = window.setInterval(() => {
      n += 1;
      setState({ key: runKey, count: Math.min(n, total) });
      if (n >= total) window.clearInterval(timer);
    }, stepMs);
    return () => window.clearInterval(timer);
  }, [runKey, total, stepMs, reduced]);

  return reduced ? total : Math.min(count, total);
}

function readSession<T>(key: string, fallback: T): T {
  try {
    const raw = window.sessionStorage.getItem(key);
    return raw === null ? fallback : (JSON.parse(raw) as T);
  } catch {
    return fallback;
  }
}

/**
 * useState mirrored to sessionStorage: a per-tab convenience (the guided demo's progress survives a
 * visit to /compare or /audit). Reads happen after mount, so server and first client render agree.
 */
export function useSessionState<T>(key: string, initial: T, revive?: (stored: T) => T): [T, (next: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(initial);
  const current = useRef<T>(initial);
  const reviveRef = useRef(revive);

  useEffect(() => {
    let stored = readSession(key, current.current);
    if (reviveRef.current) stored = reviveRef.current(stored);
    current.current = stored;
    setValue(stored);
  }, [key]);

  // Writes are synchronous, so a value set just before navigating away is not lost.
  const update = useCallback(
    (next: T | ((prev: T) => T)) => {
      const resolved = typeof next === "function" ? (next as (p: T) => T)(current.current) : next;
      current.current = resolved;
      try {
        window.sessionStorage.setItem(key, JSON.stringify(resolved));
      } catch {
        // Storage unavailable: the value simply does not survive navigation.
      }
      setValue(resolved);
    },
    [key],
  );

  return [value, update];
}
