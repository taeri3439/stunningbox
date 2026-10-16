# Editable In-Figure Text (OOXML Group Render) — Design

**Status:** validated end-to-end via spikes v1→v4 (2026-07-09) against the real OnlyOffice DS ConvertService.

## Problem

In-figure chart/diagram text (donut labels, triangle-diagram bullets, map callouts, the
research-methodology figure the user flagged) is translated (Phase A, PR #34) but **baked
into a flattened PNG** (`figure_overlay.composite_document` → `image_ref`). Consequences:
the user cannot edit that text in the document editor; re-translation / glossary changes
need a full PNG re-composite; and the composited text is at the mercy of the server's font
availability (the tofu bug, mitigated by PR #45 but still a bake-time hazard).

## Goal

Render an overlay figure as an **inline OOXML group** — the redacted base PNG plus one real
text box per overlay — so the in-figure text is **editable in the OnlyOffice editor like body
text**, round-trips on save, and participates in re-translation/glossary — while preserving
the figure's visual layout to ≥99% (validated on-screen, not just structurally).

## Why this is possible without breaking the layout (the spike findings)

The naive "just make text boxes" approach breaks the layout (proven in spike v1: re-wrapped
lines, truncated labels, garbled rotated labels). Fidelity requires four disciplines, all
validated in spike v4 against the DS render:

1. **Pre-fit with the SAME algorithm as the composite.** Reuse `figure_overlay.fit_text`
   verbatim to compute the fitted font px and the exact line breaks, then emit **pre-wrapped**
   lines (`<w:br/>` between them) with an **exact** line rule (`w:line = (fitted_px+2)px` in
   twips, `w:lineRule="exact"`), `bodyPr wrap="none" anchor="ctr"` and zero insets. The
   renderer is then forbidden to re-wrap → wrap points are identical to the composite.
2. **Font unification on NanumGothic (the decisive insight).** The DS ships NanumGothic in
   `core-fonts/nanum/` and registers it in `AllFonts.js`; it has **no** Malgun or Noto-CJK-Sans
   (`fc-match :lang=ko` → DejaVu). By using NanumGothic for **all three** roles — the PIL
   measurer (`fit_text`), the docx `w:rFonts`, and the DS renderer — the same font file governs
   measurement and rendering, so there is zero substitution surprise. DS PDF spans confirm every
   text run renders as `NanumGothic`.
3. **Floor-quantized half-points.** Declare `w:sz = floor(fitted_px * 72/RENDER_DPI * 2)` (floor,
   not round) so the declared point size never exceeds what `fit_text` measured → no bold/oversize
   drift.
4. **Center-rotation for rotated labels.** `xfrm rot="((-rotation)%360)*60000"` with the footprint
   swapped `(w,h)→(h,w)` about the bbox midpoint. DS renders this correctly (v1's garble was a
   coordinate bug, fixed).

Spike v4 result (editable group vs shipped composite, DS-rendered): identical wraps / positions /
sizes; structurally-different pixels (>96/255) = **3.89%**, and a difference heatmap proves that
residual is **purely glyph-edge anti-aliasing** (PIL vs DS rasterizing the same NanumGothic) — no
shifted lines, no re-wraps, no missing text.

## Architecture

### Font (Task 1)
Bundle `backend/assets/fonts/NanumGothic.ttf` (SIL OFL 1.1, from the DS core-fonts — redistributable).
`figure_overlay.resolve_font` prefers the bundled NanumGothic first (it already probes
`assets/fonts/`, just for a different filename); the renderer's group path uses the **same** bundled
path so measurement and the `w:rFonts` family agree. Production already has NanumGothic in the DS
image; `fonts-noto-cjk` (PR #45) remains the composite-fallback safety net.

### Group renderer (Task 2) — `renderers/figure_group.py` (new module)
`build_figure_group_xml(block, base_rid, emu_w, emu_h, px_w, px_h, font_path, font_name) -> str`
emits the `w:drawing` group XML: base `pic:pic` at full extent + one `wps:wsp` text box per overlay,
each pre-fitted per the four disciplines above. The group's `wp:docPr name="fig{block_id}"` and each
shape's `wps:cNvPr name="ov{oi}"` key it for sync. Pure function (no docx I/O) → unit-testable by
asserting on the XML (fitted sizes, br-count = line-count, rot attr, names).

### Render integration (Task 3) — `docx_renderer._add_figure`
When `block.overlays` **and** `block.base_image_ref` exist and the base asset is readable, register
the base image part (steal its `r:embed` rId via a throwaway `add_picture`, then remove the throwaway
run), append the group XML to a fresh paragraph's run, and return that paragraph (so `_mark_break`
still works). **On ANY exception, fall back to the current `image_ref` (composite PNG) embed** — worst
case = today's behavior. Opaque figures (no overlays) are unchanged.

### Save-sync (Task 4) — `docx_sync`
New pass in `apply_docx_edits_to_model`: after the text-block rebuild, scan every `w:drawing` in the
docx for a group whose `wp:docPr name` matches `fig{block_id}` of a `FigureBlock` in the model; for each
child shape `ov{oi}`, read its `txbxContent` text (joining `<w:br/>`-split lines back with spaces, since
the overlay stores a single string) and write it to `overlays[oi].target_text`. **Must tolerate the DS
re-save wrapping each `wps` in `mc:AlternateContent` (mc:Choice + mc:Fallback)** — read the `mc:Choice`
(or the first occurrence), matching by shape name not document position. Unedited round-trip is identity.

### Pipeline wiring (Task 5)
- `process.get_translated_model` / `retranslate_one_segment`: keep `composite_document` running (it
  still populates `image_ref` as the render fallback and the non-overlay path). No behavior change needed
  there beyond ensuring `base_image_ref` survives — the renderer now consumes it.
- Re-translation of an `overlay:{oi}` segment already updates `overlays[oi].target_text` via
  `iter_segments`; the next render picks it up in the group text box. Confirm the segment round-trips.

## Testing

**Deterministic (per task, TDD):** font resolution prefers bundled Nanum; `build_figure_group_xml`
asserts fitted size (floor-quantized), `<w:br/>` count == wrapped-line count, `wrap="none"`/exact line
rule, rotated shape footprint swap + rot attr, shape/group names; `_add_figure` group-vs-fallback
branch (fallback on unreadable base); `docx_sync` reads a hand-built figure group (raw wps AND
mc:AlternateContent-wrapped) → overlays, and unedited round-trip identity.

**REAL-SCREEN 99% fidelity gate (the user's hard requirement):** render the real veterinary report
(EN→KO, 8 overlay figures) through the actual pipeline; load in the browser (OnlyOffice editor + the
DS-rendered viewer pages); for **each** overlay figure, DS-render and pixel-compare against the source
figure appearance (composite as the proxy reference), computing the structurally-different-pixel ratio
(>96/255, which excludes anti-aliasing) — **require <1% for every figure** (≥99% preserved). Also verify
editability: double-click a figure's text in the editor, type, save, confirm the edit is in the saved
docx's `txbxContent` and re-renders. Capture screenshots of every figure before/after.

**Regression:** ko→en (expanding direction, no overlays typically) and the existing figure_overlay
composite tests stay green — the composite path is untouched; the group path is additive with fallback.

## Risks / decisions

- **Bullet glyphs:** source bullets are Wingdings PUA chars (``); the composite shows `.notdef` □, DS
  maps to Webdings. Moot once the group render (not the composite) is the reference, but map `` → a
  standard bullet for polish (in-scope small item in Task 2).
- **Very dense figures:** the fallback guarantees no regression; the 99% gate will flag any figure where
  the group underperforms the composite, and those fall back automatically.
- **NanumGothic ≠ the source's actual font:** we already substitute (the source fonts aren't embedded);
  Nanum is the highest-fidelity choice because it's what the renderer will actually use. Latin runs
  (WAVLD, AVMA…) render in Nanum's Latin, matching the composite which also used one font.
