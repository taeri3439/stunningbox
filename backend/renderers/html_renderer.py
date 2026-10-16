"""Render the semantic model to HTML for the web compare-viewer.

HTML flow layout is the natural fit for reflow: the skeleton (headings, lists, real
tables, figures, captions) is preserved while text expands freely across the page.
"""
from __future__ import annotations

import re
from html import escape

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
    build_segment_index_map,
)
from renderers._layout import (
    INK_HEX,
    MAX_LINE_PITCH,
    body_line_spacing,
    content_pt,
    dominant_body_pt,
    scaled_pt,
    widths_would_cram,
)


_HEX_RE = re.compile(r"[0-9A-Fa-f]{3,8}")


def _hex(value: "str | None") -> "str | None":
    """A source colour is safe to interpolate into a CSS value only if it is pure hex digits
    (no quotes/semicolons/brackets that could break out of the style attribute). None when
    the value is missing or not hex — callers then fall back to a safe default or omit it."""
    return value if value and _HEX_RE.fullmatch(value) else None


def _mark(text: str, pattern: "re.Pattern | None") -> str:
    """Escape `text`; if a pattern is given, wrap each match in <mark class="gloss">."""
    if not pattern:
        return escape(text)
    out, last = [], 0
    for m in pattern.finditer(text):
        out.append(escape(text[last:m.start()]))
        out.append(f'<mark class="gloss">{escape(m.group(0))}</mark>')
        last = m.end()
    out.append(escape(text[last:]))
    return "".join(out)


def _inline_style(block, body_pt: "float | None", body_ls: "float | None") -> str:
    """Per-block CSS declarations mirroring docx_renderer._apply_style: scaled font size,
    weight, italic/underline, source colour, alignment, measured indent, capped line pitch.
    Font family is intentionally omitted (system stack is safer for CJK in the browser)."""
    d: list[str] = []
    size = scaled_pt(getattr(block, "font_size", None), body_pt)
    if size is not None:
        d.append(f"font-size:{size}pt")
    bold = getattr(block, "bold", None)
    if bold is True:
        d.append("font-weight:700")
    elif bold is False:
        d.append("font-weight:400")
    if getattr(block, "italic", None):
        d.append("font-style:italic")
    if getattr(block, "underline", None):
        d.append("text-decoration:underline")
    color = _hex(getattr(block, "color", None))
    if color:
        d.append(f"color:#{color}")
    align = getattr(block, "align", None)
    if align in ("center", "right", "justify"):
        d.append(f"text-align:{align}")
    indent = getattr(block, "indent", None)
    if indent:
        d.append(f"margin-left:{indent:g}pt")
    ls = getattr(block, "line_spacing", None) or body_ls
    if ls:
        d.append(f"line-height:{round(min(max(ls, 1.15), MAX_LINE_PITCH), 2):g}")
    return ";".join(d)


def _para_body(block, highlight_pattern, seg, bi: int) -> str:
    """Paragraph/Caption body: per-line seg spans (joined by <br>) when .lines, else one .text seg."""
    if block.lines:
        return "<br>".join(
            seg(_mark(line, highlight_pattern), (bi, "line", li))
            for li, line in enumerate(block.lines)
        )
    return seg(_mark(block.text, highlight_pattern), (bi, "text"))


def _li_body(block, highlight_pattern, seg, bi: int) -> str:
    """List item body — same line/text handling as _para_body."""
    return _para_body(block, highlight_pattern, seg, bi)


def _cell_body(cell, highlight_pattern, seg, bi, ci) -> str:
    if seg is None:               # non-wrap callers (defensive)
        seg = lambda inner, key: inner
    if cell.lines:
        return "<br>".join(
            seg(_mark(line, highlight_pattern), (bi, "cellline", ci, li))
            for li, line in enumerate(cell.lines)
        )
    return seg(_mark(cell.text, highlight_pattern), (bi, "cell", ci))


def render_html(doc: SemanticDocument, asset_url_prefix: str = "assets",
                highlight_pattern: "re.Pattern | None" = None,
                anchor_blocks: bool = False, wrap_segments: bool = False) -> str:
    body_pt = dominant_body_pt(doc)
    body_ls = body_line_spacing(doc)
    cpt = content_pt(doc)

    seg_map = build_segment_index_map(doc) if wrap_segments else {}

    def _seg(inner: str, key) -> str:
        """Wrap rendered inner HTML in a clickable segment span (no-op unless wrap_segments)."""
        if not wrap_segments:
            return inner
        idx = seg_map.get(key)
        return inner if idx is None else f'<span class="seg" data-seg="{idx}">{inner}</span>'

    parts: list[str] = []
    if doc.running_header:
        parts.append(_render_band(doc.running_header))
    prev_page: int | None = None
    for i, block in enumerate(doc.blocks):
        page = getattr(block, "page", None)
        if page is not None and prev_page is not None and page != prev_page:
            if doc.running_footer:
                parts.append(_render_band(doc.running_footer, footer=True))
            parts.append('<div class="page-gap" data-page-break="true"></div>')
            if doc.running_header:
                parts.append(_render_band(doc.running_header))
        if page is not None:
            prev_page = page

        _blk_id = getattr(block, "block_id", None)
        anchor = (f' id="blk{_blk_id}"' if _blk_id is not None else "") if anchor_blocks else ""
        sty = _inline_style(block, body_pt, body_ls)
        sa = f' style="{sty}"' if sty else ""
        if isinstance(block, HeadingBlock):
            lvl = min(max(block.level, 1), 6)
            marker = f'<span class="marker">{escape(block.marker)}</span> ' if block.marker else ""
            parts.append(f"<h{lvl}{anchor}{sa}>{marker}{_seg(_mark(block.text, highlight_pattern), (i, 'text'))}</h{lvl}>")
        elif isinstance(block, ListItemBlock):
            ls_sty = sty
            if getattr(block, "indent", None) is None and block.level:
                extra = f"margin-left:{block.level * 1.5:g}em"
                ls_sty = f"{sty};{extra}" if sty else extra
            lsa = f' style="{ls_sty}"' if ls_sty else ""
            parts.append(
                f'<div class="li"{anchor}{lsa}>'
                f'<span class="marker">{escape(block.marker)}</span> '
                f"{_li_body(block, highlight_pattern, _seg, i)}</div>"
            )
        elif isinstance(block, ParagraphBlock):
            parts.append(f"<p{anchor}{sa}>{_para_body(block, highlight_pattern, _seg, i)}</p>")
        elif isinstance(block, TableBlock):
            parts.append(_render_table(block, highlight_pattern, anchor, body_pt, cpt, seg=_seg, bi=i))
        elif isinstance(block, FigureBlock):
            # Emit invisible overlay seg anchors so data-seg indices are present in the
            # DOM even when the figure has no image (e.g. overlays-only or test docs).
            overlay_spans = ""
            if wrap_segments and block.overlays:
                overlay_spans = "".join(
                    _seg("", (i, "overlay", oi))
                    for oi in range(len(block.overlays))
                )
            if block.image_ref:
                fig_sty, img_sty = [], ""
                wrap = getattr(block, "wrap", None)
                if wrap in ("left", "right"):
                    fig_sty.append(f"float:{wrap};max-width:50%;margin:.3em 1em")
                elif block.align in ("center", "right"):
                    fig_sty.append(f"text-align:{block.align}")
                if block.width:
                    img_sty = f' style="width:{min(block.width, cpt):g}pt;height:auto"'
                fa = f' style="{";".join(fig_sty)}"' if fig_sty else ""
                parts.append(
                    f'<figure{anchor}{fa}>'
                    f'<img src="{asset_url_prefix}/{escape(block.image_ref)}" alt="figure"{img_sty}/>'
                    f"{overlay_spans}</figure>"
                )
            elif overlay_spans:
                parts.append(f'<span{anchor}>{overlay_spans}</span>')
        elif isinstance(block, CaptionBlock):
            parts.append(f'<p class="caption"{anchor}{sa}>{_para_body(block, highlight_pattern, _seg, i)}</p>')
        elif isinstance(block, DividerBlock):
            th = block.thickness or 0.75
            col = _hex(block.color) or INK_HEX
            parts.append(
                f'<hr{anchor} style="border:0;border-top:{th:g}pt solid #{col};margin:.5em 0">'
            )

    if doc.running_footer:
        parts.append(_render_band(doc.running_footer, footer=True))

    if doc.page_width:
        # Paper-width sheet, but never wider than the viewport — in a narrow pane it shrinks
        # to fit (text reflows) instead of overflowing into a horizontal scroll.
        pg = (f"width:{doc.page_width:g}pt;max-width:100%;"
              f"padding:{(doc.margin_top or 36):g}pt {(doc.margin_right or 36):g}pt "
              f"{(doc.margin_bottom or 36):g}pt {(doc.margin_left or 36):g}pt")
        page_open = f'<div class="page" style="{pg}">'
    else:
        page_open = '<div class="page">'
    body = "\n".join(parts)
    return f"""<!doctype html>
<html lang="{escape(doc.target_lang or doc.source_lang)}">
<meta charset="utf-8">
<style>
  html {{ background: #eef1f4; }}
  body {{ font-family: system-ui, "Malgun Gothic", sans-serif; line-height: 1.5;
          margin: 0; padding: 1.5rem 0; color: #1a1a1a; }}
  .page {{ background: #fff; max-width: 820px; margin: 0 auto; padding: 2rem 1.25rem;
           box-shadow: 0 1px 4px rgba(0,0,0,.12); overflow-wrap: break-word;
           box-sizing: border-box; }}
  h1,h2,h3,h4,h5,h6 {{ line-height: 1.3; margin: 1.2em 0 .5em; }}
  .marker {{ color: #555; font-weight: 600; }}
  .li {{ margin: .25em 0; }}
  .caption {{ text-align: center; color: #555; font-size: .9em; font-style: italic; }}
  .tbl {{ overflow-x: auto; max-width: 100%; margin: 1em 0; }}
  table {{ border-collapse: collapse; width: 100%; max-width: 100%; table-layout: fixed; }}
  th, td {{ border: 1px solid #999; padding: .35em .55em; text-align: left; vertical-align: top;
            overflow-wrap: anywhere; }}
  th {{ background: #f3f3f3; }}
  figure {{ margin: 1em 0; text-align: center; }}
  figure img {{ max-width: 100%; height: auto; }}
  .band {{ display: flex; justify-content: space-between; gap: 1em; color: #555;
           font-size: 9pt; padding: .25em 0; }}
  .page-gap {{ height: 1.5rem; }}
  mark.gloss {{ background: #fde68a; color: inherit; padding: 0 .05em; border-radius: 2px; }}
  .seg {{ display: inline; }}
  .seg-clickable .seg {{ cursor: pointer; border-radius: 2px; }}
  .seg-clickable .seg:hover {{ background: #eef3ff; }}
  .seg-active {{ background: #dbe7ff; outline: 1.5px solid #2563eb; border-radius: 2px; }}
</style>
<body>
{page_open}
{body}
</div>
</body>
</html>"""


def render_fragment(doc: SemanticDocument, asset_url_prefix: str = "assets") -> str:
    """Just the body markup (no <html>/<style>) for embedding in the SPA viewer."""
    full = render_html(doc, asset_url_prefix)
    return full.split("<body>", 1)[1].rsplit("</body>", 1)[0].strip()


_VALIGN = {"top": "top", "center": "middle", "bottom": "bottom"}


def _cell_style(cell) -> str:
    d: list[str] = []
    align = getattr(cell, "align", None)
    if align in ("center", "right", "justify"):
        d.append(f"text-align:{align}")
    valign = _VALIGN.get(getattr(cell, "valign", None) or "")
    if valign:
        d.append(f"vertical-align:{valign}")
    shading = _hex(getattr(cell, "shading", None))
    if shading:
        d.append(f"background:#{shading}")
    color = _hex(getattr(cell, "color", None))
    if color:
        d.append(f"color:#{color}")
    if getattr(cell, "vertical", False):
        d.append("writing-mode:vertical-rl")
    return ";".join(d)


def _render_band(band: RunningBand, footer: bool = False) -> str:
    """A running header/footer: left | center | right as a flex band, with the optional
    horizontal rule on the inner edge (header → bottom, footer → top)."""
    spans = "".join(
        f"<span>{escape(t or '')}</span>"
        for t in (band.left, band.center, band.right)
    )
    style = ""
    if band.rule:
        edge = "top" if footer else "bottom"
        style = (f' style="border-{edge}:{(band.rule_size or 0.75):g}pt solid '
                 f'#{_hex(band.rule_color) or INK_HEX}"')
    return f'<div class="band"{style}>{spans}</div>'


def _render_table(block: TableBlock, highlight_pattern: "re.Pattern | None" = None,
                  anchor: str = "", body_pt: "float | None" = None,
                  content_pt_val: float = 455.0, seg=None, bi=None) -> str:
    ncols = max((sum(max(1, c.colspan) for c in row) for row in block.rows), default=0)
    colgroup = ""
    fr = block.col_widths
    if (fr and ncols and len(fr) == ncols
            and not widths_would_cram(block.rows, fr, ncols, content_pt_val, body_pt or 11.0)):
        # Percent widths (not pt) so the proportional columns scale with the responsive
        # sheet. Combined with the global `table-layout: fixed`, the table can never exceed
        # the sheet width (content wraps), so there's no horizontal scroll on a narrow pane.
        cols = "".join(f'<col style="width:{round(f * 100, 1):g}%">' for f in fr)
        colgroup = f"<colgroup>{cols}</colgroup>"

    ci = 0
    rows_html = []
    for row in block.rows:
        cells = []
        for cell in row:
            tag = "th" if cell.is_header else "td"
            attrs = ""
            if cell.rowspan > 1:
                attrs += f' rowspan="{cell.rowspan}"'
            if cell.colspan > 1:
                attrs += f' colspan="{cell.colspan}"'
            cs = _cell_style(cell)
            if cs:
                attrs += f' style="{cs}"'
            cells.append(f"<{tag}{attrs}>{_cell_body(cell, highlight_pattern, seg, bi, ci)}</{tag}>")
            ci += 1
        rows_html.append("<tr>" + "".join(cells) + "</tr>")
    caption = (f"<caption>{(seg or (lambda x, k: x))(escape(block.caption), (bi, 'caption'))}</caption>"
               if block.caption else "")
    # Wrap in a scroll container so a genuinely wide table (more columns than the narrow
    # pane can show) scrolls locally instead of forcing the whole page to scroll sideways.
    return (f'<div class="tbl"{anchor}>'
            f"<table>{caption}{colgroup}{''.join(rows_html)}</table></div>")
