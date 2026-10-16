"""Per-user document ownership in Supabase Postgres (`documents` table), accessed with the
service-role key via PostgREST. The backend is the writer/authority; the frontend reads its
own rows via supabase-js + RLS. Env is read at call time so the module imports without it."""
from __future__ import annotations

import os

import httpx

_TIMEOUT = 10.0
# LOCAL DEV ONLY (DEV_NO_AUTH=1): in-memory ownership rows when Supabase is not configured.
_MEM: dict[str, dict] = {}


def _dev_mem() -> bool:
    return os.environ.get("DEV_NO_AUTH") == "1"


def _base() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not configured")
    return url, key


def _headers(key: str, extra: dict | None = None) -> dict:
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    if extra:
        h.update(extra)
    return h


def create(doc_id: str, owner_id: str, title: str, source_lang: str, target_lang: str) -> None:
    if _dev_mem():
        _MEM[doc_id] = {
            "owner_id": owner_id,
            "title": title,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "status": "processing",
        }
        return
    url, key = _base()
    r = httpx.post(
        f"{url}/rest/v1/documents",
        headers=_headers(key, {"Content-Type": "application/json", "Prefer": "return=minimal"}),
        json={
            "id": doc_id,
            "owner_id": owner_id,
            "title": title,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "status": "processing",
        },
        timeout=_TIMEOUT,
    )
    r.raise_for_status()


def get_owner(doc_id: str) -> str | None:
    if _dev_mem():
        row = _MEM.get(doc_id)
        return row["owner_id"] if row else None
    url, key = _base()
    r = httpx.get(
        f"{url}/rest/v1/documents",
        headers=_headers(key),
        params={"id": f"eq.{doc_id}", "select": "owner_id"},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0]["owner_id"] if rows else None


def set_status(doc_id: str, status: str) -> None:
    if _dev_mem():
        if doc_id in _MEM:
            _MEM[doc_id]["status"] = status
        return
    url, key = _base()
    r = httpx.patch(
        f"{url}/rest/v1/documents",
        headers=_headers(key, {"Content-Type": "application/json", "Prefer": "return=minimal"}),
        params={"id": f"eq.{doc_id}"},
        json={"status": status},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
