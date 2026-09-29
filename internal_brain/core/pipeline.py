"""The query path: plan, retrieve under filter, expand, verify live, assemble, answer,
guard, mask, log. The probabilistic steps (planner, answer model) sit between deterministic
ones, so no model output is trusted on its own. The audit entry is appended before the
response goes back, then the alert rules run over the trail.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from ..audit.log import AuditEntry, AuditLog
from ..config import Settings
from ..models import NO_RESULT_MESSAGE, AskResponse, AuditActor, Citation, Decision, GuardStats, Identity, Provenance
from . import dlp
from .answer import Answerer
from .assemble import assemble
from .entitlements import EntitlementResolver
from .events import now_iso
from .evidence import attach_evidence
from .guard import GuardResult, guard, rerender, verify_support, verify_support_lexical
from .llm import LLMClient
from .planner import Planner
from .retrieve import Retriever
from .verify_live import LiveVerifier

if TYPE_CHECKING:
    from ..audit.alerts import AlertEngine


def uniform_no_result() -> AskResponse:
    """The one response shape for restricted, missing and unanswerable alike."""
    return AskResponse(answer=NO_RESULT_MESSAGE, citations=[], provenance=[], no_result=True, sentences=[])


class StageClock:
    """Measures each pipeline stage; the timings go into the audit entry (and the glass-box view)."""

    def __init__(self) -> None:
        self.started = time.perf_counter()
        self._mark = self.started
        self.timings: dict[str, float] = {}

    def lap(self, stage: str) -> None:
        now = time.perf_counter()
        self.timings[stage] = round((now - self._mark) * 1000, 2)
        self._mark = now

    @property
    def elapsed_s(self) -> float:
        return time.perf_counter() - self.started


def mask_output(guarded: GuardResult) -> dict[str, int]:
    """DLP on the model's output, sentence by sentence (defence in depth: the context is already masked)."""
    counts: dict[str, int] = {}
    if guarded.no_answer:
        return counts
    for sentence in guarded.sentences:
        result = dlp.mask(sentence.text)
        if result.found:
            sentence.text = result.text
            for kind, n in result.counts.items():
                counts[kind] = counts.get(kind, 0) + n
    if counts:
        rerender(guarded, guarded.sentences)
    return counts


class AskPipeline:
    def __init__(
        self,
        settings: Settings,
        entitlements: EntitlementResolver,
        planner: Planner,
        retriever: Retriever,
        verifier: LiveVerifier,
        answerer: Answerer,
        audit: AuditLog,
        llm: LLMClient,
        alerts: "AlertEngine | None" = None,
    ):
        self.settings = settings
        self.entitlements = entitlements
        self.planner = planner
        self.retriever = retriever
        self.verifier = verifier
        self.answerer = answerer
        self.audit = audit
        self.llm = llm
        self.alerts = alerts

    async def ask(self, identity: Identity, question: str) -> tuple[AskResponse, AuditEntry]:
        clock = StageClock()

        # 0. who is asking, right now
        principals = await self.entitlements.principals_for(identity)
        clock.lap("entitlements")

        # 1. plan (sees only the question)
        plan = await self.planner.plan(question)
        clock.lap("plan")

        # 2. retrieve under filter (Gate 1 inside the index query) and 3. expand links
        retrieval = self.retriever.retrieve(plan, principals)
        clock.lap("gate1")
        expanded, expansion_denied = self.retriever.expand_links(retrieval.candidates, principals)
        clock.lap("expand")

        # 4. verify live (Gate 2) with read-through refresh
        verification = await self.verifier.verify(retrieval.candidates + expanded, principals)
        clock.lap("gate2")

        # 5. assemble and 6. answer
        context = assemble(verification.kept, self.settings.context_budget_chars)
        clock.lap("assemble")
        raw, model = await self.answerer.answer(question, identity, context)
        clock.lap("answer")

        # 7. guard (deterministic), the verifier pass (lexical by default, or a second model call), DLP on the output
        guarded = guard(raw, context)
        if self.settings.verifier == "llm" and self.llm.enabled:
            guarded = await verify_support(guarded, context, self.llm, self.settings.llm_model_answer)
        elif self.settings.verifier in ("lexical", "llm"):
            guarded = verify_support_lexical(guarded, context, self.settings.verifier_min_overlap)
        redactions = mask_output(guarded)
        clock.lap("guard")

        # decisions: verified candidates, then link expansions denied at Gate 1, then audit-only shadow denials
        decisions: list[Decision] = list(verification.decisions) + list(expansion_denied) + list(retrieval.denied)
        gate1_denied = [d.doc for d in decisions if d.gate1 == "deny" and d.rule == "not_member"]
        previously = self.audit.last_decisions(identity.id, gate1_denied)
        for d in decisions:
            if d.doc in gate1_denied and previously.get(d.doc) == "allow":
                d.rule = "revoked"  # this user used to get this document and no longer does

        docs_by_id = {d.doc_id: d for d in context.docs}
        verified_at = now_iso()
        if guarded.no_answer:
            response = uniform_no_result()
            outcome = "no_result"
        else:
            cited = [doc_id for doc_id in guarded.cited if doc_id in docs_by_id]
            citations = [
                Citation(
                    doc=doc_id,
                    platform=docs_by_id[doc_id].platform,  # type: ignore[arg-type]
                    title=docs_by_id[doc_id].title,
                    url=docs_by_id[doc_id].url,
                    updated=docs_by_id[doc_id].updated,
                    chunks=[c.chunk_id for c in docs_by_id[doc_id].chunks],
                )
                for doc_id in cited
            ]
            provenance = [
                Provenance(doc=doc_id, rule=docs_by_id[doc_id].rule, verified_at=verified_at, version=docs_by_id[doc_id].version)
                for doc_id in cited
            ]
            response = AskResponse(
                answer=guarded.text,
                citations=citations,
                provenance=provenance,
                no_result=False,
                sentences=attach_evidence(guarded, context),
            )
            outcome = "answered"

        stats = GuardStats(
            citations_rejected=guarded.stats.citations_rejected,
            claims_stripped=guarded.stats.claims_stripped,
            unsupported_claims=guarded.stats.unsupported_claims,
            events=guarded.events,
            redactions=redactions,
        )
        latency_ms = int(clock.elapsed_s * 1000)

        # 8. log, before the response is returned
        entry = self.audit.record_query(
            actor=AuditActor(id=identity.id, principals=principals.tokens),
            query=question,
            plan=plan.model_dump(),
            decisions=decisions,
            sent_to_model=context.sent,
            answer=response.answer,
            guard=stats,
            outcome=outcome,
            latency_ms=latency_ms,
            model=model,
            timings_ms=clock.timings,
        )

        # 9. insider-threat rules over the trail (alerts are chained entries too)
        if self.alerts is not None:
            self.alerts.after_query(identity.id)

        # 10. constant-time floor for empty results: restricted and non-existent take the same time
        if outcome == "no_result" and self.settings.no_result_min_latency_ms > 0:
            remaining = self.settings.no_result_min_latency_ms / 1000.0 - clock.elapsed_s
            if remaining > 0:
                await asyncio.sleep(remaining)
        return response, entry
