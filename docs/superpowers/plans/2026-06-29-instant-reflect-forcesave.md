# Instant-Reflect Force-Save Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When leaving 문서 편집 (OnlyOffice) for a tool view (재번역/용어/고유명사), force-save the live editor session and block the target view behind a "변경사항 반영 중…" overlay until the model is synced — so the view opens already showing the edit, instead of stale-then-refresh after a multi-second poll.

**Architecture:** A new backend `onlyoffice.forcesave()` helper issues a DS CommandService `forcesave` command; a `POST /api/documents/{id}/forcesave` route exposes it (auth-gated). The DS's resulting status-6 callback already downloads the docx + syncs the model (`_sync_model_from_saved_docx`) — no callback/sync changes needed. The frontend gates the four header buttons' edit→tool transitions through an async `requestMode()` that force-saves, polls the segment signature until the sync lands (bounded ~6s), then switches and reveals. Every branch falls through to a plain switch so it never hangs; the existing post-edit poll remains the fallback.

**Tech Stack:** FastAPI + httpx + PyJWT (backend), Next.js 16 / React / Tailwind v4 (frontend), OnlyOffice Document Server 9.4.0 Community.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-06-29-instant-reflect-forcesave-design.md`. Builds on Phase 1 (branch `editor-model-sync-2026-06-27`).
- **DS endpoint:** `POST {DS_INTERNAL_URL}/coauthoring/CommandService.ashx` (the modern `/command` path 404s on this 9.4.0 build). Use `onlyoffice.DS_INTERNAL_URL` (backend→DS), never `PUBLIC_URL`/`BACKEND_URL`.
- **DS forcesave error codes:** `0` ok · `1` no live session for key · `2` bad callback url · `3` internal · `4` no unsaved changes (benign) · `5` bad command · `6` invalid token. Treat `0` and `4` as success for switching; anything else → client falls back.
- **Force-save is async:** `error:0` = accepted, not saved. The save lands via the status-6 callback; the frontend must still wait for the model to sync (segment-signature flip).
- **Key must match the live session.** Use the key `editor_config` issued (persisted), not a recompute, to avoid `error:1`. The route must **NOT** re-render/`_ensure_docx` (that would change mtime → key mismatch).
- **Scope:** body-text edits only (existing Phase 1 surface). TOC/table/figure/structure don't flip the signature → gate times out (~6s) and switches (unchanged content). That is (b)/Phase 2, not this plan.
- **Never block forever / never break the save:** all backend sync is best-effort (existing pattern); all frontend branches end in `setMode(target)` + `setSyncing(false)`.
- **Security (standing):** do not stage `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`, scratch `backend/_*.py`, `test-pdfs/`, or root images. Stage only the files each task names. Do not print/read secrets.
- **Run backend tests:** `cd backend && python -m pytest tests/<file> -v` (the repo's venv is `backend/.venv`; use `.venv/Scripts/python -m pytest` if `python` is not the venv).

---

### Task 1: Backend `onlyoffice.forcesave()` helper

**Files:**
- Modify: `backend/onlyoffice.py` (add `import httpx`; add `forcesave()` at end of file, after `verify_callback`)
- Test: `backend/tests/test_onlyoffice_forcesave.py` (create)

**Interfaces:**
- Consumes: existing `onlyoffice.sign()`, `onlyoffice.document_key()`, `onlyoffice.DS_INTERNAL_URL`, `onlyoffice.JWT_SECRET`.
- Produces: `forcesave(docx_path: str, key: str | None = None) -> dict` — POSTs the DS `forcesave` command, returns the parsed `{"key","error"}` response.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_onlyoffice_forcesave.py`:

```python
import jwt
import onlyoffice


class _FakeResp:
    def __init__(self, payload):
        self._p = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._p


def test_forcesave_builds_signed_command(monkeypatch):
    monkeypatch.setattr(onlyoffice, "JWT_SECRET", "testsecret")
    monkeypatch.setattr(onlyoffice, "DS_INTERNAL_URL", "http://ds:8080")
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp({"key": json["key"], "error": 0})

    monkeypatch.setattr(onlyoffice.httpx, "post", fake_post)

    res = onlyoffice.forcesave("/x/output.en.docx", key="abc123")

    assert res == {"key": "abc123", "error": 0}
    assert captured["url"] == "http://ds:8080/coauthoring/CommandService.ashx"
    assert captured["json"]["c"] == "forcesave"
    assert captured["json"]["key"] == "abc123"
    decoded = jwt.decode(captured["json"]["token"], "testsecret", algorithms=["HS256"])
    assert decoded["c"] == "forcesave" and decoded["key"] == "abc123"


def test_forcesave_recomputes_key_when_none_and_omits_token_without_secret(monkeypatch, tmp_path):
    monkeypatch.setattr(onlyoffice, "JWT_SECRET", "")  # JWT disabled
    p = tmp_path / "output.en.docx"
    p.write_bytes(b"docxbytes")
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["json"] = json
        return _FakeResp({"key": json["key"], "error": 4})

    monkeypatch.setattr(onlyoffice.httpx, "post", fake_post)

    res = onlyoffice.forcesave(str(p), key=None)

    assert captured["json"]["key"] == onlyoffice.document_key(str(p))
    assert "token" not in captured["json"]  # secret unset → unsigned command
    assert res["error"] == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_onlyoffice_forcesave.py -v`
Expected: FAIL — `AttributeError: module 'onlyoffice' has no attribute 'forcesave'` (and `httpx` not imported in onlyoffice).

- [ ] **Step 3: Implement the helper**

In `backend/onlyoffice.py`, add `import httpx` next to `import jwt` (near line 20):

```python
import httpx
import jwt
```

Append at the end of the file (after `verify_callback`):

```python
def forcesave(docx_path: str, key: str | None = None) -> dict:
    """Tell the Document Server to immediately save the live editing session for this doc
    (without closing it) — it responds, then POSTs a status-6 callback that we download +
    sync. `key` MUST match the key the editor opened with (persisted by the editor-config
    route); if None we recompute from the file, which is correct only while the on-disk file
    is unchanged since open. Returns the CommandService response {"key", "error"}:
    0 = accepted, 4 = no unsaved changes, 1 = no live session for key, 6 = invalid token."""
    cmd: dict = {"c": "forcesave", "key": key or document_key(docx_path)}
    token = sign(cmd)
    if token:
        cmd["token"] = token
    url = DS_INTERNAL_URL.rstrip("/") + "/coauthoring/CommandService.ashx"
    r = httpx.post(url, json=cmd, timeout=30)
    r.raise_for_status()
    return r.json()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_onlyoffice_forcesave.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/onlyoffice.py backend/tests/test_onlyoffice_forcesave.py
git commit -m "feat(sync): onlyoffice.forcesave() — DS CommandService force-save helper"
```

---

### Task 2: Persist the issued document key (editor-config) + read helper

**Files:**
- Modify: `backend/main.py` (add `_issued_key_path`, `_write_issued_key`, `_read_issued_key` near `_ensure_docx`/`_sync_model_from_saved_docx` ~line 82; call `_write_issued_key` in `editor_config` ~line 648)
- Test: `backend/tests/test_issued_key.py` (create)

**Interfaces:**
- Consumes: `process.doc_dir(STORAGE, doc_id)`; `editor_config` already computes `config["document"]["key"]` via `onlyoffice.build_editor_config`.
- Produces: `_read_issued_key(doc_id: str, lang: str) -> str | None` (used by Task 3); `_write_issued_key(doc_id: str, lang: str, key: str) -> None`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_issued_key.py`:

```python
import os
import main
from pipeline import process


def test_issued_key_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    doc_id, lang = "dkey", "en"
    os.makedirs(process.doc_dir(str(tmp_path), doc_id), exist_ok=True)
    assert main._read_issued_key(doc_id, lang) is None        # absent → None
    main._write_issued_key(doc_id, lang, "abc123")
    assert main._read_issued_key(doc_id, lang) == "abc123"


def test_issued_key_missing_dir_is_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    main._write_issued_key("ghost", "en", "k")               # no doc dir → best-effort no-op
    assert main._read_issued_key("ghost", "en") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_issued_key.py -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute '_read_issued_key'`.

- [ ] **Step 3: Implement the helpers + wire into editor_config**

In `backend/main.py`, add after `_sync_model_from_saved_docx` (after line ~111):

```python
def _issued_key_path(doc_id: str, lang: str) -> str:
    return os.path.join(process.doc_dir(STORAGE, doc_id), f"onlyoffice.{lang}.key")


def _write_issued_key(doc_id: str, lang: str, key: str) -> None:
    """Remember the document key the editor opened with, so a later force-save targets the
    same live DS session. document_key() recomputed after the file changed would mismatch
    (→ CommandService error 1). Best-effort — never break editor open."""
    try:
        with open(_issued_key_path(doc_id, lang), "w", encoding="utf-8") as fh:
            fh.write(key)
    except OSError:
        pass


def _read_issued_key(doc_id: str, lang: str) -> str | None:
    try:
        with open(_issued_key_path(doc_id, lang), encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None
```

In `editor_config` (route ~line 636-649), persist the key right after building the config:

```python
    config = onlyoffice.build_editor_config(
        doc_id, eff_lang, doc.title, docx_path, bookmark=bookmark
    )
    _write_issued_key(doc_id, eff_lang, config["document"]["key"])
    return {"document_server_url": onlyoffice.PUBLIC_URL, "config": config}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_issued_key.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_issued_key.py
git commit -m "feat(sync): persist the editor's issued document key for force-save targeting"
```

---

### Task 3: `POST /api/documents/{id}/forcesave` route

**Files:**
- Modify: `backend/main.py` (add the route adjacent to `render()` at ~line 403)
- Test: `backend/tests/test_forcesave_route.py` (create)

**Interfaces:**
- Consumes: `onlyoffice.forcesave(out, key)` (Task 1), `_read_issued_key` (Task 2), `process.get_export_model`, `process.output_docx_path`, `process.doc_dir`, `auth.owner_bearer`.
- Produces: HTTP `POST /api/documents/{doc_id}/forcesave?lang=` → `{"ok": bool, "error": int}`. `ok` is true for DS error 0 or 4; false for any other code or an exception (DS unreachable → `error: -1`). Consumed by `api.forcesave` (Task 4).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_forcesave_route.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_forcesave_route.py -v`
Expected: FAIL — all POSTs return 404 (route not defined) / assertion errors.

- [ ] **Step 3: Implement the route**

In `backend/main.py`, add immediately after the `render()` route (after line 403):

```python
@app.post("/api/documents/{doc_id}/forcesave")
def forcesave(doc_id: str, lang: str | None = None, _: str = Depends(auth.owner_bearer)):
    """Tell the DS to save the live editing session NOW (→ status-6 callback → model sync),
    so leaving 문서 편집 for a tool view reflects the edit immediately instead of after the
    multi-second disconnect-save. Best-effort: returns {ok,error}; ok = DS accepted (0) or had
    no unsaved changes (4). The client falls back to a plain switch + poll on any other result.
    Must NOT re-render (that would change the file key and miss the live session)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    doc = process.get_export_model(STORAGE, doc_id, lang)
    eff_lang = lang or doc.source_lang
    out = process.output_docx_path(STORAGE, doc_id, eff_lang)
    key = _read_issued_key(doc_id, eff_lang)  # the key the editor opened with; None → recompute
    try:
        res = onlyoffice.forcesave(out, key)
    except Exception:  # noqa: BLE001 — DS unreachable/timeout → let the client fall back
        return {"ok": False, "error": -1}
    err = res.get("error", -1)
    return {"ok": err in (0, 4), "error": err}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_forcesave_route.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Run the backend sync-related suites to confirm no regression**

Run: `cd backend && python -m pytest tests/test_onlyoffice_forcesave.py tests/test_issued_key.py tests/test_forcesave_route.py tests/test_onlyoffice_callback_sync.py tests/test_lazy_sync.py tests/test_onlyoffice_config.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_forcesave_route.py
git commit -m "feat(sync): POST /api/documents/{id}/forcesave — on-demand DS save (best-effort)"
```

---

### Task 4: Frontend force-save gate + overlay + `api.forcesave`

**Files:**
- Modify: `frontend/src/lib/api.ts` (add `forcesave` after `render`, ~line 177)
- Modify: `frontend/src/app/c/[id]/page.tsx` (add `syncing` state ~line 45; add `requestMode` function; rewire the 4 header buttons at lines 279-303; add the overlay inside the relative wrapper at line 511)

**Interfaces:**
- Consumes: `POST /api/documents/{id}/forcesave?lang=` → `{ ok: boolean; error: number }` (Task 3); existing `api.segments`.
- Produces: no new exports — internal `requestMode(target)` gate + `syncing` UI.

- [ ] **Step 1: Add the `api.forcesave` client method**

In `frontend/src/lib/api.ts`, add immediately after the `render` method (after line 177), before `replacePreview`:

```ts
  // Force the DS to save the live editor session NOW so leaving 문서 편집 reflects the edit
  // immediately. Returns {ok} (DS accepted or nothing to save) — the caller falls back to a
  // plain switch + poll when !ok. error: DS CommandService code (0 ok, 4 no-changes, 1 no
  // session, 6 bad token, -1 DS unreachable).
  forcesave: (docId: string, lang: string) =>
    authedFetch(`/api/documents/${docId}/forcesave?lang=${encodeURIComponent(lang)}`, {
      method: "POST",
    }).then((r) => asJson<{ ok: boolean; error: number }>(r)),
```

- [ ] **Step 2: Add the `syncing` state**

In `frontend/src/app/c/[id]/page.tsx`, after the `mode` state (line 45), add:

```tsx
  // Gating an edit→tool switch: true while we force-save + wait for the model to sync, so the
  // tool view opens already showing the edit instead of stale-then-refresh.
  const [syncing, setSyncing] = useState(false);
```

- [ ] **Step 3: Add the `requestMode` gate function**

In `frontend/src/app/c/[id]/page.tsx`, add this function inside the component (e.g. just after the post-edit poll `useEffect` that ends at line 188). It captures the current render's `mode`/`segments`/`doc`/`lang`:

```tsx
  // Gate the edit→tool transition: force-save the live editor session, wait (bounded) for the
  // synced edit to land in the model, THEN switch — so 재번역/용어/고유명사 open fresh. Every
  // path ends by switching, so it never hangs; on force-save failure we switch immediately and
  // the post-edit poll (above) catches up (the prior behavior).
  async function requestMode(
    target: "edit" | "retranslate" | "manage" | "proper-nouns",
  ) {
    if (mode !== "edit" || target === "edit" || !doc) {
      setMode(target); // tool→edit, tool→tool, or no doc: no gate
      return;
    }
    setSyncing(true);
    const baseline = JSON.stringify(segments.map((s) => s.translated));
    try {
      const res = await api.forcesave(doc.doc_id, lang);
      if (res.ok && res.error === 0) {
        // accepted + had unsaved changes → wait for the status-6 callback to sync the model
        for (let i = 0; i < 8; i++) {
          await new Promise((r) => setTimeout(r, 700)); // ~5.6s max
          try {
            const seg = await api.segments(doc.doc_id, lang);
            setSegments(seg.segments);
            if (JSON.stringify(seg.segments.map((s) => s.translated)) !== baseline) break;
          } catch {
            /* keep waiting */
          }
        }
      }
      // res.error === 4 (nothing to save) or !res.ok (1 / -1 / network) → switch immediately
    } catch {
      /* forcesave threw → plain switch; the post-edit poll will catch the disconnect-save */
    } finally {
      setMode(target);
      setManageKey((k) => k + 1); // reload manage/proper-noun preview + reports
      setPreviewReloadKey((k) => k + 1); // reload the 재번역 preview iframe
      setSyncing(false);
    }
  }
```

- [ ] **Step 4: Rewire the four header buttons to go through the gate**

In `frontend/src/app/c/[id]/page.tsx`, replace the `onClick` handlers at lines 279-303:

```tsx
            <button
              className={mode === "edit" ? "btn-primary" : "btn-secondary"}
              onClick={() => requestMode("edit")}
              title="문서 편집기로 돌아가기"
            >
              문서 편집
            </button>
            <button
              className={mode === "retranslate" ? "btn-primary" : "btn-secondary"}
              onClick={() => requestMode(mode === "retranslate" ? "edit" : "retranslate")}
            >
              재번역
            </button>
            <button
              className={mode === "manage" ? "btn-primary" : "btn-secondary"}
              onClick={() => requestMode(mode === "manage" ? "edit" : "manage")}
            >
              용어
            </button>
            <button
              className={mode === "proper-nouns" ? "btn-primary" : "btn-secondary"}
              onClick={() => requestMode(mode === "proper-nouns" ? "edit" : "proper-nouns")}
            >
              고유명사
            </button>
```

(The dock-strip buttons at 540-567 are left as `setMode` — they never originate from edit mode, so they never need the gate.)

- [ ] **Step 5: Add the blocking overlay**

In `frontend/src/app/c/[id]/page.tsx`, inside the relative wrapper at line 511 (`<div className="relative flex min-h-0 flex-1 flex-col">`), add the overlay as the LAST child, after the `mode` ternary closes at line 531 (so it sits above the editor's `z-5` tab mask):

```tsx
          <div className="relative flex min-h-0 flex-1 flex-col">
            {mode === "manage" || mode === "proper-nouns" ? (
              <TranslatedPreview docId={doc.doc_id} lang={lang} reloadKey={manageKey} jumpBlock={jumpBlock} />
            ) : mode === "retranslate" ? (
              <SegmentPreview
                src={api.previewUrl(doc.doc_id, lang, { segments: true })}
                activeSeg={activeSeg?.index ?? null}
                onSelectSeg={(idx) => setActiveSeg(segments.find((s) => s.index === idx) ?? null)}
                reloadKey={previewReloadKey}
                jumpBlock={activeSeg?.block ?? null}
              />
            ) : (
              <OnlyOfficeEditor
                docId={doc.doc_id}
                lang={lang}
                onConnector={setConnector}
                reloadKey={editorReloadKey}
                bookmark={editorBookmark}
              />
            )}
            {syncing && (
              <div
                className="absolute inset-0 z-10 flex items-center justify-center gap-2"
                style={{ background: "color-mix(in srgb, var(--surface) 72%, transparent)" }}
                aria-live="polite"
              >
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent"
                      style={{ color: "var(--primary)" }} />
                <span className="text-sm" style={{ color: "var(--primary)" }}>변경사항 반영 중…</span>
              </div>
            )}
          </div>
```

- [ ] **Step 6: Type-check / build the frontend (no unit-test harness exists here)**

Run: `cd frontend && npm run build`
Expected: build succeeds with no TypeScript errors in `page.tsx` / `api.ts`. (Per memory, `npm run dev` HMR is broken here — use the prod build.)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/lib/api.ts "frontend/src/app/c/[id]/page.tsx"
git commit -m "feat(sync): instant-reflect gate — force-save + '변경사항 반영 중…' overlay on view-switch"
```

---

## Verification (controller-run live checkpoints)

These require the full stack and are run by the controller (not a subagent unit task). The DS container `onlyoffice-ds` runs on `:8080`. Start a backend on a free port with `ONLYOFFICE_*` env matching the running DS, and a frontend prod build (`next build` + `next start`, HMR broken per memory) on a free port with `DEV_NO_AUTH=1`.

### Checkpoint A — force-save works against a live session (after Task 3, BEFORE Task 4)

The spec requires confirming force-save actually saves in this deployment before building the UX on it.

- [ ] Open a doc in 문서 편집 in the browser (editor mounts → `editor_config` persists the key).
- [ ] Type a unique marker (e.g. `FSV1`) into a body paragraph; do NOT navigate away.
- [ ] `curl -s -X POST "http://127.0.0.1:<backend>/api/documents/<doc>/forcesave?lang=en"` (DEV_NO_AUTH bypasses auth) → expect `{"ok":true,"error":0}`.
- [ ] Within ~2s, confirm the status-6 callback synced the model: `curl -s ".../api/documents/<doc>/segments?lang=en"` contains `FSV1` (and `translated.en.json` updated).
- [ ] If instead `error:1` (no session) recurs even with a live editor: the persisted key isn't matching — inspect `storage/<doc>/onlyoffice.en.key` vs the editor-config key; this is the one risk to resolve before Task 4. The frontend fallback still preserves correctness, but the fast path depends on this.

### Checkpoint B — live end-to-end (after Task 4)

- [ ] In 문서 편집, edit a 본문 문단 (e.g. add `B2B`), click **재번역** → the "변경사항 반영 중…" overlay shows, and within ~1–3s the 재번역 view reveals **already showing** `B2B` (no stale flash, no manual refresh). Repeat for **용어** and **고유명사**.
- [ ] Switch with **no edit** → near-instant (DS `error:4`, overlay barely flashes).
- [ ] Confirm tool→edit (clicking the active tool button) and tool→tool (재번역→용어 from the dock) stay instant with **no** overlay.
- [ ] Negative/known-limitation: an out-of-scope edit (a TOC line or a table cell) → overlay shows ~6s then switches with content unchanged (expected; (b)/Phase 2 covers it).

---

## Self-Review

**1. Spec coverage:**
- `onlyoffice.forcesave` helper → Task 1. ✓
- Persist issued key + read → Task 2. ✓
- `POST /forcesave` route (no re-render, owner_bearer, error→ok mapping, 404) → Task 3. ✓
- `api.forcesave` + `requestMode` gate + overlay + header rewiring + keep existing poll → Task 4. ✓
- Edge cases (error 4 instant, error 1/-1 fallback, timeout ~6s, key correctness, never-hang) → Task 3 (mapping) + Task 4 (`requestMode` branches). ✓
- "Verify force-save live before the gate" → Checkpoint A. ✓ Live e2e → Checkpoint B. ✓
- No backend callback/sync change (status 6 already handled) → confirmed in spec; no task needed. ✓

**2. Placeholder scan:** none — every code step shows complete code; every command shows expected output.

**3. Type consistency:** `forcesave(docx_path, key=None) -> dict` (Task 1) is called as `onlyoffice.forcesave(out, key)` in Task 3 ✓. `_read_issued_key(doc_id, lang) -> str | None` (Task 2) used in Task 3 ✓. Route returns `{ok: bool, error: int}` (Task 3) matched by `api.forcesave`'s `{ ok: boolean; error: number }` (Task 4) ✓. `requestMode` target union matches the `mode` union in page.tsx ✓.
