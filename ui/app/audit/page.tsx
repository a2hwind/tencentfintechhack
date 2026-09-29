"use client";

import Link from "next/link";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { auditNaturalLanguage, describeError, getAuditContainers, getAuditEntries, getAuditEntry, type ContainerInfo, type Entry, type FullEntry, type NlFilter } from "@/lib/api";
import { useUser } from "@/lib/user";
import { Notice, Spinner } from "@/components/ui";
import { HeadStrip } from "@/components/audit/HeadStrip";
import { EMPTY_FILTER, FilterBar, toEntriesFilter, type FilterForm } from "@/components/audit/FilterBar";
import { EntriesTable } from "@/components/audit/EntriesTable";
import { EntryDetail } from "@/components/audit/EntryDetail";
import { CannedViews, type CannedResult } from "@/components/audit/CannedViews";
import { AlertsCard } from "@/components/audit/AlertsCard";

const NL_EXAMPLE = "Everything jdoe accessed in the payment gateway space in the last 30 days";

export default function AuditPage() {
  return (
    <Suspense fallback={<Spinner label="Loading console…" />}>
      <AuditConsole />
    </Suspense>
  );
}

interface EntriesView {
  label: string;
  entries: Entry[];
  readLoggedAs: number;
}

function AuditConsole() {
  const { userId, hasRole, usersLoaded, users, ready } = useUser();
  const searchParams = useSearchParams();
  const seqParam = searchParams.get("seq");
  // ?nl=<question> prefills and runs the natural-language query; &verify=1 runs Verify chain (guided demo).
  const nlParam = searchParams.get("nl");
  const verifyParam = searchParams.get("verify") === "1";
  const fromPresenter = searchParams.get("from") === "presenter";
  const isCompliance = hasRole("compliance");

  const [containers, setContainers] = useState<ContainerInfo[]>([]);
  const [filter, setFilter] = useState<FilterForm>(EMPTY_FILTER);
  const [view, setView] = useState<EntriesView | null>(null);
  const [loadingEntries, setLoadingEntries] = useState(false);
  const [entriesError, setEntriesError] = useState<string | null>(null);

  const [nlQuestion, setNlQuestion] = useState("");
  const [nlFilter, setNlFilter] = useState<NlFilter | null>(null);
  const [nlBusy, setNlBusy] = useState(false);

  const [selectedSeq, setSelectedSeq] = useState<number | null>(null);
  const [detail, setDetail] = useState<FullEntry | null>(null);
  const [detailReadAs, setDetailReadAs] = useState<number | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const [lastRead, setLastRead] = useState<number | null>(null);
  const [headKey, setHeadKey] = useState(0);
  const requestId = useRef(0);

  const noteRead = useCallback((seq: number) => {
    setLastRead(seq);
    setHeadKey((k) => k + 1);
  }, []);

  const loadEntries = useCallback(
    async (form: FilterForm, label: string) => {
      const id = ++requestId.current;
      setLoadingEntries(true);
      setEntriesError(null);
      try {
        const res = await getAuditEntries(userId, toEntriesFilter(form));
        if (id !== requestId.current) return;
        setView({ label, entries: res.entries, readLoggedAs: res.read_logged_as });
        noteRead(res.read_logged_as);
      } catch (err: unknown) {
        if (id !== requestId.current) return;
        setEntriesError(describeError(err));
      } finally {
        if (id === requestId.current) setLoadingEntries(false);
      }
    },
    [userId, noteRead],
  );

  const loadDetail = useCallback(
    async (seq: number) => {
      setSelectedSeq(seq);
      setDetailLoading(true);
      setDetailError(null);
      try {
        const res = await getAuditEntry(userId, seq);
        setDetail(res.entry);
        setDetailReadAs(res.read_logged_as);
        noteRead(res.read_logged_as);
      } catch (err: unknown) {
        setDetail(null);
        setDetailError(describeError(err));
      } finally {
        setDetailLoading(false);
      }
    },
    [userId, noteRead],
  );

  // Initial load: containers, the default entries view (or the ?nl= question), and ?seq=N if present.
  useEffect(() => {
    if (!ready || !isCompliance) return;
    getAuditContainers(userId)
      .then(setContainers)
      .catch(() => setContainers([]));
    const q = nlParam?.trim() ?? "";
    if (q.length >= 3) {
      setNlQuestion(q);
      void runNlQuery(q);
    } else {
      void loadEntries(EMPTY_FILTER, "Latest entries");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, isCompliance, userId]);

  useEffect(() => {
    if (!ready || !isCompliance) return;
    const seq = seqParam ? Number(seqParam) : NaN;
    if (Number.isFinite(seq) && seq > 0 && seq !== selectedSeq) void loadDetail(seq);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seqParam, ready, isCompliance]);

  const selectEntry = useCallback(
    (seq: number) => {
      void loadDetail(seq);
      try {
        const url = new URL(window.location.href);
        url.searchParams.set("seq", String(seq));
        window.history.replaceState(null, "", url.toString());
      } catch {
        // URL updates are a convenience only.
      }
    },
    [loadDetail],
  );

  const applyFilter = () => {
    setNlFilter(null);
    void loadEntries(filter, "Filtered entries");
  };

  const resetFilter = () => {
    setFilter(EMPTY_FILTER);
    setNlFilter(null);
    void loadEntries(EMPTY_FILTER, "Latest entries");
  };

  const runNl = (e: FormEvent) => {
    e.preventDefault();
    const q = nlQuestion.trim();
    if (q.length < 3) return;
    void runNlQuery(q);
  };

  async function runNlQuery(q: string) {
    const id = ++requestId.current;
    setNlBusy(true);
    setLoadingEntries(true);
    setEntriesError(null);
    try {
      const res = await auditNaturalLanguage(userId, q);
      if (id !== requestId.current) return;
      setNlFilter(res.filter);
      setFilter({
        ...EMPTY_FILTER,
        actor: res.filter.actor ?? "",
        kind: res.filter.kind ?? "",
        platform: res.filter.platform ?? "",
        container: res.filter.container ?? "",
        doc: res.filter.doc ?? "",
        decision: res.filter.decision ?? "",
        days: res.filter.days ? String(res.filter.days) : "",
        limit: "100",
      });
      setView({ label: `“${res.question}”`, entries: res.entries, readLoggedAs: res.read_logged_as });
      noteRead(res.read_logged_as);
    } catch (err: unknown) {
      if (id !== requestId.current) return;
      setEntriesError(describeError(err));
    } finally {
      if (id === requestId.current) {
        setLoadingEntries(false);
        setNlBusy(false);
      }
    }
  }

  const onCannedResult = (r: CannedResult) => {
    if (r.kind === "user-access") {
      setNlFilter(null);
      setView({ label: `Queries by ${r.data.actor} in the last ${r.data.days} days`, entries: r.data.queries, readLoggedAs: r.data.read_logged_as });
    }
  };

  const docSuggestions = useMemo(() => {
    const docs = new Set<string>();
    for (const e of view?.entries ?? []) for (const d of e.decisions) docs.add(d.doc);
    return Array.from(docs).sort();
  }, [view]);

  if (!ready || !usersLoaded) {
    return <Spinner label="Loading identity…" />;
  }

  if (!isCompliance) {
    return (
      <div className="stack">
        <div className="page-title">
          <h1>Compliance console</h1>
        </div>
        <Notice kind="warn">
          <b>Switch to the compliance user to open the console.</b> The audit log is the most sensitive dataset in the system: reading it requires the <span className="mono">compliance</span> role, and every read is itself logged.
        </Notice>
      </div>
    );
  }

  return (
    <div className="stack">
      <div className="page-title">
        <h1>Compliance console</h1>
        <span className="muted small">
          Hash-chained audit log with signed checkpoints. Every read of this console is itself logged
          {lastRead !== null ? (
            <>
              {" "}
              — your latest read is entry <span className="mono">#{lastRead}</span>
            </>
          ) : null}
          .
        </span>
        {fromPresenter && (
          <Link href="/presenter" className="btn btn-sm" style={{ marginLeft: "auto" }}>
            ← Back to presenter
          </Link>
        )}
      </div>

      <AlertsCard userId={userId} onSelectSeq={selectEntry} onRead={noteRead} />

      <HeadStrip userId={userId} refreshKey={headKey} autoVerify={verifyParam} />

      <div className="audit-layout">
        <div className="stack">
          <div className="card">
            <div className="card-header">
              <h2>Ask the log</h2>
              <span className="card-sub">A question becomes a schema-validated filter; no model ever reads the log itself.</span>
            </div>
            <form className="nl-row" onSubmit={runNl}>
              <input className="input" placeholder={NL_EXAMPLE} value={nlQuestion} onChange={(e) => setNlQuestion(e.target.value)} />
              <button type="submit" className="btn btn-primary" disabled={nlBusy || nlQuestion.trim().length < 3}>
                {nlBusy ? "Asking…" : "Ask"}
              </button>
              <button type="button" className="btn" onClick={() => setNlQuestion(NL_EXAMPLE)} title="Fill in the example question">
                Example
              </button>
            </form>
            {nlFilter && (
              <div className="derived-filter" style={{ marginTop: 8 }}>
                <span className="muted">Derived filter:</span>
                {Object.entries(nlFilter).filter(([, v]) => v !== undefined && v !== null && v !== "").length === 0 ? (
                  <span className="badge badge-muted">no constraints recognised</span>
                ) : (
                  Object.entries(nlFilter)
                    .filter(([, v]) => v !== undefined && v !== null && v !== "")
                    .map(([k, v]) => (
                      <span key={k} className="chip-mono">
                        {k}={String(v)}
                      </span>
                    ))
                )}
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-header">
              <h2>Filter</h2>
            </div>
            <FilterBar value={filter} onChange={setFilter} onApply={applyFilter} onReset={resetFilter} users={users} containers={containers} busy={loadingEntries} />
          </div>

          <div className="card">
            <div className="card-header">
              <h2>Canned views</h2>
            </div>
            <CannedViews userId={userId} users={users} containers={containers} docSuggestions={docSuggestions} onResult={onCannedResult} onSelectEntry={selectEntry} onRead={noteRead} />
          </div>

          <div className="card">
            <div className="card-header">
              <h2>
                Entries
                {view ? <span className="muted small" style={{ fontWeight: 400 }}>· {view.label}</span> : null}
              </h2>
              <div className="row">
                {view && (
                  <span className="tiny muted">
                    {view.entries.length} shown · read logged as #{view.readLoggedAs}
                  </span>
                )}
                {loadingEntries && <Spinner label="Loading…" />}
              </div>
            </div>
            {entriesError && (
              <div style={{ marginBottom: 8 }}>
                <Notice kind="error">{entriesError}</Notice>
              </div>
            )}
            <EntriesTable entries={view?.entries ?? []} selectedSeq={selectedSeq} onSelect={selectEntry} emptyText={loadingEntries ? "Loading…" : "No entries match this filter."} />
          </div>
        </div>

        <EntryDetail
          entry={detail}
          loading={detailLoading}
          error={detailError}
          readLoggedAs={detailReadAs}
          onSelectSeq={selectEntry}
          onClose={() => {
            setDetail(null);
            setSelectedSeq(null);
            setDetailReadAs(null);
            try {
              const url = new URL(window.location.href);
              url.searchParams.delete("seq");
              window.history.replaceState(null, "", url.toString());
            } catch {
              // ignore
            }
          }}
        />
      </div>
    </div>
  );
}
