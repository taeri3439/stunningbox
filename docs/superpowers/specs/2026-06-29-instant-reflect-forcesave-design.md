# Instant-Reflect on View-Switch (OnlyOffice force-save gate) — Design

**Date:** 2026-06-29
**Status:** Approved (design)
**Builds on:** `2026-06-27-onlyoffice-to-model-sync-design.md` (Phase 1 text-sync, branch `editor-model-sync-2026-06-27`)
**This is feature (a):** make editor edits reflect *instantly* in the tool views (재번역/용어/고유명사) when leaving 문서 편집, instead of the current ~5–13s asynchronous poll. Feature (b) — extending sync to 목차/표/구조 (Phase 2) — is specced separately.

## Problem

Today, editing 본문 텍스트 in the OnlyOffice editor (문서 편집) and clicking a tool view (재번역/용어/고유명사) shows **stale content first**, then auto-refreshes a few seconds later when a background poll detects the sync. Root cause:

1. The DS only writes our `output.{lang}.docx` on **disconnect** (editor unmount → status-2 callback), which is multi-second.
2. The mode switches **immediately**, so the target view mounts and renders the old model before the sync lands.
3. A post-edit poll ([page.tsx:159-188](../../../frontend/src/app/c/[id]/page.tsx#L159-L188)) re-fetches segments for ~13.5s (1.5s + 6×2s, `tries < 7`) and refreshes when the translated-text signature flips — i.e. it *reacts after* the stale render.

The user approved **Approach 1**: on the edit→tool transition, force an immediate save, block the target view behind a "변경사항 반영 중…" overlay until the model is synced, then reveal the view already showing the edit. The user explicitly OK'd a save step before switching ("not mandatory, but fine if it's fastest") — so the save is automatic/invisible, not a button.

## Scope

**In scope:** the edit→tool transition triggered by the **four header buttons** ([page.tsx:279-303](../../../frontend/src/app/c/[id]/page.tsx#L279-L303)). Body-text edits (Heading/Paragraph/ListItem/Caption inside `blk{i}` bookmarks — the existing Phase 1 sync surface) reflect within ~1–3s.

**Out of scope (unchanged):** 목차(TOC)/표/그림/머리말/표지/구조 변경 — these do not change the segment signature, so the gate **times out (~6s) and proceeds** (content unchanged, as today). Feature (b)/Phase 2 addresses them. Browser-close/navigate-away saving (DS disconnect autosave) is unchanged. Tool→edit and tool→tool transitions are unchanged (no gate).

## Key facts (from codebase + DS contract mapping)

- **Backend already syncs on force-save.** The callback branches status `2` and `6` together ([main.py:676](../../../backend/main.py#L676)): both download the docx, overwrite `output.{lang}.docx`, then call `_sync_model_from_saved_docx` ([main.py:686](../../../backend/main.py#L686), impl [main.py:84-111](../../../backend/main.py#L84-L111)) which writes `translated.{lang}.json` — the single live model all tool views read. **No backend sync changes are needed**; a force-save's callback already updates the model.
- **DS `forcesave` is asynchronous.** `POST {DS}/coauthoring/CommandService.ashx` with `{c:'forcesave', key, token}` returns `{key, error}` where `error:0` means **accepted, not yet saved**. The actual save arrives via the status-6 callback. So the frontend must still wait for the sync to land (poll the segment signature) — the force-save just makes that save fire *now* (~1–2s) instead of on disconnect (~5s).
- **DS error codes:** `0` ok · `1` key missing / no live session with that key · `2` bad callback url · `3` internal · `4` no changes since last save (benign no-op) · `5` bad command · `6` invalid token. (Code `7` does not exist for CommandService — it is a *callback status*.)
- **Key must match the live session.** `document_key` ([onlyoffice.py:60-69](../../../backend/onlyoffice.py#L60-L69)) hashes path+mtime+size. The editor's session key is fixed at open; recomputing after the file changed yields a different key → `error:1`. Mitigation below (persist the issued key).
- **No central mode handler.** `mode` is one state var ([page.tsx:45](../../../frontend/src/app/c/[id]/page.tsx#L45)), set by 6 inline `setMode` calls. Only the header buttons (279-303) produce edit→tool transitions; the dock buttons (540-567) never originate from edit mode. The 재번역/용어/고유명사 header buttons are **toggles** (clicking the active tool returns to "edit") — the gate must fire only when `mode === "edit" && target !== "edit"`.
- **Editor save trigger.** `connector` is `null` on the Community build (no `createConnector`) — there is no client-side programmatic save. Force-save must be a **server-side** CommandService call; the on-disk docx is the source of truth.
- **Overlay mount point.** The main translation column wrapper ([page.tsx:511](../../../frontend/src/app/c/[id]/page.tsx#L511)) is already `relative`; an absolutely-positioned overlay (z above the editor's z-5 tab mask) covers both the editor and the target preview.

## Architecture & flow

```
User clicks 재번역/용어/고유명사 header button while mode === "edit"
        │
        ▼
requestMode(target)                       ── new async gate in page.tsx
  1. setSyncing(true)                      ── "변경사항 반영 중…" overlay over main column
  2. baselineSig = current segments signature
  3. await api.forcesave(docId, lang)      ── POST /api/documents/{id}/forcesave?lang=
        │
        ├─ error 0 (changes) → poll api.segments every ~700ms until
        │                       signature !== baselineSig, max ~6s
        ├─ error 4 (no changes) → proceed immediately
        └─ error 1 / network / DS down → fallback: proceed immediately
                                          (plain switch → disconnect-save → existing poll catches up)
  4. setMode(target); bump manageKey + previewReloadKey; setSyncing(false)
        ▼
Target view mounts already showing the synced edit (no stale flash)
```

Backend force-save path:

```
POST /api/documents/{id}/forcesave?lang=  (owner_bearer)
  → out = output_docx_path(STORAGE, id, eff_lang)        (NO re-render — must not change mtime)
  → key = read storage/<id>/onlyoffice.{lang}.key        (the key editor_config issued; fallback: document_key(out))
  → onlyoffice.forcesave(out, key)
        → cmd = {c:'forcesave', key}; cmd['token'] = sign(cmd) if secret
        → httpx.post(DS_INTERNAL_URL + '/coauthoring/CommandService.ashx', json=cmd)
        → return r.json()   # {key, error}
  → return {ok: error in (0, 4), error: <code>}
        ▼
DS force-saves the live session → POSTs status-6 callback →
  existing handler downloads docx + _sync_model_from_saved_docx → translated.{lang}.json updated
```

## Backend changes

### 1. `backend/onlyoffice.py`
- Add `import httpx` (next to `import jwt`).
- Add helper at end of file:
  ```python
  def forcesave(docx_path: str, key: str | None = None) -> dict:
      """Tell the DS to immediately save the live editing session for this doc (status-6
      callback), without closing it. `key` MUST match the key the editor opened with; if
      None, recompute from the file (correct only while the on-disk file is unchanged since
      open). Returns the DS CommandService response {"key", "error"}; error 0 = accepted,
      4 = no unsaved changes, 1 = no live session for key, 6 = invalid token."""
      cmd: dict = {"c": "forcesave", "key": key or document_key(docx_path)}
      token = sign(cmd)
      if token:
          cmd["token"] = token
      url = DS_INTERNAL_URL.rstrip("/") + "/coauthoring/CommandService.ashx"
      r = httpx.post(url, json=cmd, timeout=30)
      r.raise_for_status()
      return r.json()
  ```

### 2. `backend/main.py`
- **Persist the issued key** in `editor_config` (route at ~[main.py:636-649](../../../backend/main.py#L636-L649)): after computing the config, write the issued `document.key` to `storage/<id>/onlyoffice.{eff_lang}.key`. (Best-effort; wrap in try/except.)
- **New route** adjacent to `render()` ([main.py:389-403](../../../backend/main.py#L389-L403)):
  ```python
  @app.post("/api/documents/{doc_id}/forcesave")
  def forcesave(doc_id: str, lang: str | None = None, _: str = Depends(auth.owner_bearer)):
      if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
          raise HTTPException(status_code=404, detail="document not found")
      doc = process.get_export_model(STORAGE, doc_id, lang)
      eff_lang = lang or doc.source_lang
      out = process.output_docx_path(STORAGE, doc_id, eff_lang)
      key = _read_issued_key(doc_id, eff_lang)          # stored key, else None → helper recomputes
      try:
          res = onlyoffice.forcesave(out, key)
      except Exception:
          return {"ok": False, "error": -1}             # DS unreachable → frontend falls back
      err = res.get("error", -1)
      return {"ok": err in (0, 4), "error": err}
  ```
  `_read_issued_key(doc_id, lang)` reads `storage/<id>/onlyoffice.{lang}.key` (returns `None` if absent).

### 3. No change to the callback or sync
Status-6 callbacks already flow through download + `_sync_model_from_saved_docx`. The lazy-sync-on-read ([process.py:208-227](../../../backend/pipeline/process.py#L208-L227)) remains the safety net.

## Frontend changes

### 1. `frontend/src/lib/api.ts`
Add after `render` (~[api.ts:177](../../../frontend/src/lib/api.ts#L177)), mirroring it:
```ts
forcesave: (docId: string, lang: string) =>
  authedFetch(`/api/documents/${docId}/forcesave?lang=${encodeURIComponent(lang)}`, { method: "POST" })
    .then((r) => asJson<{ ok: boolean; error: number }>(r)),
```

### 2. `frontend/src/app/c/[id]/page.tsx`
- New `syncing` state + a `requestMode(target)` async function:
  - Guard: if `mode !== "edit" || target === "edit"` → just `setMode(target)` (no gate; preserves toggle-to-edit and tool→tool).
  - Else: `setSyncing(true)`; capture `baseline = JSON.stringify(segments.map(s => s.translated))`; `const res = await api.forcesave(doc.doc_id, lang)`.
    - If `res.ok && res.error === 0` → poll `api.segments` every ~700ms (cap ~6s / ~8 tries); on each tick `setSegments(r.segments)`; stop when `JSON.stringify(r.segments.map(s=>s.translated)) !== baseline`.
    - If `res.error === 4` or `!res.ok` (1/-1/network) → skip the wait.
  - Finally: `setMode(target)`; `setManageKey(k=>k+1)`; `setPreviewReloadKey(k=>k+1)`; `setSyncing(false)`.
- Wrap the **four header buttons** (279-303) to call `requestMode(target)` instead of inline `setMode`. The toggle buttons compute their target (`mode === X ? "edit" : X`) then call `requestMode(target)`.
- **Overlay**: inside the relative main-column wrapper ([page.tsx:511](../../../frontend/src/app/c/[id]/page.tsx#L511)), render `{syncing && <SyncOverlay/>}` — an absolutely-positioned, semi-opaque cover with a spinner + "변경사항 반영 중…", z above the editor's z-5 mask. Reuse existing design tokens (`--surface`, `--primary`, `.mono`/spinner pattern already in the codebase if present).
- **Keep the existing post-edit poll** (159-188) unchanged as the fallback safety net — it harmlessly no-ops on the happy path (content already fresh) and still catches the disconnect-save sync when force-save fell back.

## Edge cases & guarantees

- **Never hangs:** every branch (error 0 with flip, error 4, error 1/-1, network throw, timeout) ends in `setMode + setSyncing(false)`. Max overlay ≈ 6s.
- **No edits:** `error:4` → instant switch, ~0s overlay.
- **Out-of-scope edits only** (table/figure/TOC/structure): `error:0` but signature never flips → ~6s overlay then switch (content unchanged — the known Phase-1 limitation; (b) fixes it). Acceptable.
- **DS down / force-save unreliable:** graceful fallback to today's behavior (plain switch + existing poll). No regression.
- **Key correctness:** uses the key `editor_config` issued (persisted), so it matches the live session even if an intervening save changed the on-disk file. Recompute is the fallback.
- **Idempotent double-save:** after the gate switches mode, the editor unmounts → a disconnect status-2 callback re-downloads + re-syncs the same edits (no-op on `translated.json`). Harmless; minor extra I/O.
- **Best-effort sync:** consistent with existing behavior — a silently-failed sync still leaves the edit in `output.{lang}.docx` for the lazy-sync to pick up on a later read.

## Testing

**Backend (pytest, mirroring existing `MockTranslator`-style tests):**
- `onlyoffice.forcesave` builds the correct command: `c == "forcesave"`, `key` passed through, `token` present when a secret is set and absent when not; POSTs to `…/coauthoring/CommandService.ashx`. (Monkeypatch `httpx.post` to capture the request + return a canned `{error:0}`; assert the payload + that the signed token decodes to the command.)
- The route resolves `eff_lang` and `output_docx_path` correctly, returns `{ok:true,error:0}` on DS `error:0`, `{ok:true,error:4}` on `4`, `{ok:false,...}` on `1`/exception; 404 on unknown doc; owner_bearer enforced.
- `editor_config` writes `onlyoffice.{lang}.key`; `_read_issued_key` round-trips it and returns `None` when absent.

**Frontend (manual + any existing component test harness):**
- The gate fires only on edit→tool (header buttons); toggle-to-edit and dock/tool→tool do **not** show the overlay.
- The overlay covers the column and clears on completion; a forced timeout path still switches.

**Live browser (the user's real flow, prod build on a fresh port — HMR broken per memory):**
- Edit a 본문 문단 in 문서 편집 → click 재번역 → overlay "변경사항 반영 중…" → within ~1–3s the view reveals **already showing** the edit (no stale flash). Repeat for 용어/고유명사.
- No-edit switch → near-instant (error 4).
- Confirm tool→edit and tool→tool remain instant (no overlay).

## Notes / risks

- `DS_INTERNAL_URL` default `http://localhost:8080` is env-configurable (`ONLYOFFICE_DS_INTERNAL_URL`) — do not hardcode; production/clustered DS may need `?shardkey=<key>` (not needed for the single-node dev/prod here, note for the future).
- The force-save route must **not** call `_ensure_docx`/render (would change the file mtime → key mismatch → `error:1`).
- First implementation step should **verify force-save works against a live session** (sign via the backend's own `onlyoffice.sign` — no secret handling in scripts) before building the gate UX on top; if it proves unreliable in this deployment, the fallback (plain switch + poll) already preserves correctness.
