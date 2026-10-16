# Stable-ID Foundation + Reorder Sync — Design

**Date:** 2026-06-30
**Status:** Approved (design)
**Builds on:** Phase 1 text-sync, instant-reflect gate, deletion-tombstone — branch `editor-model-sync-2026-06-27` (working state at commit `6a7ad5e`).
**Scope:** Phase 2, Increment 2 — **stable-ID foundation + reorder** (deletion folded into id-based drop). **Additions** (new paragraphs with no source) are a separate spec (Increment 3), explicitly out of scope here.

## Problem

Reordering paragraphs in 문서 편집 (OnlyOffice) is not reflected in the tool views, because segment identity across the whole app is **positional** — `iter_segments` ([models.py:293-338](../../../backend/models.py#L293-L338)) defines one ordered enumeration and everything (source↔translated pairing in `build_segments` [process.py:473-483](../../../backend/pipeline/process.py#L473-L483), `retranslate_one_segment` [process.py:494-521](../../../backend/pipeline/process.py#L494-L521), glossary apply [process.py:591-595](../../../backend/pipeline/process.py#L591-L595), the `blk{i}` bookmarks, the HTML `data-seg`, and the frontend) keys off the ordinal. That holds only because `translate_document` never reorders blocks. Reordering breaks it: positions diverge between source (unchanged) and translated (reordered), so every consumer mispairs.

## The fix in one sentence

Give every block a **stable `block_id`** assigned once at ingest; identify each segment by a composite **`(block_id, sub-key)`**; thread that ID through the bookmarks, the renderers, the pairing, re-translate, and the frontend — so the translated model can reorder (and drop deleted blocks) without corrupting source↔translated alignment.

## Scope

**In:** stable `block_id`; ID-based source↔translated pairing; `blk{block_id}` bookmarks; `data-seg` = stable segment-id; reorder sync (rebuild translated block order from the docx); deletion re-expressed as id-based drop (supersedes the tombstone); backfill for existing docs.

**Out (Increment 3):** additions — new paragraphs typed in the editor have no `blk` bookmark, so they're simply not detected here (they don't appear in the views until Increment 3). Tables/figures internal structural edits remain out (Phase 1/2 text-only for those).

## Identity model (the heart of the change)

- **`block_id`**: a uuid hex string (32 chars; fits the ≤40-char, `[0-9a-zA-Z._-]` bookmark-name limit as `blk{block_id}`). Assigned once per block at source-model creation; copied verbatim into translated/edited models (they're structural copies).
- **Segment id** (stable string): `f"{block_id}:{subkey}"` where subkey ∈ `text` | `line:{i}` | `cell:{ci}` | `cellline:{ci}:{li}` | `overlay:{oi}`. Running-band fields (no block) keep a fixed id like `band:header:left` (bands never reorder). This composite is the new identity for every translatable segment.
- The integer ordinal that `iter_segments` produces today is **retired from the identity role** — it may remain as a display number, but `data-seg`, click-matching, re-translate, and editor-scroll all use the stable id (or `block_id`).

## Components

### A. Model — `block_id` ([models.py](../../../backend/models.py))
Add `block_id: Optional[str] = None` to the 7 block classes via a shared base mixin (each keeps its `type` discriminator). Optional+default → existing persisted JSON loads (id stays `None` until backfilled). Not on `TableCell`/`RunningBand`/`FigureOverlay` (cells/overlays are addressed as sub-keys of their block; overlays already carry their own `id`).

### B. Ingest assigns ids + backfill ([process.py](../../../backend/pipeline/process.py))
- Assign a fresh uuid to every block at the source-model creation points: `build_source_model` (~[105](../../../backend/pipeline/process.py#L105)), `build_source_model_from_office` (~[134](../../../backend/pipeline/process.py#L134)), `build_source_model_from_hwp` (~[161](../../../backend/pipeline/process.py#L161)).
- **Lazy backfill on load** (`load_source_model` / `get_translated_model`): if a loaded model has blocks without `block_id`, assign ids to the source and mirror them onto the translated/edited model **by the current positional order** (still valid pre-reorder), then persist once. A small helper `ensure_block_ids(source, *others)` does this idempotently. After backfill, ids are stable forever.

### C. Bookmarks by id ([docx_renderer.py](../../../backend/renderers/docx_renderer.py), [docx_sync.py](../../../backend/pipeline/docx_sync.py))
- `_bookmark_block(p, block.block_id)` → name `blk{block_id}` (was `blk{position}`). Same block kinds as today (heading/list/paragraph/caption + table first-cell; figures/dividers none).
- `read_block_lines` → returns `(ordered_ids, {block_id: lines})`: the block-ids in docx order **and** their text. (Parses the name suffix as a string, not `int`.)

### D. Structural diff — reorder + drop ([docx_sync.py](../../../backend/pipeline/docx_sync.py) `apply_docx_edits_to_model`)
Rebuild `translated.blocks` from the docx:
1. Index the model's blocks by `block_id`.
2. Walk the docx's `ordered_ids`: for each, take the model block with that id (carry it over, updating its text from the docx as today) → this produces the **reordered** surviving list.
3. Model blocks whose id is **not** in `ordered_ids` are **dropped** (deletion). Non-bookmarked blocks (figures/dividers, table interiors) that never had a readable bookmark are **kept in place** relative to their neighbours (anchored to the previous surviving bookmark) so they aren't lost — they ride along with the block before them.
4. Docx ids not in the model (additions) → ignored (Increment 3).

This **supersedes the tombstone**: the `deleted` flag and its skip-checks (from the deletion increment) are removed; deletion is now "absent id ⇒ not in the rebuilt list."

### E. ID-based pairing ([process.py](../../../backend/pipeline/process.py), [models.py](../../../backend/models.py))
- `iter_segments` / `build_segment_index_map` → yield/keep the composite seg-id (block_id + sub-key) instead of the positional `bi`. Keep the two **in lockstep** (the existing `test_render_html_segments` contract).
- `build_segments` → enumerate the **translated** model (current/editor order); for each segment compute its seg-id; look it up in a **source seg-id → (getter, span_ids)** map to attach the original text + bbox. Emit `{id, source, translated, source_bbox, block_id, ...}`. Segments whose source id is missing (Increment-3 additions) would show source = "" — not reachable in this increment.
- `retranslate_one_segment` / `apply_glossary_term` → resolve the source field and the translated field **by seg-id** (build id→field maps for both models), not by ordinal. Survives reorder.

### F. Renderers ([html_renderer.py](../../../backend/renderers/html_renderer.py))
`data-seg` = the stable seg-id string (was the ordinal); block anchor = `blk{block_id}`. Because both the source-preview and the translated-preview stamp the **same** seg-ids, a click in either pane maps to the same logical segment even when their orders differ.

### G. Frontend ([types.ts](../../../frontend/src/lib/types.ts), [api.ts](../../../frontend/src/lib/api.ts), SegmentPreview, RetranslatePanel, [page.tsx](../../../frontend/src/app/c/[id]/page.tsx))
- `Segment` gains `id: string` (stable) and `block_id: string`; click-matching uses `s.id` (the page already resolves via `segments.find(...)`, so it becomes `find(s => s.id === clickedId)`); re-translate posts `id` instead of `index`; editor scroll uses `blk{block_id}`.
- `data-seg` values are now strings; `SegmentPreview`'s click handler reads the string id.

## List order (confirmed UX)

The 재번역 list and the translated preview follow the **editor (translated) order**; the original/source pane stays in source order. Pairing by seg-id keeps the click→source-highlight links correct across the order difference.

## De-risking strategy (this is the core model every feature uses)

1. **Foundation is behavior-preserving.** Steps A–C, E, F, G with **no reorder yet** must produce results *identical* to today when nothing is reordered. The gate: the entire existing suite stays green — especially the `iter_segments`≡`build_segment_index_map` lockstep test, `build_segments`, retranslate, glossary/proper-noun audits, and the render tests.
2. **Reorder (step D's reorder branch) lands last**, with its own tests + a live verification.
3. **Backfill is idempotent** and runs lazily; existing docs keep working (ids assigned by current order while still valid).
4. **Deletion is re-verified** after the tombstone→drop switch (the line still vanishes).
5. Git `6a7ad5e` is the fallback if the migration must be abandoned.

## Edge cases & limits

- **Additions** not handled (Increment 3) — a typed-in paragraph has no `blk` bookmark, so it's invisible to the sync; documented limit.
- **Non-bookmarked blocks** (figures/dividers, table interiors) have no id in the docx; the diff anchors them to the preceding surviving block so reorder/drop of *text* blocks doesn't lose them. A reorder that moves a figure alone (no adjacent text bookmark change) isn't captured — acceptable for v1.
- **Backfill collision:** uuids are unique; mirroring source→translated by position is valid only pre-reorder, which is exactly when backfill runs (first load after upgrade).
- **Bookmark survival:** the Phase 1 spike verified the DS preserves `blk{…}` bookmarks through a round-trip; the id scheme relies on that (now the name is the uuid).

## Testing

**Behavior-preserving (foundation):** the full existing backend suite stays green (the lockstep test, build_segments, retranslate, audits, render) — proving id-based pairing == positional when nothing reorders.

**New backend tests:**
- `block_id` assigned at ingest; lazy backfill assigns + mirrors + persists; idempotent on re-load.
- `read_block_lines` returns docx-order ids + text; `blk{uuid}` round-trips.
- `apply_docx_edits_to_model`: reorder (docx ids in a new order → translated.blocks reordered to match); drop (absent id → block gone); non-bookmarked block ride-along preserved.
- `build_segments` after a simulated reorder: each translated segment pairs to the **correct** source (by id), source_bbox correct, list in translated order.
- `retranslate_one_segment` by id after reorder writes the right source/translated field (the bug that positional would cause).
- `data-seg` are stable seg-ids; source-preview and translated-preview stamp matching ids.

**Live (browser, prod build):** reorder a paragraph in 문서 편집 → 재번역 → the list + preview reflect the new order; clicking a moved segment highlights the correct source location; re-translating it writes the right sentence; deletion still vanishes; a non-reordered doc is unchanged.

## Decomposition note

This spec is one cohesive design but a large implementation. The plan will sequence it as: (A) model+ingest+backfill → (B) bookmarks by id → (C) id-based `iter_segments`/`build_segment_index_map` + `build_segments` pairing (behavior-preserving) → (D) retranslate/glossary by id → (E) renderer `data-seg` + frontend → (F) docx_sync reorder+drop (the only step that changes behavior) → live verify. Each step keeps the suite green; reorder is exercised only at step F.
