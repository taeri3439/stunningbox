# Editor Additions Sync — Design

**Date:** 2026-07-01
**Status:** Approved (design)
**Builds on:** the stable-ID foundation + reorder (`2026-06-30-stable-id-reorder-design.md`) — branch `editor-model-sync-2026-06-27`.
**Scope:** Phase 2, Increment 3 (final) — reflect **new paragraphs added in 문서 편집** into the tool views. Completes structural sync (delete + reorder + **add**).

## Problem

A paragraph typed anew in the OnlyOffice editor has **no `blk{block_id}` bookmark** (bookmarks are stamped only for blocks that existed at render time). After the stable-ID migration, `apply_docx_edits_to_model` rebuilds `translated.blocks` from the docx's bookmarked ids, so a bookmark-less new paragraph is simply **not carried over** — the addition is invisible in 재번역/용어/고유명사 and the re-rendered docx.

## Approach (A — unified rebuild)

Generalize the docx→model rebuild to also **insert** bookmark-less text paragraphs as first-class blocks. `read_block_lines` returns an *ordered* list of entries `(block_id | None, lines)`; `apply_docx_edits_to_model` walks it — known id → carry/update (reorder), absent id → drop (delete), `None`+text → **create a new block, insert here** (add). One coherent walk handles all three structural operations. A `w:p` with text but no `blk` bookmark is unambiguously a user addition (figures are images with no `w:t`; dividers are rules; table interiors are skipped) — so nothing else is mistaken for an addition.

## Components

### 1. `read_block_lines` → ordered entries incl. new paragraphs
[backend/pipeline/docx_sync.py](../../../backend/pipeline/docx_sync.py): currently returns `(ordered_ids: list[str], by_id: dict[str, list[str]])`. Change to return an **ordered list** `entries: list[tuple[str | None, list[str]]]` in document order:
- a bookmarked text paragraph → `(block_id, lines)`;
- a text paragraph with **no** `blk` bookmark and non-empty text → `(None, lines)` (an addition);
- empty paragraphs and table-internal paragraphs → skipped (as today).

Provide a small compatibility helper if other call sites want the old `(ordered_ids, by_id)` shape, or update the one consumer (`apply_docx_edits_to_model`) directly. (Only `apply` consumes it — the callback + lazy-sync route through `apply`.)

### 2. `apply_docx_edits_to_model` → insert new blocks
[backend/pipeline/docx_sync.py](../../../backend/pipeline/docx_sync.py): walk `entries` to rebuild `out.blocks`:
- `(block_id, lines)` with `block_id` in the model → carry that block, `_apply_text(block, lines)`, then its ride-along non-bookmarked blocks (T9 logic).
- `(None, lines)` → create `ParagraphBlock(block_id=uuid.uuid4().hex, text="\n".join(lines) if len(lines) > 1 else lines[0], source_span_ids=[])` and append at this position. (Multi-line handling mirrors `_apply_text`'s existing `.lines`/`.text` convention: a single line → `.text`; multiple → `.lines`.)
- model blocks whose id never appears → dropped (delete).
The new block has a real `block_id`, so on the next render it gets a `blk` bookmark and becomes a **first-class block** — subsequent reorder/delete/edit work normally.

### 3. `build_segments` → emit added segments
[backend/pipeline/process.py](../../../backend/pipeline/process.py): the loop enumerates the translated model and looks up each seg-id in the source map (`src_by_id`). Change the skip logic to distinguish:
- seg-id **not in** `src_by_id` → an addition (no source counterpart) → emit `{id, block_id, index, source: "", translated, source_bbox: None, added: True}` — do **not** apply the empty-source skip.
- seg-id **in** `src_by_id` but the source is empty/atomic → skip (unchanged).
Non-added segments get `added: False`.

### 4. Frontend — added-row display
[frontend/src/lib/types.ts](../../../frontend/src/lib/types.ts) `Segment` gains `added: boolean`. In the 재번역 list (RetranslatePanel) and SegmentPreview: an added row shows the translated text with an **"원문 없음 · 추가됨" badge** on the original side, and its re-translate button is **disabled** (no source). Added segments have no `source_bbox`, so the original-pane highlight is naturally skipped for them.

### 5. Retranslate guard (defense-in-depth)
[backend/pipeline/process.py](../../../backend/pipeline/process.py) `retranslate_one_segment` already raises `IndexError` when the seg-id is absent from the source (which is exactly the added-block case), so a stray call is rejected cleanly. The frontend disables the button; the backend guard is the backstop.

## Edge cases & limits

- **Paragraph split:** the original half keeps its `blk` bookmark (its text updated to the first half); the second half is a bookmark-less addition → inserted. Handled.
- **Empty new paragraph** (Enter with no text): no `w:t` text → skipped (no spurious empty block).
- **Added block position:** inserted at its docx-flow position among the surviving/ordered entries; the ordered walk places it correctly relative to neighbours.
- **Table/figure additions:** out of scope (can't meaningfully type those in the flow; no text paragraph).
- **Added then re-translate:** no source → re-translate disabled (UI) + backend IndexError (backstop). An added paragraph is already target-language text the user wrote; there is nothing to translate from.
- **Promotion:** after the first sync an addition has a `block_id`; it is indistinguishable from an original block thereafter (gets a bookmark on render), so all structural ops apply to it.

## Testing

**Backend:**
- `read_block_lines`: returns ordered `entries` with `(None, lines)` for a bookmark-less text paragraph interleaved among bookmarked ones, in document order; empty/table paragraphs excluded.
- `apply_docx_edits_to_model`: a docx with a new text paragraph between two bookmarked blocks → `out.blocks` gains a new `ParagraphBlock` (fresh `block_id`, correct text) at that position; existing reorder + delete still pass; a compose test (add + reorder + delete in one docx).
- `build_segments`: an added block (seg-id absent from source) is emitted with `source=""`, `added=True`; a genuinely empty-source paired segment is still skipped; non-added segments carry `added=False`.
- `retranslate_one_segment` on an added seg-id raises IndexError.

**Frontend:** `npm run build` clean; added rows render the badge + disabled re-translate (manual/build gate — no FE unit harness).

**Live (browser, prod build):** type a new paragraph in 문서 편집 → 재번역 → it appears in the list (원문 없음 badge, re-translate disabled) + in the translated preview + would export in the docx; then reorder it / delete it → those work (it's now a first-class block). Confirm a non-added neighbour is unaffected.

## Risk

Moderate — lower than the foundation (the identity work is done). Behavior changes are localized: `read_block_lines` shape, `apply` insert path, `build_segments` emit path, and the frontend badge. The `read_block_lines` return-shape change touches only `apply` (its sole consumer). Keep the existing reorder/delete tests green (add composes with them). Fallback: git `252a66a` (the completed foundation) is intact.
