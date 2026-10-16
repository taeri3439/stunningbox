"""ConvertService client: request shape, freshness cache, error mapping."""
import json
import os
import threading
import time

import pytest

import onlyoffice
from pipeline import ds_convert


class FakeResp:
    def __init__(self, payload=None, content=b""):
        self._payload = payload
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _setup(tmp_path, monkeypatch, convert_payload=None, pdf_bytes=b"%PDF-fake"):
    doc_dir = tmp_path / "doc1"
    doc_dir.mkdir()
    (doc_dir / "output.en.docx").write_bytes(b"docx-bytes")
    posts, gets = [], []
    payload = convert_payload or {
        "endConvert": True, "percent": 100, "fileType": "pdf",
        "fileUrl": "http://onlyoffice-internal/cache/files/x/output.pdf?md5=a",
    }

    def fake_post(url, json=None, headers=None, timeout=None):
        posts.append({"url": url, "body": json})
        return FakeResp(payload=payload)

    def fake_get(url, timeout=None):
        gets.append(url)
        return FakeResp(content=pdf_bytes)

    monkeypatch.setattr(ds_convert.httpx, "post", fake_post)
    monkeypatch.setattr(ds_convert.httpx, "get", fake_get)
    monkeypatch.setattr(onlyoffice, "JWT_SECRET", "testsecret")
    monkeypatch.setattr(onlyoffice, "DS_INTERNAL_URL", "http://ds-test:8080")
    monkeypatch.setattr(onlyoffice, "BACKEND_URL", "http://backend-test:9000")
    return str(tmp_path), posts, gets


def test_converts_and_writes_pdf_with_version_sidecar(tmp_path, monkeypatch):
    storage, posts, gets = _setup(tmp_path, monkeypatch)
    out = ds_convert.ensure_pdf(storage, "doc1", "en")
    assert out == ds_convert.pdf_path(storage, "doc1", "en")
    assert open(out, "rb").read() == b"%PDF-fake"
    body = posts[0]["body"]
    assert body["async"] is False and body["filetype"] == "docx" and body["outputtype"] == "pdf"
    assert body["url"] == "http://backend-test:9000/api/documents/doc1/docx?lang=en"
    assert body["key"].endswith("-pdf") and len(body["key"]) <= 128
    assert body["token"]  # signed because JWT_SECRET set
    assert posts[0]["url"] == "http://ds-test:8080/ConvertService.ashx"
    # fileUrl host rewritten to DS_INTERNAL_URL before download
    assert gets[0].startswith("http://ds-test:8080/cache/files/")
    assert open(out + ".key", encoding="utf-8").read().strip()


def test_fresh_pdf_skips_conversion(tmp_path, monkeypatch):
    storage, posts, _ = _setup(tmp_path, monkeypatch)
    ds_convert.ensure_pdf(storage, "doc1", "en")
    ds_convert.ensure_pdf(storage, "doc1", "en")
    assert len(posts) == 1  # second call served from freshness cache


def test_docx_change_reconverts(tmp_path, monkeypatch):
    storage, posts, _ = _setup(tmp_path, monkeypatch)
    ds_convert.ensure_pdf(storage, "doc1", "en")
    docx = os.path.join(storage, "doc1", "output.en.docx")
    with open(docx, "wb") as fh:
        fh.write(b"docx-bytes-v2!")  # size+mtime change -> new document_key
    ds_convert.ensure_pdf(storage, "doc1", "en")
    assert len(posts) == 2


def test_ds_error_raises(tmp_path, monkeypatch):
    storage, _, _ = _setup(tmp_path, monkeypatch, convert_payload={"error": -3})
    with pytest.raises(ds_convert.DsConvertError) as ei:
        ds_convert.ensure_pdf(storage, "doc1", "en")
    assert ei.value.code == -3


def test_missing_docx_raises(tmp_path, monkeypatch):
    storage, _, _ = _setup(tmp_path, monkeypatch)
    with pytest.raises(FileNotFoundError):
        ds_convert.ensure_pdf(storage, "nope", "en")


def test_concurrent_stale_calls_convert_exactly_once(tmp_path, monkeypatch):
    """Backend routes now fire paired concurrent ensure_pdf calls for the same (doc, lang)
    (e.g. tpages + tsegments). On a stale cache both callers used to convert and race on the
    same fixed ".tmp" path -> exactly one conversion should happen, both callers should get
    back the same valid PDF, and the .key sidecar should match the docx version."""
    doc_dir = tmp_path / "doc1"
    doc_dir.mkdir()
    docx_path = doc_dir / "output.en.docx"
    docx_path.write_bytes(b"docx-bytes")
    storage = str(tmp_path)
    expected_version = onlyoffice.document_key(str(docx_path))

    call_lock = threading.Lock()
    calls = []

    def fake_convert(doc_id, lang, version):
        with call_lock:
            calls.append(version)
        time.sleep(0.15)  # widen the race window so a second thread piles up on the lock
        return b"%PDF-fake"

    monkeypatch.setattr(ds_convert, "_convert", fake_convert)

    results = []
    errors = []

    def worker():
        try:
            results.append(ds_convert.ensure_pdf(storage, "doc1", "en"))
        except Exception as exc:  # pragma: no cover - surfaced via assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    assert len(calls) == 1  # exactly one conversion, not two racing ones
    assert len(results) == 2
    out = ds_convert.pdf_path(storage, "doc1", "en")
    assert results[0] == out and results[1] == out
    assert open(out, "rb").read() == b"%PDF-fake"
    with open(out + ".key", encoding="utf-8") as fh:
        assert fh.read().strip() == expected_version
