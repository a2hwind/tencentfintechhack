"use client";

import type { AskResult, User } from "@/lib/api";
import { fmtBytes, fmtMs } from "@/lib/format";
import { AnswerCard } from "@/components/AnswerCard";
import type { EvidenceRequest } from "@/components/EvidenceDrawer";
import { Collapsible, Notice, RoleBadges, Spinner } from "@/components/ui";

export interface PaneState {
  user: string;
  question: string;
  result: AskResult | null;
  /** The identity the shown result was asked as (the evidence drawer opens sources as this user). */
  askedAs: string | null;
  error: string | null;
  pending: boolean;
  /** SHA-256 of the raw body, hex (when the browser offers SubtleCrypto). */
  sha256: string | null;
}

export function emptyPane(user: string, question: string): PaneState {
  return { user, question, result: null, askedAs: null, error: null, pending: false, sha256: null };
}

export function ComparePane({
  tag,
  state,
  users,
  disabled,
  onUser,
  onQuestion,
  onOpenEvidence,
}: {
  tag: "A" | "B";
  state: PaneState;
  users: User[];
  disabled: boolean;
  onUser: (id: string) => void;
  onQuestion: (q: string) => void;
  onOpenEvidence: (req: EvidenceRequest) => void;
}) {
  const user = users.find((u) => u.id === state.user);
  const known = Boolean(user);
  const r = state.result;
  const answeredAs = state.askedAs;

  return (
    <section className="card compare-pane" aria-label={`Pane ${tag}`}>
      <div className="compare-pane-head">
        <span className="compare-tag" aria-hidden="true">
          {tag}
        </span>
        <div className="field" style={{ flex: 1 }}>
          <label htmlFor={`cmp-user-${tag}`}>User</label>
          <div className="row" style={{ gap: 6 }}>
            <select id={`cmp-user-${tag}`} className="select" value={state.user} onChange={(e) => onUser(e.target.value)} disabled={disabled}>
              {!known && <option value={state.user}>{state.user}</option>}
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name} ({u.id})
                </option>
              ))}
            </select>
            {user ? <RoleBadges roles={user.roles} /> : null}
            {user?.title ? <span className="muted small">{user.title}</span> : null}
          </div>
        </div>
      </div>
      <div className="field">
        <label htmlFor={`cmp-q-${tag}`}>Question</label>
        <textarea id={`cmp-q-${tag}`} className="textarea" rows={2} value={state.question} onChange={(e) => onQuestion(e.target.value)} disabled={disabled} />
      </div>

      <dl className="compare-stats">
        <div>
          <dt>Elapsed</dt>
          <dd className="mono">{r ? fmtMs(r.elapsed_ms) : "—"}</dd>
        </div>
        <div>
          <dt>Body size</dt>
          <dd className="mono">{r ? fmtBytes(r.bytes) : "—"}</dd>
        </div>
        <div>
          <dt>Audit seq</dt>
          <dd className="mono">{r?.audit_seq !== null && r?.audit_seq !== undefined ? `#${r.audit_seq}` : "—"}</dd>
        </div>
        <div>
          <dt>SHA-256</dt>
          <dd className="mono" title={state.sha256 ?? undefined}>
            {state.sha256 ? `${state.sha256.slice(0, 12)}…` : "—"}
          </dd>
        </div>
      </dl>

      {state.pending && <Spinner label={`Asking as ${state.user}…`} />}
      {state.error && <Notice kind="error">{state.error}</Notice>}
      {r && answeredAs && <AnswerCard result={r} canOpenAudit={false} asker={answeredAs} onOpenEvidence={onOpenEvidence} />}
      {r && (
        <Collapsible title={`Raw response body (${fmtBytes(r.bytes)})`}>
          <pre className="json compare-raw">{r.raw}</pre>
        </Collapsible>
      )}
    </section>
  );
}
