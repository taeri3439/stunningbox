"""Multilingual embeddings for style-reference RAG. The real providers (Voyage/OpenAI)
need a key + network, so these tests drive the deterministic offline MockEmbedder and the
pure cosine helper — they verify plumbing/ranking, not cross-lingual semantic quality."""
import math

from pipeline import embeddings as E


def test_cosine_basics():
    assert math.isclose(E.cosine([1, 0], [1, 0]), 1.0)
    assert math.isclose(E.cosine([1, 0], [0, 1]), 0.0)
    assert E.cosine([0, 0], [1, 0]) == 0.0          # zero vector is safe (no div-by-zero)


def test_mock_embedder_deterministic_and_normalized():
    e = E.MockEmbedder(dim=64)
    v1 = e.embed(["rated load capacity"])[0]
    v2 = e.embed(["rated load capacity"])[0]
    assert v1 == v2                                  # deterministic
    assert len(v1) == 64
    assert math.isclose(sum(x * x for x in v1), 1.0, abs_tol=1e-6)   # L2-normalized


def test_mock_embedder_token_overlap_ranks_higher():
    e = E.MockEmbedder(dim=256)
    q = e.embed(["rated load capacity"])[0]
    near = e.embed(["rated load test"])[0]           # shares 2 tokens
    far = e.embed(["emergency siren volume"])[0]     # shares none
    assert E.cosine(q, near) > E.cosine(q, far)


def test_mock_embedder_empty_and_batch():
    e = E.MockEmbedder(dim=32)
    assert e.embed([]) == []
    out = e.embed(["a", "b", "c"])
    assert len(out) == 3 and all(len(v) == 32 for v in out)


def test_get_embedder_mock_without_key(monkeypatch):
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    assert isinstance(E.get_embedder(), E.MockEmbedder)


def test_get_embedder_mock_respects_embedding_dim(monkeypatch):
    # A key-less prod misconfig must still produce vectors of the pgvector column dimension.
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.setenv("EMBEDDING_DIM", "1024")
    emb = E.get_embedder()
    assert isinstance(emb, E.MockEmbedder)
    assert len(emb.embed(["x"])[0]) == 1024


def test_get_embedder_provider_with_key(monkeypatch):
    # Construction must not import the provider SDK (lazy) — only embed() would.
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
    assert E.get_embedder().__class__.__name__ == "VoyageEmbedder"
    monkeypatch.setenv("EMBEDDING_PROVIDER", "openai")
    assert E.get_embedder().__class__.__name__ == "OpenAIEmbedder"
