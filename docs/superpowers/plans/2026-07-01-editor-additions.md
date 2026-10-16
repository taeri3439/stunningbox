# Editor Additions Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New paragraphs typed in 문서 편집 (OnlyOffice) reflect in the tool views (as source-less "added" segments) and the re-rendered docx.

**Architecture:** Extend the docx→model rebuild. `read_block_lines` returns an ordered list of `(block_id | None, lines)` — `None` = a bookmark-less text paragraph = a user addition. `apply_docx_edits_to_model` inserts `None`-entries as new first-class `ParagraphBlock`s (fresh `block_id`). `build_segments` emits added blocks (seg-id absent from source → `source=""`, `added=True`, no empty-skip). Frontend shows an 원문-없음 badge + disabled re-translate.

**Tech Stack:** Python, Pydantic v2, python-docx, FastAPI; Next.js/React. Tests: `backend/.venv/Scripts/python -m pytest`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-01-editor-additions-design.md`. Branch: `editor-model-sync-2026-06-27`. Fallback: commit `252a66a` (completed foundation).
- **A user addition = a top-level `w:p` with non-empty text and NO `blk` bookmark.** Figures/dividers have no `w:t` text; table interiors are skipped — so only genuine new text paragraphs are treated as additions.
- New blocks get `block_id = uuid.uuid4().hex` and `source_span_ids=[]`; multi-line text follows the existing `.lines`/`.text` convention (1 line → `.text`; >1 → `.lines`).
- `build_segments`: seg-id **not in** the source map → addition → emit with `source=""`, `added=True`, **skip the empty-source filter** (but skip if the *translated* text is itself empty). seg-id **in** the source map but empty/atomic source → skip (unchanged). Non-added → `added=False`.
- Keep the existing reorder + delete tests green — additions compose with them.
- **Security (standing):** never stage `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`, `backend/_*.py`. Stage only the files each task names; never `git add -A`.
- Run a focused test: `cd backend && .venv/Scripts/python -m pytest tests/<file> -v`; full suite: `.venv/Scripts/python -m pytest -q -p no:cacheprovider` → only the 15 pre-existing `test_auth`/`test_documents_store` failures are acceptable.

---

### Task 1: `read_block_lines` → ordered `(block_id | None, lines)` entries (behavior-preserving)

**Files:**
- Modify: `backend/pipeline/docx_sync.py` (`read_block_lines` 28-62; adapt `apply_docx_edits_to_model` 110-155 to the new shape WITHOUT inserting new blocks yet)
- Test: `backend/tests/test_read_block_lines_entries.py` (create)

**Interfaces:**
- Produces: `read_block_lines(docx_path) -> list[tuple[str | None, list[str]]]` — ordered top-level text paragraphs: `(block_id, lines)` for bookmarked, `(None, lines)` for a bookmark-less non-empty text paragraph. Table interiors + empty paragraphs excluded.
- `apply_docx_edits_to_model` still behaves EXACTLY as before this task (reorder + drop; `None`-entries ignored) — derives `ordered_ids`/`edits` from the entries.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_read_block_lines_entries.py`:

```python
import os
import models
from renderers.docx_renderer import render_docx
from pipeline.docx_sync import read_block_lines
from docx import Document
from docx.oxml.ns import qn


def test_read_returns_ordered_entries_with_none_for_new_paragraph(tmp_path):
    m = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="zero", block_id="B0", source_span_ids=["s"]),
        models.ParagraphBlock(text="one", block_id="B1", source_span_ids=["s"]),
    ])
    out = os.path.join(str(tmp_path), "d.docx")
    render_docx(m, out, str(tmp_path))
    # simulate a user-typed new paragraph (no bookmark) inserted between B0 and B1
    d = Document(out)
    body = d.element.body
    ps = [p for p in body.iter(qn("w:p"))
          if any((bm.get(qn("w:name")) or "").startswith("blk") for bm in p.iter(qn("w:bookmarkStart")))]
    new_p = body.makeelement(qn("w:p"), {})
    r = new_p.makeelement(qn("w:r"), {}); t = new_p.makeelement(qn("w:t"), {})
    t.text = "BRANDNEW"; r.append(t); new_p.append(r)
    ps[0].addnext(new_p)                          # after B0's paragraph
    d.save(out)

    entries = read_block_lines(out)
    assert entries == [("B0", ["zero"]), (None, ["BRANDNEW"]), ("B1", ["one"])]
```

- [ ] **Step 2: Run to verify it fails** — FAIL: `read_block_lines` returns a `(ordered, by_id)` tuple, not an ordered entries list.

- [ ] **Step 3: Implement**

Rewrite `read_block_lines` to walk the body's top-level `w:p` in order (skip `w:tbl`), detecting bookmark-less text paragraphs:

```python
def read_block_lines(docx_path: str) -> list[tuple[str | None, list[str]]]:
    """Ordered top-level text paragraphs from a rendered/edited .docx.

    (block_id, lines) for a paragraph carrying a `blk{block_id}` bookmark; (None, lines)
    for a text paragraph with NO blk bookmark (a user addition). Empty paragraphs and
    table interiors are skipped. Returned in document order (drives reorder/insert).
    """
    d = Document(docx_path)
    entries: list[tuple[str | None, list[str]]] = []
    for p in d.element.body.iterchildren(qn("w:p")):   # top-level paragraphs only (tables skipped)
        block_id = None
        for bm in p.iter(qn("w:bookmarkStart")):
            name = bm.get(qn("w:name")) or ""
            if name.startswith("blk") and name[3:]:
                block_id = name[3:]
                break
        lines = _paragraph_lines(p)
        if block_id is not None:
            entries.append((block_id, lines))
        elif any(ln.strip() for ln in lines):          # bookmark-less + has text ⇒ addition
            entries.append((None, lines))
        # else: empty bookmark-less paragraph ⇒ skip
    return entries
```

Adapt `apply_docx_edits_to_model` to the new shape WITHOUT inserting new blocks yet (behavior unchanged this task): at the top replace `ordered_ids, edits = read_block_lines(docx_path)` with

```python
    entries = read_block_lines(docx_path)
    ordered_ids = [bid for bid, _ in entries if bid is not None]
    edits = {bid: lines for bid, lines in entries if bid is not None}
```

(everything below is unchanged; `None`-entries are ignored for now — Task 2 handles them.)

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_read_block_lines_entries.py tests/test_docx_sync_read.py tests/test_docx_sync_reorder.py -v` → PASS (update `test_docx_sync_read.py` to the entries shape). Full suite → no NEW failures.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/docx_sync.py backend/tests/test_read_block_lines_entries.py backend/tests/test_docx_sync_read.py
git commit -m "feat(sync): read_block_lines returns ordered (block_id|None, lines) entries"
```

---

### Task 2: `apply_docx_edits_to_model` inserts new paragraphs

**Files:**
- Modify: `backend/pipeline/docx_sync.py` (`apply_docx_edits_to_model`)
- Test: `backend/tests/test_docx_sync_additions.py` (create)

**Interfaces:**
- Consumes: entries from Task 1.
- Produces: after `apply`, a `(None, lines)` entry becomes a new `ParagraphBlock(block_id=<uuid hex>, source_span_ids=[])` inserted at that docx position; reorder + delete unchanged.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_docx_sync_additions.py`:

```python
import os
import models
from pipeline.docx_sync import apply_docx_edits_to_model
from renderers.docx_renderer import render_docx
from docx import Document
from docx.oxml.ns import qn


def _insert_new_paragraph_after(path, after_block_id, text):
    d = Document(path); body = d.element.body
    target = None
    for p in body.iterchildren(qn("w:p")):
        if any((bm.get(qn("w:name")) or "") == f"blk{after_block_id}" for bm in p.iter(qn("w:bookmarkStart"))):
            target = p; break
    np = body.makeelement(qn("w:p"), {}); r = np.makeelement(qn("w:r"), {}); t = np.makeelement(qn("w:t"), {})
    t.text = text; r.append(t); np.append(r); target.addnext(np); d.save(path)


def test_new_paragraph_inserted_as_block(tmp_path):
    m = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="zero", block_id="B0", source_span_ids=["s"]),
        models.ParagraphBlock(text="one", block_id="B1", source_span_ids=["s"]),
    ])
    out = os.path.join(str(tmp_path), "d.docx"); render_docx(m, out, str(tmp_path))
    _insert_new_paragraph_after(out, "B0", "ADDED")

    synced = apply_docx_edits_to_model(m, out)

    texts = [b.text for b in synced.blocks]
    assert texts == ["zero", "ADDED", "one"]                     # inserted at the right position
    added = synced.blocks[1]
    assert added.block_id and added.block_id not in ("B0", "B1") # fresh id
    assert added.source_span_ids == []
```

(Add a compose test: an added paragraph + a reorder + a delete in one docx yields the expected block sequence.)

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_docx_sync_additions.py -v` → FAIL: the new paragraph is ignored (not inserted).

- [ ] **Step 3: Implement**

Add `import uuid` at the top of `docx_sync.py`. In `apply_docx_edits_to_model`, keep the ride-along grouping loop (built from the model's blocks + `edits`), then rewrite the rebuild walk to iterate `entries` (not `ordered_ids`), inserting new blocks for `None`:

```python
    entries = read_block_lines(docx_path)
    out = model.model_copy(deep=True)
    by_id = {b.block_id: b for b in out.blocks if getattr(b, "block_id", None)}
    edit_ids = {bid for bid, _ in entries if bid is not None}

    # ride-along grouping (unchanged): non-bookmarked model blocks anchor to the preceding
    # surviving text block; deleted text blocks (id not in edit_ids) are dropped.
    trailing: dict[str | None, list] = {}
    leading: list = []
    anchor: str | None = None
    for b in out.blocks:
        bid = getattr(b, "block_id", None)
        if isinstance(b, _TEXT_BLOCKS) and bid is not None:
            if bid in edit_ids:
                anchor = bid
        else:
            (trailing.setdefault(anchor, []) if anchor else leading).append(b)

    new_blocks = list(leading)
    for bid, lines in entries:
        if bid is None:
            # user-added paragraph → new first-class block
            text = "\n".join(lines) if len([l for l in lines if l]) > 1 else (lines[0] if lines else "")
            nb = ParagraphBlock(block_id=uuid.uuid4().hex,
                                lines=lines if len(lines) > 1 else None,
                                text="" if len(lines) > 1 else text,
                                source_span_ids=[])
            new_blocks.append(nb)
            continue
        b = by_id.get(bid)
        if b is None:
            continue
        _apply_text(b, lines)
        new_blocks.append(b)
        new_blocks.extend(trailing.get(bid, []))
    out.blocks = new_blocks
    return out
```

(A `None`-entry with `>1` non-empty line becomes a `.lines` block; a single line becomes a `.text` block — matching how originals are modelled. Keep it simple: single-line is the common case.)

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_docx_sync_additions.py tests/test_docx_sync_reorder.py tests/test_docx_sync_deletion.py -v` → PASS. Full suite → no NEW failures.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/docx_sync.py backend/tests/test_docx_sync_additions.py
git commit -m "feat(sync): apply_docx_edits_to_model inserts new paragraphs as first-class blocks"
```

---

### Task 3: `build_segments` emits added segments

**Files:**
- Modify: `backend/pipeline/process.py` (`build_segments` loop 512-526)
- Test: `backend/tests/test_build_segments_added.py` (create)

**Interfaces:**
- Produces: segment dicts gain `"added": bool`; a segment whose seg-id is absent from the source map is emitted with `source=""`, `added=True`, `source_bbox=None` (unless its translated text is empty). All others get `added=False`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_build_segments_added.py`:

```python
import os, json
import models
from pipeline import process


def _seed_with_added(tmp_path):
    storage, doc_id, lang = str(tmp_path), "d", "en"
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    src = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="원문0", block_id="B0", source_span_ids=["s0"])])
    with open(os.path.join(process.doc_dir(storage, doc_id), "source.json"), "w", encoding="utf-8") as fh:
        json.dump(src.model_dump(), fh)
    tr = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="t0", block_id="B0", source_span_ids=["s0"]),
        models.ParagraphBlock(text="ADDED translation", block_id="NEW1", source_span_ids=[])])
    process.save_translated_model(storage, doc_id, lang, tr)
    return storage, doc_id, lang


def test_added_segment_emitted_with_empty_source(tmp_path):
    storage, doc_id, lang = _seed_with_added(tmp_path)
    segs = process.build_segments(storage, doc_id, lang)
    by_id = {s["id"]: s for s in segs}
    assert by_id["B0:text"]["added"] is False and by_id["B0:text"]["source"] == "원문0"
    added = by_id["NEW1:text"]
    assert added["added"] is True and added["source"] == "" and added["translated"] == "ADDED translation"
```

- [ ] **Step 2: Run to verify it fails** — FAIL: the added block (source "") is skipped by the empty-source filter; no `added` key.

- [ ] **Step 3: Implement** — in `build_segments`, replace the loop body to branch on missing-source:

```python
    for get, _set, _span, block_id, seg_id in iter_segments(translated):
        t = get()
        if seg_id not in src_by_id:                     # no source counterpart ⇒ user addition
            if t.strip():                               # skip a blank new paragraph
                out.append({"index": i, "id": seg_id, "block_id": block_id,
                            "source": "", "translated": t, "source_bbox": None, "added": True})
            i += 1
            continue
        src_get, span_ids = src_by_id[seg_id]
        s = src_get()
        if not s.strip() or _is_atomic(s):
            i += 1
            continue
        bbox = (extractor.segment_source_bbox(span_ids, span_map, page_sizes)
                if span_map else None)
        out.append({"index": i, "id": seg_id, "block_id": block_id,
                    "source": s, "translated": t, "source_bbox": bbox, "added": False})
        i += 1
    return out
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_build_segments_added.py tests/test_build_segments_idpair.py tests/test_build_segments_deletion.py -v` → PASS. Full suite → no NEW failures (existing tests may need `"added"` accommodated only if they assert exact dict equality — most read fields, so unaffected; fix any that do).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/process.py backend/tests/test_build_segments_added.py
git commit -m "feat(sync): build_segments emits source-less added segments (added=true)"
```

---

### Task 4: Frontend — added-row badge + disabled re-translate

**Files:**
- Modify: `frontend/src/lib/types.ts` (`Segment` gains `added: boolean`), `frontend/src/components/RetranslatePanel.tsx`, `frontend/src/components/SegmentPreview.tsx` (if it renders per-segment source), `frontend/src/app/c/[id]/page.tsx` (if it surfaces source text)
- Verify: `cd frontend && npm run build`

**Interfaces:**
- Consumes: `build_segments` dicts now carry `added: boolean` + `source: ""` for additions.

- [ ] **Step 1: Update types + rendering**

- `types.ts` `Segment`: add `added: boolean;`.
- `RetranslatePanel.tsx`: for a row where `seg.added`, render the original/source side as a muted badge **"원문 없음 · 추가됨"** instead of the source text, and render the re-translate button as **disabled** (e.g. `disabled={seg.added}` with a title "추가된 문단은 원문이 없어 재번역할 수 없습니다"). Read the actual current row + button markup and apply minimally.
- `SegmentPreview.tsx` / `page.tsx`: added segments have `source: ""` + `source_bbox: null`, so the source-pane highlight already no-ops; no change needed unless a component assumes `source` is non-empty — check and guard.

- [ ] **Step 2: Build** — `cd frontend && npm run build` → succeeds, no TS errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/components/RetranslatePanel.tsx frontend/src/components/SegmentPreview.tsx "frontend/src/app/c/[id]/page.tsx"
git commit -m "feat(sync): frontend shows 원문-없음 badge + disables re-translate for added segments"
```

---

## Verification (controller-run live checkpoint, screenshots)

Full stack (DS :8080, backend with the new code on a fresh port, prod frontend build, `DEV_NO_AUTH=1`); re-render the test doc for a fresh DS key.

- [ ] **Add:** in 문서 편집, type a brand-new paragraph (distinctive text) between two existing ones → 재번역 → it appears in the segment list with the **원문 없음 · 추가됨** badge, re-translate disabled, and in the translated preview at the right position. Screenshot.
- [ ] **Promotion:** after that, reorder the new paragraph / delete it → those work (it's now a first-class block with a block_id).
- [ ] **Compose:** add + reorder + delete in one editing session all reflect together.
- [ ] **No-op:** a doc with no additions is unchanged.

---

## Self-Review

**1. Spec coverage:** read_block_lines ordered entries incl. None → T1; apply inserts new blocks → T2; build_segments emits added → T3; frontend badge + disabled retranslate → T4; retranslate backend guard already raises IndexError for absent seg-id (from the foundation) → no task needed; live add/promotion/compose → Verification. All spec components covered.

**2. Placeholder scan:** none — every step has concrete code + tests + commands. The frontend task gives the exact fields/behavior; the implementer reads the current RetranslatePanel/SegmentPreview markup (standard).

**3. Type consistency:** `read_block_lines -> list[tuple[str|None, list[str]]]` (T1) consumed by `apply` (T1 compat + T2 insert); new `ParagraphBlock(block_id=uuid hex, source_span_ids=[])` (T2) → seg-id absent from source map in `build_segments` (T3) → `added: true` → `Segment.added` (T4). `added` boolean is consistent from process.py through types.ts. The `_is_atomic`/empty-source skip is preserved for non-added segments.
