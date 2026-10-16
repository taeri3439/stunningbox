# Native HWP extractor (Path C)

## Context / why

`.hwp` uploads currently take a lossy, fragile route: `hwp5html` (pyhwp) renders the HWP
to XHTML, that XHTML is copied to `.html`, **LibreOffice converts it to PDF**, and the PDF
is fed to the **vision pipeline** (analyze → reconcile), which *re-recovers* structure a
vision model guesses from a flattened render.

Diagnosis (2026-06-03, on the real `내진스토퍼 KFI기준` sample, `storage/8283bdb81efe/source.hwp`):

1. **pyhwp wasn't installed** in the local venv → the path couldn't run at all (the sample
   had produced zero artifacts). Installed `pyhwp==0.1b15` (builds on Python 3.14).
2. **LibreOffice HTML→PDF fails** on this environment ("source file could not be loaded"),
   repeatedly — the core step of the current path is unreliable.
3. **Massive, by-design information loss.** `hwp5html` *already* emits rich, structured data
   that the current path throws away by flattening to PDF then guessing it back with a paid
   vision call:
   - real page geometry (`.Section-0 { width: 210mm }` + margins 20/15/20mm)
   - per-span char shapes: `font-family` (e.g. `휴먼명조`/`함초롬바탕`), `font-size`,
     `font-weight: bold`, `color` (e.g. `#0000ff`)
   - per-paragraph para shapes: `text-align` (justify/center/right), `text-indent` +
     `padding-left` (hanging-indent depth for lists)
   - real HTML `<table>`s (7 in the sample), `<img>`s with explicit mm dimensions
4. The Phase-1 fidelity work was tuned for *real* PDFs; for HWP the "PDF" is a generic
   LibreOffice HTML render, so the captured geometry/fonts are LibreOffice defaults.

Alternatives tested and rejected: **Path A** (keep current, stabilize LibreOffice) keeps the
lossy/expensive/fragile design; **Path B** (`hwp5odt` → docx → reuse `docx_extractor`) is
**broken** — `hwp5odt` fails RelaxNG validation and produces an empty ODT.

## Approach — Path C: native extractor

Add `hwp_extractor.py` that parses the `hwp5html` XHTML + CSS **directly** into a
`SemanticDocument`, exactly as `docx_extractor.py` does for Word. No vision API (free,
deterministic, fast); reuses the existing renderer + translator unchanged; removes the
unreliable LibreOffice HTML→PDF step from the structure path.

### Module boundary (testability)

- `convert.hwp_to_html(src_path, out_dir) -> str` — run `hwp5html`, return the output dir
  (containing `index.xhtml`, `styles.css`, `bindata/`). The pyhwp/subprocess wrapper.
- `hwp_extractor.parse_hwp_html(html_dir, asset_dir, source_lang) -> SemanticDocument` —
  **pure parse** of an hwp5html output dir. Unit-tested with synthetic fixtures (no binary).
- `hwp_extractor.extract_hwp(hwp_path, asset_dir, source_lang) -> SemanticDocument` — thin:
  `hwp_to_html` to a temp dir, then `parse_hwp_html`. Mirrors `extract_docx`'s signature.

### CSS resolver

Parse `styles.css` + the inline `<style>` in `index.xhtml` into:
- `charshape[id] -> {font, size_pt, bold, italic, color}` from `span.charshape-N` (+ its
  `.lang-ko` variant for the East-Asian family). `font` → `universal_font(...)`
  (sans→Arial, serif→Times New Roman). `color`: `#000000`/near-black → None (ink); else
  uppercase `RRGGBB` (so `#0000ff` cross-refs are preserved).
- `parashape[id] -> {align, indent_pt}` from `p.parashape-N`. `align` from `text-align`
  (`justify`/`center`/`right`; `left`/missing → None). `indent_pt` from `padding-left`.
- `borderfill[id] -> {shading}` from `.borderfill-N` `background-color` (white/none → None).
- page geometry: `width` from `.Section-0` (→ `page_width`); `page_height` = A4 ratio when
  width≈210mm; margins from `.HeaderPageFooter` (`margin-*`) plus `.Page` padding.

### Block mapping (walk the `div.Page` children in order)

Each top-level `<p class="Normal parashape-N">`:
- **Table** — if it contains `span.TableControl > table`: emit `TableBlock`. Rows from
  `<tr>`, cells from `<td>` (`colspan`/`rowspan` attrs; strip `\r`), `is_header` for the
  first row, `shading` from the cell's `borderfill` class, `align`/`valign` best-effort.
- **Figure** — if it contains `<img>`: convert `bindata/BINxxxx.bmp` → PNG (Pillow) into
  `asset_dir` as `hwpimgN.png`, emit `FigureBlock(image_ref, width_pt, height_pt)` from the
  `style` mm dimensions. Non-raster bindata (`.OLE`) → skipped.
- **Text** — resolve dominant char shape (font/size/bold/color) across the paragraph's
  spans and the para shape (align/indent), then classify:
  - **Heading** if dominant `size > body_size` and bold, or it matches a chapter/section
    marker (`^제\s*\d+\s*[장절]`) while centered. Level by size rank (largest → 1).
  - **List item** if the text starts with a list marker (`1.`, `①`, `가.`, `(1)`, `1)` …):
    split marker from text → `ListItemBlock(marker, text, indent, level)`. Level from the
    hanging-indent depth.
  - else **`ParagraphBlock`**. Empty paragraphs are dropped.

`body_size` = the most common resolved font size across all text paragraphs.

### Wiring

- `process.build_source_model_from_hwp(src_path, doc_id, storage_dir, source_lang,
  on_progress)` — mirror of `build_source_model_from_office`: extract → write
  `source.json` + `report.json` (`{"ingest": "hwp", "blocks": n}`).
- `main._run_job`: route `.hwp` → `build_source_model_from_hwp` (drop the
  hwp→pdf→vision branch). `.docx`/`.doc` unchanged; `.pdf` unchanged.
- `convert.hwp_to_pdf` (+ its test) is removed — dead after this — replaced by
  `hwp_to_html`.

### Out of scope (v1)

- **Source preview images** for the viewer's left "원본" column. Native extraction produces
  no source PDF, so HWP behaves like the existing DOCX path (editor only, empty left
  preview). A best-effort preview render can be added later, decoupled so its failure never
  breaks the structure pipeline.
- Inline mixed formatting (the bold `제1조(목적)` prefix is block-level None in v1 — Phase 2
  inline runs).
- Running header/footer (hwp5html emits none here), `.OLE` embedded objects, `.hwpx`.

## Testing

`backend/tests/test_hwp_extractor.py` — drives `parse_hwp_html` with small synthetic
hwp5html-shaped fixtures (`index.xhtml` + `styles.css` written to `tmp_path`), no pyhwp
binary needed:
- CSS resolver: charshape font/size/bold/color (near-black→None, `#0000ff` preserved);
  parashape align/indent; page geometry (210mm→A4 + margins).
- Classification: a large bold centered line → heading; `제1장` → heading; `1. …` →
  list item with marker split; plain line → paragraph.
- Table: 2×2 with a `colspan`/`rowspan` and a shaded `borderfill` cell.
- Figure: an `<img>` over a fixture BMP → PNG written to asset_dir, width/height in pt;
  `.OLE` skipped.
- Integration smoke (skipped if pyhwp or the sample is absent): `extract_hwp` on the real
  sample yields tables ≥ 5, headings ≥ 1, A4 geometry.

Plus the existing suite stays green (the removed `hwp_to_pdf` test is replaced).

## Verification

1. Unit tests above (TDD).
2. Full `pytest` suite green.
3. End-to-end on the real sample: `extract_hwp` → `SemanticDocument` → render docx →
   render to PNG (LibreOffice docx→pdf, which works) → eyeball vs the source: fonts
   (sans/serif), justified body, centered headings, tables, figures, A4 layout.
