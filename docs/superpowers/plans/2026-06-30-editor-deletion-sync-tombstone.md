# Editor Deletion Sync (tombstone) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deleting a whole line/paragraph in 문서 편집 (OnlyOffice) reflects in the 재번역/용어/고유명사 views and the re-rendered docx.

**Architecture:** Soft-delete (tombstone): the deleted block stays in the model so positions and `blk{i}` bookmarks never shift (which is what keeps the source↔translated positional pairing valid); a `deleted` flag is set on it during sync, and every output/render reader skips it. `iter_segments`/`build_segment_index_map` are deliberately left untouched.

**Tech Stack:** Python, Pydantic v2 models, python-docx, FastAPI backend. Tests: pytest via `backend/.venv/Scripts/python -m pytest`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-30-editor-deletion-sync-tombstone-design.md`. Branch: `editor-model-sync-2026-06-27`.
- **DO NOT modify `iter_segments` or `build_segment_index_map`** ([models.py:293-381](../../../backend/models.py#L293-L381)). They must keep enumerating every block positionally (including deleted ones). Skipping deleted blocks happens ONLY at the output/render layer. This is the load-bearing invariant — a reviewer must confirm neither was touched.
- The `deleted` flag goes ONLY on the four bookmarked text-block classes: `HeadingBlock`, `ParagraphBlock`, `ListItemBlock`, `CaptionBlock`. NOT on Table/Figure/Divider/TableCell/RunningBand (their deletion isn't detectable via the bookmark scheme).
- All readers test the flag via `getattr(block, "deleted", False)` so block kinds without the field are safe.
- `deleted: bool = False` — default False, so existing persisted `source.json`/`translated.json`/`edited.json` load unchanged.
- Detection only flags text blocks: `block.deleted = (i not in edits)` for `_TEXT_BLOCKS`; never touch non-text blocks.
- Run a focused test with `cd backend && .venv/Scripts/python -m pytest tests/<file> -v`.
- **Security (standing):** never stage `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`, `backend/_*.py` scratch, `test-pdfs/`, or root images. Stage only the files each task names; never `git add -A`.

---

### Task 1: Model — `deleted` flag on the four text-block classes

**Files:**
- Modify: `backend/models.py` (HeadingBlock 67-81, ParagraphBlock 84-100, ListItemBlock 103-119, CaptionBlock 181-195)
- Test: `backend/tests/test_block_deleted_field.py` (create)

**Interfaces:**
- Produces: `HeadingBlock.deleted: bool`, `ParagraphBlock.deleted: bool`, `ListItemBlock.deleted: bool`, `CaptionBlock.deleted: bool` (default `False`). Consumed by Tasks 2-4.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_block_deleted_field.py`:

```python
from models import HeadingBlock, ParagraphBlock, ListItemBlock, CaptionBlock, SemanticDocument


def test_text_blocks_have_deleted_default_false():
    assert HeadingBlock(text="h").deleted is False
    assert ParagraphBlock(text="p").deleted is False
    assert ListItemBlock(text="l").deleted is False
    assert CaptionBlock(text="c").deleted is False


def test_deleted_can_be_set_true():
    assert ParagraphBlock(text="p", deleted=True).deleted is True


def test_old_json_without_deleted_still_validates():
    # persisted models predate the field — must load with deleted defaulting to False
    doc = SemanticDocument.model_validate(
        {"source_lang": "ko", "blocks": [{"type": "paragraph", "text": "x"}]}
    )
    assert doc.blocks[0].deleted is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_block_deleted_field.py -v`
Expected: FAIL — `ParagraphBlock` has no attribute/field `deleted` (validation error on `deleted=True`, AttributeError on `.deleted`).

- [ ] **Step 3: Add the field to the four classes**

In `backend/models.py`, add this line as the LAST field of each of the four classes — immediately after their `source_span_ids: list[str] = Field(default_factory=list)` line (HeadingBlock ~81, ParagraphBlock ~100, ListItemBlock ~119, CaptionBlock ~195):

```python
    deleted: bool = False  # tombstone: removed in the editor; skipped by all output/render readers
```

(Add it to all four classes. Do NOT add it to TableBlock, FigureBlock, DividerBlock, TableCell, or RunningBand.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_block_deleted_field.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/tests/test_block_deleted_field.py
git commit -m "feat(sync): add deleted tombstone flag to text block models"
```

---

### Task 2: Detection — `apply_docx_edits_to_model` sets `deleted`

**Files:**
- Modify: `backend/pipeline/docx_sync.py:73-102` (`apply_docx_edits_to_model`)
- Test: `backend/tests/test_docx_sync_deletion.py` (create)

**Interfaces:**
- Consumes: `deleted` field (Task 1); existing `read_block_lines`, `_TEXT_BLOCKS`.
- Produces: after `apply_docx_edits_to_model(model, docx)`, a text block whose `blk{i}` bookmark is absent from the docx has `.deleted == True`; one still present has `.deleted == False`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_docx_sync_deletion.py`:

```python
import os
import models
from pipeline import process
from pipeline.docx_sync import apply_docx_edits_to_model
from renderers.docx_renderer import render_docx
from docx import Document
from docx.oxml.ns import qn


def _delete_paragraph_by_bookmark(path, name):
    """Simulate an editor deletion: remove the w:p that contains bookmark `name`."""
    d = Document(path)
    for bm in list(d.element.body.iter(qn("w:bookmarkStart"))):
        if bm.get(qn("w:name")) == name:
            p = bm
            while p is not None and p.tag != qn("w:p"):
                p = p.getparent()
            if p is not None:
                p.getparent().remove(p)
            break
    d.save(path)


def test_missing_bookmark_marks_block_deleted(tmp_path):
    m = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="first", source_span_ids=["s0"]),
        models.ParagraphBlock(text="second", source_span_ids=["s1"]),
        models.ParagraphBlock(text="third", source_span_ids=["s2"]),
    ])
    out = os.path.join(str(tmp_path), "doc.docx")
    render_docx(m, out, str(tmp_path))
    _delete_paragraph_by_bookmark(out, "blk1")          # user deletes the 2nd paragraph

    synced = apply_docx_edits_to_model(m, out)

    assert synced.blocks[1].deleted is True             # bookmark gone -> tombstoned
    assert synced.blocks[0].deleted is False            # survivors untouched
    assert synced.blocks[2].deleted is False
    assert synced.blocks[2].text == "third"             # survivor text still synced


def test_present_bookmark_clears_deleted(tmp_path):
    m = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="alive", source_span_ids=["s0"], deleted=True),
    ])
    out = os.path.join(str(tmp_path), "doc.docx")
    render_docx(m, out, str(tmp_path))                  # renders with blk0 present
    synced = apply_docx_edits_to_model(m, out)
    assert synced.blocks[0].deleted is False            # bookmark present -> flag cleared
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_docx_sync_deletion.py -v`
Expected: FAIL — `synced.blocks[1].deleted` is `False` (detection not implemented; the block is currently left untouched when its bookmark is missing).

- [ ] **Step 3: Add detection in the loop**

In `backend/pipeline/docx_sync.py`, in `apply_docx_edits_to_model`, the loop currently starts:

```python
    for i, block in enumerate(out.blocks):
        if i not in edits or not isinstance(block, _TEXT_BLOCKS):
            continue
```

Insert the deletion-detection as the first statement in the loop, BEFORE that `continue`:

```python
    for i, block in enumerate(out.blocks):
        if isinstance(block, _TEXT_BLOCKS):
            # A text block's blk{i} bookmark missing from the saved docx ⇒ the user deleted
            # that paragraph in the editor. Tombstone it (kept in the list so positions +
            # bookmarks of survivors don't shift). Present again ⇒ clear a prior flag.
            block.deleted = i not in edits
        if i not in edits or not isinstance(block, _TEXT_BLOCKS):
            continue
        # ... existing text-update logic unchanged ...
```

(Leave the rest of the function — `lines`, `_strip_marker`, `.lines` length preservation, TOC skip — exactly as is.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_docx_sync_deletion.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the existing sync suites (no regression)**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_docx_sync_apply.py tests/test_docx_sync_read.py tests/test_onlyoffice_callback_sync.py tests/test_lazy_sync.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/docx_sync.py backend/tests/test_docx_sync_deletion.py
git commit -m "feat(sync): tombstone text blocks whose bookmark was deleted in the editor"
```

---

### Task 3: `build_segments` skips deleted blocks

**Files:**
- Modify: `backend/pipeline/process.py:472-484` (`build_segments` loop)
- Test: `backend/tests/test_build_segments_deletion.py` (create)

**Interfaces:**
- Consumes: `deleted` field (Task 1); existing `build_segments`, `iter_segments`, `iter_text_fields`.
- Produces: `build_segments` omits any segment whose translated block is `deleted`, while the `index` of every other segment is unchanged vs. the no-deletion baseline.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_build_segments_deletion.py`:

```python
import os, json
import models
from pipeline import process
from renderers.docx_renderer import render_docx


def _seed(tmp_path, deleted_idx=None):
    storage, doc_id, lang = str(tmp_path), "d", "en"
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    src = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="원문0", source_span_ids=["s0"]),
        models.ParagraphBlock(text="원문1", source_span_ids=["s1"]),
        models.ParagraphBlock(text="원문2", source_span_ids=["s2"]),
    ])
    with open(os.path.join(process.doc_dir(storage, doc_id), "source.json"), "w", encoding="utf-8") as fh:
        json.dump(src.model_dump(), fh)
    tr = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="trans0", source_span_ids=["s0"]),
        models.ParagraphBlock(text="trans1", source_span_ids=["s1"], deleted=(deleted_idx == 1)),
        models.ParagraphBlock(text="trans2", source_span_ids=["s2"]),
    ])
    process.save_translated_model(storage, doc_id, lang, tr)
    return storage, doc_id, lang


def test_build_segments_omits_deleted_and_keeps_other_indices(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, deleted_idx=1)
    segs = process.build_segments(storage, doc_id, lang)
    # the deleted block's segment is gone; the others keep their original indices (0 and 2)
    by_index = {s["index"]: s["translated"] for s in segs}
    assert 1 not in by_index                       # deleted segment omitted
    assert by_index[0] == "trans0"
    assert by_index[2] == "trans2"                 # index 2 unchanged (no shift)


def test_build_segments_baseline_no_deletion(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, deleted_idx=None)
    segs = process.build_segments(storage, doc_id, lang)
    assert {s["index"] for s in segs} == {0, 1, 2}  # all present when nothing deleted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_build_segments_deletion.py -v`
Expected: FAIL — `test_build_segments_omits_deleted_and_keeps_other_indices` fails because index 1 is still present (deleted blocks are not yet skipped).

- [ ] **Step 3: Add the skip in the loop**

In `backend/pipeline/process.py`, `build_segments` loop currently:

```python
    out = []
    src_segs = list(iter_segments(source))
    tr_fields = list(iter_text_fields(translated))
    for i, (get, _set, span_ids, block_idx) in enumerate(src_segs):
        s = get()
        if not s.strip() or _is_atomic(s):
            continue
        t = tr_fields[i][0]() if i < len(tr_fields) else ""
```

Insert the deleted-skip right after the atomic check (before computing `t`):

```python
        if not s.strip() or _is_atomic(s):
            continue
        # tombstone: a paragraph deleted in the editor stays in the model (so indices don't
        # shift) but its segment is omitted here. `i` still advances, so every other segment
        # keeps its index — only this one leaves a gap.
        if block_idx is not None and getattr(translated.blocks[block_idx], "deleted", False):
            continue
        t = tr_fields[i][0]() if i < len(tr_fields) else ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_build_segments_deletion.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/process.py backend/tests/test_build_segments_deletion.py
git commit -m "feat(sync): build_segments omits deleted blocks (indices of survivors preserved)"
```

---

### Task 4: Renderers skip deleted blocks (docx + html)

**Files:**
- Modify: `backend/renderers/docx_renderer.py:134` (block loop)
- Modify: `backend/renderers/html_renderer.py:135-146` (block loop)
- Test: `backend/tests/test_render_deletion.py` (create)

**Interfaces:**
- Consumes: `deleted` field (Task 1).
- Produces: `render_docx` and `render_html` emit nothing for a deleted block, and the `blk{i}` ids of surviving blocks are unchanged (block stays in `doc.blocks`).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_render_deletion.py`:

```python
import os
import models
from renderers.docx_renderer import render_docx
from renderers.html_renderer import render_html
from docx import Document
from docx.oxml.ns import qn


def _model():
    return models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="KEEPZERO", source_span_ids=["s0"]),
        models.ParagraphBlock(text="GONEONE", source_span_ids=["s1"], deleted=True),
        models.ParagraphBlock(text="KEEPTWO", source_span_ids=["s2"]),
    ])


def test_render_docx_omits_deleted_and_keeps_survivor_bookmarks(tmp_path):
    out = os.path.join(str(tmp_path), "d.docx")
    render_docx(_model(), out, str(tmp_path))
    d = Document(out)
    body = d.element.body
    text = "".join(t.text or "" for t in body.iter(qn("w:t")))
    assert "GONEONE" not in text                       # deleted block not rendered
    assert "KEEPZERO" in text and "KEEPTWO" in text
    names = {bm.get(qn("w:name")) for bm in body.iter(qn("w:bookmarkStart"))}
    assert "blk0" in names and "blk2" in names          # survivor bookmark ids unchanged
    assert "blk1" not in names                           # deleted block's bookmark absent


def test_render_html_omits_deleted(tmp_path):
    html = render_html(_model(), anchor_blocks=True)
    assert "GONEONE" not in html
    assert "KEEPZERO" in html and "KEEPTWO" in html
    assert 'id="blk0"' in html and 'id="blk2"' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_render_deletion.py -v`
Expected: FAIL — "GONEONE" is still rendered in both docx and html (deleted blocks not yet skipped).

- [ ] **Step 3a: Skip deleted blocks in `render_docx`**

In `backend/renderers/docx_renderer.py`, the loop is `for i, block in enumerate(doc.blocks):` (line 134). Insert the skip as the FIRST statement inside the loop, BEFORE the cover-break comment/logic (so a deleted block can't trigger a spurious page break):

```python
    for i, block in enumerate(doc.blocks):
        if getattr(block, "deleted", False):
            continue   # tombstoned in the editor — omit, but keep it in the list so blk{i} ids of survivors don't shift
        # ... existing cover_break logic + block rendering unchanged ...
```

- [ ] **Step 3b: Skip deleted blocks in `render_html`**

In `backend/renderers/html_renderer.py`, the loop is `for i, block in enumerate(doc.blocks):` (line 135). The page-gap logic (136-144) updates `prev_page`; the anchor/rendering starts at line 146 (`anchor = f' id="blk{i}"' ...`). Insert the skip AFTER the `prev_page` update and BEFORE `anchor = ...` (so page tracking stays intact, only rendering is skipped):

```python
        if page is not None:
            prev_page = page
        if getattr(block, "deleted", False):
            continue   # tombstoned — omit from the rendered HTML
        anchor = f' id="blk{i}"' if anchor_blocks else ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_render_deletion.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run render + segment suites (no regression; confirm the invariant test still passes)**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_render_html_segments.py tests/test_render_html_layout.py tests/test_render_pagination.py tests/test_docx_render_merge.py -v`
Expected: PASS (all) — confirms `iter_segments`/`build_segment_index_map` lockstep is intact (they were not modified).

- [ ] **Step 6: Commit**

```bash
git add backend/renderers/docx_renderer.py backend/renderers/html_renderer.py backend/tests/test_render_deletion.py
git commit -m "feat(sync): docx + html renderers omit deleted (tombstoned) blocks"
```

---

## Verification (controller-run live checkpoint)

After Task 4, run a live end-to-end check (full stack — DS `:8080`, a backend on a fresh port with `ONLYOFFICE_*` env + `DEV_NO_AUTH=1`, a prod frontend build with the force-save gate). Re-render the test doc first for a fresh DS session key (avoids stale cross-backend session cache).

- [ ] Open a doc in 문서 편집. Delete a whole **body paragraph** (select the line incl. its paragraph mark, delete).
- [ ] Click 재번역 (force-save gate from feature (a)) → within a couple seconds the deleted paragraph is **gone** from the segment list + preview, with no manual refresh.
- [ ] Confirm a neighboring paragraph's text and order are intact (no shift/corruption) — pick a distinctive neighbor and verify its content + that the segment indices around the gap didn't renumber wrongly.
- [ ] Switch to 용어 / 고유명사 → the deleted paragraph is absent there too.
- [ ] (Known limit, expected) the original (source) pane still shows the paragraph — that's the documented v1 cosmetic gap, not a bug.

---

## Self-Review

**1. Spec coverage:**
- Model `deleted` flag on the 4 text classes → Task 1. ✓
- Detection in `apply_docx_edits_to_model` (text blocks only; present clears flag) → Task 2. ✓
- `build_segments` omits deleted + preserves other indices → Task 3. ✓
- `render_docx` + `render_html` omit deleted, survivor `blk{i}` stable → Task 4. ✓
- Audits inherit via `build_segments` (no code change) → covered by Task 3; no task needed. ✓
- Invariant: `iter_segments`/`build_segment_index_map` untouched → enforced by Global Constraints + re-checked by Task 4 Step 5. ✓
- Live verification → Verification checkpoint. ✓

**2. Placeholder scan:** none — every step has concrete code + exact commands + expected output.

**3. Type consistency:** `deleted: bool` (Task 1) is read via `getattr(block, "deleted", False)` in Tasks 3 & 4 and set as `block.deleted = i not in edits` in Task 2 — consistent. `_TEXT_BLOCKS` in Task 2 is the existing tuple of the same 4 classes. `block_idx` (from `iter_segments`) guarded for `None` before indexing `translated.blocks` in Task 3. No type/name drift.
