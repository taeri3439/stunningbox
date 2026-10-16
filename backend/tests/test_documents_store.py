import pytest

import documents_store


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
    def json(self):
        return self._payload


def test_get_owner_returns_uid(monkeypatch):
    monkeypatch.setattr(documents_store, "_base", lambda: ("https://x.supabase.co", "svc"))
    monkeypatch.setattr(
        documents_store.httpx, "get",
        lambda url, headers=None, params=None, timeout=None: _Resp(200, [{"owner_id": "u1"}]),
    )
    assert documents_store.get_owner("doc1") == "u1"


def test_get_owner_missing_returns_none(monkeypatch):
    monkeypatch.setattr(documents_store, "_base", lambda: ("https://x.supabase.co", "svc"))
    monkeypatch.setattr(
        documents_store.httpx, "get",
        lambda url, headers=None, params=None, timeout=None: _Resp(200, []),
    )
    assert documents_store.get_owner("missing") is None


def test_create_posts_row(monkeypatch):
    captured = {}
    monkeypatch.setattr(documents_store, "_base", lambda: ("https://x.supabase.co", "svc"))
    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _Resp(201)
    monkeypatch.setattr(documents_store.httpx, "post", fake_post)
    documents_store.create("doc1", "u1", "title", "ko", "en")
    assert captured["json"]["id"] == "doc1"
    assert captured["json"]["owner_id"] == "u1"
    assert captured["json"]["status"] == "processing"
    assert captured["url"].endswith("/rest/v1/documents")


def test_set_status_patches_row(monkeypatch):
    captured = {}
    monkeypatch.setattr(documents_store, "_base", lambda: ("https://x.supabase.co", "svc"))
    def fake_patch(url, headers=None, params=None, json=None, timeout=None):
        captured.update(url=url, params=params, json=json)
        return _Resp(204)
    monkeypatch.setattr(documents_store.httpx, "patch", fake_patch)
    documents_store.set_status("doc1", "done")
    assert captured["url"].endswith("/rest/v1/documents")
    assert captured["params"]["id"] == "eq.doc1"
    assert captured["json"]["status"] == "done"


def test_set_status_raises_on_error(monkeypatch):
    monkeypatch.setattr(documents_store, "_base", lambda: ("https://x.supabase.co", "svc"))
    monkeypatch.setattr(
        documents_store.httpx, "patch",
        lambda url, headers=None, params=None, json=None, timeout=None: _Resp(400),
    )
    with pytest.raises(RuntimeError):
        documents_store.set_status("doc1", "done")
