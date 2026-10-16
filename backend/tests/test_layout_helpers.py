from models import ParagraphBlock, SemanticDocument, TableCell
from renderers._layout import (
    content_pt,
    dominant_body_pt,
    scaled_pt,
    widths_would_cram,
)


def test_scaled_pt_maps_to_11pt_body():
    assert scaled_pt(10, 10) == 11          # body size → 11pt
    assert scaled_pt(20, 10) == 22          # 2x body → 22pt
    assert scaled_pt(100, 10) == 30         # clamped to 30 max
    assert scaled_pt(1, 10) == 8            # clamped to 8 min
    assert scaled_pt(None, 10) is None      # no size → no scaling
    assert scaled_pt(10, None) is None


def test_content_pt_from_geometry_and_fallback():
    doc = SemanticDocument(page_width=595.0, margin_left=72.0, margin_right=72.0)
    assert content_pt(doc) == 451.0         # 595 - 72 - 72
    assert content_pt(SemanticDocument()) == 455.0  # A4 text-width fallback


def test_dominant_body_pt_is_most_common_paragraph_size():
    doc = SemanticDocument(blocks=[
        ParagraphBlock(text="a", font_size=10.0),
        ParagraphBlock(text="b", font_size=10.0),
        ParagraphBlock(text="c", font_size=18.0),
    ])
    assert dominant_body_pt(doc) == 10.0
    assert dominant_body_pt(SemanticDocument()) is None


def test_widths_would_cram_flags_long_latin_in_narrow_col():
    rows = [[TableCell(text="Classification"), TableCell(text="x")]]
    assert widths_would_cram(rows, [0.1, 0.9], 2, 400.0, 11.0) is True
    assert widths_would_cram(rows, [0.6, 0.4], 2, 400.0, 11.0) is False
    cjk = [[TableCell(text="분류표"), TableCell(text="값")]]
    assert widths_would_cram(cjk, [0.1, 0.9], 2, 400.0, 11.0) is False
