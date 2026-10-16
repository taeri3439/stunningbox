"""HWP -> SemanticDocument (Path C). The native HWP-ingest counterpart to docx_extractor.

pyhwp's `hwp5html` renders a binary .hwp into semantic XHTML + CSS that already carries
everything we need: page geometry, per-paragraph alignment/indent (`parashape-N`),
per-span font/size/bold/colour (`charshape-N`), real HTML tables, and images. We parse
that directly into a SemanticDocument — no vision model, no LibreOffice round-trip — so the
renderer and translator consume HWP exactly as they do PDF/DOCX.

The pure parser `parse_hwp_html(html_dir, ...)` takes an already-produced hwp5html output
dir, which makes it unit-testable with small synthetic fixtures; `extract_hwp` is the thin
wrapper that runs `hwp5html` first.
"""
from __future__ import annotations

import os
import re
import tempfile
from collections import Counter
from typing import Optional

from lxml import etree

from models import (
    FigureBlock,
    HeadingBlock,
    ListItemBlock,
    ParagraphBlock,
    SemanticDocument,
    TableBlock,
    TableCell,
    universal_font,
)
from pipeline import convert

_XH = "{http://www.w3.org/1999/xhtml}"
_MM_PT = 72.0 / 25.4
_A4_RATIO = 297.0 / 210.0  # A4 height/width, to derive page height from the CSS width
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, recover=True)

# A chapter/section line ("제1장 …", "제2절 …") is a heading even when not enlarged.
_CHAPTER_RE = re.compile(r"^제\s*\d+\s*[장절]")
# Leading list markers: 1.  1)  (1)  ① .. ⑳  가. 나) … (restricted to ordinal jamo).
_MARKER_RE = re.compile(
    r"^\s*("
    r"\d{1,2}[.)]"
    r"|\(\d{1,2}\)"
    r"|[①-⑳]"
    r"|[가나다라마바사아자차카타파하][.)]"
    r")\s+(\S.*)$"
)


# --------------------------------------------------------------------------- #
# CSS resolution (hwp5html emits flat `selector { prop: val; … }` rules)
# --------------------------------------------------------------------------- #


def _decl(css: str, selector: str) -> str:
    """The declaration body of the first rule whose selector ends with `selector`
    (handles compound selectors like `.Section-0 .HeaderPageFooter`)."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


def _prop(decl: str, name: str) -> Optional[str]:
    """A property value within a declaration body. The look-behind stops `color` from
    matching inside `background-color` (and similar prefixes)."""
    m = re.search(r"(?<![-\w])" + re.escape(name) + r"\s*:\s*([^;]+)", decl)
    return m.group(1).strip() if m else None


def _pt(value: Optional[str]) -> Optional[float]:
    """A CSS length (pt/mm/px) -> points."""
    if not value:
        return None
    m = re.match(r"(-?[\d.]+)\s*(pt|mm|px)?", value.strip())
    if not m:
        return None
    num = float(m.group(1))
    unit = m.group(2) or "pt"
    if unit == "mm":
        return round(num * _MM_PT, 1)
    if unit == "px":
        return round(num * 0.75, 1)
    return round(num, 1)


def _hex(value: Optional[str]) -> Optional[str]:
    """A CSS colour -> 'RRGGBB' (uppercase). Near-black is treated as ink (None) so body
    text isn't pinned to a literal #000000 the renderer would otherwise honour."""
    if not value:
        return None
    m = re.search(r"#([0-9a-fA-F]{6})", value)
    if not m:
        return None
    h = m.group(1).upper()
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return None if max(r, g, b) <= 0x20 else h


def _fill(value: Optional[str]) -> Optional[str]:
    """A cell background-color -> 'RRGGBB', ignoring white/none."""
    if not value:
        return None
    m = re.search(r"#([0-9a-fA-F]{6})", value)
    if not m:
        return None
    h = m.group(1).upper()
    return None if h == "FFFFFF" else h


def _first_family(fam: Optional[str]) -> Optional[str]:
    if not fam:
        return None
    first = fam.split(",")[0].strip().strip('"').strip("'")
    return first or None


def _charshape(css: str, cid: int) -> dict:
    """Resolve a `charshape-N` to font/size/bold/italic/colour. The East-Asian (`.lang-ko`)
    rule carries the real family + size; the base rule carries weight/style/colour."""
    base = _decl(css, f"span.charshape-{cid}")
    ko = _decl(css, f"span.charshape-{cid}.lang-ko")
    weight = _prop(base, "font-weight") or _prop(ko, "font-weight")
    style = _prop(base, "font-style") or _prop(ko, "font-style")
    return {
        "font": universal_font(_first_family(
            _prop(ko, "font-family") or _prop(base, "font-family"))),
        "size": _pt(_prop(ko, "font-size") or _prop(base, "font-size")),
        "bold": True if weight in ("bold", "700", "800", "900") else None,
        "italic": True if style == "italic" else None,
        "color": _hex(_prop(base, "color") or _prop(ko, "color")),
    }


def _parashape(css: str, pid: int) -> dict:
    """Resolve a `parashape-N` to alignment + left indent (pt)."""
    decl = _decl(css, f"p.parashape-{pid}")
    align = _prop(decl, "text-align")
    return {
        "align": align if align in ("center", "right", "justify") else None,
        "indent": _pt(_prop(decl, "padding-left")) or None,
    }


def _geometry(css: str) -> dict:
    """Page size + text margins from the CSS. Width comes from `.Section-0`; height is the
    A4 ratio of that width (hwp5html only emits width). Margins combine the
    `.HeaderPageFooter` margin with the `.Page` padding."""
    width = _pt(_prop(_decl(css, ".Section-0"), "width"))
    hpf = _decl(css, ".HeaderPageFooter")
    page = _decl(css, ".Page")

    def margin(side: str, pad: str) -> Optional[float]:
        m = _pt(_prop(hpf, f"margin-{side}"))
        p = _pt(_prop(page, f"padding-{pad}")) if pad else None
        if m is None and p is None:
            return None
        return round((m or 0) + (p or 0), 1)

    return {
        "page_width": width,
        "page_height": round(width * _A4_RATIO, 1) if width else None,
        "margin_left": margin("left", ""),
        "margin_right": margin("right", ""),
        "margin_top": margin("top", "top"),
        "margin_bottom": margin("bottom", "bottom"),
    }


# --------------------------------------------------------------------------- #
# Element parsing
# --------------------------------------------------------------------------- #


def _local(el) -> str:
    return etree.QName(el).localname


def _class_id(classstr: Optional[str], kind: str) -> Optional[int]:
    if not classstr:
        return None
    m = re.search(kind + r"-(\d+)", classstr)
    return int(m.group(1)) if m else None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\r", " ")).strip()


def _split_marker(text: str):
    """(marker, rest) if `text` opens with a list marker, else (None, text)."""
    m = _MARKER_RE.match(text)
    return (m.group(1), m.group(2).strip()) if m else (None, text)


def _text_style(p, css: str) -> dict:
    """Dominant char shape (weighted by visible text length) + the paragraph's para shape."""
    counts: Counter = Counter()
    for span in p.iter(_XH + "span"):
        cid = _class_id(span.get("class"), "charshape")
        if cid is not None:
            counts[cid] += max(len((span.text or "").strip()), 1)
    cs = _charshape(css, counts.most_common(1)[0][0]) if counts else {}
    pid = _class_id(p.get("class"), "parashape")
    ps = _parashape(css, pid) if pid is not None else {}
    return {**cs, **ps}


def _is_heading(style: dict, text: str, body_size: Optional[float]) -> bool:
    size = style.get("size")
    if size and body_size and size > body_size + 0.5 and style.get("bold"):
        return True
    return bool(_CHAPTER_RE.match(text)) and style.get("align") == "center"


def _common(style: dict) -> dict:
    """Block-level visual attributes shared by every text block kind."""
    return {
        "font": style.get("font"), "color": style.get("color"),
        "bold": style.get("bold"), "italic": style.get("italic"),
        "align": style.get("align"), "font_size": style.get("size"),
    }


def _cell_align(td, css: str) -> Optional[str]:
    p = td.find(_XH + "p")
    pid = _class_id(p.get("class"), "parashape") if p is not None else None
    return _parashape(css, pid)["align"] if pid is not None else None


def _col_fractions(geom: list, ncols: int) -> Optional[list]:
    """Per-column width fractions from the cells' `width:` styles. hwp5html writes an
    explicit width on every <td>, so the row that resolves all columns at the finest
    granularity (fewest column spans) sets the proportions. Returns None when widths are
    missing or a row is only partially sized — the renderer then uses equal columns."""
    best = None  # (max_span, expanded_widths)
    for row in geom:
        if sum(cs for _, cs in row) != ncols:
            continue  # a row shortened by an inbound rowspan can't define every column
        if any(w is None or w <= 0 for w, _ in row):
            continue
        expanded: list = []
        for w, cs in row:
            expanded += [w / cs] * cs  # a spanned cell splits evenly across its columns
        max_span = max(cs for _, cs in row)
        if best is None or max_span < best[0]:
            best = (max_span, expanded)
        if max_span == 1:
            break  # exact per-column widths — nothing finer to find
    if best is None or sum(best[1]) <= 0:
        return None
    total = sum(best[1])
    return [round(w / total, 4) for w in best[1]]


def _table_block(table, css: str) -> TableBlock:
    """Parse an HTML `<table>` to a TableBlock. HTML already gives explicit colspan/rowspan
    and omits spanned cells in later rows — exactly the convention the renderer expects."""
    rows = []
    geom = []  # parallel to rows: [(width_pt | None, colspan), …] → proportional col widths
    for tr in table.iter(_XH + "tr"):
        cells = []
        widths = []
        for td in tr:
            if _local(td) not in ("td", "th"):
                continue
            colspan = int(td.get("colspan") or 1)
            cells.append(TableCell(
                text=_clean("".join(td.itertext())),
                colspan=colspan,
                rowspan=int(td.get("rowspan") or 1),
                shading=_fill(_prop(_decl(
                    css, f".borderfill-{_class_id(td.get('class'), 'borderfill')}"),
                    "background-color")),
                align=_cell_align(td, css),
            ))
            widths.append((_pt(_prop(td.get("style") or "", "width")), colspan))
        if cells:
            rows.append(cells)
            geom.append(widths)
    ncols = max((sum(max(1, c.colspan) for c in r) for r in rows), default=0)
    return TableBlock(rows=rows, col_widths=_col_fractions(geom, ncols) if ncols else None)


def _style_dim(style: str, name: str) -> Optional[str]:
    m = re.search(re.escape(name) + r"\s*:\s*([\d.]+\s*mm)", style)
    return m.group(1) if m else None


def _figure_block(img, html_dir: str, asset_dir: str, counter: list) -> Optional[FigureBlock]:
    """Copy an `<img>`'s bindata file into asset_dir as PNG; skip non-raster (.OLE) refs."""
    src = img.get("src") or ""
    path = os.path.join(html_dir, *src.split("/"))
    if not src or not os.path.exists(path):
        return None
    style = img.get("style") or ""
    counter[0] += 1
    out = f"hwpimg{counter[0]}.png"
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.convert("RGB").save(os.path.join(asset_dir, out))
    except Exception:  # noqa: BLE001 — embedded OLE / unreadable raster -> skip gracefully
        return None
    return FigureBlock(
        image_ref=out,
        width=_pt(_style_dim(style, "width")),
        height=_pt(_style_dim(style, "height")),
    )


def _page_div(root):
    for div in root.iter(_XH + "div"):
        if (div.get("class") or "").strip() == "Page":
            return div
    return root.find(".//" + _XH + "body")


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def _load_css(html_dir: str, raw_xhtml: str) -> str:
    css = ""
    path = os.path.join(html_dir, "styles.css")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            css = fh.read()
    inline = re.findall(r"<style[^>]*>(.*?)</style>", raw_xhtml, re.S)
    return css + "\n" + "\n".join(inline)


_LABEL_TAG_RE = re.compile(r"\[[^\]]*\]")  # a bare "[별표1]" table tag, not the real title


def _doc_title(blocks: list) -> Optional[str]:
    """A label for the viewer header. The first heading is the real title; heading-less
    specs (별표 tables) use their first substantive text line, skipping a bare '[별표N]'
    tag and stray short markers so the header shows the document name, not '[별표1]'."""
    head = next((b.text.strip() for b in blocks
                 if isinstance(b, HeadingBlock) and b.text.strip()), None)
    if head:
        return head[:120]
    texts = [b.text.strip() for b in blocks
             if isinstance(b, (ParagraphBlock, ListItemBlock)) and b.text.strip()]
    for t in texts:
        if len(t) > 4 and not _LABEL_TAG_RE.fullmatch(t):
            return t[:120]
    return texts[0][:120] if texts else None


def parse_hwp_html(html_dir: str, asset_dir: str, source_lang: str = "ko") -> SemanticDocument:
    """Parse an hwp5html output dir (index.xhtml + styles.css + bindata/) into a
    SemanticDocument. Pure given the dir — no subprocess, no network."""
    os.makedirs(asset_dir, exist_ok=True)
    xhtml = os.path.join(html_dir, "index.xhtml")
    with open(xhtml, encoding="utf-8") as fh:
        raw = fh.read()
    root = etree.fromstring(raw.encode("utf-8"), _PARSER)
    css = _load_css(html_dir, raw)
    page = _page_div(root)

    # Pass 1: classify each top-level <p> as table / figure / text (with resolved style).
    items = []  # ("table", el) | ("figure", img) | ("text", text, style)
    for el in page if page is not None else []:
        name = _local(el)
        # A table arrives two ways in hwp5html: wrapped in <p><span class="TableControl">…
        # OR as a bare <table> child of the page (test-item / 시험순서 tables). Handle both
        # — skipping bare <table> children dropped whole tables (제26·27조 were lost).
        table = el if name == "table" else (
            el.find(".//" + _XH + "table") if name == "p" else None)
        if table is not None:
            # HWP floats figures inside a frame table; hwp5html keeps that as a <table>
            # wrapping an <img>. Surface those images as figures (otherwise they were
            # dropped — _table_block only reads cell text — leaving an empty box). A short
            # "[그림 N]" caption in the frame becomes a centered paragraph; a real table
            # that merely contains an image keeps its grid.
            imgs_in = table.findall(".//" + _XH + "img")
            if imgs_in:
                items += [("figure", img) for img in imgs_in]
                texts = [t for td in table.iter(_XH + "td")
                         if (t := _clean("".join(td.itertext())))]
                if sum(len(t) for t in texts) > 24 or len(texts) > 2:
                    items.append(("table", table))
                else:
                    items += [("text", t, {"align": "center"}) for t in texts]
            else:
                items.append(("table", table))
            continue
        if name != "p":
            continue
        imgs = el.findall(".//" + _XH + "img")
        if imgs:
            items += [("figure", img) for img in imgs]
            continue
        text = _clean("".join(el.itertext()))
        if not text:
            continue
        items.append(("text", text, _text_style(el, css)))

    # Body size = the most common text size; heading sizes rank into heading levels.
    sizes = [it[2]["size"] for it in items if it[0] == "text" and it[2].get("size")]
    body_size = Counter(sizes).most_common(1)[0][0] if sizes else 12.0
    head_sizes = sorted({it[2].get("size") for it in items if it[0] == "text"
                         and _is_heading(it[2], it[1], body_size) and it[2].get("size")},
                        reverse=True)
    level_of = {s: i + 1 for i, s in enumerate(head_sizes)}

    # Pass 2: build blocks in reading order.
    blocks = []
    img_counter = [0]
    for it in items:
        if it[0] == "table":
            blocks.append(_table_block(it[1], css))
        elif it[0] == "figure":
            fig = _figure_block(it[1], html_dir, asset_dir, img_counter)
            if fig is not None:
                blocks.append(fig)
        else:
            _, text, style = it
            common = _common(style)
            if _is_heading(style, text, body_size):
                blocks.append(HeadingBlock(
                    level=level_of.get(style.get("size"), 1), text=text, **common))
                continue
            marker, rest = _split_marker(text)
            if marker is not None:
                blocks.append(ListItemBlock(
                    marker=marker, text=rest, indent=style.get("indent"), **common))
            else:
                blocks.append(ParagraphBlock(
                    text=text, indent=style.get("indent"), **common))

    return SemanticDocument(source_lang=source_lang, title=_doc_title(blocks),
                            blocks=blocks, **_geometry(css))


def extract_hwp(
    hwp_path: str, asset_dir: str, source_lang: str = "ko", work_dir: str | None = None
) -> SemanticDocument:
    """Render `hwp_path` with hwp5html, then parse it into a SemanticDocument.

    `work_dir` is where hwp5html writes its `hwphtml/` output. Pass the document's storage
    dir to PERSIST it (the viewer renders that XHTML as the left-pane "original"); omit it
    for a throwaway temp dir (tests / when no preview is needed)."""
    out = work_dir or tempfile.mkdtemp(prefix="hwpx_")
    html_dir = convert.hwp_to_html(hwp_path, out)
    return parse_hwp_html(html_dir, asset_dir, source_lang=source_lang)
