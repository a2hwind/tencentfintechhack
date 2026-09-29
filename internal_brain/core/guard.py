"""Output guard: a deterministic check after the probabilistic step.

Every sentence must end with a citation to a document that was actually assembled for
this query. A sentence citing anything else is dropped (guard:citation_rejected); a
sentence with no citation is dropped (guard:unsupported_claim). An answer with no
surviving sentence becomes the uniform no-result. A second pass then scores each
surviving sentence against its cited chunk: lexically (deterministic, the default) or with
a second small model call (VERIFIER=llm).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import NO_ANSWER_TOKEN, GuardStats
from .assemble import Context
from .embeddings import content_terms, tokenize
from .llm import LLMClient, LLMError

CITATION_RE = re.compile(r"\[doc:([^\]\s]+)\]")
CITATION_GROUP_RE = re.compile(r"((?:\s*\[doc:[^\]\s]+\]\s*[,;]?)+)")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


@dataclass
class GuardedSentence:
    text: str
    citations: list[str]


@dataclass
class GuardResult:
    text: str
    sentences: list[GuardedSentence] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)  # unique doc ids in order of first use
    stats: GuardStats = field(default_factory=GuardStats)
    no_answer: bool = False
    events: list[str] = field(default_factory=list)


def _units(raw: str) -> list[tuple[str, list[str]]]:
    """Split raw model output into (sentence, citations) units.

    Text before a citation group belongs to it; if that text holds several sentences, only
    the last one is covered by the citation and the earlier ones are uncited. Text after the
    final citation group is uncited.
    """
    units: list[tuple[str, list[str]]] = []
    pos = 0
    for m in CITATION_GROUP_RE.finditer(raw):
        segment = raw[pos : m.start()]
        cites = CITATION_RE.findall(m.group(0))
        pieces = [p.strip() for p in SENTENCE_SPLIT_RE.split(segment.strip()) if p.strip()]
        if pieces:
            for piece in pieces[:-1]:
                units.append((piece, []))
            units.append((pieces[-1], cites))
        else:
            units.append(("", cites))
        pos = m.end()
        # swallow a full stop that follows the citation group
        while pos < len(raw) and raw[pos] in ".!? \n\t":
            pos += 1
    tail = raw[pos:].strip()
    if tail:
        for piece in SENTENCE_SPLIT_RE.split(tail):
            piece = piece.strip()
            if piece and re.search(r"[A-Za-z0-9]", piece):
                units.append((piece, []))
    return units


def guard(raw: str, context: Context) -> GuardResult:
    allowed = context.doc_ids
    text = (raw or "").strip()
    result = GuardResult(text="")
    if not text or text.upper().startswith(NO_ANSWER_TOKEN):
        result.no_answer = True
        return result
    for body, cites in _units(text):
        body = body.strip().rstrip(" .")
        if not cites:
            if body:
                result.stats.claims_stripped += 1
                result.events.append(f"guard:unsupported_claim: {body[:80]}")
            continue
        if any(c not in allowed for c in cites):
            result.stats.citations_rejected += 1
            result.events.append(f"guard:citation_rejected: {','.join(cites)}")
            continue
        if not body:
            continue
        result.sentences.append(GuardedSentence(text=body, citations=list(dict.fromkeys(cites))))
        for c in cites:
            if c not in result.cited:
                result.cited.append(c)
    if not result.sentences:
        result.no_answer = True
        return result
    result.text = " ".join(f"{s.text} {' '.join(f'[doc:{c}]' for c in s.citations)}." for s in result.sentences)
    return result


def rerender(result: GuardResult, kept: list[GuardedSentence]) -> GuardResult:
    result.sentences = kept
    result.cited = list(dict.fromkeys(c for s in kept for c in s.citations))
    if not kept:
        result.no_answer = True
        result.text = ""
    else:
        result.text = " ".join(f"{s.text} {' '.join(f'[doc:{c}]' for c in s.citations)}." for s in kept)
    return result


NUMBER_RE = re.compile(r"\d[\d,.]*")


def lexical_support(sentence: str, evidence: str, min_overlap: float = 0.25) -> tuple[bool, float]:
    """Deterministic support check: does the cited text carry the sentence's content terms and numbers?

    A sentence whose substantive terms are mostly absent from its cited chunk, or that states a
    number the chunk never mentions, is treated as unsupported. Thresholds are loose on purpose
    (paraphrase survives; invention does not).
    """
    terms = content_terms(sentence)
    evidence_lower = evidence.lower()
    for number in NUMBER_RE.findall(sentence):
        if number.rstrip(".,") and number.rstrip(".,") not in evidence_lower:
            return False, 0.0
    if not terms:
        return True, 1.0
    evidence_terms = set(tokenize(evidence))
    overlap = len(terms & evidence_terms) / len(terms)
    return overlap >= min_overlap, overlap


def verify_support_lexical(result: GuardResult, context: Context, min_overlap: float = 0.25) -> GuardResult:
    """Second pass without a model: drop sentences their cited chunks do not lexically support."""
    if result.no_answer:
        return result
    chunks_by_doc = {d.doc_id: "\n\n".join(c.text for c in d.chunks) for d in context.docs}
    kept: list[GuardedSentence] = []
    for sentence in result.sentences:
        evidence = "\n\n".join(chunks_by_doc.get(c, "") for c in sentence.citations)
        supported, overlap = lexical_support(sentence.text, evidence, min_overlap)
        if not supported:
            result.stats.unsupported_claims += 1
            result.events.append(f"guard:unsupported_claim (lexical, overlap {overlap:.2f}): {sentence.text[:80]}")
            continue
        kept.append(sentence)
    return rerender(result, kept)


VERIFIER_SYSTEM = """You check whether a sentence is supported by an excerpt. Answer with exactly one word: yes or no."""


async def verify_support(result: GuardResult, context: Context, llm: LLMClient, model: str) -> GuardResult:
    """Optional second pass: drop sentences the cited chunk does not support."""
    if result.no_answer or not llm.enabled:
        return result
    chunks_by_doc = {d.doc_id: "\n\n".join(c.text for c in d.chunks) for d in context.docs}
    kept: list[GuardedSentence] = []
    for sentence in result.sentences:
        excerpt = "\n\n".join(chunks_by_doc.get(c, "") for c in sentence.citations)
        try:
            verdict = await llm.chat(model, VERIFIER_SYSTEM, f"Excerpt:\n{excerpt}\n\nSentence: {sentence.text}", temperature=0.0, max_tokens=3)
        except LLMError:
            verdict = "yes"  # verifier unavailable: keep the guard's decision, do not invent a denial
        if verdict.strip().lower().startswith("no"):
            result.stats.unsupported_claims += 1
            result.events.append(f"guard:unsupported_claim: {sentence.text[:80]}")
            continue
        kept.append(sentence)
    return rerender(result, kept)


_rerender = rerender  # backwards-compatible alias
