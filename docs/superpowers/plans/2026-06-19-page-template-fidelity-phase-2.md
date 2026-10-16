# Page-Template Fidelity Phase 2 (header/footer chrome) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reproduce a born-digital PDF's repeating page chrome in the translated DOCX — the decorative header band + logo and the footer color bar — as full-width images anchored behind the text, with the running title/footer text kept translatable and the page number rendered as an auto-updating Word PAGE field.

**Architecture:** Decorative strips are captured **once** from a representative body page by re-rendering the strip region with its text glyphs redacted (reusing `figure_overlay.render_redacted_clip`, which keeps vector art + images), guarded by a non-white-fraction check so plain headers don't emit blank images. The strip PNGs ride on the existing `RunningBand` model (new `image` + `page_number` fields). At render time they become page-anchored, behind-text drawings in the page-2+ header/footer (Phase 1 already enabled `different_first_page_header_footer`, so page 1 keeps the cover); the band text + a PAGE field render on top via the existing `_render_band` table.

**Tech Stack:** Python, PyMuPDF (render/redaction), Pillow (px dims + non-white histogram), python-docx + raw lxml OOXML (`backend/renderers/_ooxml.py`), pytest with synthetic `fitz` PDFs.

**Branch:** `page-template-fidelity-phase2-2026-06-19` (off `page-template-fidelity-2026-06-18`, which carries Phase 1).

---

## File Structure

- **Modify** `backend/models.py` — add `RunningBand.image` + `RunningBand.page_number` (Task 1).
- **Modify** `backend/renderers/_ooxml.py` — factor a shared behind-text anchor; add `add_band_image` + `add_page_field` (Task 2).
- **Modify** `backend/pipeline/reconciler.py` — strip-bbox + capture + representative-page + page-number-side helpers, wired into `reconcile()` (Task 3).
- **Modify** `backend/renderers/docx_renderer.py` — anchor band images + render the PAGE field in `_render_band` (Task 4).
- **Tests:** `backend/tests/test_running_chrome_image.py` (new, Tasks 2–4), extend `backend/tests/test_cover_background.py` only if needed; Task 5 adds an end-to-end reconcile→render test.

Reuses (unchanged): `figure_overlay.render_redacted_clip`, `_page_nonwhite_frac`, `RawPage.spans/images/hrules`, `_pick_band`/`_band_rule`/`_chrome_side`, the `_render_band` table path, `add_page_background`.

---

### Task 1: Model fields — `RunningBand.image` + `page_number`

**Files:**
- Modify: `backend/models.py:222-231` (`RunningBand`)
- Test: `backend/tests/test_running_chrome_image.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_running_chrome_image.py`:
```python
from models import RunningBand, SemanticDocument


def test_running_band_image_and_page_number_default_none():
    b = RunningBand()
    assert b.image is None
    assert b.page_number is None


def test_running_band_round_trips_image_and_page_number():
    b = RunningBand(left="Market Report", image="hdr.png", page_number="right",
                    rule=True, rule_size=0.75, rule_color="123456")
    loaded = RunningBand.model_validate(b.model_dump())
    assert loaded.image == "hdr.png"
    assert loaded.page_number == "right"
    assert loaded.left == "Market Report"


def test_old_source_json_without_new_fields_loads():
    # A source.json written before Phase 2 has no image/page_number keys.
    doc = SemanticDocument.model_validate(
        {"running_header": {"left": "X", "rule": False}})
    assert doc.running_header.image is None
    assert doc.running_header.page_number is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_running_chrome_image.py -v`
Expected: FAIL — `AttributeError`/`ValidationError` on `image`/`page_number` (fields don't exist yet).

- [ ] **Step 3: Add the fields**

In `backend/models.py`, replace the `RunningBand` body (lines 226-231) so the two new fields sit before `rule`:
```python
    left: Optional[str] = None
    center: Optional[str] = None
    right: Optional[str] = None
    image: Optional[str] = None  # decoration-strip PNG (asset-dir basename, text-redacted),
    #                              anchored full-width behind the band text. None → no decoration.
    page_number: Optional[str] = None  # "left" | "center" | "right" → a Word PAGE field there.
    rule: bool = False  # a horizontal line spanning the band (e.g. under the header)
    rule_size: Optional[float] = None  # rule thickness (pt); None → default
    rule_color: Optional[str] = None  # rule hex colour 'RRGGBB'; None → ink
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_running_chrome_image.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Confirm `image`/`page_number` are NOT yielded as translatable**

`iter_segments` (models.py:299-304) iterates only `("left", "center", "right")` on bands — so `image`/`page_number` are already excluded. No change needed; just confirm by reading those lines.

- [ ] **Step 6: Commit**

```bash
git add backend/models.py backend/tests/test_running_chrome_image.py
git commit -m "feat(models): RunningBand.image + page_number for Phase 2 chrome"
```

---

### Task 2: OOXML builders — band-image anchor + PAGE field

**Files:**
- Modify: `backend/renderers/_ooxml.py` (refactor `add_page_background`; add `add_band_image`, `add_page_field`)
- Test: `backend/tests/test_running_chrome_image.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_running_chrome_image.py`:
```python
import os
from docx import Document
from docx.oxml.ns import qn
from PIL import Image
from renderers import _ooxml


def _png(path, w=600, h=80, color=(40, 80, 160)):
    Image.new("RGB", (w, h), color).save(path)
    return path


def test_add_band_image_anchors_full_width_behind_text_at_offset(tmp_path):
    img = _png(str(tmp_path / "band.png"))
    d = Document()
    sec = d.sections[0]
    # width 500pt, the band image is 500pt wide x 60pt tall, pinned 740pt down (a footer).
    _ooxml.add_band_image(sec.footer, img, width_pt=500.0, height_pt=60.0, y_offset_pt=740.0)
    xml = sec.footer._element.xml
    assert "anchor" in xml and 'behindDoc="1"' in xml
    extent = sec.footer._element.findall(".//" + qn("wp:extent"))[0]
    assert extent.get("cx") == str(int(500.0 * 12700))  # full-width EMU
    assert extent.get("cy") == str(int(60.0 * 12700))
    # The vertical posOffset places the strip at the bottom.
    offsets = [e.text for e in sec.footer._element.iter(qn("wp:posOffset"))]
    assert str(int(740.0 * 12700)) in offsets


def test_add_page_field_inserts_PAGE_fldchar(tmp_path):
    d = Document()
    p = d.add_paragraph()
    _ooxml.add_page_field(p)
    xml = p._p.xml
    assert "fldChar" in xml and 'w:fldCharType="begin"' in xml
    assert "PAGE" in xml
    assert 'w:fldCharType="end"' in xml


def test_add_page_background_still_full_page(tmp_path):
    # Regression: Phase-1 cover anchor unchanged after the refactor.
    img = _png(str(tmp_path / "cover.png"), w=595, h=842)
    d = Document()
    _ooxml.add_page_background(d.sections[0].first_page_header, img, 595.0, 842.0)
    xml = d.sections[0].first_page_header._element.xml
    assert 'behindDoc="1"' in xml
    extent = d.sections[0].first_page_header._element.findall(".//" + qn("wp:extent"))[0]
    assert extent.get("cx") == str(int(595.0 * 12700))
    assert extent.get("cy") == str(int(842.0 * 12700))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_running_chrome_image.py -k "band_image or page_field or page_background" -v`
Expected: FAIL — `add_band_image`/`add_page_field` don't exist (`AttributeError`).

- [ ] **Step 3: Refactor + add the builders**

Edit `backend/renderers/_ooxml.py`. Replace the whole body of `add_page_background` (lines 10-52) and append two new functions, factoring the shared anchor logic into a private helper:
```python
def _anchor_behind_text(hf, image_path: str, cx_emu: int, cy_emu: int,
                        x_emu: int, y_emu: int) -> None:
    """Anchor `image_path` as a floating, behind-text picture in header/footer `hf`, sized
    cx_emu × cy_emu and positioned at (x_emu, y_emu) relative to the page."""
    p = hf.paragraphs[0] if hf.paragraphs else hf.add_paragraph()
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
    extent = inline.find(qn("wp:extent"))
    extent.set("cx", str(cx_emu))
    extent.set("cy", str(cy_emu))
    for ext in inline.iter(qn("a:ext")):
        ext.set("cx", str(cx_emu))
        ext.set("cy", str(cy_emu))
    simple_pos = OxmlElement("wp:simplePos")
    simple_pos.set("x", "0")
    simple_pos.set("y", "0")
    pos_h = OxmlElement("wp:positionH")
    pos_h.set("relativeFrom", "page")
    off_h = OxmlElement("wp:posOffset")
    off_h.text = str(x_emu)
    pos_h.append(off_h)
    pos_v = OxmlElement("wp:positionV")
    pos_v.set("relativeFrom", "page")
    off_v = OxmlElement("wp:posOffset")
    off_v.text = str(y_emu)
    pos_v.append(off_v)
    extent.addprevious(simple_pos)
    extent.addprevious(pos_h)
    extent.addprevious(pos_v)
    wrap = OxmlElement("wp:wrapNone")
    inline.find(qn("wp:docPr")).addprevious(wrap)


def add_page_background(header, image_path: str, page_width_pt: float, page_height_pt: float) -> None:
    """Anchor `image_path` as a full-page image behind text in `header` (use a section's
    first-page header for a cover). The image fills the whole page and sits behind the text."""
    _anchor_behind_text(header, image_path,
                        int(Pt(page_width_pt)), int(Pt(page_height_pt)), 0, 0)


def add_band_image(hf, image_path: str, width_pt: float, height_pt: float,
                   y_offset_pt: float) -> None:
    """Anchor `image_path` as a full-width strip behind text in header/footer `hf`, sized
    width_pt × height_pt, pinned x=0 and y=y_offset_pt from the page top (0 for a header
    band, page_height−height for a footer band)."""
    _anchor_behind_text(hf, image_path,
                        int(Pt(width_pt)), int(Pt(height_pt)), 0, int(Pt(y_offset_pt)))


def add_page_field(paragraph) -> None:
    """Append a Word PAGE field (auto page number) to `paragraph` as a fldChar run sequence."""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = "PAGE"
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)
```
Update the module docstring (line 1-2) to drop the "(and, in Phase 2, PAGE fields)" parenthetical since it's now built:
```python
"""Raw-OOXML builders python-docx doesn't expose: behind-text page-anchored images
(full-page covers + full-width header/footer bands) and PAGE fields. Kept separate so
docx_renderer stays focused."""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_running_chrome_image.py -k "band_image or page_field or page_background" -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Regression — Phase-1 cover render still works**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_cover_background.py -v`
Expected: PASS (the refactor preserves `add_page_background` behaviour; the cover-render XML test still passes).

- [ ] **Step 6: Commit**

```bash
git add backend/renderers/_ooxml.py backend/tests/test_running_chrome_image.py
git commit -m "feat(ooxml): full-width band-image anchor + PAGE field builders"
```

---

### Task 3: Reconciler — capture decorative strips + page-number side

**Files:**
- Modify: `backend/pipeline/reconciler.py` (new module constants + helpers; wire into `reconcile()` band construction at lines 613-622)
- Test: `backend/tests/test_running_chrome_image.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_running_chrome_image.py` (these use the same `fitz`/`extract`/`reconcile` pattern as `test_cover_background.py` — import them the same way that file does):
```python
import fitz
from pipeline import extractor, reconciler
from pipeline.reconciler import reconcile


def _chrome_pdf(path, pages=3):
    """A doc whose body pages carry a colored top band + header title + a footer color bar
    + a page number — the decoration Phase 2 must capture."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=300, height=400)
        # decorative header band (full-width colored strip across the top)
        page.draw_rect(fitz.Rect(0, 0, 300, 36), fill=(0.15, 0.3, 0.55))
        page.insert_text((20, 24), "Market Report", fontsize=11, color=(1, 1, 1))
        # body text
        page.insert_text((20, 200), f"body line on page {i}", fontsize=10)
        # decorative footer color bar + page number
        page.draw_rect(fitz.Rect(0, 372, 300, 400), fill=(0.15, 0.3, 0.55))
        page.insert_text((270, 392), str(i + 1), fontsize=10, color=(1, 1, 1))
    doc.save(path)
    doc.close()
    return path


def _plain_pdf(path, pages=3):
    """Body pages with header/footer TEXT but no decoration (white background)."""
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=300, height=400)
        page.insert_text((20, 16), "Plain Header", fontsize=10)
        page.insert_text((20, 200), f"body {i}", fontsize=10)
        page.insert_text((270, 392), str(i + 1), fontsize=10)
    doc.save(path)
    doc.close()
    return path


def _blocks_for(raw, header_text="Market Report"):
    # Minimal analyzer output: mark the header title as chrome on each page so the band TEXT
    # is collected (page-number digits are chrome too); body line as a paragraph.
    page_blocks = []
    for pg in raw.pages:
        blocks = []
        for s in pg.spans:
            t = s.text.strip()
            if t == header_text or t.isdigit():
                blocks.append({"type": "chrome", "text": t, "source_span_ids": [s.id]})
            else:
                blocks.append({"type": "paragraph", "text": t, "source_span_ids": [s.id]})
        page_blocks.append(blocks)
    return page_blocks


def test_capture_header_and_footer_band_images(tmp_path):
    p = _chrome_pdf(str(tmp_path / "chrome.pdf"))
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(p, ad)
    out, _ = reconcile(raw, _blocks_for(raw), p, ad, source_lang="en")
    assert out.running_header and out.running_header.image, "header decoration captured"
    assert out.running_footer and out.running_footer.image, "footer decoration captured"
    assert os.path.exists(os.path.join(ad, out.running_header.image))
    assert os.path.exists(os.path.join(ad, out.running_footer.image))
    # The running title stays translatable text (not baked only into the image).
    assert out.running_header.left == "Market Report"


def test_footer_page_number_side_detected(tmp_path):
    p = _chrome_pdf(str(tmp_path / "chrome.pdf"))
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(p, ad)
    out, _ = reconcile(raw, _blocks_for(raw), p, ad, source_lang="en")
    assert out.running_footer.page_number == "right"  # digit sat at the right edge


def test_plain_header_emits_no_band_image(tmp_path):
    p = _plain_pdf(str(tmp_path / "plain.pdf"))
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(p, ad)
    out, _ = reconcile(raw, _blocks_for(raw, header_text="Plain Header"), p, ad, source_lang="en")
    # No decoration → no blank image anchored; the header text band still exists.
    assert out.running_header is None or out.running_header.image is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_running_chrome_image.py -k "band_images or page_number_side or no_band_image" -v`
Expected: FAIL — `running_header.image` is None / `page_number` is None (capture not wired yet).

- [ ] **Step 3: Add module constants**

In `backend/pipeline/reconciler.py`, after `COVER_MAX_BODY_BLOCKS = 3` (line 45) add:
```python
# Header/footer decorative strip = the top/bottom this fraction of the page. The strip is
# rendered (text redacted) and kept only if enough of it is non-white (real decoration), so a
# plain header with just text doesn't anchor a blank image.
BAND_STRIP_FRAC = 0.12
# Minimum non-white fraction of a rendered (text-redacted) strip for it to count as decoration.
# A thin rule alone is well under this (it stays a cell-border rule); a color bar/band clears it.
BAND_DECORATION_NONWHITE_FRAC = 0.06
```

- [ ] **Step 4: Add the capture + detection helpers**

First, add `RawPage` to the `from models import (...)` block (lines 17-30) — it is NOT currently imported but the helpers below type-hint it. Add the line `    RawPage,` alphabetically (after `ParagraphBlock,`).

Then, in `backend/pipeline/reconciler.py`, add these functions near `_pick_band`/`_band_rule` (after line 1291). `re`, `RawDocument`, and `figure_overlay` are already imported (used by the cover/chrome paths):
```python
def _representative_chrome_page(raw: RawDocument) -> int | None:
    """Index of a body page to capture repeating chrome from. Chrome repeats, so the first
    non-cover page (index 1) is representative; None when the doc has no body page."""
    return 1 if len(raw.pages) >= 2 else None


def _band_strip_bbox(pg: RawPage, top: bool) -> tuple[float, float, float, float] | None:
    """The full-width header (top) / footer (bottom) strip region in PDF points, or None if
    the page has no geometry. Bounded by BAND_STRIP_FRAC of the page height."""
    if not (pg.width and pg.height):
        return None
    band = BAND_STRIP_FRAC * pg.height
    if top:
        return (0.0, 0.0, pg.width, band)
    return (0.0, pg.height - band, pg.width, pg.height)


def _capture_band_image(pdf_path: str, pg: RawPage, page_index: int, asset_dir: str,
                        top: bool) -> str | None:
    """Render the header/footer strip of `pg` with its text glyphs redacted (vector art +
    logo images kept) → a PNG basename, but ONLY if the strip carries real decoration
    (non-white fraction ≥ threshold). Returns None for a plain/blank strip or on failure."""
    region = _band_strip_bbox(pg, top)
    if region is None:
        return None
    rx0, ry0, rx1, ry1 = region
    redact = [s.bbox for s in pg.spans
              if ry0 <= (s.bbox[1] + s.bbox[3]) / 2 <= ry1]  # text in the strip → removed
    name = f"{'hdr' if top else 'ftr'}_band.png"
    try:
        out = figure_overlay.render_redacted_clip(
            pdf_path, page_index, list(region), redact, asset_dir, name)
    except Exception:  # noqa: BLE001 — a degenerate strip just yields no decoration
        return None
    if _page_nonwhite_frac(out, asset_dir) < BAND_DECORATION_NONWHITE_FRAC:
        return None  # plain strip — keep the rule path, don't anchor a blank image
    return out


def _page_number_side(items: list) -> str | None:
    """The horizontal side (left/center/right) where a page-number digit recurs in the footer
    chrome, for a PAGE field. None if no page-number-like item repeats."""
    sides_by_count: dict[str, int] = {}
    for text, side, _pg in items:
        if re.fullmatch(r"[\divxlcDIVXLC\-\.\s/]+", (text or "").strip() or "x"):
            sides_by_count[side] = sides_by_count.get(side, 0) + 1
    if not sides_by_count:
        return None
    return max(sides_by_count.items(), key=lambda kv: kv[1])[0]
```

- [ ] **Step 5: Wire capture into `reconcile()`**

In `backend/pipeline/reconciler.py`, replace the `SemanticDocument(...)` construction (lines 613-621) so the bands get their image/page_number after `_pick_band`:
```python
    blocks = _stitch_across_pages(blocks, report)

    header_band = _pick_band(header_items, _band_rule(raw, top=True))
    footer_band = _pick_band(footer_items, _band_rule(raw, top=False))
    # Phase 2: capture repeating decorative strips (band/logo, footer bar) as behind-text
    # images, and the page-number side as a PAGE field. Captured once from a representative
    # body page; only kept when the strip actually carries decoration.
    rep = _representative_chrome_page(raw)
    if rep is not None:
        hdr_img = _capture_band_image(pdf_path, raw.pages[rep], rep, asset_dir, top=True)
        if hdr_img:
            header_band = header_band or RunningBand()
            header_band.image = hdr_img
        ftr_img = _capture_band_image(pdf_path, raw.pages[rep], rep, asset_dir, top=False)
        if ftr_img:
            footer_band = footer_band or RunningBand()
            footer_band.image = ftr_img
    pn_side = _page_number_side(footer_items)
    if pn_side:
        footer_band = footer_band or RunningBand()
        footer_band.page_number = pn_side

    doc = SemanticDocument(
        source_lang=source_lang,
        blocks=blocks,
        title=_guess_title(blocks),
        running_header=header_band,
        running_footer=footer_band,
        cover_background=cover_bg,
        **_page_geometry(raw),
    )
    return doc, report
```

- [ ] **Step 6: Run the Task-3 tests**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_running_chrome_image.py -v`
Expected: PASS (all Task 1–3 tests, including header/footer capture, page-number side = "right", and plain-header → no image).

- [ ] **Step 7: Regression — reconciler suites**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_cover_background.py tests/test_reconciler_figure_recovery.py -v`
Expected: PASS (chrome-text + cover behaviour unchanged; the new capture only adds image/page_number).

- [ ] **Step 8: Commit**

```bash
git add backend/pipeline/reconciler.py backend/tests/test_running_chrome_image.py
git commit -m "feat(reconciler): capture header/footer decoration strips + page-number side"
```

---

### Task 4: docx_renderer — anchor band images + render PAGE field

**Files:**
- Modify: `backend/renderers/docx_renderer.py` (`render_docx` lines 106-109; `_render_band` lines 402-430; new `_anchor_band_image` helper)
- Test: `backend/tests/test_running_chrome_image.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_running_chrome_image.py`:
```python
from renderers.docx_renderer import render_docx
from models import RunningBand, SemanticDocument, ParagraphBlock


def test_render_band_image_anchored_behind_header_text(tmp_path):
    ad = str(tmp_path / "assets")
    os.makedirs(ad, exist_ok=True)
    Image.new("RGB", (600, 80), (40, 80, 160)).save(os.path.join(ad, "hdr_band.png"))
    doc = SemanticDocument(
        running_header=RunningBand(left="Market Report", image="hdr_band.png"),
        page_width=595.0, page_height=842.0,
        margin_left=72.0, margin_right=72.0, margin_top=72.0, margin_bottom=72.0,
        blocks=[ParagraphBlock(text="body", page=1)])
    out = str(tmp_path / "o.docx")
    render_docx(doc, out, ad)
    d = Document(out)
    xml = d.sections[0].header._element.xml
    assert 'behindDoc="1"' in xml  # band image anchored behind text
    assert "Market Report" in xml   # translatable title still rendered on top


def test_render_footer_page_field(tmp_path):
    ad = str(tmp_path / "assets")
    os.makedirs(ad, exist_ok=True)
    doc = SemanticDocument(
        running_footer=RunningBand(center="", page_number="right"),
        page_width=595.0, page_height=842.0,
        margin_left=72.0, margin_right=72.0, margin_top=72.0, margin_bottom=72.0,
        blocks=[ParagraphBlock(text="body", page=1)])
    out = str(tmp_path / "o.docx")
    render_docx(doc, out, ad)
    d = Document(out)
    xml = d.sections[0].footer._element.xml
    assert "fldChar" in xml and "PAGE" in xml  # auto page number field present


def test_render_chromeless_doc_unaffected(tmp_path):
    ad = str(tmp_path / "assets")
    os.makedirs(ad, exist_ok=True)
    doc = SemanticDocument(blocks=[ParagraphBlock(text="hello", page=0)])
    out = str(tmp_path / "o.docx")
    render_docx(doc, out, ad)
    d = Document(out)
    assert "hello" in d.paragraphs[0].text or any("hello" in p.text for p in d.paragraphs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_running_chrome_image.py -k "band_image_anchored or footer_page_field or chromeless" -v`
Expected: FAIL — no `behindDoc` in the header (image not anchored) / no `fldChar` (PAGE field not rendered).

- [ ] **Step 3: Add `_anchor_band_image` + render-time wiring**

In `backend/renderers/docx_renderer.py`, add a helper near `_render_band` (after line 430). It reads the PNG's pixel aspect to size the full-width strip in points:
```python
def _anchor_band_image(hf, image_name: str, asset_dir: str, doc: "SemanticDocument",
                       at_top: bool) -> None:
    """Anchor a captured decoration strip full-width behind the band text. The strip was
    rendered across the full page width, so width = page_width and height preserves the PNG
    aspect; a footer strip is pinned to the page bottom."""
    path = os.path.join(asset_dir, image_name)
    if not (image_name and os.path.exists(path) and doc.page_width and doc.page_height):
        return
    try:
        from PIL import Image as _Img
        with _Img.open(path) as im:
            px_w, px_h = im.size
    except Exception:  # noqa: BLE001
        return
    if not px_w:
        return
    width_pt = float(doc.page_width)
    height_pt = width_pt * (px_h / px_w)
    y_offset = 0.0 if at_top else max(0.0, float(doc.page_height) - height_pt)
    _ooxml.add_band_image(hf, path, width_pt, height_pt, y_offset)
```
Then update the band-render block in `render_docx` (lines 106-109) to anchor the images after rendering the text band:
```python
    if doc.running_header:
        _render_band(sec.header, doc.running_header, content_width, font=body_font)
        if doc.running_header.image:
            _anchor_band_image(sec.header, doc.running_header.image, asset_dir, doc, at_top=True)
    if doc.running_footer:
        _render_band(sec.footer, doc.running_footer, content_width, footer=True, font=body_font)
        if doc.running_footer.image:
            _anchor_band_image(sec.footer, doc.running_footer.image, asset_dir, doc, at_top=False)
```

- [ ] **Step 4: Render the PAGE field inside `_render_band`**

In `backend/renderers/docx_renderer.py`, replace `_render_band` (lines 402-430) so a `page_number` side gets a cell (even with no text) and a PAGE field is appended there:
```python
def _render_band(hf, band: RunningBand, content_width, footer: bool = False,
                 font: str | None = None) -> None:
    """Render a running header/footer as a borderless 1-row table: left | (center) | right.
    A table pins each element to its edge in EVERY renderer; tab stops render inconsistently
    across Word/OnlyOffice/LibreOffice (the right element drifts to mid-line). The band's
    horizontal rule is drawn as a cell border (header → bottom edge, footer → top). A
    page_number side gets a Word PAGE field (auto page number) appended in its cell."""
    sides = [(band.left, "left"), (band.center, "center"), (band.right, "right")]
    active = [(t, a) for t, a in sides if t or (band.page_number == a)]
    if not active:
        if band.rule:
            _paragraph_border(hf.paragraphs[0], "top" if footer else "bottom",
                              band.rule_size, band.rule_color)
        return
    table = hf.add_table(rows=1, cols=len(active), width=content_width)
    _clear_table_borders(table)
    col_w = int(content_width) // len(active)
    for i, (text, align) in enumerate(active):
        cell = table.cell(0, i)
        cell.width = Emu(col_w)
        cp = cell.paragraphs[0]
        cp.alignment = _ALIGN[align]
        if text:
            run = cp.add_run(text)
            run.font.color.rgb = INK
            if font:
                _set_run_font(run, font)
        if band.page_number == align:
            _ooxml.add_page_field(cp)
        if band.rule:
            _cell_band_rule(cell, "top" if footer else "bottom", band.rule_size, band.rule_color)
    # Move the table ahead of the (required) trailing empty paragraph so it sits at the top.
    hf.paragraphs[0]._p.addprevious(table._tbl)
```

- [ ] **Step 5: Run the Task-4 tests**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_running_chrome_image.py -v`
Expected: PASS (band image anchored behind header text; footer PAGE field present; chrome-less doc unaffected).

- [ ] **Step 6: Regression — renderer suites**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_cover_background.py tests/test_render_pagination.py -v`
Expected: PASS (cover render, geometry, and existing band-text rendering unchanged).

- [ ] **Step 7: Commit**

```bash
git add backend/renderers/docx_renderer.py backend/tests/test_running_chrome_image.py
git commit -m "feat(docx): anchor decoration strips behind chrome + render PAGE field"
```

---

### Task 5: Integration test + full suite + dogfood

**Files:**
- Test: `backend/tests/test_running_chrome_image.py` (end-to-end reconcile→render)

- [ ] **Step 1: Write the end-to-end test**

Append to `backend/tests/test_running_chrome_image.py`:
```python
def test_end_to_end_chrome_pdf_to_docx(tmp_path):
    p = _chrome_pdf(str(tmp_path / "chrome.pdf"))
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(p, ad)
    doc, _ = reconcile(raw, _blocks_for(raw), p, ad, source_lang="en")
    out = str(tmp_path / "o.docx")
    render_docx(doc, out, ad)
    d = Document(out)
    # different-first-page so page-1 chrome differs from body pages
    assert d.sections[0].different_first_page_header_footer
    header_xml = d.sections[0].header._element.xml
    footer_xml = d.sections[0].footer._element.xml
    assert 'behindDoc="1"' in header_xml  # header band image
    assert 'behindDoc="1"' in footer_xml  # footer bar image
    assert "fldChar" in footer_xml and "PAGE" in footer_xml  # auto page number
```

- [ ] **Step 2: Run the end-to-end test**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_running_chrome_image.py::test_end_to_end_chrome_pdf_to_docx -v`
Expected: PASS.

- [ ] **Step 3: Run the full backend suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all pass (prior count + the new `test_running_chrome_image.py` tests), 1 skipped as before.

- [ ] **Step 4: Commit**

```bash
git add backend/tests/test_running_chrome_image.py
git commit -m "test(chrome): end-to-end PDF→DOCX decoration + page-field integration"
```

- [ ] **Step 5: Dogfood (controller-run, not the subagent)**

The controller runs the veterinary report through the live stack (backend :8003 / OnlyOffice :8080 / frontend prod build) per the dogfood mechanics in memory, and confirms in OnlyOffice + the exported DOCX:
- the diamond header band + logo appear on body pages (page 2+),
- the footer color bar appears with an auto page number that increments,
- page 1 still shows the cover (Phase 1) with no body chrome,
- a plain HWP/PDF (no decoration) renders unchanged (no blank strips).

---

## Self-Review

**Spec coverage:**
- `RunningBand.image`/`page_number` model fields → Task 1. ✓ (spec "Data model")
- Header strip capture (text-redacted, logo included) → Task 3 `_capture_band_image`. ✓ (spec "Header strip" + test 3)
- Footer strip capture + page-number side → Task 3 `_capture_band_image` + `_page_number_side`. ✓ (spec "Footer strip" + test 4)
- DOCX behind-text band anchors + PAGE field + `_ooxml.py` helpers → Tasks 2 & 4. ✓ (spec "Rendering")
- Redaction reuse (text removed, art kept) → reuses `render_redacted_clip` (Phase 1 tested it removes glyphs/keeps art); strip capture inherits it. ✓ (spec test 5)
- Regression (chrome-less docs unaffected; existing band text still renders) → Task 4 `test_render_chromeless_doc_unaffected` + Task 3/4 regression runs. ✓ (spec test 6)
- Different-first-page already set by Phase 1; cover on page 1, chrome on page 2+ → Task 5 asserts. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✓

**Type consistency:** `add_band_image(hf, image_path, width_pt, height_pt, y_offset_pt)` — same signature in Task 2 (def), Task 2 test, and Task 4 `_anchor_band_image` caller. `add_page_field(paragraph)` — same in Task 2 def, Task 2 test, Task 4 `_render_band`. `_capture_band_image(pdf_path, pg, page_index, asset_dir, top)` — same in def + `reconcile()` caller. `RunningBand.image`/`page_number` names consistent across Tasks 1/3/4. ✓

**Risk notes (from spec, accepted v1):** strip bounds use a fixed `BAND_STRIP_FRAC` (0.12) rather than hrule-derived — simpler and robust; the non-white guard prevents blank anchors. Header-text-over-band alignment is approximate (reflow header paragraph vs. anchored image) — tune `BAND_STRIP_FRAC`/header distance in dogfood if needed. Representative page = index 1; documents whose page-1 chrome differs from later pages get page-1's band (accepted v1).
