"""Semantic document model — the central contract of the pipeline.

The extractor produces raw geometry (RawSpan/RawImage/RawPage). The analyzer turns
that into a tree of `Block`s (the semantic model). Translation only mutates the
`text` fields of blocks; structure, markers, and image references never change.
Renderers (HTML/docx) consume the semantic model. This separation is what keeps the
logical skeleton intact regardless of how much the translated text expands.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Raw extraction layer (ground truth produced by PyMuPDF, never translated)
# --------------------------------------------------------------------------- #


class RawSpan(BaseModel):
    """A run of text with identical styling at a position on a page."""

    id: str
    page: int
    text: str
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 in PDF points
    font: str = ""
    size: float = 0.0
    bold: bool = False
    color: int = 0
    rotation: int = 0  # 0 | 90 | -90, from the source line's writing direction


class RawImage(BaseModel):
    """A raster/vector image region on a page, exported to a PNG file."""

    id: str
    page: int
    bbox: tuple[float, float, float, float]
    path: str  # filename relative to the document asset dir


class RawPage(BaseModel):
    width: float
    height: float
    spans: list[RawSpan] = Field(default_factory=list)
    images: list[RawImage] = Field(default_factory=list)
    # Horizontal rules (vector lines/thin rects): (y, x0, x1, thickness_pt, color_hex) in
    # PDF points. These are drawings, not text, surfaced here for the reconciler.
    hrules: list[tuple[float, float, float, float, str]] = Field(default_factory=list)
    render_path: str = ""  # full-page PNG used for vision analysis


class RawDocument(BaseModel):
    source_lang: Optional[str] = None
    pages: list[RawPage] = Field(default_factory=list)

    def all_spans(self) -> list[RawSpan]:
        return [s for p in self.pages for s in p.spans]


# --------------------------------------------------------------------------- #
# Semantic layer (the reflow-able skeleton)
# --------------------------------------------------------------------------- #


class _IdentifiedBlock(BaseModel):
    block_id: Optional[str] = None  # stable per-block id (uuid hex); assigned at ingest


class HeadingBlock(_IdentifiedBlock):
    type: Literal["heading"] = "heading"
    level: int = 1
    marker: Optional[str] = None  # e.g. "4." — literal, never translated
    text: str = ""
    page: Optional[int] = None  # 0-based source PDF page, for page-boundary breaks
    font_size: Optional[float] = None  # dominant source font size (pt) — for hierarchy
    bold: Optional[bool] = None
    font: Optional[str] = None  # dominant source font family (e.g. "Batang", "Malgun Gothic")
    color: Optional[str] = None  # dominant source text colour, hex 'RRGGBB'; None → ink
    marker_color: Optional[str] = None  # marker's own hex 'RRGGBB' when it differs from the
    #                                      block colour (e.g. a gold 'FIGURE 3' on a teal title);
    #                                      None → marker renders in the block colour (default).
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    align: Optional[str] = None  # "center" | "right" | "justify" | None(=left)
    line_spacing: Optional[float] = None  # leading as a multiple of font size
    # Measured top gap (pt) above this block. Cover-page-only: the reconciler sets this to
    # reproduce a designed cover's deliberate vertical rhythm (e.g. a title sitting well
    # below the page top). None (the default, and every pre-existing source.json) → no
    # explicit gap, ordinary paragraph flow.
    space_before: Optional[float] = None
    # Per-page-boundary top-space compensation (page-parity anchoring, T3). When this block
    # is the first on its source page and a hard page break lands right above it, the
    # break can visually eat into the block's own natural top margin; the reconciler
    # measures a compensating gap (pt) for sparse body pages and records it here. None
    # (the default, and every pre-existing source.json) -> no-op. The docx renderer
    # applies it as space_before ONLY in page-parity mode (see _page_parity_on) — outside
    # parity mode there's no page-parity break to compensate for; block.space_before
    # (cover rhythm) wins over this field whenever both are set.
    page_space_before: Optional[float] = None
    source_span_ids: list[str] = Field(default_factory=list)


class ParagraphBlock(_IdentifiedBlock):
    type: Literal["paragraph"] = "paragraph"
    text: str = ""
    page: Optional[int] = None
    font_size: Optional[float] = None
    bold: Optional[bool] = None
    font: Optional[str] = None  # dominant source font family
    color: Optional[str] = None  # source text colour, hex 'RRGGBB'; None → ink
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    align: Optional[str] = None  # "center" | "right" | "justify" | None(=left)
    line_spacing: Optional[float] = None
    indent: Optional[float] = None  # left indent (pt), measured from the page text margin
    # When set, the original had meaningful line breaks (a list/legend/address, not body
    # wrap): render each line on its own line. None → reflow `text`.
    lines: Optional[list[str]] = None
    # Measured top gap (pt) above this block. Cover-page-only (see HeadingBlock.space_before).
    space_before: Optional[float] = None
    # Per-page-boundary top-space compensation (page-parity anchoring, T3); see
    # HeadingBlock.page_space_before. None -> no-op; the docx renderer applies it as
    # space_before only in page-parity mode (block.space_before wins when set).
    page_space_before: Optional[float] = None
    source_span_ids: list[str] = Field(default_factory=list)


class ListItemBlock(_IdentifiedBlock):
    type: Literal["list_item"] = "list_item"
    marker: str = ""  # e.g. "4.1.1", "1)", "가)" — literal, never translated
    level: int = 0
    text: str = ""
    page: Optional[int] = None
    font_size: Optional[float] = None
    bold: Optional[bool] = None
    font: Optional[str] = None  # dominant source font family
    color: Optional[str] = None  # source text colour, hex 'RRGGBB'; None → ink
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    align: Optional[str] = None  # "center" | "right" | "justify" | None(=left)
    line_spacing: Optional[float] = None
    indent: Optional[float] = None  # left indent (pt); overrides the level-based default
    lines: Optional[list[str]] = None
    # Measured top gap (pt) above this block. Cover-page-only (see HeadingBlock.space_before).
    space_before: Optional[float] = None
    # Per-page-boundary top-space compensation (page-parity anchoring, T3); see
    # HeadingBlock.page_space_before. None -> no-op; the docx renderer applies it as
    # space_before only in page-parity mode (block.space_before wins when set).
    page_space_before: Optional[float] = None
    source_span_ids: list[str] = Field(default_factory=list)


class TableCell(BaseModel):
    text: str = ""
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False
    vertical: bool = False  # source text written vertically → render rotated 90°
    font: Optional[str] = None  # dominant source font family
    color: Optional[str] = None  # source text colour, hex 'RRGGBB'; None → ink
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    align: Optional[str] = None  # per-cell "center" | "right" | "justify" | None(=left)
    shading: Optional[str] = None  # cell background fill, hex 'RRGGBB'; None → no fill
    valign: Optional[str] = None  # "top" | "center" | "bottom" | None
    lines: Optional[list[str]] = None  # preserved multi-line cell content
    source_span_ids: list[str] = Field(default_factory=list)


class TableBlock(_IdentifiedBlock):
    type: Literal["table"] = "table"
    rows: list[list[TableCell]] = Field(default_factory=list)
    caption: Optional[str] = None
    page: Optional[int] = None
    # Relative column widths (fractions summing to 1, one per grid column) recovered from
    # the source's real vertical grid lines. None → the renderer distributes columns evenly.
    col_widths: Optional[list[float]] = None
    source_span_ids: list[str] = Field(default_factory=list)


class FigureOverlay(BaseModel):
    """One run of text that sat inside a figure. The composite PNG is drawn from these;
    `source_text` is preserved (for re-translation), only `target_text` is translated.
    Note: `bbox` is (x, y, w, h) in figure-local pixels — unlike RawSpan.bbox which is
    (x0, y0, x1, y1) in PDF points."""

    id: str  # the originating RawSpan id (re-translation targeting in Phase B)
    source_text: str = ""
    target_text: str = ""  # filled by translation; the only translated leaf field here
    # Figure-LOCAL pixel box at the render DPI: (x, y, w, h), origin = figure top-left.
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    font_size: float = 0.0  # px at render DPI
    color: Optional[str] = None  # hex 'RRGGBB'; None → ink (renderer default)
    align: str = "left"  # "left" | "center" | "right"
    rotation: int = 0  # 0 | 90 | -90


class FigureBlock(_IdentifiedBlock):
    type: Literal["figure"] = "figure"
    image_ref: str = ""  # filename in the asset dir; the (translated) PNG the renderer embeds
    base_image_ref: str = ""  # text-redacted PNG; source for the per-language composite
    overlays: list[FigureOverlay] = Field(default_factory=list)  # in-figure text ([] = opaque)
    width: Optional[float] = None  # source width (pt) — render at true size, not stretched
    height: Optional[float] = None  # source height (pt)
    align: Optional[str] = None  # "center" | "right" | None(=left), from bbox x-position
    # When text sat beside the image in the source, float it to this side ("left"/"right")
    # with square text-wrap so the neighbouring text stays alongside instead of below.
    wrap: Optional[str] = None
    page: Optional[int] = None


class CaptionBlock(_IdentifiedBlock):
    type: Literal["caption"] = "caption"
    text: str = ""
    page: Optional[int] = None
    font_size: Optional[float] = None
    bold: Optional[bool] = None
    font: Optional[str] = None  # dominant source font family
    color: Optional[str] = None  # source text colour, hex 'RRGGBB'; None → ink
    italic: Optional[bool] = None
    underline: Optional[bool] = None
    align: Optional[str] = None  # "center" | "right" | "justify" | None(=left)
    line_spacing: Optional[float] = None
    indent: Optional[float] = None  # left indent (pt), measured from the page text margin
    lines: Optional[list[str]] = None
    # Measured top gap (pt) above this block. Cover-page-only (see HeadingBlock.space_before).
    space_before: Optional[float] = None
    # Per-page-boundary top-space compensation (page-parity anchoring, T3); see
    # HeadingBlock.page_space_before. None -> no-op; the docx renderer applies it as
    # space_before only in page-parity mode (block.space_before wins when set).
    page_space_before: Optional[float] = None
    source_span_ids: list[str] = Field(default_factory=list)


class DividerBlock(_IdentifiedBlock):
    """A horizontal rule (section separator / underline) recovered from page drawings.
    Carries no text — rendered as a bottom-bordered empty paragraph."""

    type: Literal["divider"] = "divider"
    thickness: Optional[float] = None  # source line weight (pt); None → default
    color: Optional[str] = None  # hex 'RRGGBB'; None → ink
    page: Optional[int] = None


Block = Annotated[
    Union[
        HeadingBlock,
        ParagraphBlock,
        ListItemBlock,
        TableBlock,
        FigureBlock,
        CaptionBlock,
        DividerBlock,
    ],
    Field(discriminator="type"),
]


class RunningBand(BaseModel):
    """A repeating page header/footer band: up to three positioned texts (left/center/
    right, as in the source) plus an optional horizontal rule across the band."""

    left: Optional[str] = None
    center: Optional[str] = None
    right: Optional[str] = None
    image: Optional[str] = None  # decoration-strip PNG (asset-dir basename, text-redacted),
    #                              anchored full-width behind the band text. None → no decoration.
    page_number: Optional[str] = None  # "left" | "center" | "right" → a Word PAGE field there.
    page_number_format: Optional[str] = None  # e.g. "Page {n} of {m}" — {n}=PAGE, {m}=NUMPAGES
    rule: bool = False  # a horizontal line spanning the band (e.g. under the header)
    rule_size: Optional[float] = None  # rule thickness (pt); None → default
    rule_color: Optional[str] = None  # rule hex colour 'RRGGBB'; None → ink


class SemanticDocument(BaseModel):
    """Page-independent reading-order sequence of blocks (post-reflow skeleton)."""

    source_lang: str = "ko"
    target_lang: Optional[str] = None
    title: Optional[str] = None
    running_header: Optional[RunningBand] = None  # repeating page header (chrome)
    running_footer: Optional[RunningBand] = None  # repeating page footer (chrome)
    blocks: list[Block] = Field(default_factory=list)
    # Source page geometry (pt). The renderer reproduces the original paper size + text
    # margins so the output paginates like the source instead of defaulting to US Letter
    # (which is shorter than A4 and balloons a translated A4 doc onto extra pages). All
    # Optional → existing source.json on disk loads unchanged.
    page_width: Optional[float] = None
    page_height: Optional[float] = None
    margin_left: Optional[float] = None
    margin_right: Optional[float] = None
    margin_top: Optional[float] = None
    margin_bottom: Optional[float] = None
    # Full-bleed background for the first page (cover): an asset-dir PNG with the page's text
    # redacted, anchored behind text on page 1. None → no cover background (unchanged behaviour).
    cover_background: Optional[str] = None
    # Direction-gated page-parity anchoring (docx renderer): put a page_break_before at
    # every source-page boundary, so the translated document paginates 1:1 with the
    # source. None (the default) -> the renderer decides from source_lang/target_lang
    # (ON for a text-shrinking direction, e.g. en->ko; OFF for an expanding one, e.g.
    # ko->en — see docx_renderer._page_parity_on). True/False here explicitly overrides
    # that inference. Old source.json on disk loads as None -> unchanged behaviour.
    page_parity: Optional[bool] = None


# --------------------------------------------------------------------------- #
# Shared helpers: walk every translatable text field of the model.
# Translation and reconciliation both rely on this so they stay in lock-step.
# --------------------------------------------------------------------------- #


def make_seg_id(block_id: str, *parts) -> str:
    """Stable segment id: block_id joined with its sub-key parts (e.g. 'B:line:3')."""
    return ":".join([block_id, *(str(p) for p in parts)])


# Translated output is rendered in viewers (OnlyOffice DS on Linux, the browser, Word)
# that may not have the source's Korean fonts. Rather than emit an exact Korean family
# name that silently falls back to a serif where it's missing, we map every source font
# to a universally-available family BY CLASS, so the sans/serif character is preserved
# everywhere. Hints cover romanized PDF names and Hangul DOCX names.
_SERIF_HINT = ("batang", "myeongjo", "myungjo", "gungsuh", "nanummyeongjo", "times",
               "georgia", "serif", "roman", "바탕", "명조", "궁서")
_SANS_HINT = ("gothic", "dotum", "gulim", "malgun", "nanumgothic", "arial", "helvetica",
              "verdana", "tahoma", "calibri", "sans", "고딕", "돋움", "굴림", "맑은")


def universal_font(name: Optional[str]) -> Optional[str]:
    """Map a source font name to a universally-available family by class: serif → "Times
    New Roman", sans → "Arial", unknown → None (renderer keeps its default). Keeps the
    sans/serif character of the source in any viewer without depending on installed
    Korean fonts."""
    if not name:
        return None
    n = name.lower()
    if any(h in n for h in _SERIF_HINT):
        return "Times New Roman"
    if any(h in n for h in _SANS_HINT):
        return "Arial"
    return None


def iter_segments(doc: SemanticDocument):
    """Yield (getter, setter, span_ids, block_id, seg_id) for every translatable string.

    span_ids is the owning block/cell's source_span_ids (block-level granularity) — used
    to locate the segment's bbox on the original page for the re-translation highlight.
    block_id is the stable per-block identifier (None for running-band fields) — used to
    scroll the editor to that block via a bookmark. seg_id is the stable segment identifier
    composed of block_id (or positional fallback) and sub-key parts. Running-band fields
    have no source spans (yield []). Markers and image refs are excluded — structural.
    """
    for which, band in (("header", doc.running_header), ("footer", doc.running_footer)):
        if band:
            for attr in ("left", "center", "right"):
                if getattr(band, attr):
                    get, set_ = _field_accessor(band, attr)
                    yield get, set_, [], None, f"band:{which}:{attr}"
    for bi, block in enumerate(doc.blocks):
        block_id = getattr(block, "block_id", None)
        _bid = block_id if block_id is not None else str(bi)
        if isinstance(block, HeadingBlock):
            get, set_ = _field_accessor(block, "text")
            yield get, set_, block.source_span_ids, block_id, make_seg_id(_bid, "text")
        elif isinstance(block, (ParagraphBlock, ListItemBlock, CaptionBlock)):
            # Preserved multi-line blocks translate line-by-line; others as one text.
            if block.lines:
                for li in range(len(block.lines)):
                    get, set_ = _list_accessor(block.lines, li)
                    yield get, set_, block.source_span_ids, block_id, make_seg_id(_bid, "line", li)
            else:
                get, set_ = _field_accessor(block, "text")
                yield get, set_, block.source_span_ids, block_id, make_seg_id(_bid, "text")
        elif isinstance(block, TableBlock):
            if block.caption is not None:
                get, set_ = _field_accessor(block, "caption")
                yield get, set_, block.source_span_ids, block_id, make_seg_id(_bid, "caption")
            ci = 0
            for row in block.rows:
                for cell in row:
                    if cell.lines:
                        for li in range(len(cell.lines)):
                            get, set_ = _list_accessor(cell.lines, li)
                            yield get, set_, cell.source_span_ids, block_id, make_seg_id(_bid, "cellline", ci, li)
                    else:
                        get, set_ = _field_accessor(cell, "text")
                        yield get, set_, cell.source_span_ids, block_id, make_seg_id(_bid, "cell", ci)
                    ci += 1
        elif isinstance(block, FigureBlock):
            for oi, ov in enumerate(block.overlays):
                get, set_ = _overlay_accessor(ov)
                yield get, set_, [ov.id], block_id, make_seg_id(_bid, "overlay", oi)


def build_segment_index_map(doc: "SemanticDocument") -> dict:
    """Map a location key -> seg-id string, in the SAME order as iter_segments().

    The renderer uses this to stamp data-seg="{seg_id}" so HTML spans can be looked up
    by stable id. MUST stay in lockstep with iter_segments above — tested together in
    test_render_html_segments.py.
    Keys: ("band", which, attr) | (bi, "text") | (bi, "line", li) | (bi, "caption")
          | (bi, "cell", ci) | (bi, "cellline", ci, li) | (bi, "overlay", oi)
    Values: seg-id strings (e.g. "H:text", "P:line:0", "band:header:left").
    """
    m: dict = {}
    for which, band in (("header", doc.running_header), ("footer", doc.running_footer)):
        if band:
            for attr in ("left", "center", "right"):
                if getattr(band, attr):
                    m[("band", which, attr)] = f"band:{which}:{attr}"
    for bi, block in enumerate(doc.blocks):
        block_id = getattr(block, "block_id", None)
        _bid = block_id if block_id is not None else str(bi)
        if isinstance(block, HeadingBlock):
            m[(bi, "text")] = make_seg_id(_bid, "text")
        elif isinstance(block, (ParagraphBlock, ListItemBlock, CaptionBlock)):
            if block.lines:
                for li in range(len(block.lines)):
                    m[(bi, "line", li)] = make_seg_id(_bid, "line", li)
            else:
                m[(bi, "text")] = make_seg_id(_bid, "text")
        elif isinstance(block, TableBlock):
            if block.caption is not None:
                m[(bi, "caption")] = make_seg_id(_bid, "caption")
            ci = 0
            for row in block.rows:
                for cell in row:
                    if cell.lines:
                        for li in range(len(cell.lines)):
                            m[(bi, "cellline", ci, li)] = make_seg_id(_bid, "cellline", ci, li)
                    else:
                        m[(bi, "cell", ci)] = make_seg_id(_bid, "cell", ci)
                    ci += 1
        elif isinstance(block, FigureBlock):
            for oi in range(len(block.overlays)):
                m[(bi, "overlay", oi)] = make_seg_id(_bid, "overlay", oi)
    return m


def iter_text_fields(doc: SemanticDocument):
    """(getter, setter) pairs for every translatable string — see iter_segments."""
    for get, set_, *_ in iter_segments(doc):
        yield get, set_


def collect_texts(doc: SemanticDocument) -> list[str]:
    return [get() for get, _ in iter_text_fields(doc)]


def _field_accessor(obj: BaseModel, attr: str):
    def getter() -> str:
        return getattr(obj, attr) or ""

    def setter(value: str) -> None:
        setattr(obj, attr, value)

    return getter, setter


def _list_accessor(lst: list, i: int):
    """(getter, setter) for one element of a list — used for preserved `lines`."""

    def getter() -> str:
        return lst[i] or ""

    def setter(value: str) -> None:
        lst[i] = value

    return getter, setter


def _overlay_accessor(ov: "FigureOverlay"):
    """(getter, setter) for a figure overlay: translate source_text → target_text, keeping
    source_text intact so the overlay can be re-translated later (Phase B)."""

    def getter() -> str:
        return ov.source_text or ""

    def setter(value: str) -> None:
        ov.target_text = value

    return getter, setter
