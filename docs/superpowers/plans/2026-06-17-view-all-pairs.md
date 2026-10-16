# View All Sentence Pairs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user open a full, scrollable list of every 원문↔번역 pair in a reference file (before committing), from both the single-file detection card and the batch triage rows.

**Architecture:** New embedding-free backend endpoint `POST /api/style/references/pairs` returns ALL pairs for one file (reusing `analyze`'s extraction via a shared `_extract_preview` helper; sequential/paired stay positional estimates). Frontend adds `api.styleReferencePairs`, a page-owned `PairListModal`, and a "전체 N쌍 보기" button in the card + in `BatchReviewTable` rows (via an `onViewPairs` callback).

**Tech Stack:** FastAPI + pytest (backend); Next 16 / React 19 / TS (frontend, **no test runner** → verify via `next build` + browser).

**Verify commands:**
- Backend: `cd "c:/Users/danie/OneDrive/문서/바탕 화면/stunningboxreflow/stunningboxreflow/backend" && ./.venv/Scripts/python.exe -m pytest -q 2>&1 | tail -6`
- Frontend build: `cd "c:/Users/danie/OneDrive/문서/바탕 화면/stunningboxreflow/stunningboxreflow/frontend" && BACKEND_URL=http://127.0.0.1:8002 node ./node_modules/next/dist/bin/next build 2>&1 | tail -20`

**Git discipline (every commit):** `git add` ONLY the task's feature paths. NEVER `git add -A`. NEVER stage `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`, `backend/_ingest_style_refs.py`, `backend/_retry_failed_refs.py`, `backend/_verify_classify.py`, `test-pdfs/`, or `*.jpeg`/`*.png`.

---

## Task 1: Backend `pairs_preview` (refactor `analyze` to share extraction)

**Files:**
- Modify: `backend/pipeline/reference_intake.py` (the `analyze` function near the end)
- Test: `backend/tests/test_reference_intake.py`

Current `analyze` computes `(style, pairs, estimated)` inline. Extract that into `_extract_preview` and reuse it in both `analyze` (truncates to 4) and a new `pairs_preview` (returns all).

- [ ] **Step 1: Write failing tests** in `backend/tests/test_reference_intake.py`:

```python
def test_pairs_preview_interleaved_returns_all_pairs(_txt):
    p = _txt("i.txt", "방수총 정의.\nMonitor definition here.\n고정형 정의.\nFixed type definition.\n또 다른.\nAnother one here.\n")
    info = RI.analyze(p)
    full = RI.pairs_preview(p)
    assert full["mode"] == "interleaved" and full["estimated"] is False
    assert len(full["pairs"]) == info["pair_count"] >= 3      # ALL pairs, not truncated to 4
    assert full["pairs"][0] == {"source": full["pairs"][0]["source"], "target": full["pairs"][0]["target"]}
    assert set(full["pairs"][0]) == {"source", "target"}

def test_pairs_preview_source_only_is_empty(_txt):
    p = _txt("s.txt", "한국어 원문만 있는 문서입니다.\n번역이 없어요.\n")
    full = RI.pairs_preview(p)
    assert full["mode"] == "source_only" and full["pairs"] == []

def test_pairs_preview_sequential_is_estimated(_txt):
    p = _txt("q.txt", "첫 문장.\n둘째 문장.\n셋째 문장.\nFirst sentence here.\nSecond sentence here.\nThird sentence here.\n")
    full = RI.pairs_preview(p)
    assert full["mode"] == "sequential" and full["estimated"] is True
    assert len(full["pairs"]) >= 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_reference_intake.py -q`
Expected: FAIL (`pairs_preview` not defined).

- [ ] **Step 3: Refactor `analyze` + add `pairs_preview`.** Replace the body of `analyze` (the part after the `source_only` early return that computes `style, pairs, estimated`) by extracting a helper, and add `pairs_preview`. The final shape:

```python
def _extract_preview(target_path, source_path, mode):
    """(style_segments, pairs, estimated) for `mode` WITHOUT embedding. sequential/paired_files
    are positional estimates here — real alignment happens at ingest."""
    if mode in ("interleaved", "columns", "target_only"):
        style, pairs = extract_for_mode(target_path, source_path, mode)
        return style, pairs, False
    if mode == "paired_files":
        src = _style_segments(extract_lines(source_path))
        tgt = _style_segments(extract_lines(target_path))
    else:  # sequential
        tagged = _tag(extract_lines(target_path))
        src = _sentences(" ".join(t for l, t in tagged if l == "ko"))
        tgt = _sentences(" ".join(t for l, t in tagged if l == "en"))
    n = min(len(src), len(tgt))
    return tgt, [(src[i], tgt[i]) for i in range(n)], True


def analyze(target_path: str, source_path: str | None = None) -> dict:
    """Detect the format and preview what would be extracted — WITHOUT embedding (cheap).
    For alignment modes (sequential/paired) pair_count is an estimate and samples are positional;
    the real alignment runs at ingest time."""
    mode = classify(target_path, source_path)
    if mode == "source_only":
        return {"mode": mode, "style_count": 0, "pair_count": 0, "sample_pairs": [],
                "sample_style": [], "has_pairs": False, "estimated": False,
                "message": "번역 내용이 없는 파일이에요 — 레퍼런스로 쓰려면 원문과 번역이 함께 있는 파일을 올려주세요."}
    style, pairs, estimated = _extract_preview(target_path, source_path, mode)
    return {
        "mode": mode, "style_count": len(style), "pair_count": len(pairs),
        "sample_pairs": [{"source": s, "target": t} for s, t in pairs[:4]],
        "sample_style": style[:4], "has_pairs": bool(pairs), "estimated": estimated,
    }


def pairs_preview(target_path: str, source_path: str | None = None, mode: str | None = None) -> dict:
    """ALL 원문↔번역 pairs for one file, WITHOUT embedding. `mode` overrides auto-detection.
    sequential/paired_files are positional estimates (estimated=True)."""
    m = mode or classify(target_path, source_path)
    if m == "source_only":
        return {"mode": m, "estimated": False, "pairs": []}
    _, pairs, estimated = _extract_preview(target_path, source_path, m)
    return {"mode": m, "estimated": estimated,
            "pairs": [{"source": s, "target": t} for s, t in pairs]}
```
(Delete the old inline `if mode in (...) / else / n = min(...)` block in `analyze` that this replaces — `_extract_preview` now holds it. Keep everything else in the file unchanged.)

- [ ] **Step 4: Run the new tests + the full reference_intake suite (guards the refactor)**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_reference_intake.py -q`
Expected: PASS (new tests + all existing `analyze`/`classify` tests still green).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/reference_intake.py backend/tests/test_reference_intake.py
git commit -m "feat(style): reference_intake.pairs_preview (all pairs, embedding-free) + share _extract_preview with analyze"
```

---

## Task 2: Backend route `POST /api/style/references/pairs`

**Files:**
- Modify: `backend/main.py` (add the route after `style_reference_analyze`, near line 503)
- Test: `backend/tests/test_style_api.py`

- [ ] **Step 1: Write failing test** in `backend/tests/test_style_api.py` (follow the existing TestClient + multipart pattern in that file; if it has a helper to post a temp file, reuse it):

```python
def test_pairs_endpoint_returns_all_pairs(client, tmp_path):
    p = tmp_path / "i.txt"
    p.write_text("방수총 정의.\nMonitor definition here.\n고정형 정의.\nFixed type definition.\n또.\nAnother one here.\n", encoding="utf-8")
    with open(p, "rb") as fh:
        r = client.post("/api/style/references/pairs", files={"target": ("i.txt", fh, "text/plain")})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "interleaved" and body["estimated"] is False
    assert body["count"] == len(body["pairs"]) >= 3
    assert set(body["pairs"][0]) == {"source", "target"}

def test_pairs_endpoint_source_only_empty(client, tmp_path):
    p = tmp_path / "s.txt"
    p.write_text("한국어 원문만 있는 문서입니다.\n번역이 없어요.\n", encoding="utf-8")
    with open(p, "rb") as fh:
        r = client.post("/api/style/references/pairs", files={"target": ("s.txt", fh, "text/plain")})
    assert r.status_code == 200 and r.json()["count"] == 0 and r.json()["pairs"] == []
```
(Match this file's existing fixture names for the TestClient — if it's called something other than `client`, use that. The style routes run under `DEV_NO_AUTH`/test auth the same way the existing `analyze`/`references` tests in this file do.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_style_api.py -q`
Expected: FAIL (404, route not defined).

- [ ] **Step 3: Add the route** in `backend/main.py` immediately after the `style_reference_analyze` function (after its `finally: shutil.rmtree(...)`):

```python
@app.post("/api/style/references/pairs")
async def style_reference_pairs(
    target: UploadFile = File(...),
    source: UploadFile | None = File(None),
    mode: str = Form(""),
    _: str = Depends(auth.current_user_bearer),
):
    """Full list of every 원문↔번역 pair for one reference file (no storage, no embedding).
    sequential/paired are positional estimates (estimated=true)."""
    if not _style_ext_ok(target.filename) or (source and not _style_ext_ok(source.filename)):
        raise HTTPException(400, "Supported reference formats: PDF, Word, HWP, TXT/CSV")
    from pipeline import reference_intake
    tmp = tempfile.mkdtemp(prefix="stylepairs_")
    try:
        tpath, spath = await _stage_style_files(tmp, target, source)
        try:
            res = reference_intake.pairs_preview(tpath, spath, mode.strip() or None)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"Pairs preview failed: {e}")
        res["count"] = len(res["pairs"])
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
```

- [ ] **Step 4: Run tests**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_style_api.py -q` then the full suite `./.venv/Scripts/python.exe -m pytest -q`.
Expected: PASS; full suite green.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_style_api.py
git commit -m "feat(style): POST /api/style/references/pairs — full pair list endpoint"
```

---

## Task 3: Frontend API client + `PairListModal`

**Files:**
- Modify: `frontend/src/lib/api.ts` (add fn + type near the other style fns / the `StyleAnalysis` type)
- Create: `frontend/src/app/app/style/PairListModal.tsx`

- [ ] **Step 1: Add the API fn + type** in `frontend/src/lib/api.ts`. Add the fn inside the `api` object next to `analyzeStyleReference`:

```ts
  // All 원문↔번역 pairs for one reference file (no storage/embedding).
  styleReferencePairs: (args: { target: File; source?: File | null; mode?: string }) => {
    const fd = new FormData();
    fd.append("target", args.target);
    if (args.source) fd.append("source", args.source);
    if (args.mode) fd.append("mode", args.mode);
    return authedFetch("/api/style/references/pairs", { method: "POST", body: fd }).then((r) =>
      asJson<StylePairs>(r),
    );
  },
```
And add the type next to `StyleAnalysis`:
```ts
export type StylePairs = {
  mode: StyleMode;
  estimated: boolean;
  count: number;
  pairs: { source: string; target: string }[];
};
```

- [ ] **Step 2: Create `frontend/src/app/app/style/PairListModal.tsx`:**

```tsx
"use client";

import { useEffect, useState } from "react";
import { api, type StyleMode } from "@/lib/api";

export function PairListModal({
  file,
  source,
  mode,
  onClose,
}: {
  file: File;
  source?: File | null;
  mode?: "" | StyleMode;
  onClose: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pairs, setPairs] = useState<{ source: string; target: string }[]>([]);
  const [estimated, setEstimated] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api
      .styleReferencePairs({ target: file, source, mode: mode || undefined })
      .then((r) => {
        if (cancelled) return;
        setPairs(r.pairs);
        setEstimated(r.estimated);
      })
      .catch((e) => !cancelled && setError(e instanceof Error ? e.message : String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [file, source, mode]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(15,23,42,0.45)" }}
      onClick={onClose}
    >
      <div
        className="flex max-h-[80vh] w-full max-w-2xl flex-col rounded-[var(--r-lg)] border border-border shadow-xl"
        style={{ background: "var(--surface)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-bold">전체 {pairs.length}쌍</span>
            <span className="slug break-all">{file.name}</span>
            {estimated && (
              <span className="rounded-full px-2 py-0.5 text-xs font-semibold"
                title="정확한 짝은 추가(커밋) 시 정렬로 확정됩니다"
                style={{ color: "var(--primary)", background: "var(--primary-soft)" }}>추정</span>
            )}
          </div>
          <button type="button" aria-label="닫기" onClick={onClose}
            className="shrink-0 text-text-3 transition-colors hover:text-primary">✕</button>
        </div>
        <div className="flex-1 overflow-auto px-5 py-4">
          {loading && <p className="slug">불러오는 중…</p>}
          {error && <p className="slug" style={{ color: "var(--danger)" }}>불러오지 못했어요 — {error}</p>}
          {!loading && !error && pairs.length === 0 && (
            <p className="slug">표시할 문장 쌍이 없어요.</p>
          )}
          {!loading && !error && pairs.length > 0 && (
            <ol className="flex flex-col gap-2.5">
              {pairs.map((p, i) => (
                <li key={i} className="flex flex-col gap-0.5">
                  <span className="text-sm text-text-2 break-words">{p.source}</span>
                  <span className="text-sm break-words" style={{ color: "var(--primary-hover)" }}>{p.target}</span>
                </li>
              ))}
            </ol>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Build (typecheck)** — run the frontend build/verify command. Expected: clean compile (the modal is unused until Task 4 — that's fine). Fix any TS/token errors (if `--r-lg` is missing, it exists in globals.css alongside `--r-md`).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/app/app/style/PairListModal.tsx
git commit -m "feat(style): styleReferencePairs API + PairListModal component"
```

---

## Task 4: Wire the "전체 N쌍 보기" button into both surfaces

**Files:**
- Modify: `frontend/src/app/app/style/page.tsx`
- Modify: `frontend/src/app/app/style/BatchReviewTable.tsx`

- [ ] **Step 1: Page-owned modal state + import** in `page.tsx`. Add import:
```tsx
import { PairListModal } from "./PairListModal";
```
Add state (near other upload state):
```tsx
const [pairsModal, setPairsModal] = useState<{ file: File; source: File | null; mode: "" | StyleMode } | null>(null);
```

- [ ] **Step 2: Render the modal** once, near the end of the component's returned JSX (e.g. just before the closing wrapper):
```tsx
{pairsModal && (
  <PairListModal
    file={pairsModal.file}
    source={pairsModal.source}
    mode={pairsModal.mode}
    onClose={() => setPairsModal(null)}
  />
)}
```

- [ ] **Step 3: Button in the single-file detection card.** In the `!batch && analysis` card, where `analysis.sample_pairs` are listed, add — only when there are pairs — a button under the samples:
```tsx
{analysis.pair_count > 0 && (
  <button type="button"
    onClick={() => setPairsModal({ file: target!, source, mode: modeOverride })}
    className="slug self-start transition-colors hover:text-primary">
    전체 {analysis.pair_count}쌍 보기
  </button>
)}
```
(`target`, `source`, `modeOverride` are existing state in this file; `target` is non-null inside the `analysis` block. `modeOverride` is `"" | StyleMode`.)

- [ ] **Step 4: `onViewPairs` for the batch table.** Pass a new prop to `<BatchReviewTable …>`:
```tsx
onViewPairs={(id) => {
  const r = rows.find((row) => row.id === id);
  if (r) setPairsModal({ file: r.file, source: null, mode: r.modeOverride });
}}
```

- [ ] **Step 5: Add the button in `BatchReviewTable.tsx`.** Add `onViewPairs: (id: string) => void;` to the props type and destructure it. In the expanded panel (`open === r.id && a`), after the sample-pairs `<ul>`, add — only when the row has pairs:
```tsx
{a.pair_count > 0 && (
  <button type="button" onClick={() => onViewPairs(r.id)}
    className="slug self-start transition-colors hover:text-primary">
    전체 {a.pair_count}쌍 보기
  </button>
)}
```

- [ ] **Step 6: Build + browser verify**

Run the frontend build (expect clean). Then restart the prod server, open `/app/style`:
- Single file (a bilingual HWP): card shows samples + "전체 N쌍 보기" → click → modal lists ALL pairs, scrolls, ✕/Esc/backdrop close.
- 2+ files: expand a bilingual row → "전체 N쌍 보기" → modal for that file. Expand a sequential/estimated file (if available) → modal shows the "추정" badge. The source_only row has no pairs button.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/app/style/page.tsx frontend/src/app/app/style/BatchReviewTable.tsx
git commit -m "feat(style): '전체 N쌍 보기' button in detection card + batch rows, opening PairListModal"
```

---

## Task 5: Final verification

- [ ] **Step 1:** Full backend suite — `cd backend && ./.venv/Scripts/python.exe -m pytest -q` → expect all pass (previous 318 + the new pairs tests).
- [ ] **Step 2:** Frontend `next build` clean.
- [ ] **Step 3:** Controller browser walkthrough (both surfaces, estimated badge, close).
- [ ] **Step 4:** `git status --short` must still show `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts` as **unstaged**.

---

## Notes
- No new prod deps; endpoint is embedding-free (reuses extraction only).
- The "전체 N쌍 보기" button is gated on `pair_count > 0`, so `source_only`/`target_only` never open the modal.
- Do not commit local-only auth-bypass files, scratch files, `test-pdfs/`, or screenshots.
