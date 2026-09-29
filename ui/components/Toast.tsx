"use client";

import { useEffect } from "react";

export interface ToastMessage {
  id: number;
  kind: "ok" | "error" | "info";
  text: string;
}

/** A small result toast, bottom right; errors and explanations stay twice as long. The live region is always mounted. */
export function Toast({ toast, onDismiss, ms = 5000 }: { toast: ToastMessage | null; onDismiss: () => void; ms?: number }) {
  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(onDismiss, toast.kind === "ok" ? ms : ms * 2);
    return () => window.clearTimeout(timer);
  }, [toast, onDismiss, ms]);

  return (
    <div className="toast-region" role="status" aria-live="polite">
      {toast && (
        <div key={toast.id} className={`toast toast-${toast.kind}`}>
          <span className="toast-icon" aria-hidden="true">
            {toast.kind === "ok" ? "✓" : toast.kind === "error" ? "!" : "i"}
          </span>
          <span className="toast-text">{toast.text}</span>
          <button type="button" className="icon-btn" aria-label="Dismiss notification" onClick={onDismiss}>
            <span aria-hidden="true">×</span>
          </button>
        </div>
      )}
    </div>
  );
}
