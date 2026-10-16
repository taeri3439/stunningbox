"""Style-reference HTTP routes: upload/list/delete references + style_domain on document
upload. Auth is bypassed by conftest (uid='test-uid'); the in-memory store (DEV_NO_AUTH=1)
+ MockEmbedder keep it offline."""
import pytest
from fastapi.testclient import TestClient

import main
from pipeline import style_memory as SM

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _dev(monkeypatch):
    monkeypatch.setenv("DEV_NO_AUTH", "1")
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    SM._reset()
    yield
    SM._reset()


def test_reference_upload_list_delete_roundtrip():
    txt = ("Rated load shall not exceed the limit.\n"
           "The siren volume must be 90 dB at one metre.\n").encode("utf-8")
    r = client.post(
        "/api/style/references",
        data={"domain": "fire", "target_lang": "en", "source_lang": "ko", "name": "manualA"},
        files={"target": ("ref.txt", txt, "text/plain")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["segment_count"] == 2

    domains = client.get("/api/style/domains").json()["domains"]
    assert any(d["domain"] == "fire" and d["segments"] == 2 for d in domains)

    refs = client.get("/api/style/references", params={"domain": "fire"}).json()["references"]
    assert len(refs) == 1 and refs[0]["name"] == "manualA"
    rid = refs[0]["id"]

    assert client.delete(f"/api/style/references/{rid}").status_code == 200
    assert client.get("/api/style/domains").json()["domains"] == []


def test_reference_upload_requires_domain():
    r = client.post(
        "/api/style/references",
        data={},
        files={"target": ("a.txt", b"a sufficiently long sentence here.", "text/plain")},
    )
    assert r.status_code == 422


def test_reference_upload_rejects_bad_ext():
    r = client.post(
        "/api/style/references",
        data={"domain": "fire"},
        files={"target": ("a.exe", b"nope", "application/octet-stream")},
    )
    assert r.status_code == 400


_INTERLEAVED = (
    "방수총 정의 문장입니다.\nFirefighting monitor definition here.\n"
    "고정형 정의입니다.\nFixed type definition here.\n"
).encode("utf-8")


def test_analyze_detects_interleaved():
    r = client.post("/api/style/references/analyze",
                    files={"target": ("ref.txt", _INTERLEAVED, "text/plain")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["mode"] == "interleaved" and j["pair_count"] == 2
    assert j["sample_pairs"][0]["target"].startswith("Firefighting")


def test_analyze_flags_source_only():
    txt = "한국어 원문만 있는 문서입니다.\n번역이 전혀 없습니다.\n".encode("utf-8")
    r = client.post("/api/style/references/analyze",
                    files={"target": ("s.txt", txt, "text/plain")})
    assert r.status_code == 200 and r.json()["mode"] == "source_only"


def test_upload_interleaved_auto_stores_tm_pairs():
    r = client.post("/api/style/references", data={"domain": "소방"},
                    files={"target": ("ref.txt", _INTERLEAVED, "text/plain")})
    assert r.status_code == 200, r.text
    refs = client.get("/api/style/references", params={"domain": "소방"}).json()["references"]
    assert refs[0]["has_pairs"] is True and refs[0]["segment_count"] >= 2


def test_upload_source_only_rejected_422():
    txt = "한국어 원문만 있는 문서.\n번역이 없습니다.\n".encode("utf-8")
    r = client.post("/api/style/references", data={"domain": "소방"},
                    files={"target": ("s.txt", txt, "text/plain")})
    assert r.status_code == 422


def test_upload_bad_mode_override_yields_nothing_rejected_422():
    # mode override mismatched to content (interleaved on a Korean-only file) → 0 segments → 422
    txt = "한국어 원문만 있는 문서.\n번역이 전혀 없습니다.\n".encode("utf-8")
    r = client.post("/api/style/references", data={"domain": "소방", "mode": "interleaved"},
                    files={"target": ("s.txt", txt, "text/plain")})
    assert r.status_code == 422


def test_pairs_endpoint_returns_all_pairs(tmp_path):
    p = tmp_path / "i.txt"
    p.write_text(
        "방수총 정의.\nMonitor definition here.\n고정형 정의.\nFixed type definition.\n또.\nAnother one here.\n",
        encoding="utf-8",
    )
    with open(p, "rb") as fh:
        r = client.post("/api/style/references/pairs", files={"target": ("i.txt", fh, "text/plain")})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "interleaved" and body["estimated"] is False
    assert body["count"] == len(body["pairs"]) >= 3
    assert set(body["pairs"][0]) == {"source", "target"}


def test_pairs_endpoint_source_only_empty(tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("한국어 원문만 있는 문서입니다.\n번역이 없어요.\n", encoding="utf-8")
    with open(p, "rb") as fh:
        r = client.post("/api/style/references/pairs", files={"target": ("s.txt", fh, "text/plain")})
    assert r.status_code == 200 and r.json()["count"] == 0 and r.json()["pairs"] == []


def test_document_upload_persists_style_domain(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    monkeypatch.setattr(main, "_run_job", lambda *a, **k: None)   # no real processing
    monkeypatch.setattr(main.process, "save_doc_style",
                        lambda s, d, domain, owner: captured.update(domain=domain, owner=owner))
    r = client.post(
        "/api/documents",
        data={"source_lang": "ko", "target_lang": "en", "style_domain": "fire"},
        files={"pdf": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 200, r.text
    assert captured == {"domain": "fire", "owner": "test-uid"}
