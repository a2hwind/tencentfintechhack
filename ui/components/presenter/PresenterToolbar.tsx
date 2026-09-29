"use client";

import type { FormEvent } from "react";
import type { User } from "@/lib/api";
import { QUICK_ACTIONS, QUICK_ACTION_ROW, type QuickActionKey } from "@/lib/demoActions";
import type { ExampleQuestion } from "@/lib/demoBeats";

export function PresenterToolbar({
  users,
  asker,
  onAskerChange,
  question,
  onQuestionChange,
  onAsk,
  examples,
  onExample,
  busy,
  beatsOpen,
  onToggleBeats,
  actionBusy,
  onAction,
}: {
  users: User[];
  asker: string;
  onAskerChange: (id: string) => void;
  question: string;
  onQuestionChange: (q: string) => void;
  onAsk: () => void;
  examples: ExampleQuestion[];
  onExample: (ex: ExampleQuestion) => void;
  /** An ask, action or beat is in flight. */
  busy: boolean;
  beatsOpen: boolean;
  onToggleBeats: () => void;
  actionBusy: QuickActionKey | null;
  onAction: (key: QuickActionKey) => void;
}) {
  const known = users.some((u) => u.id === asker);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    onAsk();
  };

  return (
    <div className="card pv-toolbar">
      <form className="pv-ask-row" onSubmit={submit}>
        <label className="pv-field-label" htmlFor="pv-asker">
          Ask as
        </label>
        <select id="pv-asker" className="select pv-asker-select" value={asker} onChange={(e) => onAskerChange(e.target.value)}>
          {!known && <option value={asker}>{asker}</option>}
          {users.map((u) => (
            <option key={u.id} value={u.id}>
              {u.name} ({u.id})
            </option>
          ))}
        </select>
        <input className="input pv-question" value={question} onChange={(e) => onQuestionChange(e.target.value)} placeholder="Ask a question as this person…" aria-label="Question" />
        <button type="submit" className="btn btn-primary" disabled={busy || !question.trim()}>
          Ask
        </button>
        <span className="pv-toolbar-spacer" />
        <button type="button" className={`btn${beatsOpen ? " btn-active" : ""}`} onClick={onToggleBeats} aria-pressed={beatsOpen}>
          Guided demo
        </button>
        <button type="button" className="btn btn-danger" onClick={() => onAction("reset")} disabled={busy} title={QUICK_ACTIONS.reset.hint}>
          {actionBusy === "reset" ? "Resetting…" : "Reset demo"}
        </button>
      </form>

      <div className="pv-toolbar-row">
        <span className="pv-row-label">Examples</span>
        <div className="pv-chips">
          {examples.map((ex) => (
            <button key={ex.key} type="button" className="chip chip-btn pv-example" onClick={() => onExample(ex)} disabled={busy} title={`Ask as ${ex.asker}: ${ex.question}`}>
              <span className="pv-example-user">{ex.asker}</span>
              {ex.label}
            </button>
          ))}
        </div>
      </div>

      <div className="pv-toolbar-row">
        <span className="pv-row-label">
          Admin <span className="muted">(as admin)</span>
        </span>
        <div className="pv-chips">
          {QUICK_ACTION_ROW.map((key) => (
            <button key={key} type="button" className="btn btn-sm" onClick={() => onAction(key)} disabled={busy} title={QUICK_ACTIONS[key].hint}>
              {actionBusy === key ? "Working…" : QUICK_ACTIONS[key].label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
