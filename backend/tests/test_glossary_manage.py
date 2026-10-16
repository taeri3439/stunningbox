import os

from docx import Document

from models import ParagraphBlock, SemanticDocument
from pipeline import process
from pipeline.translator import MockTranslator
from renderers.docx_renderer import render_docx


def _seed(tmp_path, source_text, translated_text, glossary):
    storage, doc_id, lang = str(tmp_path), "doc1", "en"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    process._write_json(os.path.join(base, "source.json"),
        SemanticDocument(source_lang="ko",
            blocks=[ParagraphBlock(text=source_text, source_span_ids=[])]).model_dump())
    tr = SemanticDocument(source_lang="ko", target_lang=lang,
        blocks=[ParagraphBlock(text=translated_text, source_span_ids=[])])
    process.save_translated_model(storage, doc_id, lang, tr)
    render_docx(tr, process.output_docx_path(storage, doc_id, lang),
                process.asset_dir(storage, doc_id))
    process.save_doc_glossary(storage, doc_id, glossary)
    return storage, doc_id, lang


def test_glossary_report_classifies(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, "정격하중 점검", "Rated Load check",
                                  {"정격하중": "Rated Load", "캐빈": "Cabin"})
    terms = {t["source"]: t for t in process.glossary_report(storage, doc_id, lang)}
    assert terms["정격하중"]["status"] == "applied"
    assert terms["캐빈"]["status"] == "absent"


def test_glossary_report_empty_when_no_glossary(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, "x", "y", {})
    assert process.glossary_report(storage, doc_id, lang) == []


def test_update_glossary_term_replaces_and_saves(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, "정격하중", "Rated Load here",
                                  {"정격하중": "Rated Load"})
    res = process.update_glossary_term(storage, doc_id, "정격하중", "Load Rating", lang)
    assert res["replaced"] == 1
    assert process.load_doc_glossary(storage, doc_id)["정격하중"] == "Load Rating"
    assert process.get_translated_model(storage, doc_id, lang).blocks[0].text == "Load Rating here"


def test_add_glossary_term_registers(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, "캐빈", "Cabin", {})
    process.add_glossary_term(storage, doc_id, "캐빈", "Cab")
    assert process.load_doc_glossary(storage, doc_id) == {"캐빈": "Cab"}


def test_apply_glossary_term_retranslates_affected(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, "캐빈", "old", {"캐빈": "Cab"})
    res = process.apply_glossary_term(storage, doc_id, "캐빈", lang,
                                      translator=MockTranslator())
    assert res["retranslated"] == 1
    assert "Cab" in process.get_translated_model(storage, doc_id, lang).blocks[0].text


def test_update_glossary_term_new_key(tmp_path):
    # source not yet in glossary → no replace, just registers.
    storage, doc_id, lang = _seed(tmp_path, "캐빈", "Cabin", {})
    res = process.update_glossary_term(storage, doc_id, "캐빈", "Cab", lang)
    assert res["replaced"] == 0
    assert process.load_doc_glossary(storage, doc_id)["캐빈"] == "Cab"
