from models import (
    CaptionBlock,
    DividerBlock,
    FigureBlock,
    HeadingBlock,
    ListItemBlock,
    ParagraphBlock,
    RunningBand,
    SemanticDocument,
    TableBlock,
    TableCell,
)
from renderers.html_renderer import render_html


def test_typography_emitted_as_inline_css():
    doc = SemanticDocument(blocks=[
        ParagraphBlock(text="body", font_size=10.0),  # sets dominant body = 10pt
        ParagraphBlock(text="big centered", font_size=20.0, align="center"),
        ParagraphBlock(text="bold red", bold=True, color="CC0000"),
        ParagraphBlock(text="indented", indent=24.0),
        ParagraphBlock(text="loose", line_spacing=2.0),
    ])
    html = render_html(doc)
    assert "font-size:22pt" in html        # 20pt scaled against 10pt body → 22pt
    assert "text-align:center" in html
    assert "font-weight:700" in html
    assert "color:#CC0000" in html
    assert "margin-left:24pt" in html
    assert "line-height:1.4" in html       # 2.0 capped at MAX_LINE_PITCH


def test_page_sheet_uses_geometry():
    doc = SemanticDocument(page_width=595.0, margin_left=72.0, margin_right=72.0,
                           margin_top=72.0, margin_bottom=72.0,
                           blocks=[ParagraphBlock(text="x")])
    html = render_html(doc)
    assert 'class="page"' in html
    assert "width:595pt" in html
    assert "max-width:100%" in html        # shrinks to fit a narrow pane (no h-scroll)
    assert "box-sizing: border-box" in html  # padding counts inside the width cap (no overflow)
    assert "padding:72pt 72pt 72pt 72pt" in html


def test_page_sheet_fallback_without_geometry():
    html = render_html(SemanticDocument(blocks=[ParagraphBlock(text="x")]))
    assert 'class="page"' in html
    assert "width:595pt" not in html       # no inline width → CSS default (max-width:820px)


def test_list_indent_prefers_measured_then_level():
    doc = SemanticDocument(blocks=[
        ListItemBlock(marker="1)", level=2, text="lvl only"),
        ListItemBlock(marker="2)", level=2, text="measured", indent=30.0),
    ])
    html = render_html(doc)
    assert "margin-left:3em" in html       # level 2 * 1.5em, no measured indent
    assert "margin-left:30pt" in html      # measured indent wins


def test_caption_still_rendered():
    html = render_html(SemanticDocument(blocks=[CaptionBlock(text="Fig 1")]))
    assert 'class="caption"' in html and "Fig 1" in html


def test_table_colgroup_and_cell_styles():
    doc = SemanticDocument(page_width=595.0, margin_left=72.0, margin_right=72.0, blocks=[
        TableBlock(
            col_widths=[0.7, 0.3],
            rows=[[
                TableCell(text="설명입니다", align="center", valign="center",
                          shading="EEEEEE"),
                TableCell(text="값", vertical=True),
            ]],
        ),
    ])
    html = render_html(doc)
    assert 'class="tbl"' in html                          # scroll-container wrapper
    assert "<colgroup>" in html
    assert "table-layout: fixed" in html                  # global CSS → table never overflows
    assert "width:70%" in html and "width:30%" in html   # proportional %, scales with sheet
    assert "text-align:center" in html
    assert "vertical-align:middle" in html        # valign center → middle
    assert "background:#EEEEEE" in html
    assert "writing-mode:vertical-rl" in html


def test_table_cram_drops_proportional_widths():
    # 5% width for a long Latin word would cram → no proportional colgroup (columns fall
    # back to equal under the global table-layout:fixed, so the word wraps instead).
    doc = SemanticDocument(page_width=595.0, margin_left=72.0, margin_right=72.0, blocks=[
        TableBlock(col_widths=[0.05, 0.95],
                   rows=[[TableCell(text="Classification"), TableCell(text="x")]]),
    ])
    html = render_html(doc)
    assert "<colgroup>" not in html        # proportional widths dropped on cram
    assert "width:5%" not in html


def test_figure_width_align_and_float():
    doc = SemanticDocument(page_width=595.0, margin_left=72.0, margin_right=72.0, blocks=[
        FigureBlock(image_ref="fig1.png", width=200.0, align="right"),
        FigureBlock(image_ref="fig2.png", wrap="left"),
    ])
    html = render_html(doc)
    assert "width:200pt" in html
    assert "text-align:right" in html
    assert "float:left" in html


def test_divider_rendered():
    doc = SemanticDocument(blocks=[DividerBlock(thickness=1.5, color="888888")])
    html = render_html(doc)
    assert "<hr" in html
    assert "border-top:1.5pt solid #888888" in html


def test_running_bands_rendered_and_repeated_per_page():
    doc = SemanticDocument(
        running_header=RunningBand(left="KFI", right="p.1", rule=True, rule_color="999999"),
        running_footer=RunningBand(center="footer"),
        blocks=[
            ParagraphBlock(text="page0", page=0),
            ParagraphBlock(text="page1", page=1),  # page change → footer+header repeat
        ],
    )
    html = render_html(doc)
    assert html.count('class="band"') >= 3          # top header + (footer+header at break) + bottom footer
    assert "KFI" in html and "footer" in html
    assert "border-bottom:" in html                  # header rule


def test_color_fields_sanitised_against_attribute_breakout():
    evil = 'red" onmouseover="alert(1)'
    doc = SemanticDocument(
        running_footer=RunningBand(center="f", rule=True, rule_color=evil),
        blocks=[
            ParagraphBlock(text="x", color=evil),
            DividerBlock(color=evil),
            TableBlock(rows=[[TableCell(text="c", shading=evil, color=evil)]]),
        ],
    )
    html = render_html(doc)
    assert "onmouseover" not in html       # injected handler neutralised
    assert evil not in html                # raw malicious string never reaches output
    # a valid hex colour still renders
    ok = render_html(SemanticDocument(blocks=[ParagraphBlock(text="x", color="CC0000")]))
    assert "color:#CC0000" in ok
