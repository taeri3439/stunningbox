import os

import pytest
from fastapi.testclient import TestClient

import main
from models import ParagraphBlock, SemanticDocument
from pipeline import process
from renderers.docx_renderer import render_docx


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    return TestClient(main.app)


def _seed(tmp_path, glossary):
    storage, doc_id, lang = str(tmp_path), "doc1", "en"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    process._write_json(os.path.join(base, "source.json"),
        SemanticDocument(source_lang="ko",
            blocks=[ParagraphBlock(text="정격하중 점검", source_span_ids=[])]).model_dump())
    tr = SemanticDocument(source_lang="ko", target_lang=lang,
        blocks=[ParagraphBlock(text="Rated Load check", source_span_ids=[])])
    process.save_translated_model(storage, doc_id, lang, tr)
    render_docx(tr, process.output_docx_path(storage, doc_id, lang),
                process.asset_dir(storage, doc_id))
    process.save_doc_glossary(storage, doc_id, glossary)
    return doc_id, lang


def test_glossary_report_route(client, tmp_path):
    doc_id, lang = _seed(tmp_path, {"정격하중": "Rated Load"})
    r = client.get(f"/api/documents/{doc_id}/glossary-report?lang={lang}")
    assert r.status_code == 200
    assert r.json()["terms"][0]["status"] == "applied"


def test_preview_route_highlights(client, tmp_path):
    doc_id, lang = _seed(tmp_path, {"정격하중": "Rated Load"})
    r = client.get(f"/api/documents/{doc_id}/preview?lang={lang}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<mark" in r.text and "Rated Load" in r.text


def test_update_term_route(client, tmp_path):
    doc_id, lang = _seed(tmp_path, {"정격하중": "Rated Load"})
    r = client.post(f"/api/documents/{doc_id}/glossary/term?lang={lang}",
                    json={"source": "정격하중", "target": "Load Rating"})
    assert r.status_code == 200
    assert r.json()["status"] == "updated"
    assert process.load_doc_glossary(str(tmp_path), doc_id)["정격하중"] == "Load Rating"


def test_add_term_route(client, tmp_path):
    doc_id, lang = _seed(tmp_path, {})
    r = client.post(f"/api/documents/{doc_id}/glossary/term?lang={lang}",
                    json={"source": "캐빈", "target": "Cabin"})
    assert r.status_code == 200
    assert r.json()["status"] == "added"
    assert process.load_doc_glossary(str(tmp_path), doc_id) == {"캐빈": "Cabin"}


def test_add_term_apply_retranslates_and_rerenders(client, tmp_path):
    doc_id, lang = _seed(tmp_path, {})  # source paragraph contains "정격하중"
    r = client.post(f"/api/documents/{doc_id}/glossary/term?lang={lang}",
                    json={"source": "정격하중", "target": "Cap-X", "apply": True})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "added" and body["retranslated"] == 1
    assert "Cap-X" in process.get_translated_model(str(tmp_path), doc_id, lang).blocks[0].text


def test_update_term_without_prerendered_docx(client, tmp_path):
    # No output.docx seeded → _ensure_docx must render it before bulk_replace edits it (no 500).
    storage, doc_id, lang = str(tmp_path), "doc2", "en"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    process._write_json(os.path.join(base, "source.json"),
        SemanticDocument(source_lang="ko",
            blocks=[ParagraphBlock(text="정격하중", source_span_ids=[])]).model_dump())
    process.save_translated_model(storage, doc_id, lang,
        SemanticDocument(source_lang="ko", target_lang=lang,
            blocks=[ParagraphBlock(text="Rated Load", source_span_ids=[])]))
    process.save_doc_glossary(storage, doc_id, {"정격하중": "Rated Load"})
    r = client.post(f"/api/documents/{doc_id}/glossary/term?lang={lang}",
                    json={"source": "정격하중", "target": "Load Rating"})
    assert r.status_code == 200
    assert r.json()["status"] == "updated"
    assert process.load_doc_glossary(storage, doc_id)["정격하중"] == "Load Rating"
