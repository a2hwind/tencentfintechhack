"""Embeddings behind one interface.

`hash`      feature-hashed word unigrams and bigrams, L2-normalised. Deterministic, no
            download, no model: it keeps the demo dependency-free and the tests fast.
            Keyword search (FTS5/BM25) does most of the ranking work in this setup.
`bge-small` sentence-transformers BAAI/bge-small-en-v1.5 (pip install -e ".[embeddings]").
`hunyuan`   Tencent Hunyuan's embedding model through the OpenAI-compatible /embeddings
            endpoint (same LLM_BASE_URL and LLM_API_KEY); `openai` is the same client for
            any other provider. EMBEDDING_MODEL picks the model name.

All return unit vectors, so cosine similarity is a dot product. Each embedder carries the
similarity floor below which a vector hit is noise for that model.
"""

from __future__ import annotations

import hashlib
import re
from typing import Protocol

import httpx
import numpy as np

TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-\.]*[a-z0-9]|[a-z0-9]")

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "is", "are", "was", "were", "be", "been", "it", "this",
    "that", "with", "as", "at", "by", "from", "we", "our", "you", "your", "i", "me", "my", "what", "which", "who", "whom",
    "how", "when", "where", "why", "did", "do", "does", "there", "were", "any", "please", "about", "tell", "give", "show",
    "status", "latest", "last", "week", "raised", "have", "has", "had", "s", "us", "them", "they", "he", "she", "its",
}


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


GENERIC_TERMS = {
    "report", "status", "doc", "document", "documents", "page", "pages", "ticket", "tickets", "thread", "threads",
    "issue", "issues", "file", "files", "summary", "update", "updates", "plan", "meeting", "notes", "design",
    "discussion", "team", "link", "latest", "new", "old", "info", "information", "details", "question",
}


def content_terms(text: str) -> set[str]:
    """Query terms that carry meaning: no stopwords, no one-letter tokens, no generic nouns."""
    return {t for t in tokenize(text) if len(t) > 1 and t not in STOPWORDS and t not in GENERIC_TERMS}


class Embedder(Protocol):
    name: str
    dim: int
    min_similarity: float  # below this, a vector hit is noise for this embedder

    def embed(self, texts: list[str]) -> np.ndarray: ...


class HashEmbedder:
    name = "hash-256"
    min_similarity = 0.12

    def __init__(self, dim: int = 256):
        self.dim = dim

    def _features(self, text: str) -> list[str]:
        toks = [t for t in tokenize(text) if t not in STOPWORDS]
        return toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for feature in self._features(text):
                digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
                value = int.from_bytes(digest, "big")
                index = value % self.dim
                sign = 1.0 if (value >> 63) & 1 else -1.0
                weight = 1.0 if "_" not in feature else 0.6  # bigrams count a little less
                out[row, index] += sign * weight
            norm = np.linalg.norm(out[row])
            if norm > 0:
                out[row] /= norm
        return out


class SentenceTransformerEmbedder:
    name = "bge-small-en-v1.5"
    min_similarity = 0.5

    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from sentence_transformers import SentenceTransformer  # optional dependency

        self.model = SentenceTransformer(model_name)
        self.dim = int(self.model.get_sentence_embedding_dimension())

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors = self.model.encode(texts, normalize_embeddings=True, convert_to_numpy=True)
        return np.asarray(vectors, dtype=np.float32)


class OpenAICompatibleEmbedder:
    """POST {base_url}/embeddings, the shape Hunyuan, OpenAI and most gateways share."""

    def __init__(self, base_url: str, api_key: str, model: str = "hunyuan-embedding", min_similarity: float = 0.45, timeout_s: float = 30.0, batch_size: int = 32):
        if not base_url or not api_key:
            raise ValueError("EMBEDDINGS=hunyuan|openai needs LLM_BASE_URL and LLM_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.name = f"openai:{model}"
        self.min_similarity = min_similarity
        self.batch_size = batch_size
        self.dim = 0
        self._client = httpx.Client(timeout=timeout_s)

    def embed(self, texts: list[str]) -> np.ndarray:
        vectors: list[np.ndarray] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            response = self._client.post(
                f"{self.base_url}/embeddings",
                json={"model": self.model, "input": batch},
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = sorted(response.json()["data"], key=lambda d: d.get("index", 0))
            for row in data:
                vec = np.asarray(row["embedding"], dtype=np.float32)
                norm = np.linalg.norm(vec)
                vectors.append(vec / norm if norm > 0 else vec)
        if not vectors:
            return np.zeros((0, self.dim or 1), dtype=np.float32)
        self.dim = int(vectors[0].shape[0])
        return np.stack(vectors)


def make_embedder(kind: str, base_url: str = "", api_key: str = "", model: str = "", min_similarity: float | None = None, timeout_s: float = 30.0) -> Embedder:
    if kind in ("bge-small", "sentence-transformers"):
        embedder: Embedder = SentenceTransformerEmbedder()
    elif kind in ("hunyuan", "openai"):
        embedder = OpenAICompatibleEmbedder(base_url, api_key, model or "hunyuan-embedding", timeout_s=timeout_s)
    else:
        embedder = HashEmbedder()
    if min_similarity is not None:
        embedder.min_similarity = min_similarity  # type: ignore[misc]
    return embedder
