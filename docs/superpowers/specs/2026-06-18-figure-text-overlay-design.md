# In-Figure Text Overlay Translation — Design

**Status:** Approved (brainstorm, 2026-06-18)
**Branch:** `figure-text-overlay-2026-06-17`

## Goal

Translate the text *baked inside* charts, maps, and infographic-style diagrams of a
born-digital PDF, and render it back **in place** — so a translated market-research report
shows "북미 41.6%" on the map where the source showed "North America 41.6%", with the
original vector artwork (donut shapes, map fills, triangle process-flow boxes) preserved.

## Problem

Today the PDF pipeline (`extractor` → `analyzer` vision → `reconciler` → translate →
reflow DOCX/HTML) treats every chart/diagram/map as an **opaque raster figure**. Worse, it
*deliberately drops* the text spans sitting inside a figure's bbox (`reconciler`'s
`_spans_inside_figures` suppression) so they don't echo as floating translated text over the
image. For a chart-heavy report that means the donut-chart percentages, bar-chart axis labels,
geographic-map callouts, and process-flow bullet text all stay in the source language —
a large fraction of the document's substance.

**Key enabling fact:** the target documents are *born-digital*, not scanned. PyMuPDF already
extracts every in-figure label as a real text span with a bounding box, font size, and color.
We are currently throwing that data away. This is therefore **not an OCR problem** — it is a
"keep the spans we already have, translate them, and draw them back over the figure" problem.

## Locked decisions (from brainstorm)

1. **Target behavior:** in-place overlay — translated text drawn at the original glyph
   positions over the figure (not a caption/legend below it, not chart reconstruction).
2. **Masking:** redact the *text layer* with PyMuPDF (`add_redact_annot` + `apply_redactions`),
   keeping vector artwork. No pixel inpainting (only possible because docs are born-digital).
3. **Output form:** a single **composite PNG** per figure (translated text drawn on with PIL),
   emitted through the existing `FigureBlock` path — so the DOCX/HTML renderers are untouched.
4. **Editability:** the composite PNG is a *derived artifact*; the source of truth is the
   structured list of in-figure text overlays. Clicking a figure in the viewer surfaces those
   overlays as re-translatable segments (reusing the existing 재번역 mechanism); editing one
   re-composites the PNG.
5. **Scope:** born-digital **PDF** figures only in v1. OCR (raster-baked text), chart
   reconstruction, HWP/DOCX figures, and arbitrary-angle/curved text are out of scope.

## Architecture & data flow

The change is concentrated in **extraction + translation + a new composite renderer**. The
output renderers (`docx_renderer`, `html_renderer`) and the document model's overall shape are
unchanged — a figure is still a `FigureBlock` carrying one PNG.

```
PDF figure region
  └─[1] extract in-figure spans  →  list[FigureOverlay]   (instead of dropping them)
  └─[2] redact those spans + render region to PNG  →  base_image_ref  (vector art kept)
  └─[3] translate overlays via iter_text_fields  →  target_text       (glossary + style RAG)
  └─[4] PIL composite: draw target_text on base PNG at each bbox  →  image_ref
  └─[5] (edit) user corrects/re-translates an overlay  →  re-run step 4 (and step 3 on 재번역)
FigureBlock(image_ref) → existing DOCX/HTML renderer (unchanged)
```

## Data model (`backend/pipeline/models.py`)

Extend `FigureBlock` and add `FigureOverlay`:

```python
@dataclass
class FigureOverlay:
    id: str               # stable id, unique within the figure (re-translation targeting)
    source_text: str      # original, e.g. "North America"
    target_text: str      # translated, e.g. "북미"  ← the only translated leaf field
    bbox: tuple[float, float, float, float]  # x, y, w, h in figure-local pixels (render DPI)
    font_size: float      # px at render DPI
    color: str            # hex, sampled from the source span
    align: str            # "left" | "center" | "right" (horizontal anchor)
    rotation: int         # 0 | 90 | -90

@dataclass
class FigureBlock:
    image_ref: str            # composite (translated) PNG — what the renderer embeds
    base_image_ref: str       # text-redacted PNG — source for re-composite (new)
    overlays: list[FigureOverlay]   # in-figure text, source of truth (new; [] = opaque figure)
    width: float
    height: float
    align: str
    wrap: str
```

- `overlays == []` means a plain opaque figure (status quo behavior; `base_image_ref` may equal
  `image_ref`).
- `iter_text_fields` (models.py) gains `overlays[].target_text` as translatable leaf fields so
  the existing translator, glossary, and style-RAG paths cover them with no translator change.
- `source_span_ids`, `bbox`, `id`, `image_ref`, `base_image_ref` are **never** translated.

## Figure selection heuristic

A figure becomes an **overlay figure** iff its region contains **≥ 1 born-digital text span
whose text has a letter** (Unicode letter, any script). Otherwise it stays opaque.

- ✅ overlay: donut/pie charts, bar-chart axis/legend labels, map callouts, triangle
  process-flow diagrams, data-triangulation diagrams.
- ⛔ opaque: the MarketsandMarkets logo (raster, no spans), the Knowledge Store screenshot
  (text baked into pixels — OCR territory, out of scope).
- Numeric-only spans ("41.6%") still go through translation (returned unchanged) but do not by
  themselves qualify a figure — the letter test does.
- **Cover page (p.1)** — diagonal background figure with title spans on top — naturally
  qualifies as an overlay figure. Cover classification is variable; treat correct cover output
  as a dogfood validation item, not a guaranteed v1 invariant.

## Composite rendering (`backend/pipeline/figure_overlay.py`, new)

PIL (Pillow) draws each `target_text` onto the base PNG:

1. Open `base_image_ref`. For each overlay, render `target_text` with a bundled Korean-capable
   font (Pretendard / Noto Sans KR `.ttf`) at `font_size`, color `color`.
2. **Auto-fit (text expansion):** measure rendered width; if it exceeds `bbox` width, shrink
   the font stepwise to a floor (~60% of original, or a min px). If still too wide at the floor,
   wrap on spaces to ≤ 2 lines and check it fits `bbox` height; shrink further if needed.
3. **Anchor** by `align` horizontally and vertical-center within `bbox`.
4. **Rotation:** render rotated overlays (`90` / `-90`) to a transparent sub-image, rotate, and
   paste at `bbox` (axis titles like "Market Size (USD Million)").
5. Save as `image_ref`. Deterministic given (base PNG, overlays) so it is golden-testable.

Direction note: en→ko labels usually shrink (comfortable); ko→en expands — auto-fit handles both.

## Edit loop (Phase B)

In-figure overlays are treated as **segments** in the existing re-translation system, so
재번역, glossary, and style RAG apply unchanged.

- **Clickable figures:** the HTML preview marks overlay figures with the block id
  (`data-` attribute). Clicking populates the tool-dock 재번역 panel with that figure's overlays
  (원문 → 번역, each editable).
- **Segment address space:** overlays join the existing segment id space as
  `fig:<figureBlockId>:<overlayId>`.
- **Two edit paths:**
  - *Manual edit* — user corrects `target_text` → lightweight PATCH updates the field →
    re-composite PNG (composite step only; **no model call**).
  - *Re-translate* — `process.retranslate_one_segment` is extended to recognize `fig:` targets:
    re-translate `source_text` (glossary + style) → update `target_text` → re-composite.
- **Response:** the re-composited PNG url + updated source/target pair; the viewer swaps that
  figure's `<img src>` only (live update). Re-composite is one PIL pass — effectively instant.
- The final DOCX is regenerated from the segment model as today; its figures are composite PNGs.

## Scope boundaries (YAGNI)

| In (v1) | Out / later |
|---|---|
| born-digital **PDF** figures with extractable spans | scanned / pixel-baked text (OCR) — stays opaque |
| redaction + PIL composite PNG + re-translatable overlays | chart reconstruction into native charts/tables |
| rotation 0 / 90 / -90 | arbitrary-angle / curved text |
| | HWP / DOCX figure overlays (redaction is PDF-specific) |

New dependency: **Pillow** + a bundled Korean `.ttf` (PIL needs a font file to render Hangul).
PyMuPDF redaction uses the existing PyMuPDF dependency.

## Build phases (each independently shippable, one PR each)

- **Phase A — backend core (ships translated charts):** `FigureOverlay` model + `iter_text_fields`
  wiring; in-figure span extraction (flip suppression → preservation); redaction base PNG;
  `figure_overlay.py` composite; thread through `build_source_model` / `get_translated_model`
  and the renderers. No edit loop. Delivers the core value on its own.
- **Phase B — edit loop:** clickable figures in the HTML preview; overlays as `fig:` segments;
  extend `retranslate_one_segment` + a lightweight manual-edit PATCH; re-composite + live swap.

## Testing

- **Backend pytest (TDD)**, mirroring the existing `MockTranslator` patterns:
  1. **Overlay extraction** — a figure region with known spans yields `FigureOverlay`s with the
     right text / bbox / font_size.
  2. **Redaction** — after redaction the region has **0 text spans** but vector drawings remain.
  3. **Composite** — `target_text` renders at bbox; long text shrinks/wraps so measured width ≤
     bbox width; output is deterministic.
  4. **Figure selection** — region with a letter span → overlay; pure-raster region → opaque.
  5. **Translation integration** — overlays populate `target_text` via `MockTranslator`.
  6. **(Phase B)** a `fig:` re-translate target updates the overlay and returns a new
     `image_ref`.
  - Unit fixtures use a **small synthetic PDF built with PyMuPDF** (text drawn into a region) —
    fast, deterministic, no network.
- **Frontend** (no test runner): `next build` + browser walkthrough using **this veterinary
  market-research PDF as the dogfood document** — verify the donut charts, geographic maps, and
  triangle diagrams come out with Korean labels in place.

## Critical files

- **New:** `backend/pipeline/figure_overlay.py` (extraction helpers + PIL composite + redaction);
  bundled font asset under `backend/assets/` (or existing asset dir).
- **Modify:** `backend/pipeline/models.py` (`FigureBlock`, `FigureOverlay`, `iter_text_fields`);
  `backend/pipeline/reconciler.py` (flip in-figure span *suppression* → *overlay capture*; emit
  `base_image_ref`); `backend/pipeline/process.py` (`build_source_model`, `get_translated_model`,
  `retranslate_one_segment` for `fig:` targets); `backend/requirements.txt` (Pillow);
  `backend/main.py` (manual-edit PATCH route, Phase B); `frontend/src/app/.../viewer` HTML
  preview (clickable figures) + tool-dock 재번역 panel (overlay segments), Phase B.
- **Reuse:** `extractor.render_clip` / page render, `analyzer` figure bbox, `translator` +
  `iter_text_fields`, glossary + style-RAG context, `docx_renderer` / `html_renderer` figure
  embedding (unchanged).

## Risks / open questions (to resolve during implementation)

- **PyMuPDF redaction fidelity:** confirm `apply_redactions` removes glyphs cleanly while
  leaving vector drawings (donut arcs, map fills) intact and artifact-free — verify with a spike
  on a real figure region before committing to the approach. If redaction disturbs vector art,
  fall back to drawing background-colored rectangles over the text bboxes (sampled fill).
- **Color sampling** for the overlay text when the source span color is unavailable or sits on a
  varied background (use span color when present; default to a high-contrast color otherwise).
- **Overlapping / very dense label clusters** (small pie slices) — auto-fit floor + 2-line wrap;
  accept minor imperfection over failure.
