# Running header/footer band fixes (dedup · span clustering · page numbers) — Design

**Date:** 2026-07-02  **Status:** Approved  **Branch:** `fidelity-followups-2026-07-02`
**Scope:** PDF reconciler band detection + docx band rendering. Deterministic only (no API). Feature 1 of the 4 fidelity follow-ups.

## Problem (evidence: test-pharma band data)

The RunningBand pipeline (analyzer `chrome` blocks → reconciler `_pick_band` → docx section header/footer) already works, but three defects degrade the rendered bands:

1. **Cross-side duplication.** The analyzer emits a page's header sometimes as ONE merged chrome block ("2.7.2 Summary…   CB-03-01… cream 1%" → median-x → `center`) and sometimes as split left/right blocks. Per-side voting (`_pick_running`) then fills **all three sides**, with `center` = concatenation of left+right → `_render_band` draws the text twice.
   Observed: `left="2.7.2 Summary…"`, `center="2.7.2 Summary… CB-03-01…"`, `right="CB-03-01…"`.
2. **Merged multi-part chrome.** A single chrome block spanning distant runs ("Sun Pharmaceutical Industries Ltd.␣␣␣…␣␣Confidential") is classified to ONE side by the median x of all its spans, baking run-on whitespace into one cell instead of splitting into sides.
3. **Page numbers lost.** "Page 1 of 32" varies per page → fails `_pick_running`'s repeat vote, and `_page_number_side`'s charset (`[\divxlcDIVXLC\-\.\s/]+`) rejects the words "Page/of" → no PAGE field at all. Original page numbering disappears.

## Approach (all in `backend/pipeline/reconciler.py` + small model/renderer additions)

**A. Span-gap clustering** — new pure helper `_split_chrome_clusters(spans, page_w) -> list[(text, x_center, y_center)]`: sort a chrome block's spans by x0, split where the horizontal gap exceeds `0.08 * page_w` (24pt fallback), join each cluster's span texts with single spaces. The chrome branch (reconciler.py:177-189) emits one `(text, side, page)` item **per cluster** (side from the cluster's own x-center; header/footer by the cluster's y). Fixes 2 and makes per-page classification consistent (a merged block yields the same left/right items as split blocks), reducing 1 at the source.

**B. Cross-side dedup** — new pure helper `_dedupe_band_sides(left, center, right)` applied in `_pick_band`: normalize whitespace; drop `center` when it merely restates the split sides (`center == left`, `center == right`, or both `left` and `right` are substrings of `center`); drop `right` when `left == right`. Safety net for pages where clustering still disagrees.

**C. Page-number formats** — replace `_page_number_side` with `_page_number_spec(items) -> (side, format) | None` recognizing per-item: `Page N of M` → `"Page {n} of {m}"`, `N/M` → `"{n}/{m}"`, `- N -` → `"- {n} -"`, bare digits/roman (legacy charset) → `"{n}"`. A side qualifies when ≥2 pages hit; most-common format wins; the side with most hits wins. `RunningBand` gains `page_number_format: Optional[str] = None`. docx `_render_band` renders it via a new `_append_page_number(paragraph, fmt)` that interleaves literal runs with a `PAGE` field for `{n}` and a new `_ooxml.add_numpages_field` (`NUMPAGES`) for `{m}`. The format string is renderer chrome, not a translatable segment (`iter_segments` untouched). html preview behavior unchanged.

## Guardrails

- `iter_segments` / `build_segment_index_map` untouched (band seg-ids unchanged).
- Existing band tests (`test_running_chrome_image.py`, `test_text_formatting.py` band tests) stay green; the bare-digit page-number fixture must still detect (legacy repeat rule preserved, variation NOT required).
- Old stored models (no `page_number_format`) validate unchanged (Optional field).
- No API calls; `rebuild_source_model` applies the fix to existing docs from cached analysis.

## Verification

- TDD unit tests per helper (clustering / dedup / page-number spec / `_append_page_number` docx read-back with PAGE+NUMPAGES fields).
- Full backend suite green (except the 15 pre-existing protected-file failures).
- Integration: rebuild isolated `_hf-verify-pharma` (cached analysis, no API) → assert band sides are pairwise non-duplicated and footer `page_number` + `page_number_format="Page {n} of {m}"` detected; render the rebuilt source model with `render_docx` and read the section header/footer back with python-docx (header cells contain each text once; footer contains PAGE/NUMPAGES fields).
