# In-Image Text Translation (vision OCR) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Translate text baked into images (no born-digital spans) by reading it with a vision model, masking the original with a simple background fill, and compositing the translation — feeding the existing `figure_overlay` pipeline as a second overlay source.

**Architecture:** A new `figure_ocr` module runs one vision OCR+classify call per *flagged* opaque figure → `FigureOverlay`s for translate-regions (keep logos/URLs untouched) + a simple-fill base image. The analyzer flags text-bearing figures (`has_text`); the reconciler runs OCR only on those. Everything downstream (translation, per-language composite, viewer) is the existing figure-overlay path, unchanged.

**Tech Stack:** Python 3, Anthropic vision (same client/model as the analyzer), Pillow, PyMuPDF, pytest. No new dependency.

**Spec:** `docs/superpowers/specs/2026-06-19-in-image-text-translation-design.md`

---

## Git / safety constraints (apply to EVERY commit)

- Work on the existing branch `in-image-text-translation-2026-06-19` (off `main`, in-place).
- `git add` ONLY the files listed per task. **NEVER `git add -A`/`.`**
- NEVER stage: `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`, any
  `backend/_*.py`, `test-pdfs/`, or root images.
- Run tests from `backend`: `cd backend && .venv/Scripts/python.exe -m pytest`.

## File structure

- **Create:** `backend/pipeline/figure_ocr.py` — `OcrRegion`, `get_ocr`, `ClaudeOcr`, `MockOcr`,
  `overlays_from_regions`, `fill_mask`. One responsibility: turn an image into translate-overlays
  + a text-erased base.
- **Modify:** `backend/pipeline/analyzer.py` (add `has_text` to the figure block schema + prompt).
- **Modify:** `backend/pipeline/reconciler.py` (run OCR on flagged opaque figures; inject `ocr`).
- **Create tests:** `backend/tests/test_figure_ocr.py`.

---

### Task 1: `figure_ocr` — `OcrRegion`, `MockOcr`, `get_ocr`, `ClaudeOcr.ocr_classify`

**Files:**
- Create: `backend/pipeline/figure_ocr.py`
- Test: `backend/tests/test_figure_ocr.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_figure_ocr.py
"""In-image text translation — vision OCR overlay source."""
from pipeline import figure_ocr
from pipeline.figure_ocr import OcrRegion


def test_get_ocr_returns_mock_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(figure_ocr.get_ocr(), figure_ocr.MockOcr)


def test_mock_ocr_returns_injected_regions():
    regions = [OcrRegion(text="North America", bbox=(10.0, 8.0, 90.0, 16.0), kind="translate"),
               OcrRegion(text="www.example.com", bbox=(10.0, 60.0, 120.0, 12.0), kind="keep")]
    ocr = figure_ocr.MockOcr(regions)
    assert ocr.ocr_classify("anything.png") == regions
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_ocr.py -v`
Expected: FAIL — module `figure_ocr` does not exist.

- [ ] **Step 3: Implement**

Create `backend/pipeline/figure_ocr.py`:

```python
"""Vision OCR for text baked into images (no born-digital span layer).

A second overlay source for the figure-overlay pipeline: read the visible text in an opaque
figure, classify each region translate-vs-keep, and emit FigureOverlays for the translatable
ones plus a simple-fill base image (original text erased). Generative inpainting is out of v1.

Two implementations share one interface (mirrors analyzer/translator):
  * ClaudeOcr — vision (uses ANTHROPIC_API_KEY)
  * MockOcr   — deterministic, injectable regions, for tests / offline
"""
from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field

from models import FigureOverlay
from pipeline.analyzer import ANALYZER_MODEL

# Keep regions that must NOT be translated (left untouched in the image).
KEEP_KINDS = {"keep"}


@dataclass
class OcrRegion:
    text: str
    bbox: tuple[float, float, float, float]  # (x, y, w, h) in the IMAGE's pixels
    kind: str  # "translate" | "keep"


_OCR_SYSTEM = """You read the visible text in an image and return each text region.
For each region give the text verbatim, its pixel bounding box [x, y, w, h] (top-left origin),
and a `kind`:
- "translate": meaningful content text (titles, labels, sentences) that should be translated.
- "keep": text that must stay as-is — brand/logo wordmarks, URLs, email addresses, product
  codes/SKUs, and bare units/symbols.
Return every readable text region. Do not invent text."""


def _ocr_schema() -> dict:
    region = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "bbox": {"type": "array", "items": {"type": "number"},
                     "minItems": 4, "maxItems": 4,
                     "description": "[x, y, w, h] in image pixels, top-left origin"},
            "kind": {"type": "string", "enum": ["translate", "keep"]},
        },
        "required": ["text", "bbox", "kind"],
    }
    return {"type": "object",
            "properties": {"regions": {"type": "array", "items": region}},
            "required": ["regions"]}


class MockOcr:
    """Returns a fixed, injected region list — deterministic, no network."""

    def __init__(self, regions: list[OcrRegion] | None = None):
        self._regions = regions or []

    def ocr_classify(self, image_path: str) -> list[OcrRegion]:
        return list(self._regions)


class ClaudeOcr:
    def __init__(self, client=None, model: str = ANALYZER_MODEL):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def ocr_classify(self, image_path: str) -> list[OcrRegion]:
        with open(image_path, "rb") as fh:
            b64 = base64.standard_b64encode(fh.read()).decode()
        tool = {"name": "emit_text_regions",
                "description": "Return the visible text regions in the image.",
                "input_schema": _ocr_schema()}
        content = [
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": "List every visible text region."},
        ]
        msg = self.client.messages.create(
            model=self.model, max_tokens=2000, system=_OCR_SYSTEM, tools=[tool],
            tool_choice={"type": "tool", "name": "emit_text_regions"},
            messages=[{"role": "user", "content": content}],
        )
        out: list[OcrRegion] = []
        for part in msg.content:
            if part.type == "tool_use" and part.name == "emit_text_regions" \
                    and isinstance(part.input, dict):
                for r in part.input.get("regions", []):
                    bb = r.get("bbox")
                    if isinstance(r.get("text"), str) and isinstance(bb, list) and len(bb) == 4:
                        out.append(OcrRegion(text=r["text"], bbox=tuple(float(v) for v in bb),
                                             kind=r.get("kind", "translate")))
        return out


def get_ocr():
    """ClaudeOcr when a key is set, else MockOcr (offline → no in-image translation)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ClaudeOcr()
    return MockOcr()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_ocr.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/figure_ocr.py backend/tests/test_figure_ocr.py
git commit -m "feat(figure_ocr): vision OCR+classify interface (Claude + Mock)"
```

---

### Task 2: `overlays_from_regions` (translate-only → FigureOverlay)

**Files:**
- Modify: `backend/pipeline/figure_ocr.py`
- Test: `backend/tests/test_figure_ocr.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
def test_overlays_from_regions_translate_only():
    regions = [OcrRegion(text="North America", bbox=(10.0, 8.0, 90.0, 16.0), kind="translate"),
               OcrRegion(text="www.example.com", bbox=(10.0, 60.0, 120.0, 12.0), kind="keep")]
    overlays = figure_ocr.overlays_from_regions(regions)
    assert len(overlays) == 1                       # keep region excluded
    ov = overlays[0]
    assert ov.source_text == "North America"
    assert ov.target_text == ""
    assert ov.bbox == (10.0, 8.0, 90.0, 16.0)
    assert ov.font_size > 0                          # estimated from box height
    assert ov.id                                     # unique, non-empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_ocr.py -k overlays_from -v`
Expected: FAIL — `overlays_from_regions` not defined.

- [ ] **Step 3: Implement (append to `figure_ocr.py`)**

```python
# A translatable overlay's font size is estimated from its box height (most of the box is the
# glyph body); shrink slightly so the rendered text doesn't overshoot the original line.
_FONT_FROM_HEIGHT = 0.8


def overlays_from_regions(regions) -> list[FigureOverlay]:
    """Translate-regions → FigureOverlays (figure-image pixel coords). keep-regions are dropped
    (left untouched in the image). bbox/font are approximate (vision OCR)."""
    out: list[FigureOverlay] = []
    for i, r in enumerate(regions):
        if r.kind in KEEP_KINDS:
            continue
        out.append(
            FigureOverlay(
                id=f"ocr_{i}",
                source_text=r.text,
                target_text="",
                bbox=tuple(float(v) for v in r.bbox),
                font_size=max(6.0, r.bbox[3] * _FONT_FROM_HEIGHT),
                color=None,   # ink default (composite falls back to ink)
                align="left",
                rotation=0,
            )
        )
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_ocr.py -k overlays_from -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/figure_ocr.py backend/tests/test_figure_ocr.py
git commit -m "feat(figure_ocr): overlays from translate regions (keep excluded)"
```

---

### Task 3: `fill_mask` (simple background-fill erase)

**Files:**
- Modify: `backend/pipeline/figure_ocr.py`
- Test: `backend/tests/test_figure_ocr.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
import os
from PIL import Image


def test_fill_mask_erases_translate_keeps_others(tmp_path):
    bg = (10, 40, 80)
    img = Image.new("RGB", (200, 120), bg)
    # simulate "text" pixels (white) in two boxes: one to translate, one to keep
    for x in range(20, 100):
        for y in range(20, 36):
            img.putpixel((x, y), (255, 255, 255))   # translate box ~ (20,20,80,16)
    for x in range(20, 120):
        for y in range(80, 92):
            img.putpixel((x, y), (255, 255, 255))   # keep box ~ (20,80,100,12)
    src = str(tmp_path / "fig.png")
    img.save(src)
    regions = [OcrRegion(text="label", bbox=(20.0, 20.0, 80.0, 16.0), kind="translate"),
               OcrRegion(text="www.x.com", bbox=(20.0, 80.0, 100.0, 12.0), kind="keep")]
    out = str(tmp_path / "base.png")
    figure_ocr.fill_mask(src, regions, out)

    res = Image.open(out).convert("RGB")
    # translate box: original white text erased → ~background colour
    assert _close(res.getpixel((60, 28)), bg, tol=40)
    # keep box: untouched → still white text present
    assert res.getpixel((60, 86)) == (255, 255, 255)


def _close(a, b, tol):
    return all(abs(a[i] - b[i]) <= tol for i in range(3))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_ocr.py -k fill_mask -v`
Expected: FAIL — `fill_mask` not defined.

- [ ] **Step 3: Implement (append to `figure_ocr.py`)**

Add `from PIL import Image, ImageDraw` to the top imports, then append:

```python
def _sample_bg(img, x0: int, y0: int, x1: int, y1: int):
    """Average colour of a thin ring just outside the box — the local background to fill with."""
    w, h = img.size
    px = img.load()
    pts = []
    for x in range(max(0, x0 - 3), min(w, x1 + 3), 2):
        if y0 - 3 >= 0:
            pts.append(px[x, y0 - 3])
        if y1 + 3 < h:
            pts.append(px[x, y1 + 3])
    for y in range(max(0, y0 - 3), min(h, y1 + 3), 2):
        if x0 - 3 >= 0:
            pts.append(px[x0 - 3, y])
        if x1 + 3 < w:
            pts.append(px[x1 + 3, y])
    if not pts:
        return (255, 255, 255)
    n = len(pts)
    return tuple(sum(p[i] for p in pts) // n for i in range(3))


def fill_mask(image_path: str, regions, out_path: str) -> None:
    """Erase each TRANSLATE region by filling its (padded) box with the sampled local background;
    keep-regions are left untouched. Writes the base image to out_path. (v1: flat-fill only.)"""
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    w, h = img.size
    pad = 2
    for r in regions:
        if r.kind in KEEP_KINDS:
            continue
        x, y, bw, bh = r.bbox
        x0, y0, x1, y1 = int(x), int(y), int(x + bw), int(y + bh)
        bg = _sample_bg(img, x0, y0, x1, y1)
        draw.rectangle(
            [max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)],
            fill=bg,
        )
    img.save(out_path)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_ocr.py -v`
Expected: PASS (all). If the translate-box assertion is borderline, the ring sample should still
average near `bg` (the box is surrounded by `bg`); do not loosen the keep-box assertion.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/figure_ocr.py backend/tests/test_figure_ocr.py
git commit -m "feat(figure_ocr): simple background-fill masking of translate regions"
```

---

### Task 4: Analyzer `has_text` flag on figure blocks

**Files:**
- Modify: `backend/pipeline/analyzer.py` (`_tool_schema`, `_SYSTEM`)
- Test: `backend/tests/test_figure_ocr.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
from pipeline import analyzer


def test_analyzer_schema_has_text_flag():
    schema = analyzer._tool_schema()
    block_props = schema["properties"]["blocks"]["items"]["properties"]
    assert "has_text" in block_props
    assert block_props["has_text"]["type"] == "boolean"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_ocr.py -k has_text_flag -v`
Expected: FAIL — `has_text` not in the block schema.

- [ ] **Step 3: Implement**

In `backend/pipeline/analyzer.py`, add `has_text` to the `block` properties in `_tool_schema()`
(after `figure_bbox`, before `source_span_ids`):

```python
            "has_text": {
                "type": "boolean",
                "description": "for a figure block: true if the figure contains readable text "
                               "worth translating (labels/sentences), false for logos/photos/"
                               "pure graphics",
            },
```

And add a rule to `_SYSTEM` (append to the figure bullet, after the existing figure rule):

```python
- For a "figure" block, set `has_text` true if the figure/diagram contains readable text worth
  translating (chart labels, callouts, a screenshot's words); false for a logo, photo, or pure
  graphic with no translatable text.
```

(`HeuristicAnalyzer` emits no figure blocks, so it needs no change — `has_text` simply stays
absent there.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_ocr.py -k has_text_flag -v`
Expected: PASS.

- [ ] **Step 5: Regression**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_analyzer.py -v`
Expected: PASS (adding an optional schema property doesn't change existing analyzer behavior).

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/analyzer.py backend/tests/test_figure_ocr.py
git commit -m "feat(analyzer): has_text flag on figure blocks (gates in-image OCR)"
```

---

### Task 5: Reconciler — run OCR on flagged opaque figures

**Files:**
- Modify: `backend/pipeline/reconciler.py`
- Test: `backend/tests/test_figure_ocr.py` (append)

Context: `reconcile(raw, page_blocks, pdf_path, asset_dir, source_lang="ko")`'s figure branch
(around line 108) clips the figure (`image_ref`, `geom`), then runs the born-digital overlay
capture (`overlays`/`base_ref` via `figure_overlay`), then builds the `FigureBlock`. `figure_overlay`
is imported. The analyzer block dict is `b`. We add an OCR fallback: when the born-digital path
produced NO overlays AND `b["has_text"]` is true AND we have a clipped `image_ref`, run the OCR
on the figure image. Inject the OCR via a new `ocr` parameter (default `None` → `figure_ocr.get_ocr()`)
so tests can supply a `MockOcr`.

- [ ] **Step 1: Write the failing test (append)**

```python
import fitz
from pipeline import extractor
from pipeline.reconciler import reconcile


def _figure_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.draw_rect(fitz.Rect(40, 40, 260, 200), fill=(0.9, 0.9, 0.9))  # a graphic, NO text spans
    p = str(tmp_path / "fig.pdf")
    doc.save(p); doc.close()
    return p


def test_reconcile_ocr_on_flagged_opaque_figure(tmp_path):
    pdf = _figure_pdf(tmp_path)
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(pdf, ad)
    page_blocks = [[{"type": "figure", "figure_bbox": [40, 40, 260, 200],
                     "has_text": True, "source_span_ids": []}]]
    ocr = figure_ocr.MockOcr([
        OcrRegion(text="Market Share", bbox=(20.0, 20.0, 100.0, 18.0), kind="translate"),
        OcrRegion(text="www.x.com", bbox=(20.0, 120.0, 90.0, 12.0), kind="keep"),
    ])
    doc, _ = reconcile(raw, page_blocks, pdf, ad, source_lang="en", ocr=ocr)
    fig = [b for b in doc.blocks if b.type == "figure"][0]
    assert fig.base_image_ref and (tmp_path / "assets" / fig.base_image_ref).exists()
    assert [o.source_text for o in fig.overlays] == ["Market Share"]  # translate only


def test_reconcile_no_ocr_when_not_flagged(tmp_path):
    pdf = _figure_pdf(tmp_path)
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(pdf, ad)
    # has_text absent → must NOT call OCR
    page_blocks = [[{"type": "figure", "figure_bbox": [40, 40, 260, 200],
                     "source_span_ids": []}]]

    class _Boom:
        def ocr_classify(self, _):
            raise AssertionError("OCR must not run on an unflagged figure")

    doc, _ = reconcile(raw, page_blocks, pdf, ad, source_lang="en", ocr=_Boom())
    fig = [b for b in doc.blocks if b.type == "figure"][0]
    assert fig.overlays == [] and fig.base_image_ref == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_ocr.py -k reconcile -v`
Expected: FAIL — `reconcile()` has no `ocr` parameter / no OCR path.

- [ ] **Step 3: Implement**

In `backend/pipeline/reconciler.py`:

(a) Add the import near the `from pipeline import figure_overlay` line:
```python
from pipeline import figure_ocr
```

(b) Add an `ocr` parameter to `reconcile` (default None):
```python
def reconcile(
    raw: RawDocument,
    page_blocks: list[list[dict]],
    pdf_path: str,
    asset_dir: str,
    source_lang: str = "ko",
    ocr=None,
) -> tuple[SemanticDocument, ReconcileReport]:
```

(c) In the `if btype == "figure":` branch, AFTER the born-digital overlay-capture block (the
`if image_ref and geom and len(geom) == 4:` block that sets `overlays`/`base_ref`) and BEFORE
`top = geom[1] if ...`, insert the OCR fallback:
```python
                # No born-digital text, but the analyzer says this figure HAS readable text →
                # OCR it: vision reads + classifies, we erase the original (simple fill) and
                # overlay the translation. Keep-regions (logos/URLs) are left in the image.
                if not overlays and image_ref and b.get("has_text"):
                    ocr_engine = ocr if ocr is not None else figure_ocr.get_ocr()
                    try:
                        regions = ocr_engine.ocr_classify(os.path.join(asset_dir, image_ref))
                        ocr_overlays = figure_ocr.overlays_from_regions(regions)
                        if ocr_overlays:
                            base_name = f"fig_p{page_index}_{len(local)}.base.png"
                            figure_ocr.fill_mask(
                                os.path.join(asset_dir, image_ref), regions,
                                os.path.join(asset_dir, base_name))
                            overlays, base_ref = ocr_overlays, base_name
                    except Exception as e:  # noqa: BLE001 — fall back to opaque on any failure
                        report.warnings.append(f"figure OCR failed p{page_index}: {e}")
```

(Confirm `os` is imported in reconciler.py — it is, from the cover work; if not, add `import os`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_ocr.py -v`
Expected: PASS (all). `test_reconcile_no_ocr_when_not_flagged` proves the cost gate (OCR never
invoked without `has_text`).

- [ ] **Step 5: Regression**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay_pipeline.py tests/test_reconciler_figure_recovery.py tests/test_cover_background.py -v`

Wait — `test_cover_background.py` is on a different branch. Use:
`cd backend && .venv/Scripts/python.exe -m pytest tests/test_figure_overlay_pipeline.py tests/test_reconciler_figure_recovery.py tests/test_reconciler_imgtable.py -v`
Expected: PASS (born-digital figures still get their overlays; the OCR path only fires when
born-digital produced none AND `has_text`). If any FAIL, STOP and report DONE_WITH_CONCERNS.

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/reconciler.py backend/tests/test_figure_ocr.py
git commit -m "feat(reconciler): OCR-translate flagged opaque figures"
```

---

### Task 6: Full suite + dogfood (controller verification)

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: all green (the main baseline + the new `test_figure_ocr.py`). Investigate any regression.

- [ ] **Step 2: Dogfood**

With a real `ANTHROPIC_API_KEY`, upload a PDF containing a screenshot/raster figure with baked
text (e.g. the veterinary report's **Knowledge Store screenshot, p17**) translating en→ko. Confirm
the figure's readable text comes out translated in place, while its URL/brand wordmarks stay
original, and the background fill is acceptable for that figure. Note any figure where the analyzer
flagged `has_text` wrongly (logo/photo) or missed a text figure, and any fill artifact on a
non-flat background (the documented v1 limitation).

- [ ] **Step 3: Confirm no stray files staged**

Run `git status --short`; confirm `backend/auth.py`, `backend/documents_store.py`,
`frontend/src/proxy.ts` are still unstaged ` M`, and no scratch/screenshot files are staged.

---

## Self-review (against the spec)

**Spec coverage:**
- Second overlay source feeding the existing composite → Tasks 1-5 (figure_ocr → reconciler → existing `figure_overlay`/`get_translated_model`). ✓
- Vision OCR + classify (translate/keep) → Task 1 (`ClaudeOcr`/`_ocr_schema`/`_OCR_SYSTEM`), Task 2 (translate-only overlays). ✓
- Simple-fill masking, no generative model → Task 3 (`fill_mask`, `_sample_bg`). ✓
- Trigger = analyzer `has_text` flag, opaque figures only, cost gate → Task 4 (flag) + Task 5 (`not overlays and has_text`; `test_reconcile_no_ocr_when_not_flagged`). ✓
- Reuse FigureOverlay / composite / translation / per-language render (no model change) → overlays are `FigureOverlay`s; downstream untouched. ✓
- MockOcr for deterministic offline tests → Task 1. ✓
- Testing (mock OCR, synthetic fixtures) + dogfood (Knowledge Store screenshot) → Tasks 1-6. ✓
- Out of scope (generative inpainting, full scanned pages, HWP/DOCX, the cover) → not touched. ✓

**Placeholder scan:** none — every code step has complete code; risks are real open questions.

**Type consistency:** `OcrRegion(text, bbox=(x,y,w,h), kind)` ↔ `MockOcr.ocr_classify`/`ClaudeOcr.ocr_classify` return `list[OcrRegion]` ↔ `overlays_from_regions(regions) -> list[FigureOverlay]` ↔ `fill_mask(image_path, regions, out_path)` ↔ `reconcile(..., ocr=None)` calling `ocr.ocr_classify(...)`. `KEEP_KINDS` used consistently in `overlays_from_regions` and `fill_mask`. `FigureOverlay(color=None)` matches the model's `Optional[str]` ink default and the composite's `None`-tolerant fill.
```
