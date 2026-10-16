"""Native HWP ingest (Path C): parse hwp5html's XHTML+CSS directly into a
SemanticDocument — no vision, no LibreOffice. Driven by small synthetic
hwp5html-shaped fixtures so the tests need neither the pyhwp binary nor a sample.

The real-sample integration smoke at the bottom is skipped when pyhwp or the
sample .hwp is unavailable."""
import os

import pytest
from PIL import Image

from models import FigureBlock, HeadingBlock, ListItemBlock, ParagraphBlock, TableBlock
from pipeline import hwp_extractor as H


# --- fixture: write an hwp5html-shaped output dir (index.xhtml + styles.css) -------

_INLINE_STYLE = """
.Section-0 { width: 210mm; }
.Section-0 .HeaderPageFooter {
  margin-top: 20mm; margin-right: 20mm; margin-bottom: 15mm; margin-left: 20mm; }
.Section-0 .Page { padding-top: 15mm; padding-bottom: 10mm; }
"""

_STYLES_CSS = """
span.charshape-1 { color: #000000; }
span.charshape-1.lang-ko { font-family: "휴먼명조", serif; font-size: 12pt; }
span.charshape-8 { color: #0000ff; }
span.charshape-8.lang-ko { font-family: "휴먼명조", serif; font-size: 12pt; }
span.charshape-13 { color: #000000; font-weight: bold; }
span.charshape-13.lang-ko { font-family: "휴먼명조", serif; font-size: 13pt; }
span.charshape-15 { color: #0000ff; font-weight: bold; }
span.charshape-15.lang-ko { font-family: "휴먼명조", serif; font-size: 16pt; }
p.parashape-0 { text-align: justify; text-indent: 0pt; }
p.parashape-29 { text-align: center; text-indent: 0pt; }
p.parashape-39 { text-align: justify; }
p.parashape-40 { text-align: center; text-indent: 0pt; }
p.parashape-67 { text-align: justify; text-indent: -12pt; padding-left: 12pt; }
.borderfill-5 { border-top: 1px solid #000000; }
.borderfill-9 { background-color: #D9D9D9; border-top: 1px solid #000000; }
"""

_BODY = """<div class="Section Section-0 Paper"><div class="HeaderPageFooter"><div class="Page">
<p class="Normal parashape-29"><span class="lang-ko charshape-15">제목입니다</span>&#13;</p>
<p class="Normal parashape-40"><span class="lang-ko charshape-13">제1장 총칙</span>&#13;</p>
<p class="Normal parashape-0"><span class="lang-ko charshape-1">제1조(목적) 이 기준은 본문이다.</span>&#13;</p>
<p class="Normal parashape-67"><span class="lang-ko charshape-1">  1. 첫째 항목</span>&#13;</p>
<p class="Normal parashape-0"><span class="lang-ko charshape-8">파란 참조</span>&#13;</p>
<p class="Normal parashape-39"></p>
<p class="Normal parashape-40"><span class="TableControl"><table class="borderfill-5">
<tr><td class="borderfill-9" colspan="2" rowspan="1"><p class="parashape-40">머리</p></td></tr>
<tr><td class="borderfill-5"><p class="parashape-0">a</p></td><td class="borderfill-5"><p class="parashape-0">b</p></td></tr>
</table></span></p>
<p class="Normal parashape-40"><img src="bindata/BIN0001.bmp" style="  width: 50mm;&#10; height: 25mm;&#10;" /></p>
</div></div></div>"""


def _write_html_dir(tmp_path, *, with_image=True) -> str:
    d = tmp_path / "hwphtml"
    d.mkdir()
    xhtml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
        '<link rel="stylesheet" href="styles.css" type="text/css" />'
        f'<style type="text/css">{_INLINE_STYLE}</style>'
        f"</head><body>{_BODY}</body></html>"
    )
    (d / "index.xhtml").write_text(xhtml, encoding="utf-8")
    (d / "styles.css").write_text(_STYLES_CSS, encoding="utf-8")
    if with_image:
        bindata = d / "bindata"
        bindata.mkdir()
        Image.new("RGB", (20, 10), (128, 64, 32)).save(bindata / "BIN0001.bmp")
        # a non-raster embedded object that must be skipped, not crash
        (bindata / "BIN0002.OLE").write_bytes(b"\x00\x01OLEjunk")
    return str(d)


@pytest.fixture
def doc(tmp_path):
    html_dir = _write_html_dir(tmp_path)
    return H.parse_hwp_html(html_dir, str(tmp_path / "assets"), source_lang="ko")


# --- page geometry ----------------------------------------------------------------

def test_page_geometry_is_a4_with_margins(doc):
    assert doc.page_width == pytest.approx(595, abs=2)   # 210mm
    assert doc.page_height == pytest.approx(842, abs=2)  # A4 portrait
    # left/right margins = 20mm ≈ 56.7pt
    assert doc.margin_left == pytest.approx(56.7, abs=1)
    assert doc.margin_right == pytest.approx(56.7, abs=1)


# --- heading classification + char/para shapes ------------------------------------

def test_title_and_chapter_become_ranked_headings(doc):
    heads = [b for b in doc.blocks if isinstance(b, HeadingBlock)]
    assert [h.text for h in heads] == ["제목입니다", "제1장 총칙"]
    title, chapter = heads
    assert title.level == 1 and chapter.level == 2          # 16pt > 13pt
    assert title.bold is True
    assert title.align == "center"
    assert title.font == "Times New Roman"                  # 휴먼명조 = serif
    assert title.color == "0000FF"                          # blue preserved


def test_body_paragraph_is_justified_with_ink_color(doc):
    paras = [b for b in doc.blocks if isinstance(b, ParagraphBlock)]
    body = next(p for p in paras if p.text.startswith("제1조"))
    assert body.align == "justify"
    assert body.bold in (None, False)
    assert body.color is None                               # #000000 → ink, not "000000"


def test_blue_reference_paragraph_keeps_color(doc):
    blue = next(b for b in doc.blocks
                if isinstance(b, ParagraphBlock) and b.text == "파란 참조")
    assert blue.color == "0000FF"


def test_numbered_line_becomes_list_item_with_marker(doc):
    li = next(b for b in doc.blocks if isinstance(b, ListItemBlock))
    assert li.marker == "1."
    assert li.text == "첫째 항목"
    assert li.indent == pytest.approx(12, abs=1)            # padding-left 12pt


def test_empty_paragraph_dropped(doc):
    assert all(getattr(b, "text", "x").strip() for b in doc.blocks
               if isinstance(b, ParagraphBlock))


# --- tables -----------------------------------------------------------------------

def test_table_grid_spans_and_shading(doc):
    tbl = next(b for b in doc.blocks if isinstance(b, TableBlock))
    assert len(tbl.rows) == 2
    top = tbl.rows[0][0]
    assert top.text == "머리" and top.colspan == 2
    assert top.shading == "D9D9D9"                          # background-color
    assert [c.text for c in tbl.rows[1]] == ["a", "b"]
    assert tbl.rows[1][0].shading is None                   # plain cell, no fill


# --- figures ----------------------------------------------------------------------

def test_image_converted_to_png_with_pt_size(doc, tmp_path):
    fig = next(b for b in doc.blocks if isinstance(b, FigureBlock))
    assert fig.image_ref.endswith(".png")
    assert os.path.exists(os.path.join(str(tmp_path / "assets"), fig.image_ref))
    assert fig.width == pytest.approx(141.7, abs=2)         # 50mm
    assert fig.height == pytest.approx(70.9, abs=2)         # 25mm


def test_block_reading_order(doc):
    kinds = [type(b).__name__ for b in doc.blocks]
    # title, chapter, body, list, blue-ref, table, figure (empty p dropped)
    assert kinds == [
        "HeadingBlock", "HeadingBlock", "ParagraphBlock", "ListItemBlock",
        "ParagraphBlock", "TableBlock", "FigureBlock",
    ]


# --- marker splitter (pure) -------------------------------------------------------

@pytest.mark.parametrize("text,marker,rest", [
    ("  1. 첫째 항목", "1.", "첫째 항목"),
    ("① 동그라미", "①", "동그라미"),
    ("가. 한글", "가.", "한글"),
    ("(1) 괄호", "(1)", "괄호"),
    ("3) 닫는괄호", "3)", "닫는괄호"),
])
def test_split_marker_detects(text, marker, rest):
    assert H._split_marker(text) == (marker, rest)


@pytest.mark.parametrize("text", [
    "제1조(목적) 이 기준은",   # article: NOT a list marker
    "그냥 본문 문장입니다",
    "제1장 총칙",
])
def test_split_marker_passes_through(text):
    assert H._split_marker(text) == (None, text)


# --- real-sample integration (skipped without pyhwp + a sample) -------------------

_SAMPLE = os.path.join(
    os.path.dirname(__file__), "..", "storage", "8283bdb81efe", "source.hwp"
)


def _hwp5html_available() -> bool:
    from pipeline import convert
    try:
        convert._hwp5html_bin()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _hwp5html_available(), reason="pyhwp/hwp5html not installed")
@pytest.mark.skipif(not os.path.exists(_SAMPLE), reason="no sample .hwp")
def test_real_sample_extracts_structure(tmp_path):
    doc = H.extract_hwp(_SAMPLE, str(tmp_path / "assets"), source_lang="ko")
    tables = [b for b in doc.blocks if isinstance(b, TableBlock)]
    heads = [b for b in doc.blocks if isinstance(b, HeadingBlock)]
    assert len(tables) >= 5
    assert len(heads) >= 1
    assert doc.page_width == pytest.approx(595, abs=3)      # A4


def test_image_in_frame_table_becomes_figure(tmp_path):
    # HWP floats figures inside a 1-cell frame table; the image must surface as a figure,
    # not be dropped into an empty bordered box (regression: 그림1 lost on a real upload).
    d = tmp_path / "hwphtml"
    d.mkdir()
    (d / "bindata").mkdir()
    Image.new("RGB", (30, 20), (10, 20, 30)).save(d / "bindata" / "BIN0001.bmp")
    body = (
        '<div class="Section Section-0 Paper"><div class="HeaderPageFooter"><div class="Page">'
        '<p class="Normal parashape-40"><span class="TableControl">'
        '<table class="borderfill-5"><tr><td class="borderfill-5"><p class="parashape-40">'
        '<img src="bindata/BIN0001.bmp" style="width: 40mm; height: 27mm;" /></p></td></tr>'
        '</table></span></p>'
        '<p class="Normal parashape-40"><span class="lang-ko charshape-1">[그림 1]</span></p>'
        '</div></div></div>'
    )
    (d / "index.xhtml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head></head>'
        f"<body>{body}</body></html>", encoding="utf-8")
    (d / "styles.css").write_text(
        'p.parashape-40 { text-align: center; }\n'
        'span.charshape-1.lang-ko { font-family: "휴먼명조", serif; font-size: 12pt; }',
        encoding="utf-8")
    doc = H.parse_hwp_html(str(d), str(tmp_path / "assets"), source_lang="ko")
    figs = [b for b in doc.blocks if isinstance(b, FigureBlock)]
    tables = [b for b in doc.blocks if isinstance(b, TableBlock)]
    assert len(figs) == 1 and figs[0].image_ref.endswith(".png")
    assert len(tables) == 0  # the empty wrapper frame table is not emitted
    assert os.path.exists(os.path.join(str(tmp_path / "assets"), figs[0].image_ref))


# --- table column widths ----------------------------------------------------------

def test_table_without_cell_widths_has_no_col_widths(doc):
    # the base-fixture table's <td>s carry no `width:` style → equal columns (None),
    # so the renderer falls back to its default instead of guessing proportions.
    tbl = next(b for b in doc.blocks if isinstance(b, TableBlock))
    assert tbl.col_widths is None


def _table_dir(tmp_path, body_rows: str) -> str:
    d = tmp_path / "hwphtml"
    d.mkdir()
    body = (
        '<div class="Section Section-0 Paper"><div class="HeaderPageFooter"><div class="Page">'
        f'<table class="TableControl borderfill-5">{body_rows}</table>'
        '</div></div></div>'
    )
    (d / "index.xhtml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head></head>'
        f"<body>{body}</body></html>", encoding="utf-8")
    (d / "styles.css").write_text('p.parashape-0 { text-align: justify; }', encoding="utf-8")
    return str(d)


def test_table_col_widths_from_cell_styles(tmp_path):
    # hwp5html writes an explicit `width: NNmm` on every <td>; the first full row's
    # widths become the proportional col_widths (30:20:50 → 0.3/0.2/0.5).
    rows = (
        '<tr>'
        '<td class="borderfill-5" style="  width: 30mm;&#10;  height: 8mm;&#10;"><p class="parashape-0">A</p></td>'
        '<td class="borderfill-5" style="  width: 20mm;&#10;  height: 8mm;&#10;"><p class="parashape-0">B</p></td>'
        '<td class="borderfill-5" style="  width: 50mm;&#10;  height: 8mm;&#10;"><p class="parashape-0">C</p></td>'
        '</tr>'
    )
    doc = H.parse_hwp_html(_table_dir(tmp_path, rows), str(tmp_path / "assets"))
    tbl = next(b for b in doc.blocks if isinstance(b, TableBlock))
    assert tbl.col_widths is not None and len(tbl.col_widths) == 3
    assert tbl.col_widths == pytest.approx([0.3, 0.2, 0.5], abs=0.01)
    assert sum(tbl.col_widths) == pytest.approx(1.0, abs=0.01)


def test_table_col_widths_prefers_finest_row_over_spans(tmp_path):
    # A header row spanning all columns must not flatten the proportions: the body row's
    # single-column widths (60:25:15) win because it resolves columns at finer granularity.
    rows = (
        '<tr><td class="borderfill-5" colspan="3" style="width: 100mm;"><p class="parashape-0">title</p></td></tr>'
        '<tr>'
        '<td class="borderfill-5" style="width: 60mm;"><p class="parashape-0">a</p></td>'
        '<td class="borderfill-5" style="width: 25mm;"><p class="parashape-0">b</p></td>'
        '<td class="borderfill-5" style="width: 15mm;"><p class="parashape-0">c</p></td>'
        '</tr>'
    )
    doc = H.parse_hwp_html(_table_dir(tmp_path, rows), str(tmp_path / "assets"))
    tbl = next(b for b in doc.blocks if isinstance(b, TableBlock))
    assert tbl.col_widths == pytest.approx([0.6, 0.25, 0.15], abs=0.01)


def test_table_col_widths_none_when_partial(tmp_path):
    # If any column lacks a width, don't guess — fall back to equal columns (None).
    rows = (
        '<tr>'
        '<td class="borderfill-5" style="width: 30mm;"><p class="parashape-0">A</p></td>'
        '<td class="borderfill-5"><p class="parashape-0">B</p></td>'
        '</tr>'
    )
    doc = H.parse_hwp_html(_table_dir(tmp_path, rows), str(tmp_path / "assets"))
    tbl = next(b for b in doc.blocks if isinstance(b, TableBlock))
    assert tbl.col_widths is None


# --- document title ---------------------------------------------------------------

def test_title_from_first_heading(doc):
    # the document's title carries the first heading so the viewer header can label it
    # (PDF/Word already set this; HWP previously left it None → blank header).
    assert doc.title == "제목입니다"


def test_title_skips_bare_table_tag(tmp_path):
    # 별표 specs open with a bare "[별표1]" tag on its own line; the title should be the
    # actual document name on the next line, not the tag.
    d = tmp_path / "hwphtml"
    d.mkdir()
    body = (
        '<div class="Section Section-0 Paper"><div class="HeaderPageFooter"><div class="Page">'
        '<p class="Normal parashape-0"><span class="lang-ko charshape-1">[별표1]</span></p>'
        '<p class="Normal parashape-0"><span class="lang-ko charshape-1">데이터 및 코드전송방식 신고부분 프로토콜 정의서</span></p>'
        '</div></div></div>'
    )
    (d / "index.xhtml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head></head>'
        f"<body>{body}</body></html>", encoding="utf-8")
    (d / "styles.css").write_text(
        'span.charshape-1.lang-ko { font-family: "휴먼명조", serif; font-size: 12pt; }',
        encoding="utf-8")
    doc = H.parse_hwp_html(str(d), str(tmp_path / "assets"))
    assert doc.title == "데이터 및 코드전송방식 신고부분 프로토콜 정의서"


def test_title_falls_back_to_first_text_without_heading(tmp_path):
    # table-style specs (e.g. 별표) have no enlarged heading; use the first text line.
    d = tmp_path / "hwphtml"
    d.mkdir()
    body = (
        '<div class="Section Section-0 Paper"><div class="HeaderPageFooter"><div class="Page">'
        '<p class="Normal parashape-0"><span class="lang-ko charshape-1">[별표 1] 프로토콜 정의서</span></p>'
        '<p class="Normal parashape-0"><span class="lang-ko charshape-1">본문 문장</span></p>'
        '</div></div></div>'
    )
    (d / "index.xhtml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head></head>'
        f"<body>{body}</body></html>", encoding="utf-8")
    (d / "styles.css").write_text(
        'span.charshape-1.lang-ko { font-family: "휴먼명조", serif; font-size: 12pt; }',
        encoding="utf-8")
    doc = H.parse_hwp_html(str(d), str(tmp_path / "assets"))
    assert doc.title == "[별표 1] 프로토콜 정의서"


def test_bare_table_child_of_page_is_extracted(tmp_path):
    # hwp5html emits some tables as a bare <table> child of the page (not wrapped in a
    # <p>); the walk must handle those too (regression: 제26조 표 / 제27조 시험순서 lost).
    d = tmp_path / "hwphtml"
    d.mkdir()
    body = (
        '<div class="Section Section-0 Paper"><div class="HeaderPageFooter"><div class="Page">'
        '<p class="Normal parashape-0"><span class="lang-ko charshape-1">앞 문단</span></p>'
        '<table class="TableControl borderfill-5">'
        '<tr><td class="borderfill-5"><p class="parashape-0">항목</p></td>'
        '<td class="borderfill-5"><p class="parashape-0">값</p></td></tr></table>'
        '<p class="Normal parashape-0"><span class="lang-ko charshape-1">뒤 문단</span></p>'
        '</div></div></div>'
    )
    (d / "index.xhtml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml"><head></head>'
        f"<body>{body}</body></html>", encoding="utf-8")
    (d / "styles.css").write_text(
        'p.parashape-0 { text-align: justify; }\n'
        'span.charshape-1.lang-ko { font-family: "휴먼명조", serif; font-size: 12pt; }',
        encoding="utf-8")
    doc = H.parse_hwp_html(str(d), str(tmp_path / "assets"), source_lang="ko")
    tables = [b for b in doc.blocks if isinstance(b, TableBlock)]
    assert len(tables) == 1 and tables[0].rows[0][0].text == "항목"
    # reading order preserved: paragraph, table, paragraph
    assert [type(b).__name__ for b in doc.blocks] == [
        "ParagraphBlock", "TableBlock", "ParagraphBlock"]
