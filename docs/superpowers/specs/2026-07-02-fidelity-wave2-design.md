# Fidelity wave 2 (→ push toward ~99% visual match) — Design

**Date:** 2026-07-02  **Status:** Approved  **Branch:** `fidelity-followups-2026-07-02`
**Scope:** three deterministic workstreams attacking the remaining visible error classes found on freshly-processed docs (watergun `ahTZyKrBSX7bj_D4KLER6iPl`, ladder `fsOdj49iXYJWO1L96wrXY1HP`).

## A. Row-grouped table rendering (plug-gauge zig-zag) + row splitting

**Evidence:** fresh analysis of watergun p21 gives vision rows == PyMuPDF grid rows (15 rows, col-sums `[7,7,6,7,6…]`, alternating short rows following a rowspan) — the DATA is right, but the rendered docx shows the nominal-size column zig-zagging between grid columns and some cells pushed into the wrong column. Diagnosis: the docx table builder lays each row's cells left-to-right **without skipping grid columns occupied by an active rowspan from a previous row** (or merges against the wrong anchor), so 6-cell rows land one column off. Additionally, rows split MID-CELL at page boundaries (orphan fragments like "Only)"/"Value)" at the top of the next page).

**Fix:**
1. In `backend/renderers/docx_renderer.py`'s table builder (`_add_table` + its merge logic): maintain a grid-occupancy map while laying rows (same simulation as `_table_structure_unreliable`) so each row's cells are placed into the first FREE grid column, with rowspan merges anchored to the correct (column-accurate) cells. Acceptance is data-driven: rebuild the fresh watergun source model, render source-language docx, read it back with python-docx — in the plug-gauge table each nominal size (40/65/75/90/100/125/150) must sit in grid column 0, every "통과/정지"-labelled cell in column 1/2 band, and each numeric row must carry its values in the same columns as the header.
2. Set `w:cantSplit` on all data rows (python-docx XML) so a row moves to the next page whole instead of splitting mid-cell. (Header repeat via `w:tblHeader` on the first row is a nice-to-have — include if trivial.)

## B. Cover vertical rhythm

**Evidence:** the ladder cover now owns page 1 but its blocks flow from the top; the original title starts ~1/3 down with large deliberate gaps.

**Fix:** blocks carry no y geometry. At reconcile time, for the COVER PAGE ONLY (page 0 with `cover_background` set), record each block's top gap: new optional field `space_before: Optional[float]` (pt) on text blocks (`_PositionedBlock` mixin or per-class field) = `top_y - prev_block_bottom_y` (prev bottom = max y1 of previous block's spans; first block measures from the page top margin), clamped to [0, 300]. The docx renderer applies `paragraph_format.space_before = Pt(value)` when set (cover blocks only — reconciler only sets it on page-0-cover blocks). Old models (field absent) render unchanged.

## C. In-figure baked text ("접합부" under "Joint")

**Evidence:** no text span contains 접합부 on the nozzle page — the label is baked into the drawing (vector paths/raster), so span-redaction can't remove it; the vision analyzer read it anyway and its translation surfaced as a stray text line ("Joint") near the figure.

**Fix (two halves):**
1. **Stop the leak:** a vision text block whose spans/bbox fall INSIDE a rendered figure region and that has NO extractable source spans (`source_span_ids` empty or all inside the figure clip) must not be emitted as a body text block (the existing figure-internal de-dup misses the span-less case). It may instead be attached as a figure overlay (half 2) or dropped.
2. **Translate in place when feasible:** if the figure has an overlay pipeline run (base/overlays), add the vision-read label as a `FigureOverlay` whose bbox comes from the vision block's position within the figure clip, and extend the compositor to **inpaint the overlay bbox with the local background** (median colour of the bbox's border pixels) before drawing `target_text` — baked labels then get visually replaced rather than doubled. If the label's figure has no overlay run or the bbox is unavailable, fall back to dropping the leak (half 1) — never render the translated label as detached body text.

**Risk containment:** inpainting only ever paints INSIDE the overlay bbox; if bbox confidence is low (missing/zero-area), skip. Half 1 alone already removes the visible defect (double text); half 2 upgrades it to a translated label. Implement half 1 first; half 2 behind the same overlay feature flag/paths already used by figure-text-overlay (Phase A).

## Out of scope / accepted limits (honest 99% framing)

- Page-count parity (24→22) — reflow by design; not addressed.
- Vision numeric/OCR errors inside tables — translation-level, not layout.
- Fonts are approximations (Batang/Calibri vs source embedded fonts).

## Verification

- Per-workstream TDD + full suite (15 pre-existing failures excluded).
- Data-driven acceptance on the FRESH docs via `rebuild_source_model` + source-language `render_docx` + python-docx read-back (no API): A) plug-gauge column map correct + no mid-cell splits; B) ladder cover blocks carry expected space_before values; C) no body text block inside the nozzle figure region; overlay inpainting produces a composite whose bbox region no longer equals the base pixels (when half 2 applies).
- Final: re-upload both PDFs through the app (fresh end-to-end, translated) and visually verify in the browser; user-facing before/after.
