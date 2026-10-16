import json
import os

import pytest
from fastapi.testclient import TestClient

import main
from models import ParagraphBlock, SemanticDocument
from pipeline import process


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Run offline: force MockTranslator regardless of the dev env / .env.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    base = process.doc_dir(str(tmp_path), "doc1")
    os.makedirs(base, exist_ok=True)
    doc = SemanticDocument(
        source_lang="ko",
        blocks=[ParagraphBlock(text="첫 문장", source_span_ids=["p0b0l0s0"], block_id="BLK0")],
    )
    with open(os.path.join(base, "source.json"), "w", encoding="utf-8") as fh:
        json.dump(doc.model_dump(), fh, ensure_ascii=False)
    return TestClient(main.app)


def test_segments_endpoint(client):
    r = client.get("/api/documents/doc1/segments?lang=en")
    assert r.status_code == 200
    body = r.json()
    assert body["lang"] == "en"
    assert body["segments"][0]["source"] == "첫 문장"
    assert body["segments"][0]["source_bbox"] is None


def test_retranslate_endpoint_and_out_of_range(client):
    client.get("/api/documents/doc1/segments?lang=en")          # warm cache
    r = client.post(
        "/api/documents/doc1/retranslate-segment",
        json={"lang": "en", "id": "BLK0:text", "preset": "concise", "guidance": "법률 용어 유지"},
    )
    assert r.status_code == 200
    assert r.json()["new_text"] != r.json()["old_text"]
    bad = client.post(
        "/api/documents/doc1/retranslate-segment",
        json={"lang": "en", "id": "NOSUCHID:text"},
    )
    assert bad.status_code == 422
