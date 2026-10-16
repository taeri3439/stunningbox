# In-Image Text Translation (vision OCR for non-extractable text) — Design

**Status:** Approved (brainstorm, 2026-06-19)
**Branch:** `in-image-text-translation-2026-06-19` (off `main`)

## Goal

Translate text that is *baked into images* of a PDF — text with no born-digital span layer
(screenshots, flattened/outlined design text, rasterized chart labels) — by reading it with a
vision model, masking the original, and compositing the translation in place. The existing
figure-overlay feature only handles born-digital extractable text; this fills the gap for the
opaque figures it leaves untranslated (e.g. the embedded "Knowledge Store" screenshot).

## Problem

The figure-overlay pipeline (`figure_overlay`) translates in-figure text only when PyMuPDF
extracts it as spans. When a figure's text is *not* extractable (a screenshot, outlined art,
a rasterized panel), there are no spans → the figure stays opaque and its text stays in the
source language. Born-digital redaction can't erase pixel text (no text layer to remove).

## Locked decisions (from brainstorm)

1. **Approach:** a *second overlay source* feeding the existing composite pipeline. A vision pass
   OCRs + classifies the text in an opaque figure → produces `FigureOverlay`s (same type the
   born-digital path produces) → the existing `figure_overlay.composite` renders the translation.
2. **Erase strategy (v1):** **simple fill** — sample the background around each text box and fill
   it (no text layer to redact). **No generative model in v1.** Generative inpainting (for
   textured/photo backgrounds) is deferred.
3. **Classification:** the vision pass labels each text region **translate** vs **keep** (logo
   wordmarks, URLs, emails, brand names, product codes, units). Only *translate* regions are
   masked + overlaid; *keep* regions are left untouched in the image.
4. **Trigger (cost control):** the analyzer (already vision-analyzing every page) adds a per-figure
   `has_text` flag; the OCR pass runs **only on opaque figures that are flagged** — logos, photos,
   and pure graphics are skipped (no wasted vision call).
5. **Scope:** PDF path; figure-level (not full scanned pages); the cover (handled separately by
   page-template Phase 1, kept as the original image) is out.

## Architecture & data flow

A new vision OCR source converges on the **same** `FigureOverlay` + composite pipeline the
born-digital path already uses, so translation, per-language compositing, re-translation (Phase B),
and the viewer are all reused unchanged.

```
born-digital path (exists): PyMuPDF spans  → FigureOverlay + redaction base
NEW OCR path:               Claude vision OCR+classify → FigureOverlay (translate-only)
                                                        + simple-fill base
                                   ↓ both converge ↓
            FigureOverlay → figure_overlay.composite (font auto-fit, rotation)
                          → per-language PNG (get_translated_model hook) → renderers
```

- The analyzer flags text-bearing figures (`has_text`). The reconciler, for an **opaque** figure
  (no born-digital overlay) that is **flagged**, runs the OCR pass to produce overlays + a
  simple-fill base, turning it into an overlay figure. Figures with born-digital text are already
  handled by that path and are NOT re-OCR'd (no overlap).

## Components

### New: `backend/pipeline/figure_ocr.py`
- `get_ocr()` mirroring `translator.get_translator()` / `embeddings.get_embedder()`: returns a
  `ClaudeOcr` (vision) when an API key is set, else a deterministic `MockOcr` so tests/offline run
  without network.
- `ocr_classify(image_path) -> list[OcrRegion]`: one vision call (model = `ANALYZER_MODEL`) with a
  **structured-output schema** returning, per visible text region: `{text, bbox [x,y,w,h] in image
  pixels, kind: "translate" | "keep"}`. The model OCRs the text, gives an approximate pixel box,
  and classifies translate-vs-keep (keep = logo wordmark / URL / email / brand / code / units).
- `overlays_from_regions(regions) -> list[FigureOverlay]`: convert the **translate** regions to
  `FigureOverlay`s — `source_text`, `bbox` (vision pixels), `font_size` (≈ bbox height), `color`
  (sampled from the region, else ink default), `rotation` (0). *keep* regions are dropped (left in
  the image).
- `fill_mask(image_path, regions, out_path) -> None`: build the base image — for each *translate*
  region, sample the surrounding background colour and fill the (padded) box, erasing the original
  text; *keep* regions untouched. PIL only.

`MockOcr.ocr_classify` returns a fixed, injectable region list so the pipeline is testable without
a vision call (same pattern as `MockTranslator`).

### Modify: `backend/pipeline/analyzer.py`
`ClaudeAnalyzer` adds a boolean `has_text` to each emitted `figure` block (true when the figure
region contains readable text worth translating). `HeuristicAnalyzer` (offline) defaults it false.
This rides the existing per-page vision call — no new call.

### Modify: `backend/pipeline/reconciler.py`
In the figure branch, for a figure that did NOT become a born-digital overlay figure (no
extractable in-figure spans) AND whose analyzer block has `has_text`: run
`figure_ocr.ocr_classify` on the figure's rendered clip → `overlays_from_regions` + `fill_mask`
(base image) → set the `FigureBlock`'s `overlays` + `base_image_ref` (exactly as the born-digital
path does). Guarded by try/except → fall back to opaque on any failure.

### Reuse (unchanged)
`models.FigureOverlay` / `FigureBlock` (no new fields), `iter_segments` overlay translation,
`figure_overlay.composite` / `composite_document`, the `get_translated_model` per-language hook,
the renderers, and the Phase-B re-translation/viewer path.

## Scope boundaries (YAGNI)

| In (v1) | Out / later |
|---|---|
| PDF opaque figures the analyzer flags `has_text` | generative inpainting (textured/photo backgrounds) |
| vision OCR + classify → simple-fill mask → composite | full scanned-page documents (page = one image) |
| translate/keep classification | HWP/DOCX paths |
| | the cover (page-template Phase 1 keeps it as the original image) |

No new runtime dependency (PyMuPDF + Pillow + the Anthropic client are already present; the vision
call uses the same client as the analyzer/translator).

## Build phases

Single plan (one cohesive addition; ~80% reuses the figure-overlay machinery). Task order:
analyzer `has_text` flag → `figure_ocr` OCR+classify (+ `MockOcr`) → `overlays_from_regions` →
`fill_mask` → reconciler wiring → integration + dogfood. (Generative inpainting is a separate
later spec.)

## Testing

- **Backend pytest (TDD)** with the vision OCR injected as `MockOcr` (deterministic, no network),
  mirroring the `MockTranslator` pattern:
  1. **OCR → overlays:** a stubbed region list yields `FigureOverlay`s for **translate** regions
     only; **keep** regions (URL/logo) are excluded.
  2. **Simple-fill mask:** a synthetic image with a text box over a known background — after
     `fill_mask` the text-box pixels match the background (text erased); a *keep* box is untouched.
  3. **Classification:** keep-items are never turned into overlays.
  4. **Analyzer flag:** `ClaudeAnalyzer` sets `has_text` on a text-bearing figure (mock/stub);
     `HeuristicAnalyzer` leaves it false.
  5. **Reconciler trigger:** OCR runs only on a flagged opaque figure; an UN-flagged figure (logo)
     gets NO OCR call (assert the injected OCR is not invoked) — the cost guard.
  6. **Integration:** a flagged opaque figure → OCR overlays + filled base → `MockTranslator`
     translation → `composite_document` → per-language `image_ref` set (reuses existing path).
  - Fixtures: synthetic PDFs/images built with `fitz`/PIL (fast, deterministic).
- **Dogfood:** the veterinary report's **Knowledge Store screenshot (p17)** — a raster screenshot
  with baked text, the exact case that stayed opaque before — should come out translated in place,
  while its URL/brand wordmarks stay original.

## Critical files

- **New:** `backend/pipeline/figure_ocr.py` (`get_ocr`, `ClaudeOcr`, `MockOcr`, `ocr_classify`,
  `overlays_from_regions`, `fill_mask`, an `OcrRegion` type).
- **Modify:** `backend/pipeline/analyzer.py` (`has_text` on figure blocks),
  `backend/pipeline/reconciler.py` (run OCR on flagged opaque figures → overlays + filled base).
- **Reuse:** `models.FigureOverlay`/`FigureBlock`, `figure_overlay.composite`/`composite_document`,
  `iter_segments`, `get_translated_model`, `extractor.render_clip`.

## Risks / open questions (resolve during implementation)

- **Vision bbox precision:** OCR boxes are approximate → fill may slightly under/over-cover the
  original text; pad the fill box and accept minor imperfection (it's why generative inpainting is
  a later option).
- **OCR accuracy:** misreads / missed regions; the translate/keep classification may err (e.g.
  treat a translatable label as a brand). Acceptable for v1; re-translation (Phase B) lets a user
  correct.
- **Simple-fill on non-flat backgrounds:** a textured/photo background shows a visible filled
  patch — the documented v1 limitation that generative inpainting would later address.
- **Cost/latency:** one vision call per flagged figure; bounded by the analyzer `has_text` gate.
- **`MockOcr` shape:** define a small injectable region-list stub so tests are deterministic and
  the offline pipeline degrades to "no in-image translation" cleanly.
- **font/colour from OCR:** estimating font size from bbox height and sampling text colour is
  approximate; default to ink when sampling is unreliable.
