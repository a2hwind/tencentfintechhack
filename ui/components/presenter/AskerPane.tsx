"use client";

import type { AskResult, User } from "@/lib/api";
import { AnswerCard } from "@/components/AnswerCard";
import type { EvidenceRequest } from "@/components/EvidenceDrawer";
import { Notice, RoleBadges, Spinner } from "@/components/ui";

export interface PresenterTurn {
  id: string;
  asker: string;
  question: string;
  result?: AskResult;
  error?: string;
  pending: boolean;
}

/**
 * "What <name> sees": only what /ask returned to the asker (the answer, its sources and the audit
 * number from the response header). Nothing here comes from the audit log.
 */
export function AskerPane({ turns, users, fallbackAsker, onOpenEvidence }: { turns: PresenterTurn[]; users: User[]; fallbackAsker: string; onOpenEvidence: (req: EvidenceRequest) => void }) {
  const latest = turns[0];
  const earlier = turns.slice(1);
  const askerId = latest?.asker ?? fallbackAsker;
  const asker = users.find((u) => u.id === askerId);
  const name = asker?.name ?? askerId;

  return (
    <section className="pv-pane pv-asker" aria-labelledby="pv-asker-title">
      <header className="pv-pane-head">
        <h2 id="pv-asker-title">What {name} sees</h2>
        <span className="muted small">
          {asker?.title ? `${asker.title} · ` : ""}
          <span className="mono">{askerId}</span>
        </span>
        {asker ? <RoleBadges roles={asker.roles} /> : null}
      </header>

      <div className="pv-scroll pv-asker-scroll">
        {!latest && (
          <div className="card empty pv-empty">
            Ask a question, pick an example, or open the guided demo. The answer {name} gets appears here, and its full trace appears on the right.
          </div>
        )}
        {latest && <Turn turn={latest} users={users} onOpenEvidence={onOpenEvidence} prominent />}

        {earlier.length > 0 && (
          <div className="pv-earlier">
            <div className="sources-label">Earlier answers ({earlier.length})</div>
            {earlier.map((t) => (
              <details key={t.id} className="collapsible pv-earlier-item">
                <summary>
                  <span className="mono muted">{t.asker}</span>
                  <span className="pv-earlier-q">{t.question}</span>
                  {t.result?.audit_seq !== undefined && t.result?.audit_seq !== null ? <span className="audit-tag">#{t.result.audit_seq}</span> : null}
                  {t.result?.no_result ? <span className="badge badge-muted">no result</span> : null}
                </summary>
                <div className="collapsible-body">
                  <Turn turn={t} users={users} onOpenEvidence={onOpenEvidence} />
                </div>
              </details>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

function Turn({ turn, users, onOpenEvidence, prominent }: { turn: PresenterTurn; users: User[]; onOpenEvidence: (req: EvidenceRequest) => void; prominent?: boolean }) {
  const name = users.find((u) => u.id === turn.asker)?.name ?? turn.asker;
  return (
    <div className={`stack-sm${prominent ? " pv-latest" : ""}`}>
      <div className="msg-user">{turn.question}</div>
      <div className="msg-user-meta">asked as {name}</div>
      {turn.pending && (
        <div className="card">
          <Spinner label={`Planning, retrieving with Gate 1, verifying with Gate 2 and answering as ${name}…`} />
        </div>
      )}
      {turn.error && <Notice kind="error">{turn.error}</Notice>}
      {turn.result && <AnswerCard result={turn.result} canOpenAudit={false} asker={turn.asker} onOpenEvidence={onOpenEvidence} />}
    </div>
  );
}
