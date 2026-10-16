"""Style-reference store + retrieval. Driven through the in-memory dev path (DEV_NO_AUTH=1)
with a deterministic MockEmbedder — this is also the path local dogfooding uses, so it
covers ingestion scoping, cosine ranking, dedup/cap, and the formatted prompt block."""
import pytest

from pipeline import embeddings as E
from pipeline import style_memory as SM


@pytest.fixture(autouse=True)
def _dev_mem(monkeypatch):
    monkeypatch.setenv("DEV_NO_AUTH", "1")   # in-memory store (no Supabase)
    SM._reset()
    yield
    SM._reset()


def test_match_ranks_by_cosine():
    e = E.MockEmbedder(128)
    SM.index_segments("u1", "fire", "ref1",
                      ["rated load test report", "emergency siren volume"], "en", embedder=e)
    q = e.embed(["rated load capacity"], input_type=E.QUERY)[0]
    hits = SM.match("u1", "fire", "style", q, k=2)
    assert hits[0]["text"] == "rated load test report"   # token overlap wins


def test_match_scoped_by_owner_and_domain():
    e = E.MockEmbedder(128)
    SM.index_segments("u1", "fire", "r", ["rated load"], "en", embedder=e)
    SM.index_segments("u2", "fire", "r", ["rated load"], "en", embedder=e)
    SM.index_segments("u1", "water", "r", ["rated load"], "en", embedder=e)
    q = e.embed(["rated load"], input_type=E.QUERY)[0]
    assert len(SM.match("u1", "fire", "style", q, k=10)) == 1   # only u1/fire row


def test_retrieve_style_context_formats_and_caps():
    e = E.MockEmbedder(128)
    SM.index_segments("u1", "fire", "r",
                      [f"rated load clause number {i}" for i in range(30)], "en", embedder=e)
    ctx = SM.retrieve_style_context("u1", "fire", ["rated load requirement"],
                                    "ko", "en", embedder=e, max_exemplars=5)
    assert "Style reference" in ctx
    assert ctx.count("\n- ") <= 5            # exemplar cap honoured
    assert "English" in ctx                  # target language named


def test_retrieve_noop_when_empty_domain_or_no_refs():
    e = E.MockEmbedder(128)
    assert SM.retrieve_style_context("u1", "", ["x"], "ko", "en", embedder=e) == ""
    assert SM.retrieve_style_context("u1", "fire", ["x"], "ko", "en", embedder=e) == ""  # no refs


def test_list_domains_and_delete_cascade():
    e = E.MockEmbedder(64)
    rid = SM.index_segments("u1", "fire", "r", ["alpha beta", "gamma delta"], "en", embedder=e)
    doms = SM.list_domains("u1")
    assert doms and doms[0]["domain"] == "fire" and doms[0]["segments"] == 2
    SM.delete_reference("u1", rid)
    assert SM.list_domains("u1") == []
    q = e.embed(["alpha beta"], input_type=E.QUERY)[0]
    assert SM.match("u1", "fire", "style", q, k=5) == []   # segments cascaded out


def test_list_references_returns_metadata():
    e = E.MockEmbedder(64)
    SM.index_segments("u1", "fire", "manual-A", ["one two", "three four"], "en", embedder=e)
    refs = SM.list_references("u1", "fire")
    assert len(refs) == 1
    assert refs[0]["name"] == "manual-A" and refs[0]["segment_count"] == 2
