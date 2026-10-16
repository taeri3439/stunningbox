# Unreliable-table image fallback — Design

**Date:** 2026-07-02  **Status:** Approved  **Branch:** `fidelity-followups-2026-07-02`
**Scope:** PDF reconciler. Deterministic. Feature 2 of the fidelity follow-ups.

## Problem (evidence: test-watergun [별도 3] plug-gauge table)

Complex merged layouts (diagonal split headers, 2-level row groups) defeat the vision grid: the watergun plug-gauge table's vision rows disagree on column count on **9 of 17 rows (53%** — Σcolspan alternates 6/5/7). The existing safety nets (PyMuPDF grid rebuild with text-overlap gate; image fallback only when a page IMAGE covers the region) don't catch it, so the rendered table drifts columns, duplicates rows, and splits across pages — far worse than showing the original as a picture. Clean tables measure 0% ragged (rubber-packing 8×4, nozzle 3×6); the mild [별도 2] case is 23%.

## Approach

After the existing grid-rebuild attempt (so geometry-corrected tables are judged on their corrected rows), run a deterministic integrity check; on failure, clip the table region from the source PDF and emit it as a **FigureBlock** (reusing the imgtable clip path), with the table caption preserved as a **CaptionBlock** (still translatable).

- `_table_structure_unreliable(rows) -> bool`: tables with ≥5 rows where the per-row Σcolspan differs from the modal Σcolspan on **≥30%** of rows, or whose modal is 0. 53% (broken) vs 0% (clean) vs 23% (mild, kept as text) — the threshold separates cleanly and is intentionally conservative.
- Fallback clip: `gbbox` (real grid bbox) if detected else `tb_bbox`, padded 3pt so drawn borders survive; `extractor.render_clip` → `imgtable_p{page}_{i}.png`. On clip failure keep the text table (never lose content).
- Emit `CaptionBlock(text=b["caption"])` just above the figure when the analyzer carried a caption; mark the region occupied (rule mask) + `figure_regions` like the existing imgtable path; `referenced.update(span_pool)`; warning `"table on p{N} rendered as image (unreliable structure)"`.

**Accepted tradeoff:** an image-fallback table's interior text stays in the source language (same as the existing graphic/image-table path). For certification-standard dimension tables (mostly numbers) this is the right trade; Phase-2 could reuse the figure-overlay machinery to translate in place.

## Guardrails

- Runs ONLY when `tb_bbox` exists (need a clip box). Small tables (<5 rows) never fall back.
- Clean/mild tables keep the text-table path (threshold 0.3 — verified against real data above).
- No API calls; applies to existing docs via `rebuild_source_model`.

## Verification

- TDD unit tests for `_table_structure_unreliable` (watergun-like 6/5/7 mix → True; regular → False; <5 rows → False; modal 0 → True).
- Reconcile-level test with a synthetic ragged table → FigureBlock + CaptionBlock + warning; clean table → TableBlock (regression).
- Dev-local integration (skippable): rebuild `_tbl-verify-watergun` copy → the p21 plug-gauge table becomes a figure; p22/p23 tables stay TableBlocks.
- Full suite green (except pre-existing protected-file failures).
