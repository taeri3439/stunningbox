# Page-Template Fidelity — Phase 1 (cover/first-page background) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reproduce a born-digital PDF's cover/first-page full-bleed background in the translated DOCX — instead of dropping the cover design into the text flow as a small inline image.

**Architecture:** When a page-0 figure covers most of the page, the reconciler treats it as the page design: it renders the whole of page 0 with its text glyphs redacted (reusing `figure_overlay.render_redacted_clip`) into `cover_background`, and suppresses the inline cover figure (the title/subtitle stay as translatable heading blocks). The DOCX renderer anchors that image full-page, behind text, in the section's first-page header (different-first-page).

**Tech Stack:** Python 3, PyMuPDF, Pillow, python-docx + raw OOXML (`lxml` via `docx.oxml`), pytest. No new dependency.

**Spec:** `docs/superpowers/specs/2026-06-18-page-template-fidelity-design.md` (this plan is **Phase 1 only**; Phase 2 = header/footer chrome gets its own plan after this merges).

---

## Git / safety constraints (apply to EVERY commit)

- Work on the existing branch `page-template-fidelity-2026-06-18` (already created, in-place).
- `git add` ONLY the specific files listed in each task. **NEVER `git add -A`/`.`**
- NEVER stage: `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`, any
  `backend/_*.py` scratch file, `test-pdfs/`, or any image at repo root.
- Run tests from `backend`: `cd backend && .venv/Scripts/python.exe -m pytest`.

## File structure

- **Modify:** `backend/models.py` (`SemanticDocument.cover_background`),
  `backend/pipeline/reconciler.py` (cover detection + suppress inline cover figure + set field).
- **Create:** `backend/renderers/_ooxml.py` (raw-OOXML behind-text full-page image anchor; Phase 2 will extend it).
- **Modify:** `backend/renderers/docx_renderer.py` (anchor `cover_background` in the first-page header).
- **Create tests:** `backend/tests/test_cover_background.py`.

---

### Task 1: Model field `SemanticDocument.cover_background`

**Files:**
- Modify: `backend/models.py` (`SemanticDocument`, after the margin fields)
- Test: `backend/tests/test_cover_background.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_cover_background.py
"""Phase 1 — cover/first-page background."""
from models import SemanticDocument


def test_cover_background_defaults_none():
    doc = SemanticDocument()
    assert doc.cover_background is None


def test_cover_background_roundtrips():
    doc = SemanticDocument(cover_background="cover_bg.png")
    loaded = SemanticDocument.model_validate(doc.model_dump())
    assert loaded.cover_background == "cover_bg.png"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_cover_background.py -v`
Expected: FAIL — `SemanticDocument` has no `cover_background` (the roundtrip test sees it dropped / the field rejected).

- [ ] **Step 3: Implement**

In `backend/models.py`, add a field to `SemanticDocument` immediately after `margin_bottom` (the last geometry field, ~line 232):

```python
    margin_bottom: Optional[float] = None
    # Full-bleed background for the first page (cover): an asset-dir PNG with the page's text
    # redacted, anchored behind text on page 1. None → no cover background (unchanged behaviour).
    cover_background: Optional[str] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_cover_background.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/tests/test_cover_background.py
git commit -m "feat(models): SemanticDocument.cover_background field"
```

---

### Task 2: Reconciler — detect cover figure, render text-redacted background, suppress inline figure

**Files:**
- Modify: `backend/pipeline/reconciler.py`
- Test: `backend/tests/test_cover_background.py` (append)

Context: the `reconcile()` per-page loop has a `if btype == "figure":` branch (around line 108) that, after `extractor.render_clip`, computes the tight `geom` and (currently) builds a `FigureBlock`. `page_w`/`page_h` are the current page's size; `page_spans` are its spans; `report` collects warnings; the `SemanticDocument` is built at the end (~line 551). `figure_overlay` is already imported. `render_redacted_clip(pdf_path, page_index, exact_bbox, redact_bboxes, asset_dir, name)` renders a region with the given span bboxes' glyphs removed.

- [ ] **Step 1: Write the failing test (append)**

```python
import fitz

from pipeline import extractor
from pipeline.reconciler import reconcile


def _cover_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    page.draw_rect(fitz.Rect(0, 0, 300, 400), fill=(0.1, 0.3, 0.5))  # full-page design
    page.insert_text((40, 120), "VETERINARY RAPID TEST MARKET", fontsize=18, color=(1, 1, 1))
    p = str(tmp_path / "cover.pdf")
    doc.save(p); doc.close()
    return p


def test_reconcile_sets_cover_background_and_suppresses_inline_figure(tmp_path):
    pdf = _cover_pdf(tmp_path)
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(pdf, ad)
    title_ids = [s.id for s in raw.pages[0].spans]  # the title span(s)
    page_blocks = [[
        {"type": "figure", "figure_bbox": [0, 0, 300, 400], "source_span_ids": []},
        {"type": "heading", "text": "VETERINARY RAPID TEST MARKET", "source_span_ids": title_ids},
    ]]
    doc, _report = reconcile(raw, page_blocks, pdf, ad, source_lang="en")

    assert doc.cover_background, "cover_background should be set"
    assert (tmp_path / "assets" / doc.cover_background).exists()
    assert not [b for b in doc.blocks if b.type == "figure"], "inline cover figure suppressed"
    assert any(b.type == "heading" for b in doc.blocks), "title stays a translatable heading"


def test_reconcile_small_figure_on_page0_stays_inline(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=300, height=400)
    page.draw_rect(fitz.Rect(20, 20, 120, 90), fill=(0, 0, 0))  # small figure (<50% of page)
    p = str(tmp_path / "small.pdf")
    doc.save(p); doc.close()
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(p, ad)
    page_blocks = [[{"type": "figure", "figure_bbox": [20, 20, 120, 90], "source_span_ids": []}]]
    out, _ = reconcile(raw, page_blocks, p, ad, source_lang="en")
    assert out.cover_background is None
    assert [b for b in out.blocks if b.type == "figure"], "small figure stays inline"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_cover_background.py -k reconcile -v`
Expected: FAIL — `cover_background` is None and the cover figure is still emitted inline.

- [ ] **Step 3: Implement**

(a) Add a module constant near the top of `backend/pipeline/reconciler.py` (after the imports):

```python
# A figure on page 0 that covers at least this fraction of the page is the page's background
# design (a cover), not an inline image — render the whole page (text redacted) as the cover.
COVER_MIN_FRAC = 0.5
```

(b) In `reconcile()`, initialize an accumulator before the page loop. Find the line
`footer_items: list[tuple[str, str, int]] = []  # (text, side, page) for the bottom band`
(around line 71) and add immediately after it:

```python
    cover_bg: str | None = None  # first-page full-bleed background (set when a cover is detected)
```

(c) In the `if btype == "figure":` branch, insert cover detection AFTER the clip block sets
`geom`/`fw`/`fh`/`align`/`wrap` and BEFORE the `base_ref = ""` line (i.e. right after
`wrap = _figure_wrap(geom, page_spans)`):

```python
                # A large figure on page 0 IS the page design (cover), not an inline image:
                # render the whole page with its text redacted as the cover background, and
                # suppress this inline figure. The title/subtitle remain heading blocks on top.
                if (page_index == 0 and geom and len(geom) == 4 and page_w and page_h
                        and (geom[2] - geom[0]) * (geom[3] - geom[1])
                        >= COVER_MIN_FRAC * page_w * page_h):
                    if cover_bg is None:
                        try:
                            cover_bg = figure_overlay.render_redacted_clip(
                                pdf_path, page_index, [0, 0, page_w, page_h],
                                [s.bbox for s in page_spans], asset_dir, "cover_bg.png")
                        except Exception as e:  # noqa: BLE001
                            report.warnings.append(f"cover background render failed: {e}")
                    if cover_bg is not None:
                        referenced.update(ids)
                        continue  # do not emit an inline figure for the cover design
                    # render failed → fall through and keep it as a normal inline figure
                base_ref = ""
```

(d) Pass the field into the `SemanticDocument(...)` constructor (~line 551). Add the line:

```python
    doc = SemanticDocument(
        source_lang=source_lang,
        blocks=blocks,
        title=_guess_title(blocks),
        running_header=_pick_band(header_items, _band_rule(raw, top=True)),
        running_footer=_pick_band(footer_items, _band_rule(raw, top=False)),
        cover_background=cover_bg,
        **_page_geometry(raw),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_cover_background.py -v`
Expected: PASS (4 passed).

- [ ] **Step 5: Regression**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_reconciler_figure_recovery.py tests/test_figure_overlay_pipeline.py tests/test_figure_clip.py -v`
Expected: PASS (existing figure behavior on non-cover pages unchanged — the new branch only fires on page 0 for a ≥50%-coverage figure). If any FAIL, STOP and report DONE_WITH_CONCERNS.

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/reconciler.py backend/tests/test_cover_background.py
git commit -m "feat(reconciler): detect cover, render text-redacted page background"
```

---

### Task 3: `_ooxml.py` page-background anchor + wire into the DOCX renderer

**Files:**
- Create: `backend/renderers/_ooxml.py`
- Modify: `backend/renderers/docx_renderer.py` (`render_docx`)
- Test: `backend/tests/test_cover_background.py` (append)

Context: `docx_renderer.render_docx(doc, out_path, asset_dir)` builds the section (`sec = d.sections[0]`), applies geometry, and sets `sec.different_first_page_header_footer = True` when a running band exists. `docx.oxml.OxmlElement`, `docx.oxml.ns.qn`, and `docx.shared.Pt` are the OOXML tools (see the existing `_float_inline` for the anchor pattern). `doc.page_width`/`page_height` are in points.

- [ ] **Step 1: Write the failing test (append)**

```python
import os
from PIL import Image
from docx import Document
from models import SemanticDocument
from renderers.docx_renderer import render_docx


def test_render_docx_anchors_cover_background(tmp_path):
    ad = str(tmp_path)
    Image.new("RGB", (300, 400), (20, 80, 130)).save(os.path.join(ad, "cover_bg.png"))
    doc = SemanticDocument(source_lang="en", cover_background="cover_bg.png",
                           page_width=300.0, page_height=400.0)
    out = str(tmp_path / "out.docx")
    render_docx(doc, out, ad)

    d2 = Document(out)
    sec = d2.sections[0]
    assert sec.different_first_page_header_footer
    xml = sec.first_page_header._element.xml
    assert 'behindDoc="1"' in xml           # background sits behind the text
    assert str(int(Pt(400.0))) in xml       # full-page height extent (EMU)
```
(Add `from docx.shared import Pt` to the test's imports.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_cover_background.py -k anchors -v`
Expected: FAIL — no cover background is rendered (no `behindDoc` anchor in the first-page header).

- [ ] **Step 3: Implement the OOXML helper**

Create `backend/renderers/_ooxml.py`:

```python
"""Raw-OOXML builders python-docx doesn't expose: behind-text page-anchored images (and,
in Phase 2, PAGE fields). Kept separate so docx_renderer stays focused."""
from __future__ import annotations

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt


def add_page_background(header, image_path: str, page_width_pt: float, page_height_pt: float) -> None:
    """Anchor `image_path` as a full-page image behind text in `header` (use a section's
    first-page header for a cover). The image fills the whole page and sits behind the text."""
    cx = int(Pt(page_width_pt))   # EMU
    cy = int(Pt(page_height_pt))
    p = header.paragraphs[0] if header.paragraphs else header.add_paragraph()
    run = p.add_run()
    run.add_picture(image_path)  # inline first; converted to a page-anchored drawing below
    drawing = run._r.find(qn("w:drawing"))
    inline = drawing.find(qn("wp:inline"))
    inline.tag = qn("wp:anchor")
    for k, v in {
        "simplePos": "0", "relativeHeight": "0", "behindDoc": "1", "locked": "0",
        "layoutInCell": "1", "allowOverlap": "1",
        "distT": "0", "distB": "0", "distL": "0", "distR": "0",
    }.items():
        inline.set(k, v)
    # Size the drawing to the full page (both the anchor extent and the picture's own extent).
    extent = inline.find(qn("wp:extent"))
    extent.set("cx", str(cx))
    extent.set("cy", str(cy))
    for ext in inline.iter(qn("a:ext")):
        ext.set("cx", str(cx))
        ext.set("cy", str(cy))
    # Position at the page origin (0,0), relative to the page.
    simple_pos = OxmlElement("wp:simplePos")
    simple_pos.set("x", "0")
    simple_pos.set("y", "0")
    pos_h = OxmlElement("wp:positionH")
    pos_h.set("relativeFrom", "page")
    off_h = OxmlElement("wp:posOffset")
    off_h.text = "0"
    pos_h.append(off_h)
    pos_v = OxmlElement("wp:positionV")
    pos_v.set("relativeFrom", "page")
    off_v = OxmlElement("wp:posOffset")
    off_v.text = "0"
    pos_v.append(off_v)
    extent.addprevious(simple_pos)
    extent.addprevious(pos_h)
    extent.addprevious(pos_v)
    wrap = OxmlElement("wp:wrapNone")
    inline.find(qn("wp:docPr")).addprevious(wrap)
```

- [ ] **Step 4: Wire into `render_docx`**

In `backend/renderers/docx_renderer.py`, add the import near the other `renderers` import (line 36):

```python
from renderers import _ooxml
```

Then in `render_docx`, after the running-header/footer block (after line 108, before the
`body_pt = _body_size(doc)` line), add:

```python
    # Cover/first-page full-bleed background: anchor it behind text in the first-page header.
    if doc.cover_background:
        bg_path = os.path.join(asset_dir, doc.cover_background)
        if os.path.exists(bg_path):
            sec.different_first_page_header_footer = True
            _ooxml.add_page_background(
                sec.first_page_header, bg_path,
                doc.page_width or float(sec.page_width.pt),
                doc.page_height or float(sec.page_height.pt),
            )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_cover_background.py -k anchors -v`
Expected: PASS.

If the `behindDoc`/extent assertion fails, inspect `sec.first_page_header._element.xml` and adjust
the anchor in `_ooxml.add_page_background` so the `wp:anchor` carries `behindDoc="1"` and a
`wp:extent` with `cx`/`cy` equal to the page EMU. Do NOT weaken the test. (Anchor child order
mirrors the working `_float_inline`: simplePos, positionH, positionV, extent, …, wrapNone, docPr.)

- [ ] **Step 6: Full file + regression**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_cover_background.py tests/test_render_pagination.py tests/test_render_table_colwidth.py tests/test_docx_render_merge.py -v`
Expected: PASS (existing DOCX rendering unchanged for docs without a cover background).

- [ ] **Step 7: Commit**

```bash
git add backend/renderers/_ooxml.py backend/renderers/docx_renderer.py backend/tests/test_cover_background.py
git commit -m "feat(docx): anchor cover background full-bleed in first-page header"
```

---

### Task 4: Full suite + dogfood (controller verification)

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all green (prior baseline 341 passed / 1 skipped + the new cover tests). Investigate any regression.

- [ ] **Step 2: Dogfood**

Upload the veterinary report (en→ko) and confirm in the viewer + exported DOCX that **page 1's
diagonal cover design now fills the whole page** (behind the translated title/subtitle), instead
of appearing as a small mid-page block. Confirm body pages and figures are unchanged from before.
Note any figure that should NOT have been treated as a cover (false positive) or a cover that
wasn't detected (false negative) for threshold tuning.

- [ ] **Step 3: Confirm no stray files staged**

Run `git status --short`; confirm `backend/auth.py`, `backend/documents_store.py`,
`frontend/src/proxy.ts` are still unstaged ` M`, and no scratch/screenshot files are staged.

---

## Self-review (against the spec, Phase 1 scope)

**Spec coverage (Phase 1 items):**
- `SemanticDocument.cover_background` model field → Task 1. ✓
- Cover detection (≥~70% → here 50% threshold, `COVER_MIN_FRAC`, tunable per the spec's open
  question) + title-redacted full-page render reusing `render_redacted_clip` + suppress inline
  cover figure; title stays a heading → Task 2. ✓
- DOCX different-first-page + full-bleed behind-text anchor in the first-page header → Task 3. ✓
- Reuse `figure_overlay.render_redacted_clip` / `extractor.render_clip` → Task 2 (no new dep). ✓
- Testing via synthetic PDFs + DOCX XML asserts; dogfood → Tasks 1–4. ✓
- Out of Phase 1 (header/footer chrome, page number, HTML preview, HWP/DOCX paths) → not touched;
  Phase 2 gets its own plan. ✓

**Placeholder scan:** none — every code step has complete code; the threshold is a named constant
with a documented rationale, not a TODO; the OOXML tuning note has concrete guidance + a reference
implementation (`_float_inline`).

**Type consistency:** `cover_background: Optional[str]` (model) ↔ `cover_bg: str | None` (reconciler
local) ↔ `doc.cover_background` (renderer) ↔ `add_page_background(header, image_path, page_width_pt,
page_height_pt)` are used consistently across Tasks 1–3.
