from fastapi.testclient import TestClient

import main
from pipeline import process


def _client(monkeypatch, tmp_path, report):
    main.app.dependency_overrides[main.auth.owner_bearer] = lambda: "u1"
    monkeypatch.setattr(process, "doc_dir", lambda *a: str(tmp_path))  # exists → passes 404 guard
    monkeypatch.setattr(process, "proper_noun_report", lambda *a, **k: report)
    return TestClient(main.app)


def test_proper_nouns_route_returns_terms(monkeypatch, tmp_path):
    rec = {"source": "스터닝박스", "translated": "StunningBox", "category": "brand",
           "count": 1, "locations": []}
    client = _client(monkeypatch, tmp_path, [rec])
    try:
        r = client.get("/api/documents/doc1/proper-nouns?lang=en")
        assert r.status_code == 200
        body = r.json()
        assert body["lang"] == "en" and body["terms"][0]["source"] == "스터닝박스"
    finally:
        main.app.dependency_overrides.clear()


def test_proper_nouns_route_404_for_missing_doc(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, [])
    monkeypatch.setattr(process, "doc_dir", lambda *a: str(tmp_path / "nope"))  # does not exist
    try:
        r = client.get("/api/documents/doc1/proper-nouns?lang=en")
        assert r.status_code == 404
    finally:
        main.app.dependency_overrides.clear()
