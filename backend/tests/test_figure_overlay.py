"""Phase A — in-figure text overlay translation."""
import os
from models import FigureBlock, FigureOverlay, RawSpan
import fitz
import pytest
from PIL import Image, ImageDraw, ImageFont
from pipeline import extractor
from pipeline import figure_overlay


def test_figure_block_overlay_defaults():
    fb = FigureBlock(image_ref="f.png")
    assert fb.overlays == []          # opaque figure by default
    assert fb.base_image_ref == ""
    ov = FigureOverlay(id="x")
    assert ov.source_text == "" and ov.target_text == ""
    assert ov.font_size == 0.0 and ov.rotation == 0 and ov.align == "left"
    assert ov.color is None


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


def test_redacted_clip_rejects_degenerate(tmp_path):
    doc = fitz.open()
    doc.new_page(width=300, height=300)
    p = str(tmp_path / "z.pdf")
    doc.save(p); doc.close()
    with pytest.raises(ValueError):
        figure_overlay.render_redacted_clip(
            p, 0, [400, 400, 500, 500], [], str(tmp_path / "a"), "z.png")  # bbox off-page


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
    from PIL import ImageChops
    base_img = Image.open(bp).convert("RGB")
    out_img = Image.open(out).convert("RGB")
    assert ImageChops.difference(base_img, out_img).getbbox() is not None  # text was drawn


def test_composite_inpaints_bbox_toward_border_colour(tmp_path):
    # Workstream C, half 2: before drawing an overlay's translated text, the compositor now
    # inpaints the overlay's OWN bbox with the median colour of its border pixels — defence
    # in depth against a baked label the upstream erasure step only partially cleared (the
    # symptom that motivated this: 접합부/"Joint" doubling near the watergun nozzle figure).
    # Synthetic base: a dark line (a diagram border, well outside the box — must not drag the
    # inpaint fill toward it) + a leftover dark "label" patch under the overlay's own bbox.
    base = Image.new("RGB", (200, 120), (255, 255, 255))
    draw = ImageDraw.Draw(base)
    draw.line([(0, 10), (200, 10)], fill=(0, 0, 0), width=1)     # dark rule, far from the box
    draw.rectangle([40, 40, 160, 90], fill=(90, 90, 90))         # leftover unerased "label"
    bp = str(tmp_path / "base.png")
    base.save(bp)

    ov = FigureOverlay(id="ocr_0", source_text="접합부", target_text="Joint",
                       bbox=(40.0, 40.0, 120.0, 50.0), font_size=16.0, color="000000")
    out = str(tmp_path / "out.png")
    figure_overlay.composite(bp, [ov], out, target_lang="en")

    base_img = Image.open(bp).convert("RGB")
    out_img = Image.open(out).convert("RGB")
    from PIL import ImageChops
    region = (40, 40, 160, 90)
    assert ImageChops.difference(base_img.crop(region), out_img.crop(region)).getbbox() is not None
    # a corner of the box far from the drawn glyphs approximates the surrounding WHITE
    # background (the border colour), not the original dark leftover patch.
    corner = out_img.getpixel((155, 85))
    assert sum(corner) / 3 > 200, f"expected the inpainted corner near white, got {corner}"


def test_composite_skips_inpaint_for_zero_area_bbox(tmp_path):
    # Risk containment: a missing/zero-area bbox is never guessed at — inpainting is skipped
    # (the text still draws, using the default top-left anchor).
    base = Image.new("RGB", (100, 60), (255, 255, 255))
    bp = str(tmp_path / "base.png")
    base.save(bp)
    ov = FigureOverlay(id="z", source_text="x", target_text="y",
                       bbox=(0.0, 0.0, 0.0, 0.0), font_size=12.0)
    out = str(tmp_path / "out.png")
    figure_overlay.composite(bp, [ov], out, target_lang="en")  # must not raise
    assert os.path.exists(out)


def test_composite_handles_multiline_overlay_text(tmp_path):
    base = Image.new("RGB", (220, 120), (255, 255, 255))
    bp = str(tmp_path / "base.png")
    base.save(bp)
    ov = FigureOverlay(id="m", source_text="line one\nline two",
                       target_text="첫째 줄\n둘째 줄", bbox=(8.0, 8.0, 180.0, 40.0),
                       font_size=16.0)
    out = str(tmp_path / "out.png")
    figure_overlay.composite(bp, [ov], out, target_lang="ko")   # must NOT raise
    assert os.path.exists(out)
    from PIL import ImageChops
    base_img = Image.open(bp).convert("RGB")
    out_img = Image.open(out).convert("RGB")
    assert ImageChops.difference(base_img, out_img).getbbox() is not None  # text drawn
