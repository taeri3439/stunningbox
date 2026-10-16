"""In-image text translation — vision OCR overlay source."""
import os

import fitz
from PIL import Image

from pipeline import extractor
from pipeline import figure_ocr
from pipeline.figure_ocr import OcrRegion
from pipeline.reconciler import reconcile


def test_get_ocr_returns_mock_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(figure_ocr.get_ocr(), figure_ocr.MockOcr)


def test_mock_ocr_returns_injected_regions():
    regions = [OcrRegion(text="North America", bbox=(10.0, 8.0, 90.0, 16.0), kind="translate"),
               OcrRegion(text="www.example.com", bbox=(10.0, 60.0, 120.0, 12.0), kind="keep")]
    ocr = figure_ocr.MockOcr(regions)
    assert ocr.ocr_classify("anything.png") == regions


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
    # translate box: original white text erased -> ~background colour
    assert _close(res.getpixel((60, 28)), bg, tol=40)
    # keep box: untouched -> still white text present
    assert res.getpixel((60, 86)) == (255, 255, 255)


def _close(a, b, tol):
    return all(abs(a[i] - b[i]) <= tol for i in range(3))


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
    page_blocks = [[{"type": "figure", "figure_bbox": [40, 40, 260, 200],
                     "source_span_ids": []}]]  # has_text absent → OCR must NOT run

    class _Boom:
        def ocr_classify(self, _):
            raise AssertionError("OCR must not run on an unflagged figure")

    doc, _ = reconcile(raw, page_blocks, pdf, ad, source_lang="en", ocr=_Boom())
    fig = [b for b in doc.blocks if b.type == "figure"][0]
    assert fig.overlays == [] and fig.base_image_ref == ""


def test_analyzer_schema_has_text_flag():
    from pipeline import analyzer

    schema = analyzer._tool_schema()
    block_props = schema["properties"]["blocks"]["items"]["properties"]
    assert "has_text" in block_props
    assert block_props["has_text"]["type"] == "boolean"


def test_fill_mask_multiple_boxes_use_local_background(tmp_path):
    # left half red, right half blue; a white "text" box well inside each half.
    img = Image.new("RGB", (200, 80), (0, 0, 0))
    for x in range(0, 100):
        for y in range(80):
            img.putpixel((x, y), (200, 30, 30))
    for x in range(100, 200):
        for y in range(80):
            img.putpixel((x, y), (30, 30, 200))
    for x in range(20, 70):
        for y in range(30, 46):
            img.putpixel((x, y), (255, 255, 255))
    for x in range(130, 180):
        for y in range(30, 46):
            img.putpixel((x, y), (255, 255, 255))
    src = str(tmp_path / "halves.png")
    img.save(src)
    regions = [OcrRegion(text="a", bbox=(20.0, 30.0, 50.0, 16.0), kind="translate"),
               OcrRegion(text="b", bbox=(130.0, 30.0, 50.0, 16.0), kind="translate")]
    out = str(tmp_path / "base.png")
    figure_ocr.fill_mask(src, regions, out)
    res = Image.open(out).convert("RGB")
    assert _close(res.getpixel((45, 38)), (200, 30, 30), tol=30)   # left box → its local red bg
    assert _close(res.getpixel((155, 38)), (30, 30, 200), tol=30)  # right box → its local blue bg


def test_analyzer_has_text_positioned_after_figure_bbox():
    from pipeline import analyzer

    block_props = list(analyzer._tool_schema()["properties"]["blocks"]["items"]["properties"])
    assert block_props.index("has_text") == block_props.index("figure_bbox") + 1


def test_reconcile_ocr_failure_falls_back_to_opaque(tmp_path):
    pdf = _figure_pdf(tmp_path)
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(pdf, ad)
    page_blocks = [[{"type": "figure", "figure_bbox": [40, 40, 260, 200],
                     "has_text": True, "source_span_ids": []}]]

    class _Fail:
        def ocr_classify(self, _):
            raise RuntimeError("vision down")

    doc, report = reconcile(raw, page_blocks, pdf, ad, source_lang="en", ocr=_Fail())
    fig = [b for b in doc.blocks if b.type == "figure"][0]
    assert fig.overlays == [] and fig.base_image_ref == ""   # graceful fallback to opaque
    assert any("OCR failed" in w for w in report.warnings)   # failure surfaced, not silent


def test_fill_mask_skips_offimage_region(tmp_path):
    img = Image.new("RGB", (200, 120), (10, 40, 80))
    src = str(tmp_path / "f.png"); img.save(src)
    regions = [OcrRegion(text="ok", bbox=(20.0, 20.0, 60.0, 16.0), kind="translate"),
               OcrRegion(text="offscreen", bbox=(99999.0, 99999.0, 50.0, 16.0), kind="translate")]
    out = str(tmp_path / "base.png")
    figure_ocr.fill_mask(src, regions, out)   # must NOT raise
    assert os.path.exists(out)


def test_reconcile_one_bad_ocr_box_does_not_drop_all_overlays(tmp_path):
    pdf = _figure_pdf(tmp_path)
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(pdf, ad)
    page_blocks = [[{"type": "figure", "figure_bbox": [40, 40, 260, 200],
                     "has_text": True, "source_span_ids": []}]]
    ocr = figure_ocr.MockOcr([
        OcrRegion(text="Good Label", bbox=(20.0, 20.0, 90.0, 18.0), kind="translate"),
        OcrRegion(text="Hallucinated", bbox=(99999.0, 99999.0, 40.0, 12.0), kind="translate"),
    ])
    doc, _ = reconcile(raw, page_blocks, pdf, ad, source_lang="en", ocr=ocr)
    fig = [b for b in doc.blocks if b.type == "figure"][0]
    assert fig.base_image_ref and (tmp_path / "assets" / fig.base_image_ref).exists()
    assert "Good Label" in [o.source_text for o in fig.overlays]  # valid overlay survived
