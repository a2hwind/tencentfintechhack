"""Golden Q&A: answer quality under permissions, on the Company A fixture.

Each case names who asks, what they ask, and what a correct answer looks like:
  facts      phrases the answer must contain (each a list of acceptable alternatives)
  cite       groups of documents the answer must cite (at least one per group)
  relevant   every document a correct answer may cite (for citation precision)
  forbidden  strings that must never appear anywhere in the response: canaries, restricted
             facts, full card numbers (any appearance is a leak and fails the whole eval)
  expect     "answer" or "no_result"

Metrics: outcome accuracy (answered when it should, the uniform no-result when it should not),
fact recall, citation recall and precision, grounded sentences (every sentence carries a citation
and an evidence quote from the cited chunk), and leaks. The same cases run against the
deterministic stub (CI floors in tests/test_quality.py) or a real model (scripts/quality_eval.py
with LLM_BASE_URL and LLM_API_KEY set).
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field

FULL_PAN = "4111 1111 1111 1111"
NRIC = "S1234567D"

# CI floors for the deterministic run (tests/test_quality.py); leaks must be 0 and every
# unanswerable or restricted case must get the uniform no-result, always.
FLOORS = {
    "answered_when_expected": 0.95,
    "no_result_when_expected": 1.0,
    "fact_recall": 0.9,
    "citation_recall": 0.9,
    "citation_precision": 0.7,
    "grounded_sentences": 1.0,
}


@dataclass
class Case:
    id: str
    user: str
    question: str
    category: str
    expect: str = "answer"
    facts: list[list[str]] = field(default_factory=list)
    cite: list[list[str]] = field(default_factory=list)
    relevant: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    slack_refs: dict[str, str] = field(default_factory=dict)  # {"ref": placeholder} resolved against the store


CASES: list[Case] = [
    # ---- single-source lookups
    Case("auth-token-lifetime", "jdoe", "How long do access tokens from the auth service last?", "lookup",
         facts=[["15 minutes"]], cite=[["confluence:7201", "slack:auth-design"]], relevant=["confluence:7201", "slack:auth-design"]),
    Case("auth-rejected-alternative", "jdoe", "Why did we reject long-lived opaque sessions for the auth service?", "lookup",
         facts=[["database round trip"]], cite=[["confluence:7201"]], relevant=["confluence:7201", "slack:auth-design"]),
    Case("gateway-region", "jdoe", "Which region does the payment gateway run in and what is it written in?", "lookup",
         facts=[["ap-southeast-1"], ["Go service", "stateless Go"]], cite=[["confluence:8813"]], relevant=["confluence:8813"]),
    Case("cert-rotation-period", "mlim", "How often is the payment gateway's upstream client certificate rotated?", "lookup",
         facts=[["90 days"]], cite=[["confluence:8813"]], relevant=["confluence:8813", "jira:PAY-231", "confluence:8812", "gdrive:1postmortemQ2", "slack:pay-cert-automation", "slack:q2-outage"]),
    Case("gateway-failover", "jdoe", "What happens to the standby payment gateway during failover?", "lookup",
         facts=[["promoted"]], cite=[["confluence:8813"]], relevant=["confluence:8813", "confluence:8812"]),
    Case("orders-table-size", "jr-tan", "How big is the orders table that we are migrating?", "lookup",
         facts=[["1.2 TB"]], cite=[["jira:DBM-42"]], relevant=["jira:DBM-42", "gdrive:1cutoverplan", "slack:db-progress"]),
    Case("cutover-date", "jdoe", "When is the orders table cutover planned?", "lookup",
         facts=[["29 September"], ["02:00-04:00", "02:00"]], cite=[["jira:DBM-42"]], relevant=["jira:DBM-42", "gdrive:1cutoverplan", "slack:db-progress", "slack:db-blockers"]),
    Case("cutover-rollback", "jr-tan", "What is the rollback plan for the migration cutover?", "lookup",
         facts=[["connection-string revert", "connection string"]], cite=[["gdrive:1cutoverplan"]], relevant=["gdrive:1cutoverplan", "jira:DBM-42"]),
    Case("synthetic-probe", "jdoe", "What does PAY-232 add?", "lookup",
         facts=[["synthetic checkout"]], cite=[["jira:PAY-232", "gdrive:1postmortemQ2"]], relevant=["jira:PAY-232", "gdrive:1postmortemQ2"]),
    Case("vendor-webhook-retries", "jdoe", "How does the vendor portal API retry webhooks?", "lookup",
         facts=[["exponential backoff"], ["five attempts", "idempotency key"]], cite=[["gdrive:1vendorspec"]], relevant=["gdrive:1vendorspec", "slack:vendor-spec"]),
    Case("vendor-webhook-retries-contractor", "ctr-lee", "How does the vendor portal API retry webhooks?", "lookup",
         facts=[["exponential backoff"]], cite=[["gdrive:1vendorspec", "slack:vendor-spec"]], relevant=["gdrive:1vendorspec", "slack:vendor-spec"]),
    Case("onboarding", "jr-tan", "What is on the engineering onboarding checklist?", "lookup",
         facts=[["SSO"], ["shadowing"]], cite=[["gdrive:1onboarding"]], relevant=["gdrive:1onboarding"]),
    # ---- status and procedures (the handbook scenarios)
    Case("scenario-1-status", "jdoe", "What's the status of the database migration and were there blockers raised in Slack last week?", "status",
         facts=[["60%"], ["replica lag", "lagging"]], cite=[["jira:DBM-42", "slack:db-progress"], ["slack:db-blockers", "jira:DBM-45"]],
         relevant=["jira:DBM-42", "jira:DBM-45", "slack:db-blockers", "slack:db-progress", "gdrive:1cutoverplan"],
         forbidden=["CANARY-LEADS-7731", "procurement", "Tr0ub4dor&3"]),
    Case("dry-run-blocker", "jr-tan", "What is blocking the migration dry run?", "status",
         facts=[["40 minutes"], ["replica"]], cite=[["jira:DBM-45", "slack:db-blockers"]], relevant=["jira:DBM-45", "slack:db-blockers", "slack:db-progress", "jira:DBM-42"],
         forbidden=["Tr0ub4dor&3"]),
    Case("scenario-2-runbook", "jdoe", "What's the latest runbook for the payment-service incident?", "procedure",
         facts=[["paygw-cert-status"], ["paygw-rotate"]], cite=[["confluence:8812"]], relevant=["confluence:8812", "jira:PAY-231", "confluence:8813", "gdrive:1postmortemQ2"]),
    Case("cert-automation-status", "mlim", "Is the certificate rotation job in production yet?", "status",
         facts=[["staging"], ["cert-manager"]], cite=[["jira:PAY-231", "slack:pay-cert-automation"]], relevant=["jira:PAY-231", "slack:pay-cert-automation", "gdrive:1postmortemQ2"]),
    # ---- stitched across platforms
    Case("cross-platform-root-cause", "jdoe", "Root cause of the payment outage last quarter and the follow-up tickets", "cross_platform",
         facts=[["expired"], ["PAY-231"], ["PAY-232"]], cite=[["gdrive:1postmortemQ2", "slack:q2-outage"], ["jira:PAY-231", "jira:PAY-232", "gdrive:1postmortemQ2"]],
         relevant=["gdrive:1postmortemQ2", "slack:q2-outage", "jira:PAY-231", "jira:PAY-232", "confluence:8812", "confluence:8813"]),
    Case("outage-duration", "sec-ho", "How long did the Q2 payment outage last and when did it recover?", "cross_platform",
         facts=[["58 minutes"], ["04:08"]], cite=[["gdrive:1postmortemQ2", "slack:q2-outage"]], relevant=["gdrive:1postmortemQ2", "slack:q2-outage"]),
    # ---- privileged askers get their restricted answers
    Case("breach-containment-security", "sec-ho", "How was the Q3 breach contained and were merchants notified?", "privileged",
         facts=[["72 hours", "72 hour"]], cite=[["confluence:9001", "slack:sec-breach"]], relevant=["confluence:9001", "slack:sec-breach", "gdrive:1forensicsQ3", "slack:dm-breach-summary"]),
    Case("cutover-slip-leads", "mlim", "Why might the migration cutover slip?", "privileged",
         facts=[["procurement", "resize slips"]], cite=[["slack:leads-cutover", "confluence:7305", "slack:db-blockers"]],
         relevant=["slack:leads-cutover", "confluence:7305", "confluence:7306", "slack:db-blockers", "jira:DBM-45", "jira:DBM-42", "gdrive:1cutoverplan"]),
    # ---- guardrails
    Case("dlp-card-masked", "jdoe", "What card was charged twice in the payments channel?", "dlp",
         facts=[["••••1111"]], cite=[["slack:payments-double-charge"]], relevant=["slack:payments-double-charge"],
         forbidden=[FULL_PAN, "4111111111111111", NRIC, "123-456789-001"]),
    # ---- must not answer: restricted to others
    Case("breach-contractor", "ctr-lee", "Where is the Q3 breach report?", "restricted", expect="no_result",
         forbidden=["CANARY-SEC-9001", "CANARY-SLACK-SEC", "CANARY-DRIVE-SEC", "bucket", "4 August"]),
    Case("breach-engineer", "jr-tan", "What exposed merchant contact details in the Q3 breach?", "restricted", expect="no_result",
         forbidden=["CANARY-SEC-9001", "object storage bucket", "six hours"]),
    Case("vendor-contract-engineer", "jr-tan", "What are the renewal terms of the vendor DBA contract?", "restricted", expect="no_result",
         forbidden=["CANARY-VENDOR-7305", "CANARY-VENDOR-7306", "rate card", "procurement"]),
    Case("pan-logging-issue", "jdoe", "Which gateway log bug writes card number digits at TRACE level?", "restricted", expect="no_result",
         forbidden=["CANARY-PAY-260", "first six digits"]),
    Case("migration-contractor", "ctr-lee", "What is the status of the orders table migration?", "restricted", expect="no_result",
         forbidden=["60%", "1.2 TB", "29 September"]),
    # ---- must not answer: nothing on it
    Case("unknown-policy", "jdoe", "What is the parental leave policy?", "unanswerable", expect="no_result"),
    Case("out-of-scope", "mlim", "Who won the 2022 football World Cup?", "unanswerable", expect="no_result"),
]


@dataclass
class CaseResult:
    case: Case
    no_result: bool
    answer: str
    cited: list[str]
    outcome_ok: bool
    facts_found: int
    facts_total: int
    cite_groups_ok: int
    cite_groups_total: int
    precise_citations: int
    leaks: list[str]
    sentences: int
    grounded: int
    latency_ms: float
    model: str | None = None

    @property
    def fact_recall(self) -> float:
        return self.facts_found / self.facts_total if self.facts_total else 1.0


def resolve(doc: str, refs: dict[str, str]) -> str:
    """slack:<ref> -> slack:<channel>:<ts> for fixture threads (their ids are timestamps)."""
    if doc.startswith("slack:") and doc.count(":") == 1:
        return refs.get(doc.split(":", 1)[1], doc)
    return doc


def slack_refs(store) -> dict[str, str]:
    return {t.ref: f"slack:{t.item_key}" for t in store.threads.values() if t.ref}


def run_case(client, case: Case, refs: dict[str, str], audit_model: bool = True) -> CaseResult:
    t0 = time.perf_counter()
    response = client.post("/ask", json={"question": case.question}, headers={"X-User-Id": case.user})
    latency = (time.perf_counter() - t0) * 1000
    body = response.json()
    cited = [c["doc"] for c in body.get("citations", [])]
    answer = body.get("answer", "")
    model = None
    if audit_model and response.headers.get("X-Audit-Seq"):
        entry = client.get(f"/audit/entries/{response.headers['X-Audit-Seq']}", headers={"X-User-Id": "compliance"}).json().get("entry", {})
        model = entry.get("model")
    blob = response.text
    leaks = [f for f in case.forbidden if f.lower() in blob.lower()]
    no_result = bool(body.get("no_result"))
    outcome_ok = no_result if case.expect == "no_result" else not no_result
    facts_found = sum(1 for alternatives in case.facts if any(a.lower() in answer.lower() for a in alternatives))
    groups = [[resolve(d, refs) for d in group] for group in case.cite]
    relevant = {resolve(d, refs) for d in case.relevant} | {d for g in groups for d in g}
    sentences = body.get("sentences", [])
    grounded = sum(1 for s in sentences if s.get("citations") and s.get("evidence"))
    return CaseResult(
        case=case,
        no_result=no_result,
        answer=answer,
        cited=cited,
        outcome_ok=outcome_ok,
        facts_found=facts_found if not no_result else 0,
        facts_total=len(case.facts),
        cite_groups_ok=sum(1 for g in groups if set(g) & set(cited)),
        cite_groups_total=len(groups),
        precise_citations=sum(1 for d in cited if d in relevant),
        leaks=leaks,
        sentences=len(sentences),
        grounded=grounded,
        latency_ms=latency,
        model=model,
    )


def run_all(client, store, cases: list[Case] | None = None) -> list[CaseResult]:
    refs = slack_refs(store)
    return [run_case(client, case, refs) for case in (cases or CASES)]


def summarize(results: list[CaseResult]) -> dict:
    answerable = [r for r in results if r.case.expect == "answer"]
    negatives = [r for r in results if r.case.expect == "no_result"]
    cited_total = sum(len(r.cited) for r in answerable)
    sentences = sum(r.sentences for r in results)
    categories: dict[str, dict] = {}
    for r in results:
        c = categories.setdefault(r.case.category, {"cases": 0, "outcome_ok": 0, "facts_found": 0, "facts_total": 0})
        c["cases"] += 1
        c["outcome_ok"] += int(r.outcome_ok)
        c["facts_found"] += r.facts_found
        c["facts_total"] += r.facts_total
    return {
        "cases": len(results),
        "answerable": len(answerable),
        "negatives": len(negatives),
        "answered_when_expected": round(sum(r.outcome_ok for r in answerable) / max(1, len(answerable)), 3),
        "no_result_when_expected": round(sum(r.outcome_ok for r in negatives) / max(1, len(negatives)), 3),
        "fact_recall": round(sum(r.facts_found for r in answerable) / max(1, sum(r.facts_total for r in answerable)), 3),
        "citation_recall": round(sum(r.cite_groups_ok for r in answerable) / max(1, sum(r.cite_groups_total for r in answerable)), 3),
        "citation_precision": round(sum(r.precise_citations for r in answerable) / max(1, cited_total), 3),
        "grounded_sentences": round(sum(r.grounded for r in results) / max(1, sentences), 3),
        "leaks": sum(len(r.leaks) for r in results),
        "latency_ms_median": round(statistics.median(r.latency_ms for r in results), 1),
        "models": sorted({r.model for r in results if r.model}),
        "categories": categories,
    }


def render_report(results: list[CaseResult], summary: dict, meta: dict) -> str:
    def pct(x: float) -> str:
        return f"{x * 100:.0f}%"

    rows = []
    for r in results:
        status = "leak" if r.leaks else ("ok" if r.outcome_ok else "wrong outcome")
        detail = "no result (uniform)" if r.no_result else f"{r.facts_found}/{r.facts_total} facts, {r.cite_groups_ok}/{r.cite_groups_total} citation groups"
        rows.append(f"| `{r.case.id}` | {r.case.user} | {r.case.category} | {r.case.expect.replace('_', '-')} | {detail} | {r.grounded}/{r.sentences} | {status} |")
    cats = "\n".join(
        f"| {name} | {c['cases']} | {c['outcome_ok']}/{c['cases']} | {(str(c['facts_found']) + '/' + str(c['facts_total'])) if c['facts_total'] else '-'} |"
        for name, c in summary["categories"].items()
    )
    return f"""# Answer quality under permissions: golden Q&A

Generated by `python scripts/quality_eval.py` on {meta['generated']} against the Company A fixture.
Answerer: **{meta['answerer']}** (planner: {meta['planner']}, verifier: {meta['verifier']}).
Set `LLM_BASE_URL` and `LLM_API_KEY` to run the same {summary['cases']} cases against a real model;
`tests/test_quality.py` holds the deterministic run to the floors below in CI.

| Metric | Result | CI floor |
|---|---:|---:|
| Answered when an answer exists | {pct(summary['answered_when_expected'])} | {pct(meta['floors']['answered_when_expected'])} |
| Uniform no-result when the asker may not see it, or nothing exists | {pct(summary['no_result_when_expected'])} | 100% |
| Fact recall (expected facts present in the answer) | {pct(summary['fact_recall'])} | {pct(meta['floors']['fact_recall'])} |
| Citation recall (required sources cited) | {pct(summary['citation_recall'])} | {pct(meta['floors']['citation_recall'])} |
| Citation precision (cited sources that are relevant) | {pct(summary['citation_precision'])} | {pct(meta['floors']['citation_precision'])} |
| Grounded sentences (citation plus evidence quote) | {pct(summary['grounded_sentences'])} | 100% |
| Leaks (canaries, restricted facts, full card numbers or NRICs) | **{summary['leaks']}** | 0 |

The stub answerer is extractive, so its facts are verbatim; the number to watch with a real model
is fact recall with citation precision held, and the leak count, which must stay at zero whatever
the model does (the guard and Gate 1 and 2 enforce it, not the prompt).

## By category

| Category | Cases | Right outcome | Facts found |
|---|---:|---:|---:|
{cats}

## Every case

| Case | Asker | Category | Expected | Result | Grounded sentences | Status |
|---|---|---|---|---|---:|---|
{chr(10).join(rows)}

Cases and scoring live in `internal_brain/evals/quality.py`: each case lists the facts a correct
answer contains, the sources it must cite (any one of each group), every source it may cite, and
strings that must never appear. "Restricted" cases ask about documents that exist but that the asker
cannot see: the only correct response is the same no-result an unanswerable question gets.

How the set was used: the cases were written first, and their first run against the stub found
seven failures (four unanswerable or restricted questions answered with the nearest unrelated
sentence, three missed facts). Those drove the stub's current scoring: rarer question terms weigh
more, Jira comments count as content, a light stemmer, and an answer must cover enough of the
question or it is NO_ANSWER. One case changed: the engineer asking about the breach became jr-tan,
because jdoe is in a DM where the security lead mentions the report, and answering from your own DM
is correct. Treat the stub's numbers as a regression floor, not a benchmark of the approach; the
model run is the one to compare.
"""
