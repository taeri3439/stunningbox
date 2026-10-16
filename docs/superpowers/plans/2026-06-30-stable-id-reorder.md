# Stable-ID Foundation + Reorder Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reordering (and deleting) paragraphs in 문서 편집 reflects in the tool views, by replacing positional segment identity with stable per-block IDs.

**Architecture:** Each block gets a stable `block_id` at ingest; each segment is identified by a composite seg-id `f"{block_id}:{subkey}"`. Bookmarks, `iter_segments`, `build_segments` pairing, re-translate, `data-seg`, and the frontend all key off IDs. `docx_sync` rebuilds the translated block order from the docx's bookmark order (reorder) and drops blocks whose id vanished (delete, superseding the tombstone). Tasks 1–8 are **behavior-preserving** (the full suite stays green); Task 9 (docx_sync reorder/drop) is the only behavior change.

**Tech Stack:** Python, Pydantic v2, python-docx, FastAPI; Next.js/React frontend. Tests: `backend/.venv/Scripts/python -m pytest`.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-30-stable-id-reorder-design.md`. Branch: `editor-model-sync-2026-06-27`. Working fallback: commit `6a7ad5e`.
- **Behavior-preserving foundation:** Tasks 1–8 must keep the ENTIRE existing backend suite green (esp. `test_render_html_segments` lockstep, `test_build_segments_*`, `test_retranslate*`, `test_glossary*`, `test_proper_noun*`, render tests). With nothing reordered, id-based results MUST equal positional results.
- **Seg-id format (verbatim):** band → `f"band:{which}:{attr}"`; heading/paragraph/caption single text → `f"{block_id}:text"`; line → `f"{block_id}:line:{li}"`; table caption → `f"{block_id}:caption"`; cell → `f"{block_id}:cell:{ci}"`; cell line → `f"{block_id}:cellline:{ci}:{li}"`; overlay → `f"{block_id}:overlay:{oi}"`.
- **`block_id`** = uuid4 hex (`uuid.uuid4().hex`, 32 chars) — fits the `blk{block_id}` bookmark name (≤40 chars, `[0-9a-zA-Z._-]`).
- `iter_segments` and `build_segment_index_map` MUST stay in lockstep (same order, same set of segments) — `test_render_html_segments` enforces it.
- Lockstep yield change is repo-wide: `iter_segments` will yield `(getter, setter, span_ids, block_id, seg_id)` (was `(getter, setter, span_ids, block_index)`). `iter_text_fields` (which ignores the extras) is unaffected.
- **Security (standing):** never stage `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`, `backend/_*.py`, `test-pdfs/`, or root images. Stage only the files each task names; never `git add -A`.

---

### Task 1: `block_id` on block models + seg-id helper

**Files:**
- Modify: `backend/models.py` (the 7 block classes + a shared base; add a `segment_id` helper)
- Test: `backend/tests/test_block_id.py` (create)

**Interfaces:**
- Produces: `block.block_id: Optional[str]` (default `None`) on HeadingBlock/ParagraphBlock/ListItemBlock/CaptionBlock/TableBlock/FigureBlock/DividerBlock. A module function `models.make_seg_id(block_id: str, *parts) -> str` returning `":".join([block_id, *map(str, parts)])`.

- [ ] **Step 1: Write the failing test**

```python
from models import (HeadingBlock, ParagraphBlock, TableBlock, FigureBlock,
                    DividerBlock, SemanticDocument, make_seg_id)

def test_blocks_have_block_id_default_none():
    for B in (HeadingBlock, ParagraphBlock, TableBlock, FigureBlock, DividerBlock):
        assert B().block_id is None

def test_block_id_settable_and_old_json_loads():
    assert ParagraphBlock(block_id="abc").block_id == "abc"
    doc = SemanticDocument.model_validate({"source_lang": "ko",
        "blocks": [{"type": "paragraph", "text": "x"}]})
    assert doc.blocks[0].block_id is None

def test_make_seg_id():
    assert make_seg_id("B", "text") == "B:text"
    assert make_seg_id("B", "line", 3) == "B:line:3"
    assert make_seg_id("B", "cellline", 2, 1) == "B:cellline:2:1"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/Scripts/python -m pytest tests/test_block_id.py -v`
Expected: FAIL — `block_id` field / `make_seg_id` missing.

- [ ] **Step 3: Implement**

In `backend/models.py`: add a shared base above the block classes and have the 7 block classes inherit it (keep each class's `type` Literal as the discriminator):

```python
class _IdentifiedBlock(BaseModel):
    block_id: Optional[str] = None  # stable per-block id (uuid hex); assigned at ingest
```

Change each block class declaration to inherit it, e.g. `class HeadingBlock(_IdentifiedBlock):` … through `class DividerBlock(_IdentifiedBlock):`. (Note: `deleted: bool = False` added in the prior increment stays on the 4 text classes for now; Task 9 removes it.)

Add near the top of the segment helpers:

```python
def make_seg_id(block_id: str, *parts) -> str:
    """Stable segment id: block_id joined with its sub-key parts (e.g. 'B:line:3')."""
    return ":".join([block_id, *(str(p) for p in parts)])
```

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_block_id.py -v` → PASS (3).

- [ ] **Step 5: Run the full suite to confirm the base-class change didn't break model loading**

Run: `cd backend && .venv/Scripts/python -m pytest -q -p no:cacheprovider` — Expected: the pre-existing 15 `test_auth`/`test_documents_store` failures only; everything else green (no NEW failures).

- [ ] **Step 6: Commit**

```bash
git add backend/models.py backend/tests/test_block_id.py
git commit -m "feat(sync): add stable block_id to block models + make_seg_id helper"
```

---

### Task 2: Assign `block_id` at ingest + lazy backfill

**Files:**
- Modify: `backend/pipeline/process.py` (add `ensure_block_ids`; call at ingest in `build_source_model`/`_from_office`/`_from_hwp`; backfill in `load_source_model` + `get_translated_model`)
- Test: `backend/tests/test_block_id_backfill.py` (create)

**Interfaces:**
- Consumes: `block.block_id` (Task 1).
- Produces: `process.ensure_block_ids(*models) -> bool` — assigns a uuid to any block lacking `block_id`, using the FIRST model as the id source and mirroring onto the others by position; returns True if it changed anything. After `load_source_model`/`get_translated_model`, every block has a `block_id`, and source.blocks[i].block_id == translated.blocks[i].block_id (pre-reorder).

- [ ] **Step 1: Write the failing test**

```python
import os, json, uuid
import models
from pipeline import process

def test_ensure_block_ids_assigns_and_mirrors():
    src = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="a"), models.ParagraphBlock(text="b")])
    tr = src.model_copy(deep=True)
    changed = process.ensure_block_ids(src, tr)
    assert changed is True
    assert all(b.block_id for b in src.blocks)
    assert [b.block_id for b in src.blocks] == [b.block_id for b in tr.blocks]  # mirrored by position
    # idempotent
    assert process.ensure_block_ids(src, tr) is False

def test_load_source_model_backfills(tmp_path):
    storage, doc_id = str(tmp_path), "d"
    os.makedirs(process.doc_dir(storage, doc_id), exist_ok=True)
    # persist an OLD model with no block_id
    old = models.SemanticDocument(source_lang="ko", blocks=[models.ParagraphBlock(text="x")])
    raw = old.model_dump(); raw["blocks"][0].pop("block_id", None)
    with open(os.path.join(process.doc_dir(storage, doc_id), "source.json"), "w", encoding="utf-8") as fh:
        json.dump(raw, fh)
    loaded = process.load_source_model(storage, doc_id)
    assert loaded.blocks[0].block_id is not None              # backfilled on load
    # and persisted: a re-load sees the same id
    assert process.load_source_model(storage, doc_id).blocks[0].block_id == loaded.blocks[0].block_id
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_block_id_backfill.py -v` → FAIL (`ensure_block_ids` missing; load doesn't backfill).

- [ ] **Step 3: Implement**

In `backend/pipeline/process.py` add:

```python
import uuid

def ensure_block_ids(*docs) -> bool:
    """Assign a stable block_id to every block lacking one. The FIRST doc is the id source;
    later docs (translated/edited) are mirrored BY POSITION (valid pre-reorder). Idempotent;
    returns True if anything changed."""
    if not docs:
        return False
    primary = docs[0]
    changed = False
    for i, blk in enumerate(primary.blocks):
        if not getattr(blk, "block_id", None):
            blk.block_id = uuid.uuid4().hex
            changed = True
        for other in docs[1:]:
            if i < len(other.blocks) and not getattr(other.blocks[i], "block_id", None):
                other.blocks[i].block_id = blk.block_id
                changed = True
    return changed
```

- Ingest: in `build_source_model`, `build_source_model_from_office`, `build_source_model_from_hwp`, immediately before writing `source.json`, call `ensure_block_ids(doc)`.
- `load_source_model`: after validating the model, `if ensure_block_ids(model): _write_json(source_path, model.model_dump())` then return it.
- `get_translated_model`: after loading source + translated (cache path), call `ensure_block_ids(source, translated)`; if it changed anything, persist both (`_write_json(source_path,…)` + `save_translated_model(…)`). (On the fresh-translate path, the translated model is a structural copy of the id-bearing source, so ids are already present.)

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_block_id_backfill.py -v` → PASS (2). Then full suite → no NEW failures.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/process.py backend/tests/test_block_id_backfill.py
git commit -m "feat(sync): assign block_id at ingest + idempotent lazy backfill on load"
```

---

### Task 3: `iter_segments` + `build_segment_index_map` key by seg-id

**Files:**
- Modify: `backend/models.py` (`iter_segments` yields `(getter, setter, span_ids, block_id, seg_id)`; `build_segment_index_map` values become seg-id strings)
- Test: `backend/tests/test_seg_ids.py` (create)

**Interfaces:**
- Consumes: `make_seg_id`, `block_id` (Tasks 1-2).
- Produces: `iter_segments` 5-tuple `(getter, setter, span_ids, block_id, seg_id)` (block_id is `None` for band fields; seg_id always set). `build_segment_index_map(doc) -> dict[key, str]` mapping each location key to its seg-id, in the SAME order/set as `iter_segments`.

- [ ] **Step 1: Write the failing test**

```python
import models

def _doc():
    d = models.SemanticDocument(source_lang="ko", blocks=[
        models.HeadingBlock(text="h", block_id="H"),
        models.ParagraphBlock(text="", lines=["a", "b"], block_id="P"),
    ])
    return d

def test_iter_segments_yields_seg_ids():
    segs = list(models.iter_segments(_doc()))
    assert [s[4] for s in segs] == ["H:text", "P:line:0", "P:line:1"]   # seg_id is 5th
    assert [s[3] for s in segs] == ["H", "P", "P"]                       # block_id is 4th

def test_index_map_and_iter_segments_lockstep():
    d = _doc()
    seg_ids_iter = [s[4] for s in models.iter_segments(d)]
    m = models.build_segment_index_map(d)
    assert set(m.values()) == set(seg_ids_iter)                          # same set
    assert len(m) == len(seg_ids_iter)                                   # 1:1
```

- [ ] **Step 2: Run to verify it fails** — FAIL (iter_segments yields a 4-tuple; map values are ints).

- [ ] **Step 3: Implement** — rewrite both walks to emit seg-ids. In `iter_segments`, for each yielded segment replace the trailing `bi`/`None` with `(block_id, seg_id)` where `block_id = getattr(block, "block_id", None)` and `seg_id = make_seg_id(block_id, *subparts)` (band fields: `block_id=None`, `seg_id=f"band:{which}:{attr}"`). Use the exact sub-key parts that match the existing `build_segment_index_map` keys (`text` / `line, li` / `caption` / `cell, ci` / `cellline, ci, li` / `overlay, oi`). In `build_segment_index_map`, replace each `m[(bi, …)] = i; i += 1` with `m[(block_id, …)] = make_seg_id(block_id, …)` (drop the integer counter; band keys map to the band seg-id). Keep the two functions structurally identical so the order/set match.

Update the two in-file callers' unpacking: `iter_text_fields` already does `for get, set_, _, _ in iter_segments(doc)` → change to `for get, set_, *_ in iter_segments(doc): yield get, set_`.

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_seg_ids.py tests/test_render_html_segments.py -v` → PASS. Then full suite → no NEW failures. (`test_render_html_segments` will need its expectations updated if it asserted integer data-seg — see Task 7; if it fails here purely on int-vs-id, note it and confirm green after Task 7. If it asserts only lockstep/counts, it stays green.)

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/tests/test_seg_ids.py
git commit -m "feat(sync): iter_segments + index_map key segments by stable seg-id"
```

---

### Task 4: `build_segments` — enumerate translated, pair source by seg-id

**Files:**
- Modify: `backend/pipeline/process.py` (`build_segments` 449-489)
- Test: `backend/tests/test_build_segments_idpair.py` (create)

**Interfaces:**
- Consumes: id-keyed `iter_segments` (Task 3).
- Produces: each segment dict now also carries `"id": <seg_id>` and `"block_id": <block_id>`; `"index"` stays as the within-fetch ordinal (display/click). The list is built by enumerating the **translated** model; `source`/`source_bbox` come from the source segment with the matching seg-id.

- [ ] **Step 1: Write the failing test**

```python
import os, json
import models
from pipeline import process

def _seed(tmp_path, reorder=False):
    storage, doc_id, lang = str(tmp_path), "d", "en"
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    src = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="원문0", block_id="B0", source_span_ids=["s0"]),
        models.ParagraphBlock(text="원문1", block_id="B1", source_span_ids=["s1"]),
    ])
    tr_blocks = [models.ParagraphBlock(text="t0", block_id="B0", source_span_ids=["s0"]),
                 models.ParagraphBlock(text="t1", block_id="B1", source_span_ids=["s1"])]
    if reorder:
        tr_blocks = tr_blocks[::-1]   # translated reordered relative to source
    tr = models.SemanticDocument(source_lang="ko", blocks=tr_blocks)
    with open(os.path.join(process.doc_dir(storage, doc_id), "source.json"), "w", encoding="utf-8") as fh:
        json.dump(src.model_dump(), fh)
    process.save_translated_model(storage, doc_id, lang, tr)
    return storage, doc_id, lang

def test_build_segments_pairs_by_id_when_unreordered(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, reorder=False)
    segs = process.build_segments(storage, doc_id, lang)
    assert [(s["source"], s["translated"]) for s in segs] == [("원문0","t0"), ("원문1","t1")]
    assert segs[0]["id"] == "B0:text" and segs[0]["block_id"] == "B0"

def test_build_segments_pairs_by_id_after_reorder(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, reorder=True)
    segs = process.build_segments(storage, doc_id, lang)
    # translated order is [t1, t0]; each still paired to its OWN source by id
    assert [(s["source"], s["translated"]) for s in segs] == [("원문1","t1"), ("원문0","t0")]
```

- [ ] **Step 2: Run to verify it fails** — `test_..._after_reorder` fails (positional pairing pairs t1 with 원문0).

- [ ] **Step 3: Implement** — rewrite the `build_segments` loop:

```python
    out = []
    # source segments indexed by seg-id (for pairing) + the source-side getter/span_ids
    src_by_id = {seg_id: (get, span_ids) for get, _set, span_ids, _bid, seg_id in iter_segments(source)}
    i = 0
    for get, _set, _span, block_id, seg_id in iter_segments(translated):
        t = get()                                  # translated text (current/editor order)
        src_get, span_ids = src_by_id.get(seg_id, (None, []))
        s = src_get() if src_get else ""           # source counterpart by id ("" = addition, Increment 3)
        if not s.strip() or _is_atomic(s):
            i += 1; continue
        bbox = (extractor.segment_source_bbox(span_ids, span_map, page_sizes) if span_map else None)
        out.append({"index": i, "id": seg_id, "block_id": block_id,
                    "source": s, "translated": t, "source_bbox": bbox})
        i += 1
    return out
```

(Note: enumerates `translated` now; `t` is the translated getter; `s` from the source map. `block` field renamed to `block_id`.)

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_build_segments_idpair.py tests/test_build_segments_deletion.py -v` → PASS. Full suite → no NEW failures (existing build_segments tests must still pass — they don't reorder, so id-pairing == positional).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/process.py backend/tests/test_build_segments_idpair.py
git commit -m "feat(sync): build_segments enumerates translated, pairs source by seg-id"
```

---

### Task 5: `retranslate_one_segment` + `apply_glossary_term` by seg-id

**Files:**
- Modify: `backend/pipeline/process.py` (`retranslate_one_segment` 492-…, `apply_glossary_term` 581-…); `backend/main.py` (the retranslate route body model) ; `backend/tests/test_retranslate_idpair.py` (create)

**Interfaces:**
- `retranslate_one_segment(storage_dir, doc_id, target_lang, seg_id: str, guidance, translator=None)` — resolves the source field and the translated field by seg-id (not ordinal). Returns `{id, old_text, new_text}`.
- `apply_glossary_term` passes seg-ids.
- The `/retranslate-segment` route's body `index: int` becomes `id: str` (RetranslateBody).

- [ ] **Step 1: Write the failing test**

```python
import os, json
import models
from pipeline import process

class _Echo:  # MockTranslator-style: returns marked text
    def translate_segment(self, text, *a, **k): return "RT:" + text

def test_retranslate_by_id_after_reorder(tmp_path):
    storage, doc_id, lang = "d", "d", "en"  # see _seed in Task 4; reuse the reorder seed
    storage, doc_id, lang = _seed_reordered(tmp_path)   # helper: source [B0,B1], translated [B1,B0]
    res = process.retranslate_one_segment(storage, doc_id, lang, "B0:text", "", translator=_Echo())
    assert res["id"] == "B0:text"
    # the WRITE landed on B0's translated field, not positionally on the first translated block
    tr = process.get_translated_model(storage, doc_id, lang)
    b0 = next(b for b in tr.blocks if b.block_id == "B0")
    assert b0.text == "RT:원문0"
```

(Provide `_seed_reordered` mirroring Task 4's reorder seed.)

- [ ] **Step 2: Run to verify it fails** — current signature takes an int index; by-id resolution absent.

- [ ] **Step 3: Implement** — build seg-id→field maps for both models and resolve by id:

```python
    source = load_source_model(storage_dir, doc_id)
    src_by_id = {seg_id: get for get, _set, _sp, _bid, seg_id in iter_segments(source)}
    if seg_id not in src_by_id:
        raise IndexError(f"segment id {seg_id!r} not found in source")
    src_text = src_by_id[seg_id]()
    # … translate (unchanged) …
    translated = get_translated_model(storage_dir, doc_id, target_lang)
    tr_by_id = {sid: set_ for _get, set_, _sp, _bid, sid in iter_segments(translated)}
    if seg_id not in tr_by_id:
        raise IndexError(f"segment id {seg_id!r} absent from the {target_lang} translation")
    old = …  # read via a getter map if needed
    tr_by_id[seg_id](new_text)
    save_translated_model(…)
    return {"id": seg_id, "old_text": old, "new_text": new_text}
```

`apply_glossary_term`: iterate source by id and pass seg-id: `for get, _set, _sp, _bid, seg_id in iter_segments(source): if count_in_text(get(), pat): retranslate_one_segment(..., seg_id, "", ...)`.

`backend/main.py`: change `RetranslateBody.index: int` → `id: str`; the route calls `retranslate_one_segment(..., body.id, ...)`.

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_retranslate_idpair.py tests/test_retranslate.py tests/test_retranslate_api.py tests/test_glossary_manage.py -v` → PASS (update the existing retranslate tests to pass a seg-id string instead of an int index). Full suite → no NEW failures.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/process.py backend/main.py backend/tests/test_retranslate_idpair.py backend/tests/test_retranslate.py backend/tests/test_retranslate_api.py
git commit -m "feat(sync): retranslate + glossary-apply resolve segments by seg-id"
```

---

### Task 6: `render_docx` bookmarks by block_id + `read_block_lines` by id+order

**Files:**
- Modify: `backend/renderers/docx_renderer.py` (`_bookmark_block` + its callers pass `block.block_id`); `backend/pipeline/docx_sync.py` (`read_block_lines`)
- Test: `backend/tests/test_bookmark_id.py` (create)

**Interfaces:**
- `_bookmark_block(paragraph, block_id: str)` → stamps `blk{block_id}` (skips if `block_id` is None).
- `read_block_lines(docx_path) -> tuple[list[str], dict[str, list[str]]]` → `(ordered_block_ids, {block_id: lines})`.

- [ ] **Step 1: Write the failing test**

```python
import os, models
from renderers.docx_renderer import render_docx
from pipeline.docx_sync import read_block_lines

def test_bookmarks_use_block_id_and_read_back_in_order(tmp_path):
    m = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="zero", block_id="B0", source_span_ids=["s"]),
        models.ParagraphBlock(text="one", block_id="B1", source_span_ids=["s"]),
    ])
    out = os.path.join(str(tmp_path), "d.docx")
    render_docx(m, out, str(tmp_path))
    ordered, by_id = read_block_lines(out)
    assert ordered == ["B0", "B1"]
    assert by_id["B0"] == ["zero"] and by_id["B1"] == ["one"]
```

- [ ] **Step 2: Run to verify it fails** — bookmarks are `blk0`/`blk1`; read_block_lines returns int-keyed dict (old signature).

- [ ] **Step 3: Implement** — `_bookmark_block(paragraph, block_id)`: `if not block_id: return`; set `w:name = f"blk{block_id}"`, `w:id` to a stable small int (use a per-render counter or `str(abs(hash(block_id)) % (10**6))` — the numeric w:id need not be the block_id). Update the 5 call sites to pass `block.block_id`. In `read_block_lines`, parse the name suffix after `blk` as the block_id STRING (not int), collect ids in document order, return `(ordered_ids, by_id)`. Update the in-table skip + `_paragraph_lines` usage unchanged.

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_bookmark_id.py tests/test_docx_sync_read.py -v` → PASS (update `test_docx_sync_read` to the new tuple return + string ids). Full suite → no NEW failures.

- [ ] **Step 5: Commit**

```bash
git add backend/renderers/docx_renderer.py backend/pipeline/docx_sync.py backend/tests/test_bookmark_id.py backend/tests/test_docx_sync_read.py
git commit -m "feat(sync): docx bookmarks keyed by block_id; read_block_lines returns ordered ids"
```

---

### Task 7: `render_html` `data-seg` = seg-id; anchor `blk{block_id}`

**Files:**
- Modify: `backend/renderers/html_renderer.py` (the `_seg` stamping + block anchor `id="blk{i}"` → `id="blk{block_id}"`)
- Test: `backend/tests/test_render_html_segments.py` (update existing) + a new assertion

**Interfaces:**
- Consumes: `build_segment_index_map` now returns seg-id strings (Task 3).
- Produces: rendered HTML where `data-seg="{seg_id}"` and block anchors are `id="blk{block_id}"`.

- [ ] **Step 1: Update/extend the test**

In `backend/tests/test_render_html_segments.py`, change expectations: `data-seg` values are the seg-id strings from `build_segment_index_map` (not ints); the lockstep assertion (`set(data-seg in html) == set(build_segment_index_map.values())`) holds with strings. Add: a block's anchor is `id="blk{block_id}"`.

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_render_html_segments.py -v` → FAIL on the new string expectations.

- [ ] **Step 3: Implement** — in `html_renderer.py`, the `_seg(text, key)` helper looks up `build_segment_index_map`[key] (now a seg-id string) and stamps `data-seg="{seg_id}"`. The block anchor `anchor = f' id="blk{i}"'` → `f' id="blk{block.block_id}"'` (guard None). Keep the per-block loop otherwise unchanged (deleted-skip from the prior increment stays until Task 9).

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_render_html_segments.py tests/test_render_html_layout.py tests/test_render_html_highlight.py -v` → PASS. Full suite → no NEW failures.

- [ ] **Step 5: Commit**

```bash
git add backend/renderers/html_renderer.py backend/tests/test_render_html_segments.py
git commit -m "feat(sync): html data-seg = stable seg-id; block anchors by block_id"
```

---

### Task 8: Frontend — `Segment.id`/`block_id`, retranslate by id, string data-seg

**Files:**
- Modify: `frontend/src/lib/types.ts` (Segment), `frontend/src/lib/api.ts` (`retranslateSegment` body `id`), `frontend/src/components/SegmentPreview.tsx`, `frontend/src/components/RetranslatePanel.tsx`, `frontend/src/app/c/[id]/page.tsx`
- Verify: `cd frontend && npm run build`

**Interfaces:**
- Consumes: `build_segments` dicts now have `id`/`block_id`; `data-seg` are strings; `/retranslate-segment` takes `{id}` (Task 5).

- [ ] **Step 1: Update the types + calls**

- `types.ts` `Segment`: add `id: string; block_id: string;` (keep `index` for display; the previous `block: number` field becomes unused — remove it and its usages).
- `api.ts` `retranslateSegment`: body `{ lang, id, preset?, guidance? }` (was `index`).
- `SegmentPreview.tsx`: the click handler reads `data-seg` as a STRING id and calls `onSelectSeg(id: string)`; `activeSeg` keyed by `seg.id`.
- `RetranslatePanel.tsx`: re-translate posts `seg.id`; any `seg.index` used for selection becomes `seg.id`.
- `page.tsx`: `setActiveSeg(segments.find((s) => s.id === id) ?? null)`; editor scroll uses `blk{activeSeg.block_id}` (was `blk{activeSeg.block}`); the post-edit poll/preview-reload logic unchanged.

- [ ] **Step 2: Build** — `cd frontend && npm run build` → must succeed, no TS errors. (No FE unit-test harness; the prod build + Task 10 live check are the gates.)

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts frontend/src/components/SegmentPreview.tsx frontend/src/components/RetranslatePanel.tsx "frontend/src/app/c/[id]/page.tsx"
git commit -m "feat(sync): frontend uses stable seg-id (retranslate, click-select, scroll)"
```

---

### Task 9: `docx_sync` — rebuild translated order by docx ids (reorder + drop); remove tombstone

**Files:**
- Modify: `backend/pipeline/docx_sync.py` (`apply_docx_edits_to_model`); `backend/models.py` (remove `deleted` field); `backend/pipeline/process.py` + `backend/renderers/*` (remove `deleted` skips); delete `backend/tests/test_docx_sync_deletion.py`/`test_build_segments_deletion.py`/`test_render_deletion.py`'s tombstone assumptions or fold into new reorder/drop tests
- Test: `backend/tests/test_docx_sync_reorder.py` (create)

**Interfaces:**
- `apply_docx_edits_to_model(model, docx_path)` → returns a model whose `blocks` are reordered to match the docx's `ordered_block_ids`, with absent-id blocks dropped, non-bookmarked blocks anchored to the preceding surviving block, and surviving blocks' text updated.

- [ ] **Step 1: Write the failing test**

```python
import os, models
from pipeline.docx_sync import apply_docx_edits_to_model
from renderers.docx_renderer import render_docx
from docx import Document
from docx.oxml.ns import qn

def _reorder_paragraphs(path):  # swap the first two w:p that carry blk bookmarks
    d = Document(path); body = d.element.body
    ps = [p for p in body.iter(qn("w:p")) if any(
        (bm.get(qn("w:name")) or "").startswith("blk") for bm in p.iter(qn("w:bookmarkStart")))]
    if len(ps) >= 2:
        a, b = ps[0], ps[1]; a.addprevious(b)   # move b before a
    d.save(path)

def test_apply_reorders_and_drops(tmp_path):
    m = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="zero", block_id="B0", source_span_ids=["s"]),
        models.ParagraphBlock(text="one", block_id="B1", source_span_ids=["s"]),
        models.ParagraphBlock(text="two", block_id="B2", source_span_ids=["s"]),
    ])
    out = os.path.join(str(tmp_path), "d.docx"); render_docx(m, out, str(tmp_path))
    _reorder_paragraphs(out)                                  # editor moves B1 before B0
    synced = apply_docx_edits_to_model(m, out)
    assert [b.block_id for b in synced.blocks][:3] == ["B1", "B0", "B2"]   # reordered to docx order
```

(Add a drop test: delete a paragraph → its id gone → block absent from `synced.blocks`.)

- [ ] **Step 2: Run to verify it fails** — current apply only updates text/tombstones; it never reorders or drops.

- [ ] **Step 3: Implement** — rewrite `apply_docx_edits_to_model`:

```python
def apply_docx_edits_to_model(model, docx_path):
    ordered_ids, edits = read_block_lines(docx_path)
    out = model.model_copy(deep=True)
    by_id = {b.block_id: b for b in out.blocks if getattr(b, "block_id", None)}
    # group non-bookmarked blocks (figures/dividers/tables) under the PRECEDING surviving id
    trailing = {}   # block_id -> [non-bookmarked blocks that followed it in the old order]
    anchor = None
    leading = []    # non-bookmarked blocks before the first bookmark
    for b in out.blocks:
        if getattr(b, "block_id", None) in by_id and b.block_id in edits:  # a readable text block (potential anchor)
            anchor = b.block_id
        elif getattr(b, "block_id", None) not in edits:   # non-bookmarked (figure/divider/table) or absent
            (trailing.setdefault(anchor, []) if anchor else leading).append(b)
    new_blocks = list(leading)
    for bid in ordered_ids:
        b = by_id.get(bid)
        if b is None:
            continue
        _apply_text(b, edits[bid])      # existing text-update logic, extracted into a helper
        new_blocks.append(b)
        new_blocks.extend(trailing.get(bid, []))
    out.blocks = new_blocks
    return out
```

Extract the existing per-block text-update (marker strip, `.lines` length preservation, TOC skip) into `_apply_text(block, lines)`. Remove the `deleted` flag from `models.py` and all `getattr(block,"deleted",…)` skips in `build_segments`/`render_docx`/`render_html` (deletion now = absent from `new_blocks`). Re-point/retire the three tombstone test files.

- [ ] **Step 4: Run to verify it passes** — `pytest tests/test_docx_sync_reorder.py -v` → PASS. Then full suite → no NEW failures (the deletion behavior is now via drop; ensure the deletion-style tests assert drop, not the flag).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/docx_sync.py backend/models.py backend/pipeline/process.py backend/renderers/docx_renderer.py backend/renderers/html_renderer.py backend/tests/
git commit -m "feat(sync): docx_sync rebuilds translated order from docx ids (reorder+drop); drop tombstone"
```

---

## Verification (controller-run live checkpoint, with screenshots)

After Task 9, full stack (DS :8080, backend with the new code on a fresh port, prod frontend build, `DEV_NO_AUTH=1`); re-render the test doc for a fresh DS key.

- [ ] **Reorder:** in 문서 편집, move a body paragraph above another (cut/paste or drag) → 재번역 → the list + preview reflect the new order; click the moved segment → the correct source location highlights; re-translate it → the right sentence updates. Screenshot.
- [ ] **Delete:** delete a unique line → it vanishes from list + preview (drop path). Screenshot.
- [ ] **No-op:** a doc with no structural change is byte-identical in the views to before the migration (regression check).
- [ ] **Backfill:** an existing doc (created before this migration) opens, gets ids assigned, and behaves normally.

---

## Self-Review

**1. Spec coverage:** block_id+backfill → T1-2; bookmarks by id → T6; id-keyed iter_segments/index_map → T3; build_segments id-pairing → T4; retranslate/glossary by id → T5; data-seg seg-id + frontend → T7-8; reorder+drop+tombstone-removal → T9; live reorder/delete/backfill → Verification. All spec components covered.

**2. Placeholder scan:** the larger rewrites (T3 iter_segments, T9 apply) describe the transformation with code skeletons + exact tests rather than a full line-for-line file; each names the exact functions, the exact seg-id format (Global Constraints), and a concrete failing test. No "TBD"/"handle edge cases".

**3. Type consistency:** `iter_segments` 5-tuple `(getter, setter, span_ids, block_id, seg_id)` is used consistently in T3/T4/T5; `make_seg_id` format matches the Global-Constraints seg-id spec and the T3 test; `read_block_lines` tuple return `(ordered_ids, by_id)` matches T6/T9; `build_segments` dict keys (`id`, `block_id`, `index`, `source`, `translated`, `source_bbox`) match T4 + the frontend `Segment` (T8); retranslate `id: str` matches main.py + frontend (T5/T8).

**Note for the executor:** several existing tests assert positional/int identity (retranslate int index, docx_sync_read int keys, render_html_segments int data-seg, the 3 tombstone test files). Updating them to the id-based contract is part of the task that changes that contract (called out in T3/T5/T6/T7/T9). Keep the full suite green at each commit (excluding the known 15 pre-existing protected-file failures).
