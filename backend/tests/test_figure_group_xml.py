from lxml import etree
from PIL import Image, ImageDraw

from models import FigureBlock, FigureOverlay
from pipeline.figure_overlay import bundled_cjk_font, fit_text, resolve_font
from renderers.figure_group import build_figure_group_xml

NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wps": "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "wpg": "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

# Private-use-area bullet glyphs some PDF font subsets emit; the renderer must map
# both to a plain bullet before measuring/emitting (see figure_group._BULLET_MAP).
_PUA_A = ""
_PUA_B = ""


def _block():
    return FigureBlock(
        block_id="B1", base_image_ref="b.png",
        overlays=[
            FigureOverlay(id="o0", target_text="주요 기업", bbox=(10, 10, 120, 20), font_size=18, align="center"),
            FigureOverlay(id="o1", target_text="수요 측면", bbox=(2, 40, 16, 80), font_size=16, rotation=90),
        ],
    )


def _parse(block):
    xml = build_figure_group_xml(block, "rId5", emu_w=914400, emu_h=685800, px_w=200, px_h=150)
    return etree.fromstring(xml.encode("utf-8")), xml


def test_group_names_key_by_block_and_overlay_index():
    root, _ = _parse(_block())
    assert root.find(".//wp:docPr", NS).get("name") == "figB1"
    shapes = root.findall(".//wps:wsp", NS)
    assert [s.find(".//wps:cNvPr", NS).get("name") for s in shapes] == ["ov0", "ov1"]


def test_base_picture_uses_rid_and_full_extent():
    root, _ = _parse(_block())
    blip = root.find(".//pic:blipFill/a:blip", NS)
    assert blip.get("{%s}embed" % NS["r"]) == "rId5"
    ext = root.find(".//pic:spPr/a:xfrm/a:ext", NS)
    assert ext.get("cx") == "914400" and ext.get("cy") == "685800"


def test_exact_line_rule_and_no_wrap():
    root, _ = _parse(_block())
    sp = root.find(".//wps:wsp//w:p/w:pPr/w:spacing", NS)
    assert sp.get("{%s}lineRule" % NS["w"]) == "exact"
    body = root.find(".//wps:wsp/wps:bodyPr", NS)
    assert body.get("wrap") == "none" and body.get("anchor") == "ctr"

    # Pin w:line (twips) for the first, non-rotated overlay to the exact formula
    # figure_group._textbox_xml uses: max(20, round((fitted_px + 2) * 72/150 * 20)),
    # where fitted_px comes from the same fit_text() call the builder makes.
    font_path = bundled_cjk_font() or resolve_font("ko")
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))
    font, _lines = fit_text("주요 기업", font_path, 18, 120, 20, draw)
    fitted_px = font.size
    expected_line_twips = max(20, round((fitted_px + 2) * 72 / 150 * 20))
    line_val = int(sp.get("{%s}line" % NS["w"]))
    assert line_val == expected_line_twips
    assert line_val >= 20


def test_declared_size_is_floor_quantized():
    # font_size 18px @150dpi -> 18*0.48=8.64pt -> floor half-points = floor(17.28)=17
    root, _ = _parse(_block())
    sz = root.find(".//wps:wsp[1]//w:rPr/w:sz", NS)
    assert int(sz.get("{%s}val" % NS["w"])) <= round(18 * 72 / 150 * 2)  # never exceeds rounded


def test_rotated_shape_swaps_footprint_and_sets_rot():
    root, _ = _parse(_block())
    xfrm = root.findall(".//wps:wsp", NS)[1].find(".//a:xfrm", NS)
    assert xfrm.get("rot") == str(((-90) % 360) * 60000)  # 270*60000
    # footprint swapped: bbox (2,40,16,80) -> ext (h,w)=(80,16) scaled by 914400/200=4572
    ext = xfrm.find("a:ext", NS)
    assert int(ext.get("cx")) == int(80 * 4572) and int(ext.get("cy")) == int(16 * 4572)


def test_bullet_pua_mapped():
    b = FigureBlock(
        block_id="B", base_image_ref="b.png",
        overlays=[FigureOverlay(id="o", target_text=_PUA_A + " item " + _PUA_B,
                                 bbox=(0, 0, 50, 20), font_size=14)],
    )
    xml = build_figure_group_xml(b, "rId1", 914400, 685800, 200, 150)
    assert _PUA_A not in xml
    assert _PUA_B not in xml
    assert "•" in xml  # bullet char present in place of the PUA glyphs


def test_empty_overlay_is_skipped_but_indices_stable():
    b = FigureBlock(
        block_id="B2", base_image_ref="b.png",
        overlays=[
            FigureOverlay(id="o0", target_text="   ", bbox=(0, 0, 50, 20), font_size=14),
            FigureOverlay(id="o1", target_text="second", bbox=(0, 30, 50, 20), font_size=14),
        ],
    )
    root, _ = _parse(b)
    shapes = root.findall(".//wps:wsp", NS)
    assert [s.find(".//wps:cNvPr", NS).get("name") for s in shapes] == ["ov1"]
