# In-Figure Text Overlay Translation — Phase A (backend core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Translate the text baked inside born-digital PDF charts/maps/diagrams and render it back in place, by capturing the in-figure text spans (instead of dropping them), redacting the original text layer, translating, and compositing a per-language PNG with PIL.

**Architecture:** A new `backend/pipeline/figure_overlay.py` owns span→overlay extraction, text-layer redaction (PyMuPDF), and PIL compositing. `models.FigureBlock` gains `overlays` + `base_image_ref`; `iter_segments` visits overlays so the existing translator/glossary/style-RAG path translates them. The reconciler's `figure` branch captures overlays + a redacted base PNG; `get_translated_model` composites the translated text onto the base per language and points `image_ref` at it. Output renderers (`docx_renderer`/`html_renderer`) are untouched — a figure is still one PNG.

**Tech Stack:** Python 3, PyMuPDF (`fitz`) 1.27, Pillow 12 (already installed), Pydantic 2, pytest. No new dependency except a bundled font asset.

**Spec:** `docs/superpowers/specs/2026-06-18-figure-text-overlay-design.md`

**Scope (Phase A):** born-digital PDF `figure` blocks only. NOT in Phase A: the edit loop / viewer wiring (Phase B), image-table figures (stay opaque), OCR, HWP/DOCX figures.

---

## Git / safety constraints (apply to EVERY commit)

- Work on the existing branch `figure-text-overlay-2026-06-17` (already created, in-place).
- `git add` ONLY the specific files listed in each task. **NEVER `git add -A`/`.`**
- NEVER stage these local-only files: `backend/auth.py`, `backend/documents_store.py`,
  `frontend/src/proxy.ts`, the scratch files `backend/_ingest_style_refs.py`,
  `backend/_retry_failed_refs.py`, `backend/_verify_classify.py`, `backend/_render_realapp.py`,
  `test-pdfs/`, or any `*.jpeg`/`*.png` screenshot at repo root.
- Run tests from the `backend` dir with the repo venv: `cd backend && .venv/Scripts/python.exe -m pytest`.

## File structure

- **Create:** `backend/pipeline/figure_overlay.py` — overlay extraction, redaction, PIL composite (one responsibility: everything about turning in-figure text into a translated PNG).
- **Create:** `backend/assets/fonts/NotoSansKR-Regular.ttf` — CJK-capable font for PIL drawing (resolver falls back to a system/DejaVu font so tests run without it).
- **Create tests:** `backend/tests/test_figure_overlay.py` (extraction/redaction/composite/font),
  `backend/tests/test_figure_overlay_segments.py` (iter_segments coverage),
  `backend/tests/test_figure_overlay_pipeline.py` (reconciler wiring + get_translated_model hook).
- **Modify:** `backend/models.py` (`FigureOverlay`, `FigureBlock` fields, `iter_segments`),
  `backend/pipeline/extractor.py` (`RawSpan.rotation` capture),
  `backend/pipeline/reconciler.py` (figure branch wiring),
  `backend/pipeline/process.py` (composite hook in `get_translated_model`).

---

### Task 1: Data model — `FigureOverlay` + `FigureBlock` fields

**Files:**
- Modify: `backend/models.py` (add `FigureOverlay` before `FigureBlock` at line 149; add two fields to `FigureBlock`)
- Test: `backend/tests/test_figure_overlay.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_figure_overlay.py
"""Phase A — in-figure text overlay translation."""
from models import FigureBlock, FigureOverlay


def test_figure_block_overlay_defaults():
    fb = FigureBlock(image_ref="f.png")
    assert fb.overlays == []          # opaque figure by default
    assert fb.base_image_ref == ""

def test_figure_overlay_roundtrip():
    ov = FigureOverlay(
        id="p0b1l0s0", source_text="North America", target_text="",
        bbox=(10.0, 20.0, 80.0, 14.0), font_size=12.0, color="1B1A17",
        align="left", rotation=0,
    )
    fb = FigureBlock(image_ref="f.png", base_image_ref="f.base.png", overlays=[ov])
    loaded = FigureBlock.model_validate(fb.model_dump())
    assert loaded.overlays[0].source_text == "North America"
    assert loaded.overlays[0].bbox == (10.0, 20.0, 80.0, 14.0)
    assert loaded.base_image_ref == "f.base.png"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay.py -v`
Expected: FAIL — `ImportError: cannot import name 'FigureOverlay'`.

- [ ] **Step 3: Implement the model**

In `backend/models.py`, insert `FigureOverlay` immediately before `class FigureBlock` (line 149):

```python
class FigureOverlay(BaseModel):
    """One run of text that sat inside a figure. The composite PNG is drawn from these;
    `source_text` is preserved (for re-translation), only `target_text` is translated."""

    id: str  # the originating RawSpan id (re-translation targeting in Phase B)
    source_text: str = ""
    target_text: str = ""  # filled by translation; the only translated leaf field here
    # Figure-LOCAL pixel box at the render DPI: (x, y, w, h), origin = figure top-left.
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    font_size: float = 0.0  # px at render DPI
    color: str = "1B1A17"  # hex 'RRGGBB'
    align: str = "left"  # "left" | "center" | "right"
    rotation: int = 0  # 0 | 90 | -90
```

Then add two fields to `FigureBlock` (after `image_ref`, line 151):

```python
class FigureBlock(BaseModel):
    type: Literal["figure"] = "figure"
    image_ref: str = ""  # filename in the asset dir; the (translated) PNG the renderer embeds
    base_image_ref: str = ""  # text-redacted PNG; source for the per-language composite
    overlays: list[FigureOverlay] = Field(default_factory=list)  # in-figure text ([] = opaque)
    width: Optional[float] = None
    height: Optional[float] = None
    align: Optional[str] = None
    wrap: Optional[str] = None
    page: Optional[int] = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/tests/test_figure_overlay.py
git commit -m "feat(models): FigureOverlay + FigureBlock overlay/base fields"
```

---

### Task 2: Capture writing direction into `RawSpan.rotation`

Rotated axis titles (e.g. "Market Size (USD Million)") must be redrawn rotated, not horizontal. PyMuPDF exposes a writing-direction unit vector per text line; map it to 0/90/-90.

**Files:**
- Modify: `backend/models.py` (`RawSpan`, add `rotation`), `backend/pipeline/extractor.py`
- Test: `backend/tests/test_figure_overlay.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
import fitz
from pipeline import extractor


def test_rawspan_rotation_captured(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=400, height=400)
    page.insert_text((50, 200), "HORIZONTAL", fontsize=12)
    page.insert_text((200, 200), "VERTICAL", fontsize=12, rotate=90)
    p = str(tmp_path / "rot.pdf")
    doc.save(p); doc.close()

    raw = extractor.extract_document(p, str(tmp_path / "assets"))
    spans = {s.text.strip(): s for s in raw.pages[0].spans}
    assert spans["HORIZONTAL"].rotation == 0
    assert abs(spans["VERTICAL"].rotation) == 90
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay.py::test_rawspan_rotation_captured -v`
Expected: FAIL — `RawSpan` has no `rotation` (AttributeError) or default 0 on the vertical span.

- [ ] **Step 3: Implement**

In `backend/models.py`, add to `RawSpan` (after `color`, line 30):

```python
    color: int = 0
    rotation: int = 0  # 0 | 90 | -90, from the source line's writing direction
```

In `backend/pipeline/extractor.py`, add the helper (near `_rgb_hex`, ~line 96):

```python
def _dir_to_rotation(d) -> int:
    """PyMuPDF line 'dir' unit vector → text rotation in degrees (0 | 90 | -90).
    Horizontal (incl. 180°) → 0; vertical reads-up → 90; reads-down → -90."""
    if not d:
        return 0
    dx, dy = d[0], d[1]
    if abs(dx) >= abs(dy):
        return 0
    return 90 if dy < 0 else -90
```

In `extract_document`, compute the rotation per line and pass it to each span. Change the line loop (lines 38-57) to:

```python
            for lno, line in enumerate(block.get("lines", [])):
                rot = _dir_to_rotation(line.get("dir"))
                for sno, span in enumerate(line.get("spans", [])):
                    text = span.get("text", "")
                    if not text.strip():
                        continue
                    font = span.get("font", "")
                    flags = span.get("flags", 0)
                    bold = bool(flags & _BOLD_FLAG) or "bold" in font.lower()
                    raw_page.spans.append(
                        RawSpan(
                            id=f"p{pno}b{bno}l{lno}s{sno}",
                            page=pno,
                            text=text,
                            bbox=tuple(span["bbox"]),
                            font=font,
                            size=round(span.get("size", 0.0), 2),
                            bold=bold,
                            color=span.get("color", 0),
                            rotation=rot,
                        )
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay.py::test_rawspan_rotation_captured -v`
Expected: PASS. If the vertical span's rotation comes back 0, print `line.get("dir")` for the rotated line and adjust `_dir_to_rotation`'s axis test — keep the test assertion (`abs(...)==90`).

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/pipeline/extractor.py backend/tests/test_figure_overlay.py
git commit -m "feat(extractor): capture per-span writing-direction rotation"
```

---

### Task 3: Redaction primitive — `render_redacted_clip` (de-risks the top spec risk)

Render a figure region to PNG with the original text glyphs removed but the vector artwork kept. This is the spec's flagged risk — verify behavior with a characterization test first.

**Files:**
- Create: `backend/pipeline/figure_overlay.py`
- Test: `backend/tests/test_figure_overlay.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
from PIL import Image
from pipeline import figure_overlay


def _avg_lum(img, box):
    crop = img.crop(box).convert("L")
    px = list(crop.getdata())
    return sum(px) / len(px)


def test_redacted_clip_removes_text_keeps_vector(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    # vector artwork: a black-bordered rectangle (the "chart")
    page.draw_rect(fitz.Rect(40, 40, 260, 120), color=(0, 0, 0), width=2)
    # text inside the region
    page.insert_text((60, 200), "North America", fontsize=14)
    p = str(tmp_path / "fig.pdf")
    doc.save(p); doc.close()

    ad = str(tmp_path / "assets")
    geom = (30, 30, 270, 230)  # exact box we render (no re-trim)
    text_bbox = (60, 188, 200, 204)  # roughly around the inserted text
    name = figure_overlay.render_redacted_clip(p, 0, geom, [text_bbox], ad, "base.png")
    img = Image.open(f"{ad}/{name}")
    scale = figure_overlay._SCALE
    # the rectangle's top border survives (a dark horizontal band near y=40)
    border = (int(45 * scale - 30 * scale), int(38 * scale - 30 * scale),
              int(255 * scale - 30 * scale), int(44 * scale - 30 * scale))
    assert _avg_lum(img, border) < 200, "vector border should remain"
    # where the text was is now light (glyphs removed, no black fill box)
    cleared = (int(60 * scale - 30 * scale), int(186 * scale - 30 * scale),
               int(190 * scale - 30 * scale), int(206 * scale - 30 * scale))
    assert _avg_lum(img, cleared) > 230, "text area should be cleared, not filled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay.py::test_redacted_clip_removes_text_keeps_vector -v`
Expected: FAIL — `figure_overlay` module does not exist.

- [ ] **Step 3: Implement the module + primitive**

Create `backend/pipeline/figure_overlay.py`:

```python
"""In-figure text overlay: capture the text inside a figure, redact it from the
rendered image, and composite the translated text back in place.

Born-digital PDFs only — the in-figure text is real PyMuPDF spans, so we redact the
text layer (keeping vector artwork) instead of OCR/inpainting.
"""
from __future__ import annotations

import os

import fitz  # PyMuPDF

from models import FigureOverlay, RawSpan
from pipeline import extractor

_SCALE = extractor._SCALE  # PDF points -> pixels at RENDER_DPI (shared with the figure clip)


def render_redacted_clip(
    pdf_path: str,
    page_index: int,
    exact_bbox,
    redact_bboxes,
    asset_dir: str,
    name: str,
) -> str:
    """Remove the glyphs in `redact_bboxes` (PDF points), then render EXACTLY `exact_bbox`
    (the figure's tight box — no re-trim, so the PNG origin/size match the overlay coords)
    to a PNG. Vector artwork and images are preserved. Returns the PNG basename.
    """
    os.makedirs(asset_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        for rb in redact_bboxes:
            # fill=False → erase the covered glyphs without painting a fill rectangle.
            page.add_redact_annot(fitz.Rect(rb), fill=False)
        # Keep images and line art; only text is removed.
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        )
        clip = fitz.Rect(exact_bbox) & page.rect
        pix = page.get_pixmap(matrix=fitz.Matrix(_SCALE, _SCALE), clip=clip, alpha=False)
        out = os.path.join(asset_dir, name)
        pix.save(out)
        return os.path.basename(out)
    finally:
        doc.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay.py::test_redacted_clip_removes_text_keeps_vector -v`
Expected: PASS.

If the cleared-area assertion fails (a black/white fill box appeared), the `fill` argument
is the culprit — try `fill=None` instead of `fill=False`. If the border vanished, the
`graphics=` flag is wrong for this PyMuPDF build — confirm `fitz.PDF_REDACT_LINE_ART_NONE`
exists (`python -c "import fitz; print(fitz.PDF_REDACT_LINE_ART_NONE)"`); if not, use the
integer `0`. Adjust the implementation, not the test.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/figure_overlay.py backend/tests/test_figure_overlay.py
git commit -m "feat(figure_overlay): text-layer redaction keeping vector artwork"
```

---

### Task 4: Overlay extraction from spans

**Files:**
- Modify: `backend/pipeline/figure_overlay.py`
- Test: `backend/tests/test_figure_overlay.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
def _span(id, text, bbox, size=10.0, color=0, rotation=0):
    return RawSpan(id=id, page=0, text=text, bbox=bbox, size=size,
                   color=color, rotation=rotation)


def test_spans_in_region_and_overlays():
    geom = (100.0, 100.0, 300.0, 200.0)
    inside = _span("a", "North America", (110, 110, 190, 124), size=10, color=0xFF0000)
    number = _span("b", "41.6%", (110, 130, 150, 144))
    outside = _span("c", "Caption below", (110, 250, 200, 264))
    spans = [inside, number, outside]

    region = figure_overlay.spans_in_region(spans, geom)
    assert {s.id for s in region} == {"a", "b"}  # both inside; outside excluded

    overlays = figure_overlay.overlays_from_spans(region, geom)
    by_id = {o.id: o for o in overlays}
    assert by_id["a"].source_text == "North America"
    assert by_id["a"].color == "FF0000"
    # figure-local pixels: (110-100)*scale, (110-100)*scale, width*scale, height*scale
    s = figure_overlay._SCALE
    assert by_id["a"].bbox == (10 * s, 10 * s, 80 * s, 14 * s)
    assert by_id["a"].font_size == 10 * s

    assert figure_overlay.has_translatable_text(overlays) is True
    assert figure_overlay.has_translatable_text(
        figure_overlay.overlays_from_spans([number], geom)) is False  # numbers only
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay.py -k "spans_in_region" -v`
Expected: FAIL — `spans_in_region`/`overlays_from_spans`/`has_translatable_text` not defined.

- [ ] **Step 3: Implement (append to `figure_overlay.py`)**

```python
def spans_in_region(page_spans, geom) -> list[RawSpan]:
    """Spans whose CENTRE falls inside the figure's tight box `geom` (PDF points)."""
    gx0, gy0, gx1, gy1 = geom
    out = []
    for s in page_spans:
        cx = (s.bbox[0] + s.bbox[2]) / 2
        cy = (s.bbox[1] + s.bbox[3]) / 2
        if gx0 <= cx <= gx1 and gy0 <= cy <= gy1:
            out.append(s)
    return out


def overlays_from_spans(spans, geom, scale: float = _SCALE) -> list[FigureOverlay]:
    """Convert in-figure spans to FigureOverlays in figure-local pixel coords."""
    gx0, gy0 = geom[0], geom[1]
    out = []
    for s in spans:
        out.append(
            FigureOverlay(
                id=s.id,
                source_text=s.text,
                target_text="",
                bbox=(
                    (s.bbox[0] - gx0) * scale,
                    (s.bbox[1] - gy0) * scale,
                    (s.bbox[2] - s.bbox[0]) * scale,
                    (s.bbox[3] - s.bbox[1]) * scale,
                ),
                font_size=s.size * scale,
                color="%06X" % (int(s.color) & 0xFFFFFF),
                align="left",
                rotation=s.rotation,
            )
        )
    return out


def has_translatable_text(overlays) -> bool:
    """True iff some overlay has an alphabetic character — the test that qualifies a figure
    for overlay treatment (a pure number/symbol figure stays opaque)."""
    return any(any(ch.isalpha() for ch in o.source_text) for o in overlays)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay.py -k "spans_in_region" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/figure_overlay.py backend/tests/test_figure_overlay.py
git commit -m "feat(figure_overlay): extract in-figure spans into overlays"
```

---

### Task 5: Font resolver + PIL composite (auto-fit + rotation)

**Files:**
- Modify: `backend/pipeline/figure_overlay.py`
- Create: `backend/assets/fonts/NotoSansKR-Regular.ttf` (artifact step below)
- Test: `backend/tests/test_figure_overlay.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
from PIL import ImageDraw, ImageFont


def test_resolve_font_returns_loadable():
    path = figure_overlay.resolve_font("ko")
    font = ImageFont.truetype(path, 20)  # must not raise
    assert font is not None


def test_fit_text_shrinks_to_width():
    img = Image.new("RGB", (200, 60), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    fp = figure_overlay.resolve_font("en")
    # a long string in a narrow 80px box must end up no wider than the box
    font, lines = figure_overlay.fit_text(
        "International Federation for Animal Health", fp, 24.0, 80.0, 40.0, draw)
    widest = max(draw.textlength(ln, font=font) for ln in lines)
    assert widest <= 80.0
    assert len(lines) <= 2


def test_composite_writes_png(tmp_path):
    base = Image.new("RGB", (200, 120), (255, 255, 255))
    bp = str(tmp_path / "base.png")
    base.save(bp)
    ov = FigureOverlay(id="x", source_text="North America", target_text="북미",
                       bbox=(10.0, 10.0, 120.0, 18.0), font_size=16.0, color="1B1A17")
    out = str(tmp_path / "out.png")
    figure_overlay.composite(bp, [ov], out, target_lang="ko")
    assert os.path.exists(out)
    assert Image.open(out).size == (200, 120)  # base dimensions preserved
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay.py -k "font or fit_text or composite" -v`
Expected: FAIL — `resolve_font`/`fit_text`/`composite` not defined.

- [ ] **Step 3: Implement (append to `figure_overlay.py`)**

Add the import at the top of the module (with the existing imports):

```python
from PIL import Image, ImageDraw, ImageFont
```

Add constants + helpers:

```python
MIN_FONT_PX = 6.0          # never shrink below this
SHRINK_FLOOR = 0.55        # shrink to at most this fraction before wrapping

_FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "fonts")
# Preference order: bundled CJK font (prod parity) → common system CJK fonts →
# DejaVuSans (shipped inside Pillow, always loadable) so tests run with no bundled asset.
_FONT_CANDIDATES = (
    os.path.join(_FONTS_DIR, "NotoSansKR-Regular.ttf"),
    "C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "DejaVuSans.ttf",
)


def resolve_font(target_lang: str = "") -> str:
    """Path/name of a TrueType font for compositing. Prefers a bundled CJK font; falls
    back to a system CJK font, then DejaVuSans (bundled with Pillow)."""
    for c in _FONT_CANDIDATES:
        if os.path.isabs(c):
            if os.path.exists(c):
                return c
        else:
            try:
                ImageFont.truetype(c, 12)  # name resolvable by Pillow?
                return c
            except OSError:
                continue
    return "DejaVuSans.ttf"


def fit_text(text, font_path, size, max_w, max_h, draw):
    """Choose a font size (and ≤2 lines) so `text` fits `max_w`×`max_h`. Returns
    (ImageFont, lines). Shrinks to SHRINK_FLOOR, then wraps on spaces, then shrinks more."""
    size = max(MIN_FONT_PX, float(size))
    floor = max(MIN_FONT_PX, size * SHRINK_FLOOR)

    def font_at(s):
        return ImageFont.truetype(font_path, int(round(s)))

    # 1) shrink (single line) down to the floor
    s = size
    while s > floor:
        if draw.textlength(text, font=font_at(s)) <= max_w:
            return font_at(s), [text]
        s -= 1.0
    # 2) at the floor, try a 2-line wrap
    f = font_at(floor)
    if draw.textlength(text, font=f) <= max_w:
        return f, [text]
    lines = _wrap_two(text, f, max_w, draw)
    # 3) still too wide → keep shrinking the wrapped lines
    s = floor
    while s > MIN_FONT_PX:
        f = font_at(s)
        lines = _wrap_two(text, f, max_w, draw)
        if all(draw.textlength(ln, font=f) <= max_w for ln in lines):
            break
        s -= 1.0
    return font_at(max(s, MIN_FONT_PX)), lines


def _wrap_two(text, font, max_w, draw):
    """Greedy split into at most two lines at the space that best balances widths."""
    words = text.split(" ")
    if len(words) < 2:
        return [text]
    best = None
    for i in range(1, len(words)):
        a = " ".join(words[:i])
        b = " ".join(words[i:])
        widest = max(draw.textlength(a, font=font), draw.textlength(b, font=font))
        if widest <= max_w and (best is None or widest < best[0]):
            best = (widest, [a, b])
    if best:
        return best[1]
    # nothing fits cleanly → split as evenly as possible
    mid = len(words) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def composite(base_path, overlays, out_path, target_lang: str = "") -> None:
    """Draw each overlay's translated text onto a copy of the redacted base PNG."""
    img = Image.open(base_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    font_path = resolve_font(target_lang)
    fill = lambda hexv: tuple(int(hexv[i:i + 2], 16) for i in (0, 2, 4))  # noqa: E731
    for ov in overlays:
        text = (ov.target_text or ov.source_text or "").strip()
        if not text:
            continue
        x, y, w, h = ov.bbox
        color = fill(ov.color if len(ov.color) == 6 else "1B1A17")
        if ov.rotation:
            _draw_rotated(img, text, font_path, ov.font_size, x, y, w, h, color, ov.rotation)
            continue
        font, lines = fit_text(text, font_path, ov.font_size, max(w, 1.0), max(h, 1.0), draw)
        line_h = (font.size + 2)
        total_h = line_h * len(lines)
        cy = y + max(0.0, (h - total_h) / 2)
        for ln in lines:
            lw = draw.textlength(ln, font=font)
            if ov.align == "center":
                lx = x + max(0.0, (w - lw) / 2)
            elif ov.align == "right":
                lx = x + max(0.0, w - lw)
            else:
                lx = x
            draw.text((lx, cy), ln, font=font, fill=color)
            cy += line_h
    img.save(out_path)


def _draw_rotated(img, text, font_path, size, x, y, w, h, color, rotation) -> None:
    """Render rotated text (90/-90) on a transparent sub-image and paste it at (x, y)."""
    font = ImageFont.truetype(font_path, max(int(round(size)), int(MIN_FONT_PX)))
    tmp_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tw = int(tmp_draw.textlength(text, font=font)) + 4
    th = int(font.size + 4)
    txt = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    ImageDraw.Draw(txt).text((2, 2), text, font=font, fill=color + (255,))
    rot = txt.rotate(rotation, expand=True)
    img.paste(rot, (int(x), int(y)), rot)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay.py -k "font or fit_text or composite" -v`
Expected: PASS (3 passed). The composite/fit tests are font-agnostic (assert geometry), so they pass via the DejaVu fallback even before the bundled font is added.

- [ ] **Step 5: Add the bundled font (production parity)**

Obtain `NotoSansKR-Regular.ttf` (Google Fonts, OFL — covers Latin + Hangul) and place it at
`backend/assets/fonts/NotoSansKR-Regular.ttf`. This makes `resolve_font` prefer it in
production so Korean composites render real glyphs (the Linux Railway image may lack a CJK
font). Tests do not depend on it.

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/figure_overlay.py backend/tests/test_figure_overlay.py backend/assets/fonts/NotoSansKR-Regular.ttf
git commit -m "feat(figure_overlay): PIL composite with font auto-fit + rotation"
```

---

### Task 6: `iter_segments` visits figure overlays (translation coverage)

Make overlays translatable through the existing pipeline: the getter returns `source_text`, the setter writes `target_text`.

**Files:**
- Modify: `backend/models.py` (`iter_segments`)
- Test: `backend/tests/test_figure_overlay_segments.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_figure_overlay_segments.py
from models import (FigureBlock, FigureOverlay, ParagraphBlock, SemanticDocument,
                    collect_texts, iter_text_fields)


def _doc_with_overlay():
    fb = FigureBlock(
        image_ref="f.png", base_image_ref="f.base.png",
        overlays=[
            FigureOverlay(id="o1", source_text="North America", font_size=10.0),
            FigureOverlay(id="o2", source_text="Europe", font_size=10.0),
        ],
    )
    return SemanticDocument(source_lang="en", blocks=[fb])


def test_overlays_are_translatable_segments():
    doc = _doc_with_overlay()
    assert collect_texts(doc) == ["North America", "Europe"]  # getter → source_text
    for get, set_ in iter_text_fields(doc):
        set_(get() + " [t]")                                   # setter → target_text
    fb = doc.blocks[0]
    assert fb.overlays[0].target_text == "North America [t]"
    assert fb.overlays[0].source_text == "North America"       # source preserved
    assert fb.overlays[1].target_text == "Europe [t]"


def test_opaque_figure_yields_nothing():
    doc = SemanticDocument(blocks=[FigureBlock(image_ref="f.png"),
                                   ParagraphBlock(text="body")])
    assert collect_texts(doc) == ["body"]  # no overlays → only the paragraph
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay_segments.py -v`
Expected: FAIL — `collect_texts` returns `[]` for the overlay doc (figures not visited).

- [ ] **Step 3: Implement**

In `backend/models.py`, add a `FigureBlock` branch inside the `for bi, block in enumerate(doc.blocks)` loop of `iter_segments` (after the `TableBlock` branch, ~line 307). First ensure `FigureBlock` is referenced (it's defined in this module, so no import needed):

```python
        elif isinstance(block, FigureBlock):
            for ov in block.overlays:
                get, set_ = _overlay_accessor(ov)
                yield get, set_, [ov.id], bi
```

Add the accessor near `_field_accessor` (~line 320):

```python
def _overlay_accessor(ov):
    """(getter, setter) for a figure overlay: translate source_text → target_text, keeping
    source_text intact so the overlay can be re-translated later (Phase B)."""

    def getter() -> str:
        return ov.source_text or ""

    def setter(value: str) -> None:
        ov.target_text = value

    return getter, setter
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay_segments.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Regression — full model/segment suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_iter_segments.py tests/test_segment_bbox.py tests/test_retranslate.py -v`
Expected: PASS (overlay yields are additive; docs without overlays are unchanged).

- [ ] **Step 6: Commit**

```bash
git add backend/models.py backend/tests/test_figure_overlay_segments.py
git commit -m "feat(models): translate figure overlays via iter_segments"
```

---

### Task 7: Wire overlay capture into the reconciler figure branch

When the analyzer emits a `figure` with interior text, build overlays + a redacted base PNG. Opaque figures (no alphabetic text) keep today's behavior.

**Files:**
- Modify: `backend/pipeline/reconciler.py` (the `if btype == "figure":` branch, lines 107-142)
- Test: `backend/tests/test_figure_overlay_pipeline.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_figure_overlay_pipeline.py
import fitz

from models import RawDocument, RawPage, RawSpan
from pipeline import extractor
from pipeline.reconciler import reconcile


def _chart_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.draw_rect(fitz.Rect(40, 40, 260, 160), color=(0, 0, 0), width=2)  # vector art
    page.insert_text((60, 90), "North America", fontsize=12)               # in-figure text
    p = str(tmp_path / "chart.pdf")
    doc.save(p); doc.close()
    return p


def test_reconcile_builds_overlay_figure(tmp_path):
    pdf = _chart_pdf(tmp_path)
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(pdf, ad)
    # one analyzer block: a figure covering the artwork+text region
    page_blocks = [[{"type": "figure", "figure_bbox": [30, 30, 270, 180],
                     "source_span_ids": []}]]
    doc, _report = reconcile(raw, page_blocks, pdf, ad, source_lang="en")

    figs = [b for b in doc.blocks if b.type == "figure"]
    assert len(figs) == 1
    fig = figs[0]
    assert fig.base_image_ref and (tmp_path / "assets" / fig.base_image_ref).exists()
    assert any(o.source_text.strip() == "North America" for o in fig.overlays)


def test_reconcile_opaque_figure_has_no_overlays(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.draw_rect(fitz.Rect(40, 40, 260, 160), fill=(0.2, 0.2, 0.2))  # pure graphic, no text
    p = str(tmp_path / "logo.pdf")
    doc.save(p); doc.close()
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(p, ad)
    page_blocks = [[{"type": "figure", "figure_bbox": [30, 30, 270, 180],
                     "source_span_ids": []}]]
    out, _ = reconcile(raw, page_blocks, p, ad, source_lang="en")
    fig = [b for b in out.blocks if b.type == "figure"][0]
    assert fig.overlays == []
    assert fig.base_image_ref == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay_pipeline.py -k "reconcile" -v`
Expected: FAIL — figure has empty `overlays` / `base_image_ref` (capture not wired).

- [ ] **Step 3: Implement**

In `backend/pipeline/reconciler.py`, add the import near the other pipeline imports at the top:

```python
from pipeline import figure_overlay
```

Then, inside the `if btype == "figure":` branch, after the existing `render_clip` block sets `geom`/`fw`/`fh`/`align`/`wrap` (after line 126, before `top = ...` on line 127), insert overlay capture and build the `FigureBlock` with the new fields. Replace lines 127-140 with:

```python
                base_ref = ""
                overlays = []
                if image_ref and geom and len(geom) == 4:
                    in_fig = figure_overlay.spans_in_region(page_spans, geom)
                    candidates = figure_overlay.overlays_from_spans(in_fig, geom)
                    if figure_overlay.has_translatable_text(candidates):
                        overlays = candidates
                        base_name = f"fig_p{page_index}_{len(local)}.base.png"
                        try:
                            base_ref = figure_overlay.render_redacted_clip(
                                pdf_path, page_index, geom,
                                [s.bbox for s in in_fig], asset_dir, base_name)
                        except Exception as e:  # noqa: BLE001 — fall back to opaque figure
                            report.warnings.append(
                                f"figure redaction failed p{page_index}: {e}")
                            overlays, base_ref = [], ""
                top = geom[1] if geom and len(geom) == 4 else _top_y(ids, by_id)
                local.append(
                    (
                        top,
                        FigureBlock(
                            image_ref=image_ref,
                            base_image_ref=base_ref,
                            overlays=overlays,
                            width=fw,
                            height=fh,
                            align=align,
                            wrap=wrap,
                            page=page_index,
                        ),
                    )
                )
                referenced.update(ids)
                continue
```

(The image-table figure branch at lines 204-234 is intentionally left unchanged — image-tables stay opaque in Phase A.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay_pipeline.py -k "reconcile" -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Regression — reconciler + figure suites**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_reconciler_figure_recovery.py tests/test_reconciler_imgtable.py tests/test_reconciler_grid_recovery.py tests/test_figure_clip.py -v`
Expected: PASS (existing figure behavior preserved).

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/reconciler.py backend/tests/test_figure_overlay_pipeline.py
git commit -m "feat(reconciler): capture in-figure overlays + redacted base PNG"
```

---

### Task 8: Composite per-language PNG in `get_translated_model`

After translation fills `target_text`, draw it onto the base PNG and point `image_ref` at the per-language composite.

**Files:**
- Modify: `backend/pipeline/figure_overlay.py` (add `composite_document`), `backend/pipeline/process.py` (`get_translated_model`)
- Test: `backend/tests/test_figure_overlay_pipeline.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
import os

from PIL import Image

from models import FigureBlock, FigureOverlay, SemanticDocument
from pipeline import figure_overlay


def test_composite_document_sets_per_lang_image_ref(tmp_path):
    ad = str(tmp_path)
    Image.new("RGB", (160, 80), (255, 255, 255)).save(os.path.join(ad, "fig.base.png"))
    fb = FigureBlock(
        image_ref="fig.png", base_image_ref="fig.base.png",
        overlays=[FigureOverlay(id="o1", source_text="North America",
                                target_text="북미", bbox=(8.0, 8.0, 100.0, 16.0),
                                font_size=14.0)],
    )
    doc = SemanticDocument(source_lang="en", target_lang="ko", blocks=[fb])

    figure_overlay.composite_document(doc, ad, "ko")

    assert doc.blocks[0].image_ref == "fig.ko.png"
    assert os.path.exists(os.path.join(ad, "fig.ko.png"))


def test_composite_document_skips_opaque(tmp_path):
    fb = FigureBlock(image_ref="fig.png")  # no overlays / no base
    doc = SemanticDocument(blocks=[fb])
    figure_overlay.composite_document(doc, str(tmp_path), "ko")
    assert doc.blocks[0].image_ref == "fig.png"  # untouched
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay_pipeline.py -k "composite_document" -v`
Expected: FAIL — `composite_document` not defined.

- [ ] **Step 3: Implement `composite_document` (append to `figure_overlay.py`)**

```python
def composite_document(doc, asset_dir: str, target_lang: str) -> None:
    """For each overlay figure, composite the translated text onto its base PNG and point
    `image_ref` at a per-language file (`<stem>.<lang>.png`). Opaque figures are untouched.
    Mutates `doc` in place."""
    from models import FigureBlock as _FigureBlock  # local import avoids a cycle at module load

    lang = (target_lang or "out").replace("/", "_")
    for block in doc.blocks:
        if not isinstance(block, _FigureBlock):
            continue
        if not block.overlays or not block.base_image_ref:
            continue
        base_path = os.path.join(asset_dir, block.base_image_ref)
        if not os.path.exists(base_path):
            continue
        stem = block.base_image_ref[:-9] if block.base_image_ref.endswith(".base.png") \
            else os.path.splitext(block.base_image_ref)[0]
        out_name = f"{stem}.{lang}.png"
        try:
            composite(base_path, block.overlays, os.path.join(asset_dir, out_name),
                      target_lang=target_lang)
            block.image_ref = out_name
        except Exception:  # noqa: BLE001 — keep the source image_ref if compositing fails
            pass
```

- [ ] **Step 4: Wire into `get_translated_model`**

In `backend/pipeline/process.py`, `get_translated_model` (lines 225-230), add the composite call after `translate_document` and before caching. `asset_dir` is already imported (used in `build_source_model`). Add the import `from pipeline import figure_overlay` near the other pipeline imports (top of file, with the line-19 import group). Then change lines 225-230 to:

```python
    translated = translate_document(
        source, target_lang, on_progress=on_progress,
        extra_glossary=load_doc_glossary(storage_dir, doc_id),
        style_context=style_context, tm_map=tm_map,
    )
    figure_overlay.composite_document(
        translated, asset_dir(storage_dir, doc_id), target_lang)
    _write_json(cache, translated.model_dump())
    return translated
```

- [ ] **Step 5: Write the wiring test (append to `test_figure_overlay_pipeline.py`)**

```python
import json

from pipeline import process


def test_get_translated_model_composites_overlay(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # force MockTranslator
    storage = str(tmp_path)
    doc_id = "doc1"
    base = process.doc_dir(storage, doc_id)
    ad = process.asset_dir(storage, doc_id)
    os.makedirs(ad, exist_ok=True)
    Image.new("RGB", (160, 80), (255, 255, 255)).save(os.path.join(ad, "fig.base.png"))

    source = SemanticDocument(
        source_lang="en", blocks=[FigureBlock(
            image_ref="fig.png", base_image_ref="fig.base.png",
            overlays=[FigureOverlay(id="o1", source_text="North America",
                                    bbox=(8.0, 8.0, 100.0, 16.0), font_size=14.0)])])
    with open(os.path.join(base, "source.json"), "w", encoding="utf-8") as fh:
        json.dump(source.model_dump(), fh)

    translated = process.get_translated_model(storage, doc_id, "ko")
    fig = [b for b in translated.blocks if b.type == "figure"][0]
    assert fig.overlays[0].target_text  # translated (MockTranslator), not empty
    assert fig.image_ref == "fig.ko.png"
    assert os.path.exists(os.path.join(ad, "fig.ko.png"))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay_pipeline.py -v`
Expected: PASS (all). If `process.doc_dir`/`asset_dir` are not module-level exports, open `process.py` and use the actual helper names (they are used internally at lines 82/100/203) — adjust the test import accordingly.

- [ ] **Step 7: Commit**

```bash
git add backend/pipeline/figure_overlay.py backend/pipeline/process.py backend/tests/test_figure_overlay_pipeline.py
git commit -m "feat(process): composite translated overlays per language"
```

---

### Task 9: Full suite + dogfood verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all green (the prior baseline was 324 passed / 1 skipped; this adds ~14 passing tests). Investigate any regression before proceeding.

- [ ] **Step 2: Dogfood the real document**

With the bundled font present and the backend running, upload the veterinary market-research
PDF (`hc - veterinary rapid test market ...pdf`) translating en→ko, and open the result.
Confirm in the viewer that:
- the donut/pie chart labels (Tier 1, C-level, region names) render in Korean **in place**;
- the geographic-map callouts (North America, Asia Pacific, the % values) are translated over
  the map;
- the triangle process-flow bullets (BOTTOM-UP / TOP-DOWN APPROACH) are translated;
- vector artwork (donut arcs, map fills, triangle shapes) is intact (no black fill boxes);
- regular body text, the data tables (TABLE 140-143), and the TOC are unchanged from before.

Note any figure that renders poorly (overflow, misplaced rotated label, redaction artifact) for
a follow-up — Phase A accepts minor imperfection over failure.

- [ ] **Step 3: Confirm no stray files are staged**

Run: `git status --short` and confirm `backend/auth.py`, `backend/documents_store.py`,
`frontend/src/proxy.ts` still show as unstaged ` M`, and no scratch/screenshot files are staged.

---

## Self-review (against the spec)

**Spec coverage:**
- Data model (`FigureBlock.base_image_ref`/`overlays`, `FigureOverlay`) → Task 1. ✓
- Redaction keeping vector art → Task 3 (+ characterization test for the flagged risk). ✓
- In-figure span extraction (flip suppression → capture) → Tasks 4, 7. ✓
- Figure selection heuristic (≥1 alphabetic span) → `has_translatable_text`, Tasks 4, 7. ✓
- Translate overlays via `iter_text_fields`/glossary/style → Task 6 (`iter_segments` branch). ✓
- PIL composite with auto-fit + rotation → Task 5; rotation capture → Task 2. ✓
- Per-language composite, renderers untouched → Task 8 (`get_translated_model` hook). ✓
- Font dependency (bundled + fallback) → Task 5. ✓ (Pillow already installed — no req change.)
- Testing strategy (synthetic-PDF fixtures, MockTranslator) → Tasks 1-8; dogfood → Task 9. ✓
- Out of scope (image-tables, OCR, HWP/DOCX, edit loop) → explicitly untouched; edit loop is Phase B. ✓

**Placeholder scan:** none — every code step has complete code; the redaction-flag uncertainty is handled by a characterization test + concrete fallback instructions, not a "TODO".

**Type consistency:** `FigureOverlay(id, source_text, target_text, bbox=(x,y,w,h), font_size, color, align, rotation)` and the helpers `spans_in_region` / `overlays_from_spans` / `has_translatable_text` / `render_redacted_clip(exact_bbox, redact_bboxes)` / `composite(base_path, overlays, out_path, target_lang)` / `composite_document(doc, asset_dir, target_lang)` are used identically across Tasks 3-8. `_SCALE` is sourced from `extractor._SCALE` everywhere.
