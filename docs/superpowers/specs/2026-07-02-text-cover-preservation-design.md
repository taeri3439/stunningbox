# Text-cover (white cover) preservation — Design

**Date:** 2026-07-02  **Status:** Approved  **Branch:** `fidelity-followups-2026-07-02`
**Scope:** PDF reconciler cover detection + page-0 chrome handling. Deterministic. Feature 3.

## Problem (evidence: test-ladder 소방장비기본규격)

Cover handling exists (redacted full-bleed `cover_bg` + first-page-header background + renderer page-break before the first `page >= 1` block) but only fires for **mostly non-white** designed covers (`_page_nonwhite_frac >= 0.4`). A classic WHITE cover (huge title, standard number, logo, lots of whitespace) fails that check, so:
- no cover background → no dedicated cover page → the TOC/body rides up onto page 1 (observed);
- the cover's whitespace design collapses;
- page-0-only chrome ("KFS 0011" top-right) never repeats on ≥2 pages → `_pick_running` drops it → the standard number VANISHES from the output.

Ladder page-0 analyzer output is complete (heading 소방장비기본규격, subtitle, doc numbers, logo figure, committee line, URL, chrome KFS 0011) — only the cover *treatment* is missing.

## Approach

1. **Relax cover detection for text covers.** New pure helper `_looks_like_text_cover(page_spans, n_pages)`: `n_pages >= 2` AND `len(page_spans) <= 18` AND the page carries a display-size span (`max span size >= 26pt`). Cover condition becomes: existing guards (`page_index == 0`, `_body_blocks <= COVER_MAX_BODY_BLOCKS`) AND (`nonwhite >= 0.4` **or** `_looks_like_text_cover(...)`). A text cover then gets the SAME machinery as designed covers: redacted background (white page + logo + rules — text redacted), translated text blocks on top, and the renderer's existing cover page-break so the cover owns page 1.
2. **Keep page-0 chrome on covers as body text.** In the chrome branch, when `page_index == 0 and cover_bg is not None`, ALSO emit each chrome cluster as an aligned `ParagraphBlock` (align = left/center/right from its x-position, y-ordered into the page flow). No visual duplication is possible: with a cover background the section uses `different_first_page_header_footer`, so running bands never render on the cover page anyway. (Items are still recorded for band voting — behavior on later pages unchanged.)

## Guardrails

- Content-heavy first pages are never covers: the existing `_body_blocks <= 3` guard stands (pharma TOC p0 has 24 list_items; watergun p0 has 4+ paragraphs → both excluded).
- Single-page docs are never covers (`n_pages >= 2`).
- Non-cover chrome behavior unchanged; designed (non-white) cover path unchanged.
- Deterministic; applies to existing docs via `rebuild_source_model`.

## Verification

- Unit tests for `_looks_like_text_cover` (ladder-like sparse+display page → True; many spans → False; no big font → False; 1-page doc → False).
- Reconcile-level test (monkeypatched `_page_nonwhite_frac` low + `figure_overlay.render_redacted_clip` stub): white sparse page-0 → `cover_background` set, chrome "KFS 0011" present as a right-aligned page-0 ParagraphBlock; content-page fixture → no cover.
- Dev-local (skippable): rebuild `_cover-verify-ladder` copy → `cover_background == "cover_bg.png"` + a page-0 block containing "KFS 0011".
- Full suite green (pre-existing protected-file failures excluded).
