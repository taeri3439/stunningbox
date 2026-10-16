"""Reference ingestion: extract text segments from an uploaded file (no vision/layout —
just sentences for style retrieval), embed, and store. Covers .txt and .docx here; PDF/HWP
share the same dispatch and are exercised in dogfood."""
import pytest

from pipeline import embeddings as E
from pipeline import style_memory as SM


@pytest.fixture(autouse=True)
def _dev(monkeypatch):
    monkeypatch.setenv("DEV_NO_AUTH", "1")
    SM._reset()
    yield
    SM._reset()


def test_ingest_txt_filters_short_and_indexes(tmp_path):
    p = tmp_path / "ref.txt"
    p.write_text("Rated load shall not exceed the limit.\n"
                 "The siren volume must be 90 dB at one metre.\nshort\n",
                 encoding="utf-8")
    SM.ingest_reference("u1", "fire", "ref.txt", str(p), "en", embedder=E.MockEmbedder(64))
    refs = SM.list_references("u1", "fire")
    assert refs and refs[0]["segment_count"] == 2          # 'short' (<8 chars) dropped
    q = E.MockEmbedder(64).embed(["rated load"], input_type=E.QUERY)[0]
    assert SM.match("u1", "fire", "style", q, k=3)          # retrievable


def test_ingest_docx_paragraphs_and_cells(tmp_path):
    import docx
    d = docx.Document()
    d.add_paragraph("The rated load capacity shall be verified by test.")
    d.add_paragraph("Emergency acoustic devices must comply with the standard.")
    fp = tmp_path / "r.docx"
    d.save(str(fp))
    SM.ingest_reference("u1", "fire", "r.docx", str(fp), "en", embedder=E.MockEmbedder(64))
    assert SM.list_references("u1", "fire")[0]["segment_count"] >= 2
