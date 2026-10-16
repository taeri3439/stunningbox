# Backend Auth Protection + Per-User Documents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** FastAPI verifies the Supabase JWT and enforces per-user document ownership; `/app` becomes a 내 문서 dashboard.

**Architecture:** A FastAPI auth layer (`auth.py`) verifies the Supabase access token (HS256 + `SUPABASE_JWT_SECRET`) from the `Authorization: Bearer` header (JSON/mutating endpoints) or the `@supabase/ssr` `sb-<ref>-auth-token` cookie (read-only nav GETs). Ownership lives in a Supabase Postgres `documents` table — the backend reads/writes it with the service-role key (`documents_store.py`), the frontend reads its own rows via supabase-js + RLS. OnlyOffice DS endpoints keep their existing JWT.

**Tech Stack:** FastAPI, PyJWT (`jwt`) + `httpx` (both already backend deps), Supabase (Postgres + Auth), Next 16 + supabase-js.

**Spec:** `docs/superpowers/specs/2026-06-11-stunningbox-backend-auth-userdocs-design.md`
**Branch:** `backend-userdocs-2026.06.11` (already created/checked out).

---

## ⚠️ Read before starting

1. **No new backend deps.** `jwt` (PyJWT) and `httpx` are already imported in `backend/onlyoffice.py`/`backend/main.py`. Use them.
2. **Confirmed `@supabase/ssr` cookie format** (context7, ssr ≥0.5): cookie name `sb-<project-ref>-auth-token`; if large, chunked into `…-auth-token.0`, `.1`, … (join in index order); value is `base64-` + **base64url**(JSON session); the JSON contains `access_token`. Default `cookieEncoding="base64url"`. A non-prefixed value = raw JSON (handle both).
3. **Supabase access tokens are HS256** here (verified from the login smoke test). Verify with `algorithms=["HS256"]`, `audience="authenticated"`; the user id is the `sub` claim.
4. **Agents cannot write `.env`** (deny-listed). The backend reads new env at call time: `SUPABASE_JWT_SECRET`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`. Modules must IMPORT fine without them (read env inside functions). The **user** adds them to `backend/.env` and runs the `documents` SQL (Task 6).
5. **Backend tests:** run with the venv python — `cd backend && .venv/Scripts/python -m pytest tests/<file> -q` (PowerShell: `cd backend; .\.venv\Scripts\python -m pytest tests\<file> -q`). The full suite must stay green; adding auth to endpoints would break existing `test_*_api.py` unless the autouse bypass fixture (Task 2) is in place — so do Task 2 before Task 3.
6. **Frontend:** `npm run lint` + `NEXT_PUBLIC_SUPABASE_URL=https://placeholder.supabase.co NEXT_PUBLIC_SUPABASE_ANON_KEY=placeholder-anon-key BACKEND_URL=http://127.0.0.1:8001 npm run build` (3 known pre-existing lint errors allowed).
7. Run npm from `frontend/`, python from `backend/`. Do not switch branches.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/documents_store.py` | **Create.** Service-role PostgREST client: `create`, `get_owner`, `set_status`. |
| `backend/auth.py` | **Create.** JWT verify + token extraction (bearer/cookie); deps `current_user`, `current_user_bearer`, `owner_bearer`, `owner_any`; `require_owner`. |
| `backend/main.py` | **Modify.** Add auth deps per endpoint tier; upload inserts the owner row; `_run_job` sets status. |
| `backend/tests/conftest.py` | **Create.** Autouse fixture: bypass auth deps + stub `documents_store` so existing endpoint tests pass unchanged. |
| `backend/tests/test_documents_store.py` | **Create.** Unit tests (httpx mocked). |
| `backend/tests/test_auth.py` | **Create.** Unit tests: verify, cookie parsing (incl. chunked), `require_owner`, the deps. |
| `frontend/src/lib/api.ts` | **Modify.** `authedFetch` attaches `Authorization: Bearer <access_token>` to JSON/mutating calls. |
| `frontend/src/components/MyDocuments.tsx` | **Create.** 내 문서 list (supabase-js + RLS). |
| `frontend/src/app/app/page.tsx` | **Modify.** Render `<MyDocuments/>` below the upload card. |

---

## Task 1: `documents_store.py` (service-role PostgREST)

**Files:** Create `backend/documents_store.py`, `backend/tests/test_documents_store.py`.

- [ ] **Step 1: Write `backend/tests/test_documents_store.py`**

```python
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
    assert captured["url"].endswith("/rest/v1/documents")
```

- [ ] **Step 2: Run — expect FAIL (module missing)**

`cd backend && .venv/Scripts/python -m pytest tests/test_documents_store.py -q` → ImportError/fail.

- [ ] **Step 3: Create `backend/documents_store.py`**

```python
"""Per-user document ownership in Supabase Postgres (`documents` table), accessed with the
service-role key via PostgREST. The backend is the writer/authority; the frontend reads its
own rows via supabase-js + RLS. Env is read at call time so the module imports without it."""
from __future__ import annotations

import os

import httpx

_TIMEOUT = 10.0


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
    url, key = _base()
    r = httpx.patch(
        f"{url}/rest/v1/documents",
        headers=_headers(key, {"Content-Type": "application/json", "Prefer": "return=minimal"}),
        params={"id": f"eq.{doc_id}"},
        json={"status": status},
        timeout=_TIMEOUT,
    )
    r.raise_for_status()
```

- [ ] **Step 4: Run — expect PASS** (`tests/test_documents_store.py`).

- [ ] **Step 5: Commit**
```bash
git add backend/documents_store.py backend/tests/test_documents_store.py
git commit -m "feat(backend): documents_store — per-user ownership via Supabase service role"
```

---

## Task 2: `auth.py` (JWT verify + token extraction + deps) + conftest bypass

**Files:** Create `backend/auth.py`, `backend/tests/test_auth.py`, `backend/tests/conftest.py`.

- [ ] **Step 1: Write `backend/tests/test_auth.py`**

```python
import base64
import json
import time

import jwt
import pytest
from fastapi import HTTPException

import auth
import documents_store

SECRET = "test-secret"


def _token(claims=None, exp_delta=3600):
    payload = {"sub": "user-1", "aud": "authenticated", "exp": int(time.time()) + exp_delta}
    if claims:
        payload.update(claims)
    return jwt.encode(payload, SECRET, algorithm="HS256")


def test_verify_valid(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    assert auth._verify_jwt(_token()) == "user-1"


def test_verify_bad_signature(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    bad = jwt.encode({"sub": "x", "aud": "authenticated"}, "other", algorithm="HS256")
    with pytest.raises(HTTPException) as e:
        auth._verify_jwt(bad)
    assert e.value.status_code == 401


def test_verify_expired(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", SECRET)
    with pytest.raises(HTTPException):
        auth._verify_jwt(_token(exp_delta=-10))


def test_token_from_cookies_single():
    sess = json.dumps({"access_token": "AT", "token_type": "bearer"})
    val = "base64-" + base64.urlsafe_b64encode(sess.encode()).decode().rstrip("=")
    cookies = {"sb-abc-auth-token": val}
    assert auth._token_from_cookies(cookies, "abc") == "AT"


def test_token_from_cookies_chunked():
    sess = json.dumps({"access_token": "AT2"})
    full = "base64-" + base64.urlsafe_b64encode(sess.encode()).decode().rstrip("=")
    mid = len(full) // 2
    cookies = {"sb-abc-auth-token.0": full[:mid], "sb-abc-auth-token.1": full[mid:]}
    assert auth._token_from_cookies(cookies, "abc") == "AT2"


def test_require_owner_ok(monkeypatch):
    monkeypatch.setattr(documents_store, "get_owner", lambda d: "user-1")
    assert auth.require_owner("doc1", "user-1") == "user-1"


def test_require_owner_mismatch_404(monkeypatch):
    monkeypatch.setattr(documents_store, "get_owner", lambda d: "someone-else")
    with pytest.raises(HTTPException) as e:
        auth.require_owner("doc1", "user-1")
    assert e.value.status_code == 404


def test_require_owner_missing_404(monkeypatch):
    monkeypatch.setattr(documents_store, "get_owner", lambda d: None)
    with pytest.raises(HTTPException) as e:
        auth.require_owner("doc1", "user-1")
    assert e.value.status_code == 404
```

- [ ] **Step 2: Run — expect FAIL** (`tests/test_auth.py`).

- [ ] **Step 3: Create `backend/auth.py`**

```python
"""Supabase auth for the FastAPI backend.

Verifies the Supabase access token (HS256, project JWT secret) and enforces per-user
document ownership. Token sources:
  - Authorization: Bearer <access_token>   (JSON / mutating endpoints; CSRF-immune)
  - sb-<ref>-auth-token cookie             (read-only navigation GETs: iframe/img/anchor)
The @supabase/ssr cookie value is `base64-`+base64url(JSON session); large sessions are
chunked into `…-auth-token.0/.1/…`. Env is read at call time (module imports without it)."""
from __future__ import annotations

import base64
import json
import os
from urllib.parse import urlparse

import jwt
from fastapi import HTTPException, Request

import documents_store


def _verify_jwt(token: str) -> str:
    secret = os.environ.get("SUPABASE_JWT_SECRET", "")
    if not secret:
        raise HTTPException(500, "Auth not configured")
    try:
        payload = jwt.decode(
            token, secret, algorithms=["HS256"], audience="authenticated"
        )
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired token")
    uid = payload.get("sub")
    if not uid:
        raise HTTPException(401, "Token missing subject")
    return uid


def _project_ref() -> str:
    host = urlparse(os.environ.get("SUPABASE_URL", "")).hostname or ""
    return host.split(".")[0]


def _b64url_json(value: str) -> dict | None:
    if value.startswith("base64-"):
        raw = value[len("base64-"):]
        try:
            decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            return json.loads(decoded)
        except Exception:  # noqa: BLE001
            return None
    try:  # raw (unprefixed) JSON
        return json.loads(value)
    except Exception:  # noqa: BLE001
        return None


def _token_from_cookies(cookies: dict[str, str], ref: str) -> str | None:
    base = f"sb-{ref}-auth-token"
    if base in cookies:
        value = cookies[base]
    else:  # reassemble chunks .0, .1, … in order
        chunks = sorted(
            (k for k in cookies if k.startswith(base + ".")),
            key=lambda k: int(k.rsplit(".", 1)[1]),
        )
        if not chunks:
            return None
        value = "".join(cookies[k] for k in chunks)
    session = _b64url_json(value)
    if not session:
        return None
    return session.get("access_token")


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if h and h.startswith("Bearer "):
        return h[len("Bearer "):]
    return None


def current_user_bearer(request: Request) -> str:
    """Identity from the Authorization header only (JSON/mutating endpoints)."""
    token = _bearer(request)
    if not token:
        raise HTTPException(401, "Missing bearer token")
    return _verify_jwt(token)


def current_user(request: Request) -> str:
    """Identity from the Authorization header OR the Supabase cookie (read-only nav GETs)."""
    token = _bearer(request) or _token_from_cookies(request.cookies, _project_ref())
    if not token:
        raise HTTPException(401, "Not authenticated")
    return _verify_jwt(token)


def require_owner(doc_id: str, uid: str) -> str:
    """404 (not 403) if the doc has no owner row or is owned by someone else."""
    owner = documents_store.get_owner(doc_id)
    if owner is None or owner != uid:
        raise HTTPException(404, "Unknown document")
    return uid


def owner_bearer(doc_id: str, request: Request) -> str:
    return require_owner(doc_id, current_user_bearer(request))


def owner_any(doc_id: str, request: Request) -> str:
    return require_owner(doc_id, current_user(request))
```

- [ ] **Step 4: Run — expect PASS** (`tests/test_auth.py`).

- [ ] **Step 5: Create `backend/tests/conftest.py`** (autouse bypass so existing endpoint tests stay green once Task 3 adds the deps)

```python
"""Test-wide auth bypass: endpoint tests exercise document logic, not auth. This overrides
the auth dependencies to a fixed test uid and stubs documents_store so no network/secret is
needed. Auth's own behavior is unit-tested directly in test_auth.py / test_documents_store.py."""
import pytest

import auth
import documents_store
import main

TEST_UID = "test-uid"


@pytest.fixture(autouse=True)
def _bypass_auth(monkeypatch):
    main.app.dependency_overrides[auth.current_user] = lambda: TEST_UID
    main.app.dependency_overrides[auth.current_user_bearer] = lambda: TEST_UID
    main.app.dependency_overrides[auth.owner_bearer] = lambda: TEST_UID
    main.app.dependency_overrides[auth.owner_any] = lambda: TEST_UID
    monkeypatch.setattr(documents_store, "create", lambda *a, **k: None)
    monkeypatch.setattr(documents_store, "get_owner", lambda d: TEST_UID)
    monkeypatch.setattr(documents_store, "set_status", lambda *a, **k: None)
    yield
    main.app.dependency_overrides.clear()
```

- [ ] **Step 6: Run the FULL backend suite — expect PASS** (conftest is harmless now; nothing depends on the deps yet).

`cd backend && .venv/Scripts/python -m pytest -q`

- [ ] **Step 7: Commit**
```bash
git add backend/auth.py backend/tests/test_auth.py backend/tests/conftest.py
git commit -m "feat(backend): Supabase JWT auth + ownership deps; test auth-bypass fixture"
```

---

## Task 3: Protect `main.py` endpoints + upload ownership + job status

**Files:** Modify `backend/main.py`.

- [ ] **Step 1: Add imports** — after `import onlyoffice` (line ~28):
```python
import auth
import documents_store
from fastapi import Depends
```
(`Depends` may already be importable from the existing `from fastapi import ...` line — extend that line instead of re-importing if so.)

- [ ] **Step 2: Apply the auth dependency to each endpoint** per this map. For each endpoint, add the dependency parameter shown (FastAPI injects `doc_id` from the path into the dependency automatically). The `_` name signals "for the side effect (auth) only".

| Endpoint (handler) | Add parameter |
|---|---|
| `upload` (`POST /api/documents`) | `uid: str = Depends(auth.current_user_bearer)` — and use `uid` (Step 3) |
| `detect_language` (`POST /api/detect-language`) | `_: str = Depends(auth.current_user_bearer)` |
| `status`, `result`, `segments`, `glossary_report`, `editor_config` (GET) | `_: str = Depends(auth.owner_bearer)` |
| `translate`, `retranslate_segment`, `render`, `replace_preview`, `replace_apply`, `glossary_term` (POST) | `_: str = Depends(auth.owner_bearer)` |
| `preview`, `asset`, `source_html`, `view`, `download` (GET nav) | `_: str = Depends(auth.owner_any)` |
| `serve_docx`, `onlyoffice_callback` | **unchanged** (OnlyOffice JWT) |
| `health`, `glossary_template`, `root` | **unchanged** (public) |

Example edits (representative — apply the same shape to every row above):
```python
# JSON GET — was: def status(doc_id: str, lang: str | None = None):
def status(doc_id: str, lang: str | None = None, _: str = Depends(auth.owner_bearer)):

# mutating POST — was: def render(doc_id: str, lang: str):
def render(doc_id: str, lang: str, _: str = Depends(auth.owner_bearer)):

# nav GET — was: def download(doc_id: str, lang: str | None = None):
def download(doc_id: str, lang: str | None = None, _: str = Depends(auth.owner_any)):

# nav GET with extra path param — was: def asset(doc_id: str, name: str):
def asset(doc_id: str, name: str, _: str = Depends(auth.owner_any)):

# nav GET — was: def source_html(doc_id: str, path: str):
def source_html(doc_id: str, path: str, _: str = Depends(auth.owner_any)):
```
For POST endpoints that take a Pydantic body (`retranslate_segment(doc_id, body)`, `replace_preview(doc_id, body, lang)`, `replace_apply(...)`, `glossary_term(doc_id, body, lang)`, `translate(doc_id, target_lang=Form(...))`), append the `_: str = Depends(auth.owner_bearer)` parameter **after** the existing params (keep `Form(...)`/body params before it). Example:
```python
def glossary_term(doc_id: str, body: GlossaryTermBody, lang: str, _: str = Depends(auth.owner_bearer)):
```

- [ ] **Step 3: Upload — insert the ownership row.** In `upload(...)`, add the `uid` dependency param and, right before the final `return`, insert the row (best-effort; the title is the uploaded filename stem):
```python
    title = os.path.splitext(os.path.basename(filename))[0] or doc_id
    documents_store.create(doc_id, uid, title, source_lang, target_lang)

    jobs.start(doc_id, source_lang, target_lang)
    threading.Thread(...).start()   # unchanged
    return { ... }                  # unchanged
```
(Place `documents_store.create(...)` BEFORE starting the job/returning, so the row exists when the client immediately navigates. If `create` raises, the upload should 500 — ownership is required, so do NOT swallow it here.)

- [ ] **Step 4: Job status → table.** In `_run_job(...)`, set terminal status (wrap so a status-write failure never crashes the job):
```python
        jobs.finish(doc_id)
        try:
            documents_store.set_status(doc_id, "done")
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        jobs.fail(doc_id, f"Processing failed: {e}")
        try:
            documents_store.set_status(doc_id, "error")
        except Exception:  # noqa: BLE001
            pass
```

- [ ] **Step 5: Run the FULL backend suite — expect PASS.** The autouse conftest bypass (Task 2) overrides the new deps and stubs `documents_store`, so existing `test_*_api.py` pass unchanged.

`cd backend && .venv/Scripts/python -m pytest -q`

- [ ] **Step 6: Add an integration check** — append to `backend/tests/test_auth.py` a test that an endpoint is actually wired (clears the bypass, asserts 401 without a token). Use FastAPI's `TestClient`:
```python
def test_endpoint_requires_auth():
    from fastapi.testclient import TestClient
    import main
    main.app.dependency_overrides.clear()  # undo autouse bypass for this test
    client = TestClient(main.app)
    r = client.get("/api/documents/whatever/status?lang=en")
    assert r.status_code == 401
```
Run `tests/test_auth.py -q` → PASS. (The autouse fixture re-applies for other tests.)

- [ ] **Step 7: Commit**
```bash
git add backend/main.py backend/tests/test_auth.py
git commit -m "feat(backend): require Supabase auth + ownership on document endpoints"
```

---

## Task 4: Frontend — attach the bearer token

**Files:** Modify `frontend/src/lib/api.ts`.

- [ ] **Step 1: Add `authedFetch`** at the top of `api.ts` (after the imports), and import the browser client:
```ts
import { createClient } from "@/lib/supabase/client";

// Attach the Supabase access token so the FastAPI backend can authorize the request.
async function authedFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const { data } = await createClient().auth.getSession();
  const token = data.session?.access_token;
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(input, { ...init, headers });
}
```

- [ ] **Step 2: Route JSON/mutating calls through `authedFetch`.** In `api.ts`, replace `fetch(` with `authedFetch(` in: `upload`, `detectLanguage`, `status`, `result`, `editorConfig`, `segments`, `retranslateSegment`, `render`, `replacePreview`, `replaceApply`, `glossaryReport`, `saveGlossaryTerm`. **Leave unchanged:** `health` (public), and the URL builders `previewUrl` / `downloadUrl` (used as iframe/anchor — they authenticate via the cookie). `detectLanguage` and `upload` send `FormData` — `authedFetch` preserves the body; do NOT set Content-Type (the browser sets the multipart boundary).

- [ ] **Step 3: Verify** — `cd frontend && npm run lint && NEXT_PUBLIC_SUPABASE_URL=https://placeholder.supabase.co NEXT_PUBLIC_SUPABASE_ANON_KEY=placeholder-anon-key BACKEND_URL=http://127.0.0.1:8001 npm run build`. Lint = 3 known pre-existing errors; build succeeds.

- [ ] **Step 4: Commit**
```bash
git add "frontend/src/lib/api.ts"
git commit -m "feat(web): attach Supabase bearer token to backend API calls"
```

---

## Task 5: Frontend — 내 문서 dashboard

**Files:** Create `frontend/src/components/MyDocuments.tsx`; modify `frontend/src/app/app/page.tsx`.

- [ ] **Step 1: Create `frontend/src/components/MyDocuments.tsx`**

```tsx
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { createClient } from "@/lib/supabase/client";
import { LANGS } from "@/lib/api";

type DocRow = {
  id: string;
  title: string | null;
  status: string;
  target_lang: string | null;
  created_at: string;
};

// Reads the signed-in user's documents straight from Supabase (RLS returns only own rows).
export function MyDocuments() {
  const [docs, setDocs] = useState<DocRow[] | null>(null);

  useEffect(() => {
    const supabase = createClient();
    supabase
      .from("documents")
      .select("id,title,status,target_lang,created_at")
      .order("created_at", { ascending: false })
      .then(({ data }) => setDocs((data as DocRow[]) ?? []));
  }, []);

  if (docs === null) return <p className="slug mt-8 px-1">불러오는 중…</p>;

  return (
    <section className="mt-12">
      <h2 className="label mb-4 px-1">내 문서</h2>
      {docs.length === 0 ? (
        <p className="text-text-2" style={{ fontSize: "var(--fs-body)" }}>
          아직 문서가 없어요 — 위에서 올려보세요.
        </p>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2">
          {docs.map((d) => (
            <li key={d.id}>
              <Link
                href={`/c/${d.id}?lang=${encodeURIComponent(d.target_lang ?? "en")}`}
                className="ticket flex items-center justify-between gap-3 p-4 transition-transform hover:-translate-y-0.5"
              >
                <span className="min-w-0">
                  <span className="block truncate font-semibold">{d.title || d.id}</span>
                  <span className="slug">
                    {LANGS[d.target_lang ?? ""] ?? d.target_lang} ·{" "}
                    {new Date(d.created_at).toLocaleDateString("ko-KR")}
                  </span>
                </span>
                <span
                  className="slug shrink-0"
                  style={{ color: d.status === "done" ? "var(--ok)" : "var(--text-3)" }}
                >
                  {d.status === "done" ? "완료" : d.status === "error" ? "오류" : "변환 중"}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
```

- [ ] **Step 2: Render it on `/app`.** In `frontend/src/app/app/page.tsx`: add `import { MyDocuments } from "@/components/MyDocuments";` (after the `Logo`/`AuthMenu` imports), and place `<MyDocuments />` inside the centered `<section className="reveal reveal-2 ...">`, immediately AFTER the closing `</div>` of the `.ticket` upload card (and before the error `<p>`/section close). Widen the section so the grid breathes: change its `max-w-2xl` to `max-w-3xl`.

- [ ] **Step 3: Verify** — `cd frontend && npm run lint && NEXT_PUBLIC_SUPABASE_URL=https://placeholder.supabase.co NEXT_PUBLIC_SUPABASE_ANON_KEY=placeholder-anon-key BACKEND_URL=http://127.0.0.1:8001 npm run build`. (With placeholder env the query returns nothing — the empty state renders; that's fine for build.)

- [ ] **Step 4: Commit**
```bash
git add "frontend/src/components/MyDocuments.tsx" "frontend/src/app/app/page.tsx"
git commit -m "feat(web): 내 문서 dashboard on /app (Supabase RLS list)"
```

---

## Task 6: Wipe storage + setup handoff + verification

**Files:** delete `backend/storage/*` contents (not the dir).

- [ ] **Step 1: Wipe existing docs** (clean slate per the spec):
```bash
# from repo root — remove all current doc folders, keep the storage dir
find backend/storage -mindepth 1 -maxdepth 1 -exec rm -rf {} +   # PowerShell: Get-ChildItem backend\storage | Remove-Item -Recurse -Force
```
Commit only if storage is tracked (it likely is gitignored — if `git status` shows nothing, skip the commit).

- [ ] **Step 2: Full backend suite + frontend build** one more time. Backend: `cd backend && .venv/Scripts/python -m pytest -q` (all green). Frontend: lint + build (above).

- [ ] **Step 3: Hand off the live setup to the user** (agents can't write `.env`; SQL is a dashboard action):
  1. **`documents` table + RLS** — run in the Supabase SQL editor:
     ```sql
     create table public.documents (
       id text primary key,
       owner_id uuid not null references auth.users(id) on delete cascade,
       title text, source_lang text, target_lang text,
       status text not null default 'processing',
       created_at timestamptz not null default now()
     );
     alter table public.documents enable row level security;
     create policy "owner can read own documents" on public.documents
       for select using (auth.uid() = owner_id);
     create index documents_owner_created_idx on public.documents (owner_id, created_at desc);
     ```
  2. **`backend/.env`** — add `SUPABASE_JWT_SECRET` (Settings→API→JWT Secret), `SUPABASE_URL` (Project URL), `SUPABASE_SERVICE_ROLE_KEY` (Settings→API→service_role secret).
  3. Restart backend (`:8001`); rebuild+restart frontend (`:3000` with the real `.env.local`).

- [ ] **Step 4: Cross-user E2E (user-run, two accounts):** Account A uploads → doc appears under A's 내 문서, opens fine. Account B cannot open A's doc URL (`/c/<A-doc>` → backend 404 on result/segments; download/preview/assets 404 via cookie). B's 내 문서 is empty. Sign-out → `/app` gated.

---

## Self-Review (plan vs spec)

**Spec coverage:** §4 table+RLS → Task 6 SQL; §5 JWT verify (bearer+cookie, HS256) → Task 2 `auth.py`; §6 endpoint tiers → Task 3 map (JSON=owner_bearer, nav=owner_any, DS unchanged, public unchanged); §6 require_owner→404 → Task 2; §7 upload insert + job status → Task 3 Steps 3-4; §8 service-role store → Task 1; §9 frontend bearer + dashboard → Tasks 4-5; §10 wipe → Task 6; §11 setup → Task 6 Step 3; §12 verification → Tasks' tests + Task 6 E2E. ✓

**Placeholder scan:** none. Every code step is complete. The `.env`/SQL are user actions (agents can't write `.env`) — surfaced as Task 6, not gaps.

**Type/name consistency:** `documents_store.{create,get_owner,set_status}` signatures match between Task 1 def, the conftest stub (Task 2), and the callers in `main.py` (Task 3). `auth.{current_user,current_user_bearer,owner_bearer,owner_any,require_owner,_verify_jwt,_token_from_cookies}` names match between `auth.py` (Task 2 Step 3), the tests (Task 2 Step 1), and the `Depends(...)` in `main.py` (Task 3). The `documents` columns (id, owner_id, title, source_lang, target_lang, status, created_at) match across the SQL (Task 6), `create()` (Task 1), and the frontend `DocRow` select (Task 5). `authedFetch` (Task 4) returns `Response` so the existing `asJson(...)` chain is unchanged.

---

## Execution Handoff

Implement with **superpowers:subagent-driven-development** (fresh subagent per task + two-stage review) or **superpowers:executing-plans**. Tasks are sequential: Task 1 → 2 (conftest references auth) → 3 (wiring relies on the bypass) → 4 → 5 → 6.
