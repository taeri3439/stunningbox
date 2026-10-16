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


def _seed(tmp_path):
    storage, doc_id, lang = str(tmp_path), "doc1", "en"
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    process._write_json(
        os.path.join(process.doc_dir(storage, doc_id), "source.json"),
        SemanticDocument(source_lang="ko",
                         blocks=[ParagraphBlock(text="원문", source_span_ids=[])]).model_dump(),
    )
    tr = SemanticDocument(source_lang="ko", target_lang=lang,
                          blocks=[ParagraphBlock(text="Hi", source_span_ids=[])])
    process.save_translated_model(storage, doc_id, lang, tr)
    render_docx(tr, process.output_docx_path(storage, doc_id, lang),
                process.asset_dir(storage, doc_id))
    return doc_id, lang


def test_forcesave_ok_passes_stored_key(client, tmp_path, monkeypatch):
    doc_id, lang = _seed(tmp_path)
    main._write_issued_key(doc_id, lang, "sessionkey")
    seen = {}

    def fake(out, key):
        seen["out"], seen["key"] = out, key
        return {"key": key, "error": 0}

    monkeypatch.setattr(main.onlyoffice, "forcesave", fake)
    r = client.post(f"/api/documents/{doc_id}/forcesave?lang={lang}")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "error": 0}
    assert seen["key"] == "sessionkey"
    assert seen["out"].endswith(f"output.{lang}.docx")


def test_forcesave_no_changes_is_ok(client, tmp_path, monkeypatch):
    doc_id, lang = _seed(tmp_path)
    monkeypatch.setattr(main.onlyoffice, "forcesave", lambda out, key: {"key": key, "error": 4})
    r = client.post(f"/api/documents/{doc_id}/forcesave?lang={lang}")
    assert r.json() == {"ok": True, "error": 4}


def test_forcesave_no_session_not_ok(client, tmp_path, monkeypatch):
    doc_id, lang = _seed(tmp_path)
    monkeypatch.setattr(main.onlyoffice, "forcesave", lambda out, key: {"key": key, "error": 1})
    r = client.post(f"/api/documents/{doc_id}/forcesave?lang={lang}")
    assert r.json() == {"ok": False, "error": 1}


def test_forcesave_ds_unreachable_returns_minus1(client, tmp_path, monkeypatch):
    doc_id, lang = _seed(tmp_path)

    def boom(out, key):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(main.onlyoffice, "forcesave", boom)
    r = client.post(f"/api/documents/{doc_id}/forcesave?lang={lang}")
    assert r.json() == {"ok": False, "error": -1}


def test_forcesave_unknown_doc_404(client):
    r = client.post("/api/documents/nope/forcesave?lang=en")
    assert r.status_code == 404
