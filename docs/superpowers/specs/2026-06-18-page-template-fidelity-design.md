# Page-Template Fidelity (cover background + running chrome) — Design

**Status:** Approved (brainstorm, 2026-06-18)
**Branch:** `page-template-fidelity-2026-06-18`

## Goal

Reproduce a born-digital PDF's **page-template design** in the translated DOCX: the cover's
full-bleed background, and the repeating page chrome (the decorative header band + publisher
logo, and the footer color bar + page number). Today these vanish — the pipeline models running
chrome as text only and has no page-background concept, so a translated report loses its cover
design, header branding, and footer bar.

## Problem

- **Cover background**: the cover's full-page diagonal design is classified as a `figure` and
  emitted as an **inline** image (clipped to content, dropped into the text flow) — it shows as a
  small mid-page block instead of a full-bleed background.
- **Header band + logo**: the analyzer marks the header region as `chrome`; the reconciler keeps
  only its **text** in `RunningBand` (left/center/right + a thin rule). The decorative diamond
  band and the logo image have no home in the model and are lost.
- **Footer bar + page number**: same — only footer text survives; the decorative color bar is
  lost and the page number is baked as literal text rather than an auto-updating field.

This is **not an OnlyOffice/DOCX limitation** — DOCX supports page backgrounds, header/footer
images and shapes, and PAGE fields, all of which OnlyOffice renders. The gap is that the pipeline
does not capture or emit these elements (Phase-1 scope modeled chrome as text + a rule).

## Locked decisions (from brainstorm)

1. **Scope of preservation**: cover/first-page background **and** header logo+decorative band
   **and** footer color bar + page number. (Full page-template fidelity, carrying source branding.)
2. **Approach A — decoration as image, text stays translatable**: rasterize decorative regions
   into images (reusing the figure-overlay **redaction** primitive to remove their text), and keep
   the header title / footer date as translatable text + an auto PAGE field drawn on top. Not
   native vector reconstruction (rejected — fragile), not whole-page-as-image (rejected — kills
   text selectability).
3. **PDF path only** in v1 (born-digital). HWP/DOCX ingest paths and HTML-preview chrome are out.

## Architecture & data flow

Decorative layers become **images anchored behind text**; text stays as translatable
runs/fields on top. Capture reuses `figure_overlay.render_redacted_clip` (render a region with
its text glyphs removed, vector art kept) + `extractor.render_clip`. The chrome repeats, so it is
captured **once** from a representative content page (as `RunningBand` text already is). Rendering
uses DOCX **different-first-page** + raw-OOXML behind-text image anchors + a PAGE field.

```
PDF
 ├─ cover page (p0): full-page design + title text
 │     → redact title spans, render full page → cover_background PNG
 │       (title/subtitle stay as translatable heading blocks)
 ├─ header strip (top of a body page): diamond band + logo + running title
 │     → redact the running-title text, render the strip → running_header.image
 │       (logo is inside the strip render; running title stays RunningBand text)
 └─ footer strip (bottom): color bar + date/copyright + page number
       → redact footer text, render strip → running_footer.image
         (footer text stays RunningBand text; page number → PAGE field at page_number side)

DOCX render (different-first-page):
  page 1   → cover_background as a full-page behind-text anchor; no header/footer chrome
  page 2+  → header.image (top, full-width, behind text) + band text
             footer.image (bottom, full-width, behind text) + footer text + PAGE field
```

## Data model (`backend/models.py`)

Text fields are unchanged (still translatable); only image/field references are added.

```python
class RunningBand(BaseModel):
    left: Optional[str] = None
    center: Optional[str] = None
    right: Optional[str] = None
    image: Optional[str] = None        # NEW: decoration-strip PNG (text-redacted), behind the text
    page_number: Optional[str] = None  # NEW: "left" | "center" | "right" → a Word PAGE field there
    rule: bool = False
    rule_size: Optional[float] = None
    rule_color: Optional[str] = None

class SemanticDocument(BaseModel):
    ...
    running_header: Optional[RunningBand] = None
    running_footer: Optional[RunningBand] = None
    cover_background: Optional[str] = None   # NEW: first-page full-bleed PNG (title-redacted)
    page_width / page_height / margins ...   # (existing)
```

- All new fields default to `None`/absent → existing `source.json` loads unchanged; docs without
  chrome/background render exactly as today.
- `image`/`cover_background`/`page_number` are structural — never translated. The band's
  `left/center/right` text remains the only translated chrome content.

## Capture (`backend/pipeline/reconciler.py`, PDF path)

Reuses `figure_overlay.render_redacted_clip(pdf_path, page_index, exact_bbox, redact_bboxes,
asset_dir, name)` and `extractor.render_clip`. `RawPage` already provides `spans`, `images`, and
`hrules`.

- **Cover background** (Phase 1): on page 0, if a `figure`/drawing layer covers ≥ ~70% of the page
  area, treat it as the cover background instead of an inline figure: render page 0 in full with
  the title/subtitle spans redacted → `cover_background`. The title/subtitle remain
  `HeadingBlock`s (translatable, rendered on top). The cover does **not** emit that inline figure.
- **Header strip** (Phase 2): region = page top down to the first `hrule` (or just below the
  chrome text band). Render the strip with the chrome text spans redacted → `running_header.image`.
  The logo (`RawImage` in that region) is naturally included in the strip render. The running
  title stays `running_header` text.
- **Footer strip** (Phase 2): region = below the last `hrule` to page bottom. Render with footer
  text redacted → `running_footer.image`. Detect the page-number digit's side (left/center/right)
  → `running_footer.page_number`.
- Capture chrome from one representative content page (chrome repeats), matching how
  `RunningBand` text is already collected.

## Rendering (`backend/renderers/docx_renderer.py` + new `backend/renderers/_ooxml.py`)

`render_docx` sets `sec.different_first_page_header_footer = True`, then:

- **Cover background (page 1)**: anchor `cover_background` as a floating picture in the
  **first-page header**, positioned at the page origin (`relativeFrom="page"`, posOffset 0/0),
  sized to the full page (`page_width × page_height` in EMU), `behindDoc=1`, `wrapNone`. The cover
  title/subtitle headings flow on top. First-page header/footer carry no other chrome.
- **Header decoration (page 2+)**: anchor `running_header.image` at the page top, full width,
  behind text, in `sec.header`; render the band text in the header paragraph on top.
- **Footer decoration (page 2+)**: anchor `running_footer.image` at the page bottom, full width,
  behind text, in `sec.footer`; render footer text + a **PAGE field** (`fldChar` run sequence) at
  the `page_number` side.

python-docx lacks behind-text/floating anchors and field insertion, so a small
`backend/renderers/_ooxml.py` provides the raw-OOXML builders (`wp:anchor` behind-text image at a
page-relative offset with a given EMU extent; a `PAGE` `fldChar` run). `docx_renderer` calls these
so it stays focused.

**Accepted imperfection**: because the body is reflow, the header title text may not align
pixel-perfectly over the band image (header-paragraph position vs. image position). v1 accepts
"approximate alignment"; tune with header distance/padding if needed.

## Scope boundaries (YAGNI)

| In (v1) | Out / later |
|---|---|
| born-digital **PDF** path | HWP / DOCX ingest paths (own chrome structure) |
| cover background + repeating header (band+logo) + footer (bar) + page number | per-page *different* backgrounds; multi-section varying chrome |
| decoration captured as raster + redaction | native vector reconstruction |
| approximate header-text-over-band alignment | pixel-perfect alignment |
| DOCX output | HTML-preview chrome rendering (compare viewer's left pane already shows the original) |

No new runtime dependency (PyMuPDF + Pillow already present; raw OOXML is `lxml`, already used by
python-docx).

## Build phases (each independently shippable, one PR each)

- **Phase 1 — cover/first-page background**: `cover_background` model field; cover-detection +
  title-redacted full-page render in the reconciler (stop emitting the inline cover figure); DOCX
  different-first-page full-bleed behind-text anchor. Fixes the most glaring issue (blank cover) on
  its own.
- **Phase 2 — header/footer decoration + page number**: `RunningBand.image` + `page_number`;
  header/footer strip capture (text-redacted); DOCX header/footer behind-text anchors + PAGE field;
  `_ooxml.py` helpers.

## Testing

- **Backend pytest (TDD)**, synthetic PDFs built with `fitz` (mirroring the figure-overlay tests):
  1. **Cover detection** — a page-0 with a full-page filled rect + title text yields a non-empty
     `cover_background`, the title stays a `HeadingBlock`, and **no inline cover figure** is emitted.
  2. **Cover render** — `render_docx` output's first-page header contains a behind-text drawing
     anchor sized to the page (assert the `behindDoc` flag + page-sized EMU extent in the XML).
  3. **Header strip capture** — a page with a top color band + header text + a logo image yields a
     non-empty `running_header.image`; the strip has the header glyphs removed (redaction) and the
     header text still lives in `running_header.left` (translatable).
  4. **PAGE field** — a footer with a page-number digit sets `running_footer.page_number`, and the
     rendered footer contains a `PAGE` `fldChar` run.
  5. **Redaction reuse** — strip/cover images have text removed but vector art kept.
  6. **Regression** — chrome-less docs are unaffected; existing header/footer **text** rendering
     still works; existing figure rendering unchanged.
  - DOCX is a zip; tests parse element XML (via python-docx's element access) and assert the
    anchor/field structure — not visual appearance.
- **Dogfood**: run the veterinary report and confirm in OnlyOffice + the exported DOCX that the
  cover design fills page 1, the diamond band + logo appear on body pages, and the footer bar +
  auto page numbers appear.

## Critical files

- **Modify**: `backend/models.py` (`RunningBand.image`/`page_number`, `SemanticDocument.cover_background`);
  `backend/pipeline/reconciler.py` (cover detection + chrome-strip capture; suppress the inline
  cover figure); `backend/renderers/docx_renderer.py` (different-first-page; anchored background;
  header/footer decoration anchors; PAGE field).
- **New**: `backend/renderers/_ooxml.py` (raw-OOXML behind-text image anchor + PAGE field builders).
- **Reuse**: `backend/pipeline/figure_overlay.render_redacted_clip`, `extractor.render_clip`,
  `RawPage.spans/images/hrules`, the existing `_render_band` text path.

## Risks / open questions (resolve during implementation)

- **Cover-detection heuristic**: the "≥ ~70% page coverage" threshold for "this figure is the page
  background" — tune so real cover designs trigger but a large-but-not-background figure does not.
- **Header/footer strip bounds**: deriving the top/bottom strip region from `hrules` + chrome-text
  y-extent robustly across documents.
- **Header-text-over-band alignment**: reflow header paragraph vs. anchored band image; accept
  approximate, tune via header distance.
- **python-docx first-page-header XML quirks**: ensure the first-page header part is created and
  the behind-text anchor renders in OnlyOffice (verify in dogfood, not only in unit XML asserts).
