"""Context assembler: deduplicate, order by relevance then recency, fit the budget, and
wrap every chunk in a <doc> tag so the model can cite by id.

Only fields the user could see in the platform itself (title, author, date) are exposed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from html import escape

from ..models import SentChunk
from .retrieve import Candidate, ChunkHit


@dataclass
class ContextDoc:
    doc_id: str
    platform: str
    title: str
    updated: str
    author: str | None
    url: str | None
    rule: str
    version: int
    chunks: list[ChunkHit]


@dataclass
class Context:
    docs: list[ContextDoc] = field(default_factory=list)
    sent: list[SentChunk] = field(default_factory=list)

    @property
    def doc_ids(self) -> set[str]:
        return {d.doc_id for d in self.docs}

    def render(self) -> str:
        blocks = []
        for d in self.docs:
            attrs = f'id="{escape(d.doc_id, quote=True)}" platform="{d.platform}" title="{escape(d.title, quote=True)}" updated="{d.updated}"'
            if d.author:
                attrs += f' author="{escape(str(d.author), quote=True)}"'
            body = "\n\n".join(c.text for c in d.chunks)
            blocks.append(f"<doc {attrs}>\n{body}\n</doc>")
        return "\n\n".join(blocks)


def assemble(candidates: list[Candidate], budget_chars: int = 28_000, max_chunks_per_doc: int = 4) -> Context:
    # relevance first; ties broken by recency (newest first)
    ordered = sorted(candidates, key=lambda c: (-round(c.score, 6), -_ts(c.item.last_modified)))
    context = Context()
    used = 0
    seen_chunks: set[str] = set()
    for candidate in ordered:
        chunks: list[ChunkHit] = []
        for chunk in sorted(candidate.chunks, key=lambda c: -c.score)[:max_chunks_per_doc]:
            if chunk.chunk_id in seen_chunks:
                continue
            if used + len(chunk.text) > budget_chars:
                break
            seen_chunks.add(chunk.chunk_id)
            used += len(chunk.text)
            chunks.append(chunk)
        if not chunks:
            continue
        chunks.sort(key=lambda c: c.chunk_id)  # document order within a doc reads better
        item = candidate.item
        context.docs.append(
            ContextDoc(
                doc_id=item.item_id,
                platform=item.platform,
                title=item.title,
                updated=item.last_modified,
                author=item.author,
                url=item.url,
                rule=candidate.rule,
                version=item.version,
                chunks=chunks,
            )
        )
        context.sent += [SentChunk(chunk=c.chunk_id, sha256=c.sha256) for c in chunks]
    return context


def _ts(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0
