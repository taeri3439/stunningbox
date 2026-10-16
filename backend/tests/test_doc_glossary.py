import json
import os

from models import ParagraphBlock, SemanticDocument
from pipeline import process
from pipeline.translator import MockTranslator


def _seed(tmp_path, glossary=None):
    storage, doc_id = str(tmp_path), "doc1"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(base, exist_ok=True)
    doc = SemanticDocument(
        source_lang="ko", blocks=[ParagraphBlock(text="정격하중", source_span_ids=[])]
    )
    with open(os.path.join(base, "source.json"), "w", encoding="utf-8") as fh:
        json.dump(doc.model_dump(), fh, ensure_ascii=False)
    if glossary is not None:
        process.save_doc_glossary(storage, doc_id, glossary)
    return storage, doc_id


def test_save_and_load_doc_glossary(tmp_path):
    storage, doc_id = _seed(tmp_path, {"정격하중": "RATED-X"})
    assert process.load_doc_glossary(storage, doc_id) == {"정격하중": "RATED-X"}


def test_load_doc_glossary_missing_is_empty(tmp_path):
    storage, doc_id = _seed(tmp_path)
    assert process.load_doc_glossary(storage, doc_id) == {}


def test_load_doc_glossary_corrupt_is_empty(tmp_path):
    # A corrupt glossary.json must degrade to {} (built-in only), not break translation.
    storage, doc_id = _seed(tmp_path)
    with open(process.glossary_path(storage, doc_id), "w", encoding="utf-8") as fh:
        fh.write("{ not valid json")
    assert process.load_doc_glossary(storage, doc_id) == {}


def test_get_translated_model_applies_doc_glossary(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # → MockTranslator offline
    storage, doc_id = _seed(tmp_path, {"정격하중": "RATED-LOAD-X"})
    model = process.get_translated_model(storage, doc_id, "en")
    assert "RATED-LOAD-X" in model.blocks[0].text
