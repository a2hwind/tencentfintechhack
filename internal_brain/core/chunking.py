"""Paragraph-aware chunking. Chunks are the unit of retrieval and of citation."""

from __future__ import annotations

import hashlib
import re

PARA_RE = re.compile(r"\n\s*\n")


def chunk_text(text: str, target: int = 600, hard_max: int = 1000) -> list[str]:
    paragraphs = [p.strip() for p in PARA_RE.split(text.strip()) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        while len(para) > hard_max:  # a single huge paragraph: split on sentence boundaries
            cut = para.rfind(". ", 0, hard_max)
            cut = cut + 1 if cut > target // 2 else hard_max
            piece, para = para[:cut].strip(), para[cut:].strip()
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece)
        if current and len(current) + len(para) + 2 > target:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks or [text.strip()]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
