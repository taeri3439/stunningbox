# Editor Deletion Sync (tombstone soft-delete) — Design

**Date:** 2026-06-30
**Status:** Approved (design)
**Builds on:** Phase 1 text-sync (`2026-06-27-onlyoffice-to-model-sync-design.md`) + instant-reflect force-save (`2026-06-29-instant-reflect-forcesave-design.md`), branch `editor-model-sync-2026-06-27`.
**Scope:** Phase 2, Increment 1 of the "structural sync" work (chosen Approach A — staged). This spec covers **deletion only**. Additions + reordering are a separate, larger spec (stable-ID migration) done next.

## Problem

Deleting a whole line/paragraph in 문서 편집 (OnlyOffice) is **not reflected** in the 재번역/용어/고유명사 views. Root cause (confirmed in code): `apply_docx_edits_to_model` ([docx_sync.py:76-77](../../../backend/pipeline/docx_sync.py#L76-L77)) matches paragraphs by their `blk{i}` bookmark and only updates the *text* of blocks that still exist — `if i not in edits: continue`. A deleted paragraph's bookmark disappears, so the sync skips it and leaves the old block in the model. The function never removes blocks; its header says structural diffs are Phase 2.

## Why deletion is safe but add/reorder are not (the constraint)

Segment identity across the whole app is **positional**: `iter_segments` ([models.py:293-338](../../../backend/models.py#L293-L338)) defines one ordered enumeration, and source↔translated pairing ([process.py:473-483](../../../backend/pipeline/process.py#L473-L483)), re-translate ([process.py:494-521](../../../backend/pipeline/process.py#L494-L521)), glossary apply ([process.py:591-595](../../../backend/pipeline/process.py#L591-L595)), the docx `blk{i}` bookmarks, the HTML `data-seg`, and the frontend all key off that ordinal. It works only because `translate_document` produces the translated model as an in-place structural copy of source (same block count/order). **Reorder and additions break this invariant** and require a stable-ID rework (next spec). **Deletion can be done without touching positional pairing** by keeping the block in the list (positions/bookmarks unchanged) and flagging it.

## The one invariant this design must not violate

`iter_segments` and `build_segment_index_map` ([models.py:340-381](../../../backend/models.py#L340-L381)) **stay unchanged** — they continue to enumerate *every* block positionally, including deleted ones. If they skipped deleted blocks, `iter_segments(source)` (no deleted flag) and `iter_segments(translated)` (has deleted flags) would diverge in length/order and corrupt the positional pairing in `build_segments`/`retranslate_one_segment`. **Skipping deleted blocks happens only at the output/render layer**, where the per-segment indices are already assigned and remain stable (the deleted segment simply leaves an unused gap in the index space).

## Architecture & flow

```
User deletes a paragraph in 문서 편집
   → force-save gate (feature a) → DS status-6 callback → backend downloads docx
   → apply_docx_edits_to_model(model, docx):
        edits = read_block_lines(docx)         # surviving blk{i} (text blocks; tables skipped)
        for i, block in enumerate(model.blocks):
            if isinstance(block, _TEXT_BLOCKS):
                block.deleted = (i not in edits)   # absent bookmark ⇒ deleted; present ⇒ not
            # (existing text-update logic for surviving blocks unchanged)
   → save_translated_model(translated.json)    # now carries deleted flags
        ▼
Readers SKIP deleted blocks (indices already fixed, so survivors are unaffected):
   - build_segments: don't emit a segment whose translated block is deleted
   - render_docx / render_html: don't render deleted blocks (block stays in list ⇒ blk{i} ids stable)
   - 용어/고유명사 audits: consume build_segments ⇒ skip automatically (no change)
        ▼
재번역/용어/고유명사 views + the re-rendered docx omit the deleted paragraph.
```

## Components

### 1. Model — `deleted` flag
[backend/models.py](../../../backend/models.py): add `deleted: bool = False` to the four bookmarked text-block classes — `HeadingBlock` (67-81), `ParagraphBlock` (84-100), `ListItemBlock` (103-119), `CaptionBlock` (181-195). Optional-with-default and no model uses `extra='forbid'`, so existing persisted `source.json`/`translated.json`/`edited.json` load unchanged (the field defaults to `False`). Tables/figures/dividers do **not** get the flag (their deletion isn't detectable via the current bookmark scheme). All readers use `getattr(block, "deleted", False)` so block kinds without the field are safe.

### 2. Detection — `apply_docx_edits_to_model`
[backend/pipeline/docx_sync.py:73-102](../../../backend/pipeline/docx_sync.py#L73-L102): inside the existing `for i, block in enumerate(out.blocks)` loop, before the existing `if i not in edits ... continue`, set the deleted flag for text blocks:
```python
if isinstance(block, _TEXT_BLOCKS):
    block.deleted = i not in edits   # bookmark gone ⇒ user deleted it; present ⇒ live (clears a prior flag)
```
The existing text-update branch (which runs only when `i in edits`) is unchanged. Non-text blocks (tables/figures/dividers) are left untouched. `read_block_lines` is unchanged.

### 3. Readers skip deleted
- **`build_segments`** ([process.py:473-483](../../../backend/pipeline/process.py#L473-L483)): the loop iterates source segments and indexes `translated`. Add: if the translated block at `block_idx` is deleted, `continue` (don't append). Because the loop still advances `i` over every source segment, the indices of non-deleted segments are unchanged — the deleted one just never appears in the output list.
- **`render_docx`** ([docx_renderer.py:134](../../../backend/renderers/docx_renderer.py#L134)): `if getattr(block, "deleted", False): continue` at the top of the per-block loop. Deleted blocks stay in `doc.blocks` (so `enumerate` index `i` — the `blk{i}` id — is unchanged for survivors) but emit nothing.
- **`render_html`** ([html_renderer.py:135](../../../backend/renderers/html_renderer.py#L135)): same `getattr` skip in its per-block loop. `build_segment_index_map` stays complete (counts deleted blocks), so the `data-seg` values stamped on the rendered (non-deleted) blocks remain consistent with `build_segments` indices.
- **Audits** (`glossary_audit`, `proper_noun_audit`): consume `build_segments` output → deleted segments are already gone → no change.

### 4. No change needed
`iter_segments`, `build_segment_index_map`, `iter_text_fields`, `translate_document`, `retranslate_one_segment`, `get_translated_model`, the force-save callback, and the frontend are unchanged. The lazy-sync and callback paths already route through `apply_docx_edits_to_model`, so the flag is set wherever a save is synced.

## Edge cases & limits (honest)

- **Source pane mismatch:** the original-pane HTML renders the *source* model (no `deleted` flag), so a paragraph deleted in the translation still shows on the source side, while its segment-list row is gone. Cosmetic only; acceptable for v1, refine later if needed.
- **Tables / figures / dividers:** deletion not detected (no readable bookmark) → out of scope; the stable-ID migration handles them.
- **Reorder / additions:** not handled here (next spec).
- **Un-delete:** setting `block.deleted = (i not in edits)` every sync means a paragraph that reappears in the docx (bookmark returns) automatically clears its flag — correct and idempotent.
- **False positives:** a text block flagged deleted only when its bookmark is absent; the Phase 1 spike verified the DS preserves all `blk{i}` bookmarks through a save round-trip, so absence reliably means user deletion.
- **Tombstones persist** in `translated.json`; the later stable-ID migration supersedes this mechanism (it drops vanished-id blocks outright).
- Deleted blocks are still translated on a full regenerate and still walked by `bulk_replace`/glossary — harmless (not shown), and not worth special-casing (YAGNI).

## Testing

**Backend (pytest):**
- `apply_docx_edits_to_model`: a text block whose `blk{i}` bookmark is absent → `deleted == True`; a block still present → `deleted == False` (clears a prior flag); table/figure/divider blocks are never flagged; surviving blocks' text still updates as before.
- `build_segments`: with a deleted block in the translated model, its segment is omitted **and** the indices of the remaining segments are unchanged vs. the no-deletion baseline (assert exact index values).
- `render_docx`: a deleted block emits no paragraph/bookmark, and the `blk{i}` ids of the surviving blocks are unchanged (assert the bookmark set).
- `render_html`: deleted block not rendered; `data-seg` values on survivors unchanged.
- Round-trip: render a model, remove one text block's paragraph+bookmark from the docx, `apply_docx_edits_to_model` → that block `deleted`, others intact → `build_segments` omits exactly that segment.

**Live (browser, prod build):** delete a body line/paragraph in 문서 편집 → click 재번역 (force-save gate) → within a couple seconds the line is gone from the segment list + preview, with no manual refresh; 용어/고유명사 likewise. Confirm a non-deleted neighbor's text/order is intact (no shift/corruption).

## Notes / risks

- The change is additive (a default-`False` flag + skip-checks); non-deleted documents behave exactly as before.
- The main risk is a reader that forgets to skip deleted blocks (showing a ghost paragraph) — covered by the render + build_segments tests.
- Keep `iter_segments`/`build_segment_index_map` untouched — that is the load-bearing invariant; a reviewer should specifically confirm neither was modified.
