import os

import pytest
from fastapi.testclient import TestClient

import main
from pipeline import process


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    return TestClient(main.app)


def test_glossary_template_download(client):
    r = client.get("/api/glossary/template")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers.get("content-disposition", "")
    body = r.content.decode("utf-8-sig")
    assert body.startswith("원어,번역어")
    assert "Aerial Ladder Vehicle" in body


def test_upload_saves_glossary(client, tmp_path):
    csv = "원어,번역어\n정격하중,RATED-X\n캐빈,CAB-X\n"
    r = client.post(
        "/api/documents",
        files={
            "pdf": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf"),
            "glossary": ("g.csv", csv.encode("utf-8-sig"), "text/csv"),
        },
        data={"source_lang": "ko", "target_lang": "en"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["glossary_terms"] == 2
    saved = process.load_doc_glossary(str(tmp_path), body["doc_id"])
    assert saved == {"정격하중": "RATED-X", "캐빈": "CAB-X"}


def test_upload_without_glossary(client):
    r = client.post(
        "/api/documents",
        files={"pdf": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"source_lang": "ko", "target_lang": "en"},
    )
    assert r.status_code == 200
    assert r.json()["glossary_terms"] == 0
