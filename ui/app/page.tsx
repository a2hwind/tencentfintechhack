"use client";

import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ask, describeError, getMe, ApiError, type AskResult, type Me } from "@/lib/api";
import { useUser } from "@/lib/user";
import { fmtDateTime } from "@/lib/format";
import { AnswerCard } from "@/components/AnswerCard";
import { EvidenceDrawer, type EvidenceRequest } from "@/components/EvidenceDrawer";
import { Collapsible, Notice, RoleBadges, Spinner } from "@/components/ui";

const EXAMPLES = [
  "What's the status of the database migration and were there blockers raised in Slack last week?",
  "What's the latest runbook for the payment-service incident?",
  "Root cause of the payment outage last quarter and the follow-up tickets",
  "Summarise the auth service design discussion and link the decision doc",
  "Where is the Q3 breach report?",
];

interface Turn {
  id: number;
  question: string;
  askedAs: string;
  askedAsName: string;
  result?: AskResult;
  error?: string;
  pending: boolean;
}

export default function ChatPage() {
  const { userId, currentUser, hasRole, ready } = useUser();
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [evidence, setEvidence] = useState<EvidenceRequest | null>(null);
  const closeEvidence = useCallback(() => setEvidence(null), []);
  const nextId = useRef(1);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const canOpenAudit = hasRole("compliance");

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end", behavior: "smooth" });
  }, [turns]);

  const submit = useCallback(
    async (text: string) => {
      const q = text.trim();
      if (!q || busy) return;
      const id = nextId.current++;
      const askedAs = userId;
      const askedAsName = currentUser?.name ?? userId;
      setTurns((prev) => [...prev, { id, question: q, askedAs, askedAsName, pending: true }]);
      setQuestion("");
      setBusy(true);
      try {
        const result = await ask(askedAs, q);
        setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, result, pending: false } : t)));
      } catch (err: unknown) {
        setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, error: friendlyAskError(err), pending: false } : t)));
      } finally {
        setBusy(false);
        textareaRef.current?.focus();
      }
    },
    [busy, userId, currentUser],
  );

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    void submit(question);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void submit(question);
    }
  };

  return (
    <div className="chat-layout">
      <div className="chat-main">
        <div className="page-title" style={{ marginBottom: 0 }}>
          <h1>Ask the brain</h1>
          <span className="muted small">Answers come only from documents you can read right now. Every answer cites its sources and lands in the audit log.</span>
        </div>

        <div className="card ask-box">
          <form onSubmit={onSubmit} className="ask-row">
            <textarea
              ref={textareaRef}
              className="textarea"
              placeholder={ready ? `Ask a question as ${currentUser?.name ?? userId}… (Enter to send, Shift+Enter for a new line)` : "Loading identity…"}
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={onKeyDown}
              rows={2}
              disabled={!ready}
            />
            <button type="submit" className="btn btn-primary" disabled={busy || !ready || !question.trim()}>
              {busy ? "Asking…" : "Ask"}
            </button>
          </form>
          <div className="examples">
            {EXAMPLES.map((ex) => (
              <button key={ex} type="button" className="chip chip-btn" onClick={() => void submit(ex)} disabled={busy || !ready} title="Ask this question">
                {ex}
              </button>
            ))}
          </div>
        </div>

        <div className="conversation">
          {turns.length === 0 && (
            <div className="empty">
              No questions yet. Try one of the examples above, or switch the acting user in the header to see how the same question answers differently.
            </div>
          )}
          {turns.map((t) => (
            <div key={t.id} className="stack-sm">
              <div className="msg-user">{t.question}</div>
              <div className="msg-user-meta">asked as {t.askedAsName}</div>
              {t.pending && (
                <div className="card">
                  <Spinner label={`Planning, retrieving with Gate 1, verifying with Gate 2 and answering as ${t.askedAsName}…`} />
                </div>
              )}
              {t.error && <Notice kind="error">{t.error}</Notice>}
              {t.result && <AnswerCard result={t.result} canOpenAudit={canOpenAudit} asker={t.askedAs} onOpenEvidence={setEvidence} />}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </div>

      <EvidenceDrawer request={evidence} onClose={closeEvidence} />

      <aside className="stack">
        <IdentityCard userId={userId} ready={ready} name={currentUser?.name} title={currentUser?.title} roles={currentUser?.roles ?? []} />
        <div className="card">
          <h2 style={{ marginBottom: 8 }}>How an answer is produced</h2>
          <ol className="small" style={{ margin: 0, paddingLeft: 18, color: "var(--text-2)" }}>
            <li>Your entitlements are resolved live from each platform.</li>
            <li>
              <b>Gate 1</b>: the index query is filtered by those principals before any retrieval.
            </li>
            <li>
              <b>Gate 2</b>: every candidate document is re-checked at its source platform.
            </li>
            <li>Only verified chunks reach the model; unsupported claims and unknown citations are stripped.</li>
            <li>Click a citation number to open the exact passage; the source is re-checked live when you open it.</li>
            <li>The decision for every document is written to the hash-chained audit log before the answer is returned.</li>
          </ol>
        </div>
      </aside>
    </div>
  );
}

function friendlyAskError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 401) return "The API does not recognise the selected identity. Pick a user in the header and try again.";
    if (err.status === 0) return `${err.detail}. Is the backend running?`;
  }
  return describeError(err);
}

function IdentityCard({ userId, ready, name, title, roles }: { userId: string; ready: boolean; name?: string; title?: string; roles: string[] }) {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!ready) return;
    let cancelled = false;
    setLoading(true);
    setMe(null);
    setError(null);
    getMe(userId)
      .then((m) => {
        if (!cancelled) setMe(m);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(describeError(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [userId, ready]);

  return (
    <div className="card">
      <div className="card-header" style={{ marginBottom: 8 }}>
        <h2>Acting as</h2>
        <RoleBadges roles={roles} />
      </div>
      <div className="stack-sm">
        <div>
          <div className="strong">{name ?? userId}</div>
          <div className="muted small">
            {title ? `${title} · ` : ""}
            <span className="mono">{userId}</span>
          </div>
        </div>
        {loading && <Spinner label="Resolving entitlements…" />}
        {error && <Notice kind="error">{error}</Notice>}
        {me && (
          <>
            <div className="small muted">
              {me.principals.length} principals resolved at {fmtDateTime(me.resolved_at)}
            </div>
            <Collapsible title={`Principals (${me.principals.length})`}>
              <div className="principals">
                {me.principals.map((p) => (
                  <span key={p} className="chip-mono">
                    {p}
                  </span>
                ))}
              </div>
            </Collapsible>
            <Collapsible title="Platform user ids">
              <dl className="kv">
                {Object.entries(me.platform_user_ids).map(([platform, id]) => (
                  <div key={platform} style={{ display: "contents" }}>
                    <dt>{platform}</dt>
                    <dd className="mono">{id}</dd>
                  </div>
                ))}
                {Object.keys(me.platform_user_ids).length === 0 && (
                  <>
                    <dt>none</dt>
                    <dd className="muted">No platform accounts mapped.</dd>
                  </>
                )}
              </dl>
            </Collapsible>
          </>
        )}
      </div>
    </div>
  );
}
