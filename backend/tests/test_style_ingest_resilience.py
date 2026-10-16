"""Resilient ingest: the style half of a reference is always stored; TM-pair indexing is
best-effort. A pair-embedding failure (e.g. an embedding-provider rate limit on the second
batch) must NOT fail the whole upload or leave a half-ingested reference — it is reported via
the returned `tm` status instead. Regression for the prod bug where 211 style segments were
stored but 0 TM pairs, with the route returning 500."""
import pytest

from pipeline import embeddings as E
from pipeline import style_memory as SM


@pytest.fixture(autouse=True)
def _dev(monkeypatch):
    monkeypatch.setenv("DEV_NO_AUTH", "1")
    SM._reset()
    yield
    SM._reset()


def _interleaved(tmp_path):
    # ko line then en line, alternating → classify=interleaved with positional pairs.
    p = tmp_path / "ref.txt"
    p.write_text(
        "정격하중은 한계를 초과하지 않아야 한다.\n"
        "The rated load shall not exceed the limit.\n"
        "경보음은 1미터에서 90데시벨이어야 한다.\n"
        "The siren volume must be 90 dB at one metre.\n",
        encoding="utf-8")
    return str(p)


def test_ingest_returns_tm_status_and_indexes_pairs(tmp_path):
    res = SM.ingest_reference("u1", "fire", "ref", _interleaved(tmp_path), "en",
                              lang_src="ko", embedder=E.MockEmbedder(64))
    assert res["id"] and res["style_count"] >= 2
    assert res["tm"]["indexed"] is True
    assert res["tm"]["pairs"] >= 2
    assert res["tm"]["error"] is None
    assert SM.domain_has_pairs("u1", "fire")


def test_pair_index_failure_keeps_style_and_reports_error(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("voyage 429 rate limited")
    monkeypatch.setattr(SM, "_index_pairs", _boom)

    res = SM.ingest_reference("u1", "fire", "ref", _interleaved(tmp_path), "en",
                              lang_src="ko", embedder=E.MockEmbedder(64))
    # Style is saved despite the pair failure — no exception, the reference is usable.
    assert res["style_count"] >= 2
    assert SM.list_references("u1", "fire")[0]["segment_count"] >= 2
    # The TM failure is surfaced (not swallowed into a 500) and has_pairs is NOT set.
    assert res["tm"]["indexed"] is False
    assert "429" in (res["tm"]["error"] or "")
    assert not SM.domain_has_pairs("u1", "fire")


def test_style_only_file_has_no_pairs_no_error(tmp_path):
    p = tmp_path / "tgt.txt"
    p.write_text("The rated load capacity shall be verified by test.\n"
                 "Emergency acoustic devices must comply with the standard.\n", encoding="utf-8")
    res = SM.ingest_reference("u1", "fire", "r", str(p), "en", embedder=E.MockEmbedder(64))
    assert res["style_count"] >= 2
    assert res["tm"] == {"indexed": False, "pairs": 0, "error": None}


def test_source_only_still_raises(tmp_path):
    p = tmp_path / "src.txt"
    p.write_text("정격하중은 한계를 초과하지 않아야 한다.\n경보음은 90데시벨이어야 한다.\n", encoding="utf-8")
    with pytest.raises(ValueError):
        SM.ingest_reference("u1", "fire", "r", str(p), "en", embedder=E.MockEmbedder(64))
