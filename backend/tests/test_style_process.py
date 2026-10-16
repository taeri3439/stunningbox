"""Process wiring: get_translated_model + retranslate_one_segment read the doc's style
domain and pass a retrieved style_context into translation. Uses MockEmbedder; the doc's
'source' text shares tokens with the reference so the mock retrieval matches (the mock can't
model cross-lingual semantics — that's verified in dogfood)."""
import os

import pytest

from models import ParagraphBlock, SemanticDocument
from pipeline import embeddings as E
from pipeline import process
from pipeline import style_memory as SM


@pytest.fixture(autouse=True)
def _dev(monkeypatch):
    monkeypatch.setenv("DEV_NO_AUTH", "1")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    SM._reset()
    yield
    SM._reset()


def _seed(storage, doc_id, *, source_text):
    os.makedirs(process.doc_dir(storage, doc_id), exist_ok=True)
    src = SemanticDocument(source_lang="ko",
                           blocks=[ParagraphBlock(text=source_text, block_id="BLK0")])
    process._write_json(
        os.path.join(process.doc_dir(storage, doc_id), "source.json"), src.model_dump())
    SM.index_segments("test-uid", "fire", "ref",
                      ["Rated load shall not exceed the limit.", "Unrelated siren clause."],
                      "en", embedder=E.MockEmbedder(64))
    process.save_doc_style(storage, doc_id, "fire", "test-uid")


def test_get_translated_model_injects_style_context(tmp_path, monkeypatch):
    storage = str(tmp_path)
    _seed(storage, "d1", source_text="rated load shall not exceed clause")
    captured = {}

    def spy(doc, target_lang, **kw):
        captured.update(kw)
        doc.target_lang = target_lang
        return doc
    monkeypatch.setattr(process, "translate_document", spy)

    process.get_translated_model(storage, "d1", "en")
    assert "Style reference" in captured["style_context"]
    assert "Rated load shall not exceed" in captured["style_context"]


def test_no_domain_means_empty_style_context(tmp_path, monkeypatch):
    storage = str(tmp_path)
    os.makedirs(process.doc_dir(storage, "d2"))
    src = SemanticDocument(source_lang="ko", blocks=[ParagraphBlock(text="rated load clause")])
    process._write_json(
        os.path.join(process.doc_dir(storage, "d2"), "source.json"), src.model_dump())
    # no save_doc_style → no domain
    captured = {}
    monkeypatch.setattr(process, "translate_document",
                        lambda doc, tl, **kw: (captured.update(kw), doc)[1])
    process.get_translated_model(storage, "d2", "en")
    assert captured["style_context"] == ""


def test_retranslate_segment_injects_style_context(tmp_path, monkeypatch):
    storage = str(tmp_path)
    _seed(storage, "d3", source_text="rated load shall not exceed clause")
    # Pre-seed the translation cache so retranslate's internal get_translated_model is a hit.
    process.save_translated_model(
        storage, "d3", "en",
        SemanticDocument(source_lang="ko", target_lang="en",
                         blocks=[ParagraphBlock(text="old translation", block_id="BLK0")]))
    captured = {}
    monkeypatch.setattr(process, "translate_segment",
                        lambda *a, **kw: (captured.update(kw), "new")[1])
    process.retranslate_one_segment(storage, "d3", "en", "BLK0:text", guidance="")
    assert "Style reference" in captured["style_context"]
