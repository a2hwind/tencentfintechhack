"""Evidence quotes: for every answer sentence, the passage in each cited chunk that supports it.

Deterministic and cheap: the quote is the chunk sentence sharing the most content terms (and
all numbers) with the answer sentence. The UI shows it highlighted inside the chunk, so every
claim is one click from the words it came from. Quotes are taken from the assembled context
only, which is already permission-checked and masked.
"""

from __future__ import annotations

import re

from ..models import AnswerSentence, EvidenceQuote
from .answer import split_sentences
from .assemble import Context
from .embeddings import content_terms, tokenize
from .guard import GuardResult

NUMBER_RE = re.compile(r"\d[\d,.]*")


def _score(sentence_terms: set[str], sentence_numbers: set[str], candidate: str) -> float:
    cand_terms = set(tokenize(candidate))
    overlap = len(sentence_terms & cand_terms)
    numbers = sum(1 for n in sentence_numbers if n in candidate)
    return overlap + 0.5 * numbers - 0.002 * len(candidate) / 100


def attach_evidence(guarded: GuardResult, context: Context) -> list[AnswerSentence]:
    by_doc: dict[str, list[tuple[str, str]]] = {}
    for doc in context.docs:
        rows: list[tuple[str, str]] = []
        for chunk in doc.chunks:
            for sentence in split_sentences(chunk.text):
                if len(sentence) >= 12:
                    rows.append((chunk.chunk_id, sentence))
        by_doc[doc.doc_id] = rows

    out: list[AnswerSentence] = []
    for s in guarded.sentences:
        terms = content_terms(s.text)
        numbers = {n.rstrip(".,") for n in NUMBER_RE.findall(s.text) if n.rstrip(".,")}
        evidence: list[EvidenceQuote] = []
        for doc_id in s.citations:
            rows = by_doc.get(doc_id) or []
            if not rows:
                continue
            best = max(rows, key=lambda row: _score(terms, numbers, row[1]))
            evidence.append(EvidenceQuote(doc=doc_id, chunk=best[0], quote=best[1]))
        out.append(AnswerSentence(text=s.text, citations=list(s.citations), evidence=evidence))
    return out
