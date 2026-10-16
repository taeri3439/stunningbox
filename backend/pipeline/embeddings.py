"""Multilingual text embeddings for style-reference retrieval (RAG).

Anthropic has no first-party embeddings API, so we call an external multilingual provider
(Voyage recommended, OpenAI alternative). Provider is selected by env at call time:
  EMBEDDING_PROVIDER = voyage | openai          (default: voyage)
  EMBEDDING_MODEL    = e.g. voyage-3 / text-embedding-3-large
  EMBEDDING_DIM      = vector dimension (MUST match the pgvector column)
  EMBEDDING_API_KEY  = provider key

Without a key, `get_embedder()` returns a deterministic `MockEmbedder` so the pipeline,
retrieval, and tests run offline (mirrors `translator.MockTranslator`). The mock is a
token-overlap hashing embedder: identical text → identical vector, shared tokens → higher
cosine. It models plumbing/ranking, NOT cross-lingual semantics — real 한↔영 neighbour
quality is only meaningful with a real provider.
"""
from __future__ import annotations

import hashlib
import math
import os
import time

import httpx

DEFAULT_PROVIDER = "voyage"
DEFAULT_MODEL = "voyage-3"
DEFAULT_DIM = 1024
_MAX_BATCH = 128  # provider request cap; embed() chunks internally so callers can pass any N
_HTTP_TIMEOUT = 90.0
_VOYAGE_URL = "https://api.voyageai.com/v1/embeddings"
_OPENAI_URL = "https://api.openai.com/v1/embeddings"

# Asymmetric retrieval hints. Voyage sharpens query↔document matching with these; OpenAI
# ignores them. The incoming source segment is the QUERY; stored reference text is DOCUMENT.
QUERY = "query"
DOCUMENT = "document"


def cosine(a, b) -> float:
    """Cosine similarity of two equal-length vectors; 0.0 if either is a zero vector."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _normalize(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return v if n == 0.0 else [x / n for x in v]


def _key() -> str:
    return os.environ.get("EMBEDDING_API_KEY", "")


def _dim() -> int:
    return int(os.environ.get("EMBEDDING_DIM", DEFAULT_DIM))


def _model() -> str:
    return os.environ.get("EMBEDDING_MODEL", DEFAULT_MODEL)


class MockEmbedder:
    """Offline embedder: hashes whitespace tokens into a fixed-dim bag, L2-normalized.
    Deterministic; shared tokens raise cosine. For tests / offline dev only."""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, texts, input_type: str = DOCUMENT) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        toks = (text or "").lower().split() or [(text or "").lower()]
        for tok in toks:
            h = hashlib.sha1(tok.encode("utf-8")).digest()
            v[int.from_bytes(h[:4], "big") % self.dim] += 1.0
        return _normalize(v)


_RETRY_STATUS = {429, 500, 502, 503, 529}
_BACKOFF = (2.0, 5.0, 15.0, 30.0, 45.0)   # seconds; covers a free-tier rolling-minute rate window


def _post_embeddings(url: str, body: dict) -> list[list[float]]:
    """One embeddings REST call, retrying transient rate-limit/5xx responses AND network
    timeouts with backoff (honours Retry-After when present). Under a saturated rate window
    the provider often stalls the response instead of returning 429, which surfaces as a
    read timeout — so timeouts are retried, not propagated. Returns vectors in input order."""
    headers = {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}
    last_resp = None
    last_exc: Exception | None = None
    for attempt in range(len(_BACKOFF) + 1):
        try:
            r = httpx.post(url, headers=headers, json=body, timeout=_HTTP_TIMEOUT)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt < len(_BACKOFF):
                time.sleep(_BACKOFF[attempt])
                continue
            raise
        if r.status_code in _RETRY_STATUS and attempt < len(_BACKOFF):
            ra = r.headers.get("retry-after", "")
            wait = float(ra) if ra.replace(".", "", 1).isdigit() else _BACKOFF[attempt]
            last_resp = r
            time.sleep(wait)
            continue
        r.raise_for_status()
        data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in data]
    if last_resp is not None:   # exhausted retries on HTTP errors — surface the last response
        last_resp.raise_for_status()
    if last_exc is not None:    # exhausted retries on timeouts
        raise last_exc
    return []


class VoyageEmbedder:
    """Voyage multilingual embeddings via the REST API (no SDK → no heavy deps)."""

    def __init__(self, model: str | None = None, dim: int | None = None):
        self.model = model or _model()
        self.dim = dim or _dim()

    def embed(self, texts, input_type: str = DOCUMENT) -> list[list[float]]:
        texts = list(texts)
        if not texts:
            return []
        it = "query" if input_type == QUERY else "document"
        out: list[list[float]] = []
        for i in range(0, len(texts), _MAX_BATCH):
            out.extend(_post_embeddings(_VOYAGE_URL, {
                "input": texts[i:i + _MAX_BATCH], "model": self.model, "input_type": it,
            }))
        return out


class OpenAIEmbedder:
    """OpenAI embeddings (text-embedding-3-*) via the REST API. `dimensions` truncates to
    EMBEDDING_DIM (MRL) so vectors match the pgvector column."""

    def __init__(self, model: str | None = None, dim: int | None = None):
        self.model = model or _model()
        self.dim = dim or _dim()

    def embed(self, texts, input_type: str = DOCUMENT) -> list[list[float]]:
        texts = list(texts)
        if not texts:
            return []
        out: list[list[float]] = []
        for i in range(0, len(texts), _MAX_BATCH):
            out.extend(_post_embeddings(_OPENAI_URL, {
                "input": texts[i:i + _MAX_BATCH], "model": self.model, "dimensions": self.dim,
            }))
        return out


def get_embedder():
    """Real provider when EMBEDDING_API_KEY is set, else the offline MockEmbedder.
    The mock honours EMBEDDING_DIM too, so a key-less prod misconfig still produces vectors
    of the pgvector column's dimension rather than a 256-d mismatch."""
    if not _key():
        return MockEmbedder(_dim())
    provider = os.environ.get("EMBEDDING_PROVIDER", DEFAULT_PROVIDER).lower()
    if provider == "openai":
        return OpenAIEmbedder()
    return VoyageEmbedder()
