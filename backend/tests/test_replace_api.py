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


def _seed(tmp_path, text="Rated Load"):
    storage, doc_id, lang = str(tmp_path), "doc1", "en"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    process._write_json(
        os.path.join(base, "source.json"),
        SemanticDocument(source_lang="ko",
                         blocks=[ParagraphBlock(text="원문", source_span_ids=[])]).model_dump(),
    )
    tr = SemanticDocument(source_lang="ko", target_lang=lang,
                          blocks=[ParagraphBlock(text=text, source_span_ids=[])])
    process.save_translated_model(storage, doc_id, lang, tr)
    render_docx(tr, process.output_docx_path(storage, doc_id, lang),
                process.asset_dir(storage, doc_id))
    return doc_id, lang


def test_preview_then_apply(client, tmp_path):
    doc_id, lang = _seed(tmp_path)
    body = {"rules": [{"find": "Rated Load", "replace": "Load Rating"}]}
    r = client.post(f"/api/documents/{doc_id}/replace/preview?lang={lang}", json=body)
    assert r.status_code == 200
    assert r.json()["total"] == 1
    r2 = client.post(f"/api/documents/{doc_id}/replace?lang={lang}", json=body)
    assert r2.status_code == 200
    assert r2.json()["results"][0]["count"] == 1
    assert process.get_translated_model(str(tmp_path), doc_id, lang).blocks[0].text == "Load Rating"


def test_replace_skips_empty_and_noop_rules(client, tmp_path):
    doc_id, lang = _seed(tmp_path)
    body = {"rules": [{"find": "", "replace": "x"},
                      {"find": "Rated Load", "replace": "Rated Load"}]}
    r = client.post(f"/api/documents/{doc_id}/replace?lang={lang}", json=body)
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_replace_unknown_doc_404(client):
    r = client.post("/api/documents/nope/replace?lang=en", json={"rules": []})
    assert r.status_code == 404


def test_preview_is_read_only(client, tmp_path):
    doc_id, lang = _seed(tmp_path)
    body = {"rules": [{"find": "Rated Load", "replace": "Load Rating"}]}
    r = client.post(f"/api/documents/{doc_id}/replace/preview?lang={lang}", json=body)
    assert r.status_code == 200
    assert r.json()["total"] == 1
    # preview must not mutate the model
    assert process.get_translated_model(str(tmp_path), doc_id, lang).blocks[0].text == "Rated Load"
