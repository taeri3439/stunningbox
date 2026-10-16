"""Stage 2 — reconciliation + global normalization.

This is where "logical structure ~100%" is actually won:
  1. Convert per-page analyzer output into the final SemanticDocument.
  2. Drop repeating chrome (headers/footers/page numbers).
  3. Materialize figures by clipping their region to an image.
  4. COVERAGE GATE: verify every extracted span is accounted for — nothing lost.
  5. Stitch blocks split across page boundaries (paragraphs and tables).
"""
from __future__ import annotations

import os
import re
import statistics
from collections import Counter

from models import (
    CaptionBlock,
    DividerBlock,
    FigureBlock,
    HeadingBlock,
    ListItemBlock,
    ParagraphBlock,
    RawDocument,
    RawPage,
    RunningBand,
    SemanticDocument,
    TableBlock,
    universal_font,
    TableCell,
)
from pipeline import extractor
from pipeline import figure_ocr
from pipeline import figure_overlay

_SENTENCE_END = re.compile(r"[.。!?:;”』」\)]\s*$|다\.?\s*$")

# A first page whose rendered image is at least this fraction NON-WHITE is a designed,
# full-bleed cover (a flat-colored page with a title), not a normal white content page.
# Real covers are split by the vision analyzer into several sub-page figures, so we key on
# the page's overall colouredness, not on any single figure's area.
COVER_PAGE_NONWHITE_FRAC = 0.4
# A genuine cover has little body text (a title, maybe a subtitle). A photo/banner-heavy CONTENT
# first page — or a scanned page the analyzer OCR'd into paragraphs — has many body blocks. Require
# sparse body content so such a page isn't misread as a cover (which would suppress its figures and,
# if textless, drop its content).
COVER_MAX_BODY_BLOCKS = 3
# A text cover (huge title + sparse text + logo) may still carry a few subtitle/doc-number/
# committee-line paragraphs — more than a bare designed cover, but far fewer than a real
# content page. Its own (looser) cap so a real ladder-style cover isn't blocked by the
# designed-cover guard above.
COVER_TEXT_MAX_BODY_BLOCKS = 8
# Header/footer decorative strip = the top/bottom this fraction of the page. The strip is
# rendered (text redacted) and kept only if enough of it is non-white (real decoration), so a
# plain header with just text doesn't anchor a blank image.
BAND_STRIP_FRAC = 0.12
# Minimum non-white fraction of a rendered (text-redacted) strip for it to count as decoration.
# A thin rule alone is well under this (it stays a cell-border rule); a color bar/band clears it.
BAND_DECORATION_NONWHITE_FRAC = 0.06
# A thin brand bar (e.g. a 2pt multi-colour footer rule) has tiny area (low non-white) but is
# vividly coloured — captured via this saturated-pixel fraction so it isn't lost. Black text /
# grey rules have ~0 saturation, so a plain strip still falls through to the rule path.
BAND_DECORATION_COLOR_FRAC = 0.01
# A WHITE text cover (huge title + sparse text + logo) fails the non-white check above but is
# still a cover: sparse span count + a display-size title. A content page carries far more spans.
COVER_MAX_SPANS = 18  # a text cover is sparse; content pages carry far more spans
COVER_DISPLAY_FONT_PT = 26.0  # a cover carries a display-size title
# Vertical-rhythm gap thresholds (cover page 0 only, workstream B): a gap this small is
# ordinary line-to-line spacing noise, not a deliberate design gap, so it's left unset (the
# renderer's normal paragraph flow already handles it). The upper bound guards against a
# runaway measurement (e.g. a mis-detected "previous" block far above) turning into an
# absurd multi-page blank run.
COVER_RHYTHM_MIN_GAP = 6.0
COVER_RHYTHM_MAX_GAP = 300.0
# Page-rhythm gate (non-cover pages, template-fidelity Task 2): generalizes the cover-only
# rhythm above to a SPARSE body page (a disclaimer page starting halfway down, a chart page
# with trailing whitespace). A dense page's inter-paragraph gaps are ordinary leading, not
# deliberate design whitespace — measuring those would double-space every dense page's
# paragraphs on render, so the whole page is gated on looking genuinely sparse first: few
# text blocks, and at least one gap unambiguously larger than normal leading.
RHYTHM_MAX_TEXT_BLOCKS = 8
RHYTHM_MIN_GAP_PT = 40.0


class ReconcileReport:
    def __init__(self) -> None:
        self.total_spans = 0
        self.covered_spans = 0
        self.missing_span_ids: list[str] = []
        self.missing_texts: list[str] = []
        self.warnings: list[str] = []

    @property
    def coverage(self) -> float:
        return self.covered_spans / self.total_spans if self.total_spans else 1.0

    def as_dict(self) -> dict:
        return {
            "total_spans": self.total_spans,
            "covered_spans": self.covered_spans,
            "coverage": round(self.coverage, 4),
            "missing_span_ids": self.missing_span_ids,
            "missing_texts": self.missing_texts,
            "warnings": self.warnings,
        }


def _page_nonwhite_frac(render_path: str, asset_dir: str) -> float:
    """Fraction of a page render that is NOT near-white. High for a designed/colored cover
    page, ~0 for a normal white text page. Uses a histogram (no per-pixel Python loop)."""
    if not render_path:
        return 0.0
    path = os.path.join(asset_dir, render_path)
    if not os.path.exists(path):
        return 0.0
    try:
        from PIL import Image
        with Image.open(path) as im:
            hist = im.convert("L").histogram()  # 256 intensity buckets
            total = sum(hist)
            nonwhite = sum(hist[:245])  # < 245 = not near-white
            return nonwhite / total if total else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def _looks_like_text_cover(page_spans, n_pages: int) -> bool:
    """A WHITE cover (huge title + sparse text + whitespace) fails the non-white
    fraction check but is still a cover: sparse span count + a display-size title.
    Single-page documents have no cover."""
    if n_pages < 2 or not page_spans or len(page_spans) > COVER_MAX_SPANS:
        return False
    return max((getattr(s, "size", 0.0) or 0.0) for s in page_spans) >= COVER_DISPLAY_FONT_PT


def _strip_colored_frac(render_path: str, asset_dir: str) -> float:
    """Fraction of a strip render that is COLORED (saturated) — high for brand decoration (a
    diamond band, a footer color bar), ~0 for black/grey text and rules. Lets a thin-but-
    coloured footer bar (small area, so low non-white) still register as decoration."""
    if not render_path:
        return 0.0
    path = os.path.join(asset_dir, render_path)
    if not os.path.exists(path):
        return 0.0
    try:
        from PIL import Image
        with Image.open(path) as im:
            sat = im.convert("HSV").getchannel("S").histogram()  # 256 saturation buckets
            total = sum(sat)
            colored = sum(sat[50:])  # S >= 50 = a real colour (not black/grey/white text)
            return colored / total if total else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def reconcile(
    raw: RawDocument,
    page_blocks: list[list[dict]],
    pdf_path: str,
    asset_dir: str,
    source_lang: str = "ko",
    ocr=None,
) -> tuple[SemanticDocument, ReconcileReport]:
    report = ReconcileReport()
    referenced: set[str] = set()
    by_id = {s.id: s for s in raw.all_spans()}  # span lookup for styles + coverage
    blocks = []
    grid_cache: dict[int, list] = {}  # page -> PyMuPDF table grids (real column geometry)
    header_items: list[tuple[str, str, int]] = []  # (text, side, page) for the top band
    footer_items: list[tuple[str, str, int]] = []  # (text, side, page) for the bottom band
    cover_bg: str | None = None  # first-page full-bleed background (set when a cover is detected)
    # Normal body-page top margin (same estimator the final page geometry uses), reused below
    # as the baseline "previous bottom" for the FIRST page-0 cover block's space_before: a
    # cover has no real previous block to measure its gap from, and the page's raw edge (y=0)
    # would overstate the gap by the margin itself. Computed once, from the whole document
    # (not page 0 alone) since a cover's own spans start well past any ordinary margin.
    doc_margin_top = _page_geometry(raw).get("margin_top") if raw.pages else None

    for page_index, raw_blocks in enumerate(page_blocks):
        pg = raw.pages[page_index] if page_index < len(raw.pages) else None
        page_w = pg.width if pg else 0.0
        page_h = pg.height if pg else 0.0
        page_spans = pg.spans if pg else []
        page_left = _page_left_margin(page_spans)

        # A designed (mostly non-white) first page is a full-bleed cover: render the whole
        # page with its text redacted as the cover background (the title/subtitle stay as
        # heading blocks on top), and suppress the page's figures (they're part of the design).
        # Guard against false positives: a cover has few body blocks, so an image-heavy CONTENT
        # first page (many paragraphs/lists/tables) is NOT treated as a cover. A WHITE text
        # cover may carry a few more subtitle/doc-number lines than a designed cover, so it
        # gets its own (looser) body-blocks cap rather than sharing the designed-cover one.
        _body_blocks = sum(1 for b in raw_blocks if isinstance(b, dict)
                           and b.get("type") in ("paragraph", "list_item", "table"))
        if page_index == 0 and pg and cover_bg is None and page_w and page_h:
            designed_cover = (
                _body_blocks <= COVER_MAX_BODY_BLOCKS
                and _page_nonwhite_frac(pg.render_path, asset_dir) >= COVER_PAGE_NONWHITE_FRAC
            )
            text_cover = (
                _body_blocks <= COVER_TEXT_MAX_BODY_BLOCKS
                and _looks_like_text_cover(page_spans, len(raw.pages))
            )
            if designed_cover or text_cover:
                try:
                    cover_bg = figure_overlay.render_redacted_clip(
                        pdf_path, 0, [0, 0, page_w, page_h],
                        [s.bbox for s in page_spans], asset_dir, "cover_bg.png")
                except Exception as e:  # noqa: BLE001
                    report.warnings.append(f"cover background render failed: {e}")

        local: list[tuple[float | None, object]] = []  # (top_y, block) in reading order
        occupied: list[tuple[float, float]] = []  # y-ranges of tables/figures (rule mask)
        figure_regions: list[tuple[float, float, float, float]] = []  # clipped figure bboxes
        # Empty row-spanning header cells (a vertical label the analyzer left blank) — a
        # leaked vertical stack can be folded back into them instead of leaking as text.
        empty_label_cells: list[dict] = []

        for b in raw_blocks:
            if not isinstance(b, dict):  # malformed analyzer output (e.g. a bare string)
                continue  # its spans, if any, are still recovered by the coverage net
            btype = b.get("type")
            ids = list(b.get("source_span_ids", []))

            if btype == "chrome":
                referenced.update(ids)  # accounted for, but reproduced as header/footer
                spans_ = [by_id[i] for i in ids if i in by_id]
                if page_h:
                    for ctext, cxc, cy in _split_chrome_clusters(spans_, page_w):
                        side = _chrome_side(cxc, page_w)
                        if cy < 0.2 * page_h:
                            header_items.append((ctext, side, page_index))
                        elif cy > 0.8 * page_h:
                            footer_items.append((ctext, side, page_index))
                        if page_index == 0 and cover_bg is not None:
                            # A cover's chrome (standard number, issue tag) is part of the
                            # cover design and usually appears ONLY here, so band voting
                            # drops it. Keep it as body text on the cover page; the band
                            # never renders on a cover (different first-page header).
                            local.append((cy, ParagraphBlock(
                                text=ctext, page=0, align=side, source_span_ids=[])))
                continue

            if btype == "figure":
                bbox = b.get("figure_bbox")
                image_ref = ""
                fw = fh = align = wrap = None
                geom = bbox
                if bbox and len(bbox) == 4:
                    name = f"fig_p{page_index}_{len(local)}.png"
                    try:
                        image_ref, geom = extractor.render_clip(
                            pdf_path, page_index, bbox, asset_dir, name
                        )
                    except Exception as e:  # noqa: BLE001
                        report.warnings.append(f"figure clip failed p{page_index}: {e}")
                    # Use the TIGHT box (vision bbox over-estimates) for size/placement.
                    occupied.append((geom[1], geom[3]))
                    figure_regions.append(tuple(geom))
                    fw = round(geom[2] - geom[0], 1)
                    fh = round(geom[3] - geom[1], 1)
                    align = _figure_align(geom, page_w)
                    wrap = _figure_wrap(geom, page_spans)
                # On a detected cover page every figure is part of the page design (already
                # captured in cover_background) — don't also emit it inline.
                if page_index == 0 and cover_bg is not None:
                    referenced.update(ids)
                    continue
                base_ref = ""
                overlays = []
                if image_ref and geom and len(geom) == 4:
                    # geom is the tight (white-trimmed) box — spans in the trimmed margin
                    # have no glyphs to paint, so filtering on geom is intentional.
                    in_fig = figure_overlay.spans_in_region(page_spans, geom)
                    candidates = figure_overlay.overlays_from_spans(in_fig, geom)
                    if figure_overlay.has_translatable_text(candidates):
                        overlays = candidates
                        base_name = f"fig_p{page_index}_{len(local)}.base.png"
                        try:
                            base_ref = figure_overlay.render_redacted_clip(
                                pdf_path, page_index, geom,
                                [s.bbox for s in in_fig], asset_dir, base_name)
                        except Exception as e:  # noqa: BLE001 — fall back to opaque figure
                            report.warnings.append(
                                f"figure redaction failed p{page_index}: {e}")
                            overlays, base_ref = [], ""
                # No born-digital text, but the analyzer flagged readable text in this figure →
                # OCR it: vision reads + classifies, we erase the original (simple fill) and
                # overlay the translation. keep-regions (logos/URLs) stay in the image.
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
                top = geom[1] if geom and len(geom) == 4 else _top_y(ids, by_id)
                local.append(
                    (
                        top,
                        FigureBlock(
                            image_ref=image_ref,
                            base_image_ref=base_ref,
                            overlays=overlays,
                            width=fw,
                            height=fh,
                            align=align,
                            wrap=wrap,
                            page=page_index,
                        ),
                    )
                )
                referenced.update(ids)
                continue

            if btype == "table":
                rows = []
                cell_id_pool: list[str] = []
                for r in b.get("rows", []):
                    if not isinstance(r, (list, tuple)):
                        continue  # malformed row
                    cells = []
                    for c in r:
                        if not isinstance(c, dict):  # bare string / malformed cell
                            c = {"text": c if isinstance(c, str) else ""}
                        cell_ids = list(c.get("source_span_ids", []) or [])
                        referenced.update(cell_ids)
                        cell_id_pool.extend(cell_ids)
                        recovered = _recover_lines(cell_ids, by_id)
                        cell_lines = [t for t, _, _ in recovered]
                        # Vertically-stacked label (one char per line): join into one
                        # phrase (so it translates as a unit) and flag it for rotated
                        # rendering, rather than treating each char as a separate line.
                        vertical = _is_vertical_cell(cell_lines) or bool(c.get("vertical"))
                        if vertical:
                            text = "".join(s.strip() for s in cell_lines)
                            preserved = None
                        else:
                            text = c.get("text", "") or ""
                            # Keep hard line breaks only when they're SEMANTIC (a list /
                            # address inside the cell); otherwise let Word reflow the text
                            # so a longer translation doesn't overflow with forced breaks.
                            preserved = (
                                cell_lines
                                if (len(cell_lines) > 1 and _preserve_breaks(recovered))
                                else None
                            )
                        c_font, c_color, c_italic = _dominant_inline(cell_ids, by_id)
                        cells.append(
                            TableCell(
                                text=text,
                                rowspan=_as_int(c.get("rowspan"), 1),
                                colspan=_as_int(c.get("colspan"), 1),
                                is_header=bool(c.get("is_header", False)),
                                vertical=vertical,
                                font=c_font,
                                color=c_color,
                                italic=c_italic,
                                align=_cell_align(c.get("align")),
                                shading=_hex6(c.get("shading")),
                                valign=_cell_valign(c.get("valign")),
                                lines=preserved,
                                source_span_ids=cell_ids,
                            )
                        )
                    rows.append(cells)
                span_pool = cell_id_pool or ids
                yr = _y_range(span_pool, by_id)
                tb_bbox = _bbox(span_pool, by_id)
                # If a page image covers the table's region, the "table" is really a rendered
                # graphic (e.g. a column of chemical-structure drawings) whose pictures the
                # text grid can't carry — they'd be dropped, leaving empty cells. Emit the
                # region as a figure so the drawings + layout survive (the few overlaid texts
                # stay in the source language — accepted for an image-table). Reuses the
                # figure-clip path; falls back to the pre-extracted image if the clip fails.
                cover = _covering_image(pg, tb_bbox)
                if cover is not None:
                    ix0, iy0, ix1, iy1 = cover.bbox
                    # Clip the union of the image and any table-text region (text can extend
                    # past the image); when the table has no spans, just use the image bbox.
                    fbbox = ([min(tb_bbox[0], ix0), min(tb_bbox[1], iy0),
                              max(tb_bbox[2], ix1), max(tb_bbox[3], iy1)]
                             if tb_bbox else [ix0, iy0, ix1, iy1])
                    image_ref, geom = cover.path, fbbox
                    name = f"imgtable_p{page_index}_{len(local)}.png"
                    try:
                        image_ref, geom = extractor.render_clip(
                            pdf_path, page_index, fbbox, asset_dir, name)
                    except Exception as e:  # noqa: BLE001
                        report.warnings.append(f"image-table clip failed p{page_index}: {e}")
                    occupied.append((geom[1], geom[3]))
                    figure_regions.append(tuple(geom))
                    local.append((
                        geom[1],
                        FigureBlock(
                            image_ref=image_ref,
                            width=round(geom[2] - geom[0], 1),
                            height=round(geom[3] - geom[1], 1),
                            align=_figure_align(geom, page_w),
                            page=page_index,
                        ),
                    ))
                    referenced.update(span_pool)
                    report.warnings.append(
                        f"table on p{page_index} rendered as image (graphic/image table)")
                    continue
                # Match this table to PyMuPDF's detected grid (the real geometry the vision
                # grid lacks): its bbox, column lines, and rebuilt cell structure.
                gbbox = gbounds = gstruct = None
                if tb_bbox:
                    if page_index not in grid_cache:
                        grid_cache[page_index] = extractor.detect_table_grids(
                            pdf_path, page_index)
                    gbbox, gbounds, gstruct = _best_grid(grid_cache[page_index], tb_bbox)
                if yr:
                    # Mask the table's whole drawn area — including its border lines — from
                    # the horizontal-rule pass, so the table's own top/bottom borders aren't
                    # emitted as spurious dividers above/below it. The detected bbox covers
                    # the borders (text spans stop short of them); when no grid was detected,
                    # pad the text range so the borders are still masked (see helper).
                    occupied.append(_table_occupied_range(yr, gbbox))
                    # Remember any blank row-spanning header cell + the table's left edge,
                    # so a leaked vertical-stack label can be folded back into it.
                    cell_xs = [by_id[i].bbox[0] for i in cell_id_pool if i in by_id]
                    left_x = min(cell_xs) if cell_xs else None
                    for row in rows:
                        for cell in row:
                            if (not cell.text and cell.rowspan > 1 and cell.is_header
                                    and not cell.source_span_ids):
                                empty_label_cells.append(
                                    {"cell": cell, "yr": yr, "left_x": left_x})
                # Adopt the detected column widths when the counts agree; when they differ
                # (an irregular table the vision grid can't represent — e.g. the management
                # ledger) rebuild the table from the real cell structure so the lines align.
                col_widths = None
                if tb_bbox:
                    ncols = max((sum(c.colspan for c in row) for row in rows), default=0)
                    if gbounds and ncols >= 2 and len(gbounds) - 1 == ncols:
                        col_widths = _fractions(gbounds)
                    elif (gbounds and len(gbounds) - 1 >= 2
                          and len(gbounds) - 1 != ncols
                          and _should_rebuild(gstruct, rows)):
                        rebuilt = _rebuild_table_rows(gstruct)
                        widths = _fractions(gbounds)
                        # Adopt the rebuild only when its geometry fits AND its cell texts
                        # match the vision table's — otherwise find_tables locked onto a
                        # different region and swapping in its cells would corrupt the table.
                        if (widths
                                and max(sum(c.colspan for c in r) for r in rebuilt)
                                == len(gbounds) - 1
                                and _rebuild_text_overlap(rebuilt, rows) >= 0.5):
                            rows, col_widths = rebuilt, widths
                # A structurally-unreliable vision grid (judged post-rebuild, so a
                # geometry-corrected table isn't unfairly degraded) drifts columns and
                # duplicates rows when rendered as text — strictly worse than a picture of
                # the original region. Clip it as a figure instead; on clip failure, fall
                # through and keep the (imperfect but readable) text table.
                if tb_bbox and _table_structure_unreliable(rows):
                    fb = list(gbbox) if gbbox else list(tb_bbox)
                    pad = 3.0
                    fb = [fb[0] - pad, fb[1] - pad, fb[2] + pad, fb[3] + pad]
                    name = f"imgtable_p{page_index}_{len(local)}.png"
                    try:
                        image_ref, geom = extractor.render_clip(
                            pdf_path, page_index, fb, asset_dir, name)
                    except Exception as e:  # noqa: BLE001 — keep the text table on clip failure
                        report.warnings.append(
                            f"unreliable-table clip failed p{page_index}: {e}")
                        image_ref, geom = "", None
                    if image_ref and geom:
                        occupied.append((geom[1], geom[3]))
                        figure_regions.append(tuple(geom))
                        cap = b.get("caption")
                        if cap:
                            local.append((geom[1] - 0.1,
                                          CaptionBlock(text=cap, page=page_index)))
                        local.append((
                            geom[1],
                            FigureBlock(
                                image_ref=image_ref,
                                width=round(geom[2] - geom[0], 1),
                                height=round(geom[3] - geom[1], 1),
                                align=_figure_align(geom, page_w),
                                page=page_index,
                            ),
                        ))
                        referenced.update(span_pool)
                        report.warnings.append(
                            f"table on p{page_index} rendered as image (unreliable structure)")
                        continue
                local.append(
                    (
                        _top_y(span_pool, by_id),
                        TableBlock(
                            rows=rows,
                            caption=b.get("caption"),
                            page=page_index,
                            col_widths=col_widths,
                            source_span_ids=ids,
                        ),
                    )
                )
                referenced.update(ids)
                continue

            referenced.update(ids)
            align = _align(ids, by_id, page_w)
            heading = btype == "heading"
            size, bold = _dominant_style(ids, by_id, heading=heading)
            font, color, italic = _dominant_inline(ids, by_id)
            ls = _line_spacing(ids, by_id, size)
            # Only left-aligned blocks carry a measured indent; center/right placement is
            # already expressed via `align`, so adding a left offset would double-shift.
            indent = (
                None
                if (heading or align in ("center", "right"))
                else _indent(ids, by_id, page_left)
            )
            top = _top_y(ids, by_id)
            # semantic line breaks (lists/legends/addresses/TOC) — headings always reflow
            preserved = None
            if not heading:
                recovered = _recover_lines(ids, by_id)
                # The analyzer's marker often sits on its own line (left column), so it gets
                # recovered as the FIRST line too. Drop that duplicate — otherwise it renders
                # twice ("4. 4.") and forces the entry onto two lines instead of one. Without
                # it a single-line TOC entry reflows via the renderer's clean _toc_split. A
                # wrapped entry instead carries the marker EMBEDDED at the head of its first
                # line ("2.2.1.2.2. Profiling...") — strip that prefix too, so the renderer's
                # "{marker} " + lines doesn't render it twice.
                marker = b.get("marker")
                if marker and recovered:
                    mk = marker.strip()
                    first = recovered[0][0].strip()
                    if first == mk:
                        recovered = recovered[1:]
                    elif mk and first.startswith(mk) and first[len(mk):len(mk) + 1].isspace():
                        t, *rest = recovered[0]
                        recovered = [(first[len(mk):].lstrip(), *rest)] + recovered[1:]
                if _preserve_breaks(recovered):
                    preserved = [t for t, _, _ in recovered]

            if btype == "heading":
                mcol = _marker_color(ids, by_id, b.get("marker"))
                local.append(
                    (
                        top,
                        HeadingBlock(
                            level=_as_int(b.get("level"), 1),
                            marker=b.get("marker"),
                            marker_color=(mcol if (mcol and mcol != color) else None),
                            text=b.get("text", ""),
                            page=page_index,
                            font_size=size,
                            bold=bold,
                            font=font,
                            color=color,
                            italic=italic,
                            align=align,
                            line_spacing=ls,
                            source_span_ids=ids,
                        ),
                    )
                )
            elif btype == "list_item":
                local.append(
                    (
                        top,
                        ListItemBlock(
                            marker=b.get("marker", ""),
                            level=_as_int(b.get("level"), 0),
                            text=b.get("text", ""),
                            page=page_index,
                            font_size=size,
                            bold=bold,
                            font=font,
                            color=color,
                            italic=italic,
                            align=align,
                            line_spacing=ls,
                            indent=indent,
                            lines=preserved,
                            source_span_ids=ids,
                        ),
                    )
                )
            elif btype == "caption":
                local.append(
                    (
                        top,
                        CaptionBlock(
                            text=b.get("text", ""),
                            page=page_index,
                            font_size=size,
                            bold=bold,
                            font=font,
                            color=color,
                            italic=italic,
                            align=align,
                            line_spacing=ls,
                            indent=indent,
                            lines=preserved,
                            source_span_ids=ids,
                        ),
                    )
                )
            else:  # default: paragraph
                local.append(
                    (
                        top,
                        ParagraphBlock(
                            text=b.get("text", ""),
                            page=page_index,
                            font_size=size,
                            bold=bold,
                            font=font,
                            color=color,
                            italic=italic,
                            align=align,
                            line_spacing=ls,
                            indent=indent,
                            lines=preserved,
                            source_span_ids=ids,
                        ),
                    )
                )

        # --- tables the vision pass missed entirely -------------------------
        # PyMuPDF can detect a real grid where the analyzer emitted NO table/figure — a
        # diagonal-header / merged / symbol-heavy table (e.g. an O/triangle inspection
        # matrix) it couldn't structure, so its spans would otherwise scatter as loose
        # paragraphs. Rebuild such a table from the detected grid so the structure survives.
        # Guards keep this from inventing tables: a solid text-bearing grid, a region no
        # vision table/figure already covers, and whose spans the vision pass left UNASSIGNED
        # (>=70%) — so a normal paragraph region a grid-detector misfires on is never replaced.
        if pg:
            if page_index not in grid_cache:
                grid_cache[page_index] = extractor.detect_table_grids(pdf_path, page_index)
            for gbbox, gbounds, gstruct in grid_cache[page_index]:
                gy0, gy1 = gbbox[1], gbbox[3]
                if any(not (gy1 <= oy0 or gy0 >= oy1) for oy0, oy1 in occupied):
                    continue  # a vision table/figure already covers this region
                if not _should_rebuild(gstruct, []):
                    continue  # not a solid, text-bearing grid
                in_grid = [
                    s for s in page_spans
                    if gbbox[0] <= (s.bbox[0] + s.bbox[2]) / 2 <= gbbox[2]
                    and gbbox[1] <= (s.bbox[1] + s.bbox[3]) / 2 <= gbbox[3]
                ]
                if not in_grid or sum(s.id not in referenced for s in in_grid) < 0.7 * len(in_grid):
                    continue  # the vision pass already accounted for this region's text
                for s in in_grid:
                    referenced.add(s.id)
                occupied.append((gy0, gy1))
                local.append((
                    gbbox[1],
                    TableBlock(rows=_rebuild_table_rows(gstruct), page=page_index,
                               col_widths=_fractions(gbounds)),
                ))
                report.warnings.append(
                    f"recovered a vision-missed table from the grid on page {page_index}")

        # --- extras spliced back in by vertical position --------------------
        extras: list[tuple[float | None, object]] = []
        # (a) text the analyzer dropped: never lose a span — recover it as a paragraph.
        recovered_n = 0
        # A leaked vertical-stack label (e.g. "개정내역") belongs in its blank row-spanning
        # header cell, not as stray single-char paragraphs below the table. Absorb it
        # BEFORE per-line recovery (which would otherwise emit each char on its own).
        _absorb_vertical_labels(page_spans, referenced, by_id, empty_label_cells)
        # Text that falls wholly inside a figure's clipped region is already shown by that
        # figure image (a flowchart's box labels, a diagram's callouts). Don't ALSO emit it as
        # text — whether the analyzer transcribed it as its own block OR it was left for the
        # recovery net — otherwise it echoes as floating (translated) text over the image and
        # inflates the page count. We keep the figure image (the faithful layout); the in-image
        # text staying in the source language is the accepted figure-text trade-off. Text level
        # with but BESIDE a figure (centre outside the tight box) is wrap text and is kept.
        if figure_regions:
            # (a) drop analyzer-emitted text blocks sitting wholly inside a figure (span-
            # backed case: a resolvable bbox proves containment). A SPAN-LESS block (no
            # source_span_ids resolve to a real extracted span at all) carries no bbox to
            # test containment with — that's the vision analyzer reading a label BAKED into
            # the figure's own pixels/vector art (no PDF text run exists for it) and
            # transcribing it as if it were a separate block. The narrowest available signal
            # for that case is adjacency: it sits immediately next to a figure in the
            # analyzer's OWN per-page reading order (`local`, not yet y-sorted).
            kept: list[tuple[float | None, object]] = []
            for i, (ty, blk) in enumerate(local):
                if isinstance(blk, (ParagraphBlock, CaptionBlock, ListItemBlock)):
                    if _spans_inside_figures(
                            getattr(blk, "source_span_ids", None) or [], by_id, figure_regions):
                        continue
                # CaptionBlock is deliberately excluded here: the reconciler itself
                # synthesizes a span-less CaptionBlock immediately before a FigureBlock when
                # an unreliable table is converted to an image (its own caption, see the
                # `_table_structure_unreliable` branch above) — indistinguishable from a
                # hallucinated label by type + adjacency alone, and a real (passing) test
                # pins that pairing. paragraph/heading/list_item have no such legitimate
                # span-less-and-figure-adjacent construction anywhere in this file.
                if (
                    isinstance(blk, (ParagraphBlock, ListItemBlock, HeadingBlock))
                    and _no_source_spans(getattr(blk, "source_span_ids", None) or [], by_id)
                    and _adjacent_to_figure(local, i)
                ):
                    report.warnings.append(
                        f"dropped a span-less text block adjacent to a figure on page "
                        f"{page_index} (probable baked-in figure label): "
                        f"{getattr(blk, 'text', '')!r}"
                    )
                    continue
                kept.append((ty, blk))
            local = kept
            # (b) keep the recovery net from re-emitting the same in-figure spans as text
            for s in page_spans:
                if s.id not in referenced and _spans_inside_figures(
                        [s.id], by_id, figure_regions):
                    referenced.add(s.id)
        for top_y, line_ids, text in _recover_unassigned(page_spans, referenced, by_id):
            referenced.update(line_ids)
            # Carry the source styling so a recovered line keeps its colour / alignment /
            # font — otherwise a page the analyzer missed entirely (rebuilt by this net)
            # loses its blue heading, centred revision block, etc. Same helpers the normal
            # paragraph path uses.
            r_size, r_bold = _dominant_style(line_ids, by_id)
            r_font, r_color, r_italic = _dominant_inline(line_ids, by_id)
            extras.append(
                (
                    top_y,
                    ParagraphBlock(
                        text=text,
                        page=page_index,
                        font_size=r_size,
                        bold=r_bold,
                        font=r_font,
                        color=r_color,
                        italic=r_italic,
                        align=_align(line_ids, by_id, page_w),
                        line_spacing=_line_spacing(line_ids, by_id, r_size),
                        indent=_indent(line_ids, by_id, page_left),
                        source_span_ids=line_ids,
                    ),
                )
            )
            recovered_n += 1
        if recovered_n:
            report.warnings.append(
                f"recovered {recovered_n} unassigned line(s) as text on page {page_index}"
            )
        # (b) horizontal rules, minus those inside a table/figure region (borders) or in
        #     the header/footer band (reproduced with the running header/footer chrome).
        top_band = 0.1 * page_h if page_h else 0.0
        bot_band = 0.9 * page_h if page_h else 0.0
        for y, _x0, _x1, thickness, color in (pg.hrules if pg else []):
            if page_h and (y < top_band or y > bot_band):
                continue
            if not _suppressed(y, occupied):
                extras.append(
                    (y, DividerBlock(page=page_index, thickness=thickness, color=color))
                )

        # A fully-designed cover (no extractable text) is kept as a faithful background image;
        # drop its vision-read blocks so the translated text doesn't double the baked-in title.
        if page_index == 0 and cover_bg is not None and not page_spans:
            if local or extras:
                report.warnings.append(
                    f"cover page 0: dropped {len(local) + len(extras)} vision-read block(s) "
                    f"(treated as a full-bleed cover image)")
            local, extras = [], []
        # A TEXT cover (title + a few subtitle/doc-number lines) keeps its blocks — the
        # original often spaces them out deliberately (title ~1/3 down the page, etc). That
        # rhythm carries no other signal in the semantic model, so capture it here as each
        # block's measured top gap (page-0-cover ONLY; a normal page's blocks get none).
        is_cover_page = page_index == 0 and cover_bg is not None
        if is_cover_page:
            if local:
                _apply_cover_rhythm(local, doc_margin_top, by_id)
        else:
            # Any other page (including a non-cover page 0): measure sparse-page vertical
            # whitespace into `page_space_before` instead — see `_apply_page_rhythm`'s gate.
            # A page's own TableBlock disqualifies it (unreliable rebuilt row heights).
            has_table = any(isinstance(blk, TableBlock) for _, blk in local)
            _apply_page_rhythm(local, page_index, doc_margin_top, by_id, has_table)
        blocks.extend(_merge_by_y(local, extras))

    # --- coverage gate -------------------------------------------------------
    report.total_spans = len(by_id)
    for sid, span in by_id.items():
        if sid in referenced:
            report.covered_spans += 1
        else:
            report.missing_span_ids.append(sid)
            report.missing_texts.append(span.text)
    if report.missing_span_ids:
        report.warnings.append(
            f"{len(report.missing_span_ids)} span(s) not assigned to any block"
        )

    blocks = _stitch_across_pages(blocks, report)

    header_band = _pick_band(header_items, _band_rule(raw, top=True))
    footer_band = _pick_band(footer_items, _band_rule(raw, top=False))
    # Phase 2: capture repeating decorative strips (band/logo, footer bar) as behind-text
    # images, and the page-number side as a PAGE field. Captured once from a representative
    # body page; only kept when the strip actually carries decoration.
    rep = _representative_chrome_page(raw)
    if rep is not None:
        hdr_img = _capture_band_image(pdf_path, raw.pages[rep], rep, asset_dir, top=True)
        if hdr_img:
            header_band = header_band or RunningBand()
            header_band.image = hdr_img
        ftr_img = _capture_band_image(pdf_path, raw.pages[rep], rep, asset_dir, top=False)
        if ftr_img:
            footer_band = footer_band or RunningBand()
            footer_band.image = ftr_img
    pn = _page_number_spec(footer_items)
    if pn:
        footer_band = footer_band or RunningBand()
        footer_band.page_number, footer_band.page_number_format = pn

    doc = SemanticDocument(
        source_lang=source_lang,
        blocks=blocks,
        title=_guess_title(blocks),
        running_header=header_band,
        running_footer=footer_band,
        cover_background=cover_bg,
        **_page_geometry(raw),
    )
    return doc, report


def _clamp_margin(v: float) -> float:
    """Keep an estimated margin in a sane range (0.25in .. 1.5in)."""
    return round(min(108.0, max(18.0, v)), 1)


def _page_geometry(raw: RawDocument) -> dict:
    """Source paper size (pt) from the first page + content margins estimated from where
    body text actually sits (robust 5th/95th percentiles of span bboxes across all pages).
    The renderer reproduces these so the translation paginates on the same paper as the
    source instead of defaulting to US Letter."""
    if not raw.pages:
        return {}
    pw = round(raw.pages[0].width, 1)
    ph = round(raw.pages[0].height, 1)
    x0s, x1s, y0s, y1s = [], [], [], []
    for p in raw.pages:
        for s in p.spans:
            x0s.append(s.bbox[0]); x1s.append(s.bbox[2])
            y0s.append(s.bbox[1]); y1s.append(s.bbox[3])
    if not x0s:
        return {"page_width": pw, "page_height": ph}

    def pct(vals: list[float], q: float) -> float:
        v = sorted(vals)
        return v[min(len(v) - 1, max(0, int(q * len(v))))]

    return {
        "page_width": pw,
        "page_height": ph,
        "margin_left": _clamp_margin(pct(x0s, 0.05)),
        "margin_right": _clamp_margin(pw - pct(x1s, 0.95)),
        "margin_top": _clamp_margin(pct(y0s, 0.05)),
        "margin_bottom": _clamp_margin(ph - pct(y1s, 0.95)),
    }


def _stitch_across_pages(blocks: list, report: ReconcileReport) -> list:
    """Conservatively merge paragraphs/tables that were split by a page break."""
    merged: list = []
    for block in blocks:
        if merged:
            prev = merged[-1]
            across_break = (
                getattr(prev, "page", None) is not None
                and getattr(block, "page", None) is not None
                and block.page != prev.page
            )
            # paragraph continuation across a page break: previous didn't end a sentence.
            # Same-page adjacency is left intact — those are genuinely separate paragraphs
            # (and reflowing already wraps within each), and merging them drops breaks.
            if (
                across_break
                and isinstance(prev, ParagraphBlock)
                and isinstance(block, ParagraphBlock)
                and prev.text
                and not prev.lines  # don't fold a break-preserving block into prose
                and not block.lines
                and not _SENTENCE_END.search(prev.text)
            ):
                prev.text = (prev.text.rstrip() + " " + block.text.lstrip()).strip()
                prev.source_span_ids += block.source_span_ids
                continue
            # table continuation across a page break: same column count, no new header row
            if (
                across_break
                and isinstance(prev, TableBlock)
                and isinstance(block, TableBlock)
                and prev.rows
                and block.rows
                and _ncols(prev.rows[-1]) == _ncols(block.rows[0])
                and not any(c.is_header for c in block.rows[0])
            ):
                prev.rows.extend(block.rows)
                prev.source_span_ids += block.source_span_ids
                report.warnings.append("merged table split across page break")
                continue
        merged.append(block)
    return merged


def _as_int(v, default: int) -> int:
    """Coerce untrusted analyzer output to int, falling back on bad/missing values."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


_HEX6_RE = re.compile(r"^[0-9A-Fa-f]{6}$")

# Heavy named weights PyMuPDF often doesn't flag as bold (Franklin Gothic Medium/Demi, etc.).
# Used to recover a HEADING's weight from its font name so the weight tier survives.
_HEAVY_WEIGHT_RE = re.compile(r"bold|semibold|demibold|demi|medium|black|heavy", re.IGNORECASE)


def _hex6(v) -> str | None:
    """Validate an analyzer-supplied colour to a 6-hex string, else None (hardening)."""
    return v.upper() if isinstance(v, str) and _HEX6_RE.match(v) else None


def _cell_align(v) -> str | None:
    return v if v in ("left", "center", "right", "justify") else None


def _cell_valign(v) -> str | None:
    return v if v in ("top", "center", "bottom") else None


def _ncols(row: list[TableCell]) -> int:
    return sum(c.colspan for c in row)


def _is_vertical_cell(lines: list[str]) -> bool:
    """A cell whose source text is written vertically: ≥3 lines, each a single character
    (e.g. '개'/'정'/'내'/'역' stacked). Such a label should read rotated, not line-by-line."""
    items = [s.strip() for s in lines if s.strip()]
    if len(items) < 3:
        return False
    return all(len(s) == 1 for s in items)


def _absorb_vertical_labels(page_spans: list, referenced: set, by_id: dict,
                            empty_label_cells: list[dict]) -> None:
    """Fold a leaked vertical-stack label back into its blank row-spanning header cell.

    The analyzer sometimes emits a tall row-spanning label cell empty (no text/spans), so
    its stacked characters end up unassigned and would each be recovered as a separate
    single-char paragraph. For each such empty cell, gather the still-unassigned spans that
    sit inside the table's y-range at its left edge; if they form a genuine vertical stack
    (≥3 tightly x-aligned single characters) fill the cell with them and mark them
    referenced. The trigger is deliberately narrow so simple tables and real orphaned text
    are untouched."""
    for meta in empty_label_cells:
        cell = meta["cell"]
        if cell.text:  # already filled
            continue
        ry0, ry1 = meta["yr"]
        left_x = meta["left_x"]
        cands = [
            s for s in page_spans
            if s.id not in referenced
            and ry0 - 2 <= 0.5 * (s.bbox[1] + s.bbox[3]) <= ry1 + 2
            and (left_x is None or s.bbox[0] <= left_x + 15)
        ]
        if len(cands) < 3:
            continue
        cands.sort(key=lambda s: s.bbox[1])  # top-to-bottom reading order
        chars = [s.text.strip() for s in cands]
        xs = [s.bbox[0] for s in cands]
        if max(xs) - min(xs) > 20:  # not a tight vertical stack (varying x → real text)
            continue
        if not all(len(c) == 1 for c in chars):  # 1 char per line → a stacked label
            continue
        cell.text = "".join(chars)
        cell.source_span_ids = [s.id for s in cands]
        cell.vertical = True
        referenced.update(s.id for s in cands)


def _dominant_style(ids: list[str], by_id: dict, heading: bool = False):
    """Representative font size (pt) + bold for a block, from its source spans.
    Headings take the largest span (the title size); body text takes the median
    (robust to stray inline sizes). Returns (None, None) when no spans are known."""
    spans = [by_id[i] for i in ids if i in by_id]
    sizes = [s.size for s in spans if s.size]
    bolds = [s.bold for s in spans]
    size = None
    if sizes:
        size = round(max(sizes) if heading else statistics.median(sizes), 1)
    bold = None
    if bolds:
        bold = sum(1 for b in bolds if b) * 2 >= len(bolds)  # majority
    # Headings often use a heavier NAMED weight (Medium/Demi/Semibold) that PyMuPDF does
    # not flag as bold; recover it from the font name so the heading weight tier survives
    # translation (the substitute font is binary-weight). Body text keeps the flag/name
    # behaviour above — a "Medium" body font is not forced bold.
    if heading and not bold:
        names = [(s.font or "") for s in spans if s.font]
        if names and sum(1 for n in names if _HEAVY_WEIGHT_RE.search(n)) * 2 >= len(names):
            bold = True
    return size, bold


def _is_ink(color: int) -> bool:
    """True when a packed RGB colour is ordinary black / near-black ink (every channel dark),
    so we don't carry it as a meaningful colour. Brightness in ANY channel — a blue, green, or
    red heading — counts as coloured. (The old packed-int threshold mis-classified non-red
    colours: pure blue 0x0000FF is the small int 255, below a 0x404040 gray, so it was wrongly
    dropped — losing blue/green headings entirely.)"""
    r, g, b = (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
    return max(r, g, b) <= 0x40


def _norm_font(name: str) -> str | None:
    """Map a PyMuPDF font name to a universal family by class: drop the subset prefix
    ("ABCDEF+Batang" → "Batang") then classify sans → "Arial" / serif → "Times New
    Roman" (via models.universal_font). The translated text renders with the right
    sans/serif character in any viewer, regardless of installed Korean fonts."""
    if not name:
        return None
    if "+" in name:
        name = name.split("+", 1)[1]
    return universal_font(name)


def _dominant_inline(ids: list[str], by_id: dict):
    """Block-level font family, colour, and italic from the source spans. font/colour are
    the most common non-ink value; italic is inferred from the font name (PyMuPDF doesn't
    give us a reliable flag, matching how bold is already derived). Returns (font, color,
    italic) with None where unknown."""
    spans = [by_id[i] for i in ids if i in by_id]
    if not spans:
        return None, None, None
    fonts = [f for s in spans if (f := _norm_font(s.font))]
    font = Counter(fonts).most_common(1)[0][0] if fonts else None
    colors = [s.color for s in spans
              if isinstance(s.color, int) and not _is_ink(s.color & 0xFFFFFF)]
    color = f"{Counter(colors).most_common(1)[0][0] & 0xFFFFFF:06X}" if colors else None
    raw_fonts = [s.font.lower() for s in spans if s.font]
    italic = (sum(1 for f in raw_fonts if "italic" in f or "oblique" in f) * 2
              >= len(raw_fonts)) if raw_fonts else None
    return font, color, (italic or None)


def _marker_color(ids: list[str], by_id: dict, marker: str | None) -> str | None:
    """Colour of the marker's own leading source spans (e.g. an accent-coloured
    'FIGURE 3' label), which can differ from the block's dominant colour. Returns hex
    'RRGGBB', or None when the leading text isn't the marker or carries no non-ink colour."""
    if not marker:
        return None
    spans = [by_id[i] for i in ids if i in by_id and (by_id[i].text or "").strip()]
    if not spans:
        return None
    spans.sort(key=lambda s: (round(s.bbox[1] / 3.0), s.bbox[0]))  # reading order (y-band, x)
    target = "".join(marker.split())
    acc, marker_spans = "", []
    for s in spans:
        acc += "".join((s.text or "").split())
        marker_spans.append(s)
        if len(acc) >= len(target):
            break
    if not target or not acc.startswith(target):
        return None
    cols = [s.color for s in marker_spans
            if isinstance(s.color, int) and not _is_ink(s.color & 0xFFFFFF)]
    if not cols:
        return None
    return f"{Counter(cols).most_common(1)[0][0] & 0xFFFFFF:06X}"


def _justified(ids: list[str], by_id: dict) -> bool:
    """True when a block's lines fill to the same right edge (justified body text): ≥2
    lines and ≥80% of the non-final lines reach within 3% of the column's right edge."""
    lines = _recover_lines(ids, by_id)
    if len(lines) < 2:
        return False
    right = max(x1 for _, _, x1 in lines)
    left = min(x0 for _, x0, _ in lines)
    col_w = right - left
    if col_w <= 0:
        return False
    inner = [x1 for _, _, x1 in lines[:-1]]
    filled = sum(1 for x1 in inner if (right - x1) < 0.03 * col_w)
    return filled >= 0.8 * len(inner)


def _align(ids: list[str], by_id: dict, page_w: float):
    """Infer paragraph alignment from the block's horizontal position on the page.
    Centered = roughly equal left/right margins; right = hugging the right edge.
    Returns None for ordinary left-aligned / full-width text."""
    x0 = [by_id[i].bbox[0] for i in ids if i in by_id]
    x1 = [by_id[i].bbox[2] for i in ids if i in by_id]
    if not x0 or not page_w:
        return None
    lm = max(0.0, min(x0))
    rm = max(0.0, page_w - max(x1))
    if (max(x1) - min(x0)) > 0.8 * page_w:  # spans the line → justified or plain left
        return "justify" if _justified(ids, by_id) else None
    if lm > 0.18 * page_w and abs(lm - rm) < 0.12 * page_w:
        return "center"
    if rm < 0.12 * page_w and lm > 0.25 * page_w:
        return "right"
    return None


# span id format is p{page}b{block}l{line}s{span} → everything before `s` keys a line.
_LINE_RE = re.compile(r"^(p\d+b\d+l\d+)s\d+$")


def _recover_lines(ids: list[str], by_id: dict) -> list[tuple[str, float, float]]:
    """Reconstruct the original lines of a block from its spans (grouped by line id),
    in reading order. Returns [(text, x0, x1)] — x0/x1 are the line's left/right edges."""
    groups: dict[str, list] = {}
    for i in ids:
        s = by_id.get(i)
        if not s:
            continue
        m = _LINE_RE.match(i)
        groups.setdefault(m.group(1) if m else i, []).append(s)
    lines = []
    for spans in groups.values():
        spans.sort(key=lambda s: s.bbox[0])  # left → right within the line
        text = re.sub(r"\s+", " ", " ".join(s.text for s in spans)).strip()
        if not text:
            continue
        y = min(s.bbox[1] for s in spans)
        x0 = min(s.bbox[0] for s in spans)
        x1 = max(s.bbox[2] for s in spans)
        lines.append((y, x0, x1, text))
    lines.sort(key=lambda t: (t[0], t[1]))  # top → bottom
    return [(t[3], t[1], t[2]) for t in lines]


def _line_spacing(ids: list[str], by_id: dict, font_size: float | None) -> float | None:
    """Leading as a multiple of font size, from the gaps between consecutive line tops."""
    if not font_size:
        return None
    tops: dict[str, float] = {}
    for i in ids:
        s = by_id.get(i)
        if not s:
            continue
        m = _LINE_RE.match(i)
        key = m.group(1) if m else i
        tops[key] = min(tops.get(key, s.bbox[1]), s.bbox[1])
    ys = sorted(tops.values())
    deltas = [b - a for a, b in zip(ys, ys[1:]) if b - a > 0]
    if not deltas:
        return None
    return round(statistics.median(deltas) / font_size, 2)


def _preserve_breaks(lines: list[tuple[str, float, float]]) -> bool:
    """True when a block's line breaks are SEMANTIC (list/legend/address/TOC) rather than
    body wrap: each line is its own unit and must stay on its own line. Body fills to the
    right → reflow; centered titles vary their left → reflow."""
    if len(lines) < 2:
        return False
    # TOC / index-style rows reach the right edge (dot leaders + page number), so the
    # ragged-right test below misses them — detect them by content first.
    if _looks_tabular([t for t, _, _ in lines]):
        return True
    x0s = [x0 for _, x0, _ in lines]
    x1s = [x1 for _, _, x1 in lines]
    left = min(x0s)
    col_right = max(x1s)
    col_w = col_right - left
    if col_w <= 0:
        return False
    left_aligned = (max(x0s) - min(x0s)) < 0.15 * col_w
    short = sum(1 for _, _, x1 in lines[:-1] if (col_right - x1) > 0.2 * col_w)
    ragged = short >= 0.6 * (len(lines) - 1)
    return left_aligned and ragged


_DOT_LEADER = re.compile(r"(\.{2,}|…|·{2,})\s*\d+\s*$")  # "... 5", "…… 12"
_ENDS_NUM = re.compile(r"\d+\s*$")
_STARTS_SECTION = re.compile(
    r"^\s*(\d+(?:\.\d+)*\.?|[IVXLCDM]+\.|[A-Z]\.|제?\s*\d+\s*(?:장|절|조|항))\b"
)


def _looks_tabular(texts: list[str]) -> bool:
    """A TOC / list-of-figures / index block: each line is a labelled entry that must
    stay on one line. Two signals (either suffices): dot-leader+page-number on most
    lines, or numbered-section starts that end in a page number on most lines."""
    if len(texts) < 2:
        return False
    if sum(1 for t in texts if _DOT_LEADER.search(t)) >= 0.5 * len(texts):
        return True
    ends_num = sum(1 for t in texts if _ENDS_NUM.search(t))
    starts_sec = sum(1 for t in texts if _STARTS_SECTION.match(t))
    return ends_num >= 0.6 * len(texts) and starts_sec >= 0.5 * len(texts)


def _page_left_margin(spans) -> float | None:
    """The page's body text left margin: the most common line-start x0. Indented blocks
    measure their offset against this."""
    xs = [round(s.bbox[0]) for s in spans if s.text.strip()]
    if not xs:
        return None
    return float(Counter(xs).most_common(1)[0][0])


def _top_y(ids: list[str], by_id: dict) -> float | None:
    ys = [by_id[i].bbox[1] for i in ids if i in by_id]
    return min(ys) if ys else None


def _y_range(ids: list[str], by_id: dict) -> tuple[float, float] | None:
    spans = [by_id[i] for i in ids if i in by_id]
    if not spans:
        return None
    return (min(s.bbox[1] for s in spans), max(s.bbox[3] for s in spans))


def _bbox(ids: list[str], by_id: dict):
    """Bounding box (x0, y0, x1, y1) of the given spans, or None."""
    xs, ys = [], []
    for i in ids:
        s = by_id.get(i)
        if s:
            xs += [s.bbox[0], s.bbox[2]]
            ys += [s.bbox[1], s.bbox[3]]
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def _img_area(b) -> float:
    return (b[2] - b[0]) * (b[3] - b[1])


def _covering_image(page, tb_bbox):
    """The page image meaning a detected "table" is really a rendered graphic — its drawings
    (and often its text too) live in the image, not in extractable spans — so it should be
    emitted as a figure rather than a text grid with empty/missing cells. Returns the image
    (largest qualifying), else None.

    Two cases:
      (a) the table HAS text spans, but a page image covers >= 85% of their region;
      (b) the table has NO spans at all (its text was read off the graphic by the vision
          model) and the page carries a substantial image (>= 10% of the page area).
    A normal text table has real spans and no large image over them, so neither case fires."""
    if not page:
        return None
    images = getattr(page, "images", [])
    if not images:
        return None
    if tb_bbox is None:  # case (b): fully-graphic table, no extractable text
        page_area = max(page.width * page.height, 1.0)
        big = [im for im in images if _img_area(im.bbox) >= 0.10 * page_area]
        return max(big, key=lambda im: _img_area(im.bbox)) if big else None
    tx0, ty0, tx1, ty1 = tb_bbox  # case (a): image covers the table's text region
    t_area = max((tx1 - tx0) * (ty1 - ty0), 1.0)
    best, best_area = None, 0.0
    for im in images:
        ix0, iy0, ix1, iy1 = im.bbox
        ox0, oy0, ox1, oy1 = max(tx0, ix0), max(ty0, iy0), min(tx1, ix1), min(ty1, iy1)
        if ox1 <= ox0 or oy1 <= oy0:
            continue  # no overlap
        inter = (ox1 - ox0) * (oy1 - oy0)
        if inter >= 0.85 * t_area and _img_area(im.bbox) > best_area:
            best, best_area = im, _img_area(im.bbox)
    return best


def _best_grid(grids, bbox):
    """The PyMuPDF (table-bbox, bounds, rebuilt-rows) whose table best overlaps `bbox`, else
    (None, None, None). `grids` are (bbox, bounds, rows) triples from detect_table_grids."""
    if not bbox:
        return None, None, None
    bx0, by0, bx1, by1 = bbox
    best, best_ov = (None, None, None), 0.0
    for tbbox, bounds, struct in grids:
        tx0, ty0, tx1, ty1 = tbbox
        ov = (max(0.0, min(bx1, tx1) - max(bx0, tx0))
              * max(0.0, min(by1, ty1) - max(by0, ty0)))
        if ov > best_ov:
            best_ov, best = ov, (tbbox, bounds, struct)
    return best if best_ov > 0 else (None, None, None)


def _fractions(bounds):
    """Relative widths of the columns between consecutive grid lines, or None if degenerate."""
    w = [bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1)]
    total = sum(w)
    return [round(x / total, 4) for x in w] if total > 0 and min(w) > 0 else None


def _rebuild_table_rows(struct):
    """Convert detect_table_grids' row structs into TableCell rows (real merges preserved)."""
    return [
        [TableCell(text=c["text"], colspan=max(1, c["colspan"]),
                   rowspan=max(1, c["rowspan"])) for c in row]
        for row in struct
    ]


def _should_rebuild(struct, vision_rows) -> bool:
    """Rebuild a vision table from the PyMuPDF grid only when that grid is a solid table —
    at least as many rows as vision found and roughly one cell per row on average — so we
    never replace a good vision table with a worse detection."""
    if not struct or len(struct) < max(2, len(vision_rows)):
        return False
    cells = sum(len(r) for r in struct)
    return cells >= len(struct) and any(c["text"] for r in struct for c in r)


def _cell_text_set(rows) -> set[str]:
    """Whitespace-stripped non-empty cell texts of a table (vision rows or rebuilt rows)."""
    out: set[str] = set()
    for row in rows:
        for c in row:
            t = re.sub(r"\s+", "", c.text or "")
            if t:
                out.add(t)
    return out


def _rebuild_text_overlap(rebuilt, vision_rows) -> float:
    """Fraction of the PyMuPDF-rebuilt table's non-empty cell texts that also appear in the
    vision table. High overlap ⇒ the detected grid describes the SAME table, so its real
    column geometry can be trusted; low overlap ⇒ find_tables locked onto a different region
    and we keep the vision table rather than swap in structurally-plausible but wrong cells."""
    rb = _cell_text_set(rebuilt)
    if not rb:
        return 0.0
    vis = _cell_text_set(vision_rows)
    return sum(1 for t in rb if t in vis) / len(rb)


_TABLE_RAGGED_FRACTION = 0.3


def _table_structure_unreliable(rows) -> bool:
    """True when the vision grid is too inconsistent to render as a real table: on a
    5+ row table, the per-row EFFECTIVE column count disagrees with the modal count
    on >=30% of rows (or the modal is 0). A row's effective width credits columns
    still covered by an open rowspan from earlier rows (row-grouped tables are
    well-formed, not ragged); genuinely self-contradictory grids (double-claimed
    columns) still disagree and fall back to an image of the original region.

    A second, independent check catches a defect the ragged-fraction test alone can
    miss: a rowspan declared too large (e.g. a nominal-size cell in a row-grouped table
    claiming 4 rows when its group is really 2) makes EVERY subsequent row's carry-
    credited width overshoot by the same fixed amount — a *consistent* wrong width, not
    a ragged one, so the >=30% fraction never trips. In a well-formed row-grouped table
    the modal credited width always equals the table's real column count (the widest
    row's own colspan sum, ignoring carry); if the modal width EXCEEDS that, some
    rowspan is double-claiming a column that another row also claims on its own —
    provably self-contradictory regardless of how the raggedness happens to distribute."""
    if len(rows) < 5:
        return False
    widths: list[int] = []
    carry: list[int] = []  # per grid column: rows still covered by an earlier rowspan
    for r in rows:
        carried = sum(1 for c in carry if c > 0)  # count coverage BEFORE decrementing
        widths.append(carried + sum(c.colspan for c in r))
        ci = 0
        for cell in r:  # place this row's cells into the free columns, left to right
            while ci < len(carry) and carry[ci] > 0:
                ci += 1
            for _ in range(max(1, cell.colspan)):
                if ci < len(carry):
                    carry[ci] = max(cell.rowspan, 0)
                else:
                    carry.append(max(cell.rowspan, 0))
                ci += 1
        carry = [c - 1 for c in carry]  # decrement the whole row once, at end-of-row
    modal = Counter(widths).most_common(1)[0][0]
    if modal <= 0:
        return True
    ragged = sum(1 for w in widths if w != modal)
    if ragged / len(widths) >= _TABLE_RAGGED_FRACTION:
        return True
    true_ncols = max((sum(c.colspan for c in r) for r in rows), default=0)
    return modal > true_ncols


def _indent(ids: list[str], by_id: dict, page_left: float | None) -> float | None:
    """Left indent (pt) of a block relative to the page text margin. None if negligible
    (< ~0.13in) so ordinary body paragraphs aren't needlessly indented."""
    if page_left is None:
        return None
    xs = [by_id[i].bbox[0] for i in ids if i in by_id]
    if not xs:
        return None
    ind = min(xs) - page_left
    # Below ~0.13in: ordinary body text. Above ~2.5in: almost always a misgrouped
    # far-right span, not a real indent — fall back to the margin rather than off-page.
    return round(ind, 1) if 9 < ind <= 180 else None


def _figure_align(bbox, page_w: float) -> str | None:
    """Horizontal placement of a figure from its box: centered (balanced margins),
    right (hugs the right edge), else None (=left)."""
    if not page_w:
        return None
    x0, x1 = bbox[0], bbox[2]
    lm = max(0.0, x0)
    rm = max(0.0, page_w - x1)
    if (x1 - x0) > 0.85 * page_w:  # full-width → leave default
        return None
    if lm > 0.12 * page_w and abs(lm - rm) < 0.12 * page_w:
        return "center"
    if rm < 0.08 * page_w and lm > 0.2 * page_w:
        return "right"
    return None


def _figure_wrap(bbox, page_spans) -> str | None:
    """If body text sits beside the figure (same vertical band, clearly to one side),
    return the side the image should float to so that text wraps alongside it:
    text on the right → float 'left'; text on the left → float 'right'."""
    fx0, fy0, fx1, fy1 = bbox[0], bbox[1], bbox[2], bbox[3]
    left = right = 0
    for s in page_spans:
        sy = (s.bbox[1] + s.bbox[3]) / 2
        if not (fy0 < sy < fy1) or not s.text.strip():
            continue
        if s.bbox[2] <= fx0 + 1:       # span entirely left of the figure
            left += 1
        elif s.bbox[0] >= fx1 - 1:     # span entirely right of the figure
            right += 1
    if max(left, right) < 2:  # not enough neighbouring text to bother wrapping
        return None
    return "left" if right >= left else "right"


def _suppressed(y: float, occupied: list[tuple[float, float]], pad: float = 4.0) -> bool:
    """True if a rule sits inside a table/figure region (its borders, not a separator)."""
    return any(y0 - pad <= y <= y1 + pad for y0, y1 in occupied)


# A lined table's own top/bottom borders sit ~one cell-pad (a small CONSTANT, ~3–10pt,
# independent of table height) beyond its text. When PyMuPDF detects no grid bbox we pad the
# text y-range by this so those borders are still masked from the hrule→divider pass, while a
# genuine separator a full blank line away (>~16pt) is left to become a real divider.
_TABLE_BORDER_PAD = 10.0


def _table_occupied_range(yr: tuple[float, float], gbbox) -> tuple[float, float]:
    """The y-span to mask from the horizontal-rule pass for a table. Prefer the detected grid
    bbox (it covers the real border lines); when absent, fall back to the text range padded by
    a small constant so the table's own borders don't leak as spurious dividers."""
    y0, y1 = yr
    if gbbox:
        return (min(y0, gbbox[1]), max(y1, gbbox[3]))
    return (y0 - _TABLE_BORDER_PAD, y1 + _TABLE_BORDER_PAD)


def _spans_inside_figures(ids, by_id: dict, regions) -> bool:
    """True iff every given span's centre falls inside one of the figure regions (and there
    is at least one resolvable span). Used to suppress text already shown by a figure image
    — a flowchart's box labels, a diagram's callouts — so it isn't echoed as floating text."""
    centers = [
        ((by_id[i].bbox[0] + by_id[i].bbox[2]) / 2, (by_id[i].bbox[1] + by_id[i].bbox[3]) / 2)
        for i in ids if i in by_id
    ]
    return bool(centers) and all(
        any(fx0 <= cx <= fx1 and fy0 <= cy <= fy1 for fx0, fy0, fx1, fy1 in regions)
        for cx, cy in centers
    )


def _no_source_spans(ids, by_id: dict) -> bool:
    """True iff NONE of the given ids resolve to a real extracted span — the block carries
    no grounding in the source PDF's text layer at all (empty source_span_ids, or every id
    is unknown/hallucinated). The complement of `_spans_inside_figures`, which requires at
    least one resolvable span; a block can fail both (e.g. no ids resolve, so it's neither
    "inside" nor "outside" a figure by that test) — that's exactly the span-less case a
    baked-in figure label produces."""
    return not any(i in by_id for i in ids)


def _adjacent_to_figure(local: list, i: int) -> bool:
    """True iff the entry immediately before or after index i in the page's OWN reading
    order (`local`, in the vision analyzer's emission order — not yet y-sorted) is a
    figure. A span-less text block only gets the figure-leak treatment when it sits right
    next to a figure in the analyzer's own block sequence: the layout signature of a baked
    figure label the analyzer transcribed as though it were a separate paragraph (no bbox
    is available for a span-less block, so containment can't be tested directly)."""
    for j in (i - 1, i + 1):
        if 0 <= j < len(local) and isinstance(local[j][1], FigureBlock):
            return True
    return False


def _recover_unassigned(page_spans, referenced: set, by_id: dict):
    """Yield (top_y, line_ids, text) for every source line the analyzer left unassigned,
    so no text is ever silently dropped. One line per yielded paragraph."""
    unref = [s for s in page_spans if s.id not in referenced]
    groups: dict[str, list] = {}
    for s in unref:
        m = _LINE_RE.match(s.id)
        groups.setdefault(m.group(1) if m else s.id, []).append(s)
    for spans in groups.values():
        spans.sort(key=lambda s: s.bbox[0])
        text = re.sub(r"\s+", " ", " ".join(s.text for s in spans)).strip()
        if not text:
            continue
        yield (min(s.bbox[1] for s in spans), [s.id for s in spans], text)


def _apply_cover_rhythm(
    local: list[tuple[float | None, object]],
    top_margin: float | None,
    by_id: dict,
) -> None:
    """Cover page (page 0) ONLY: reproduce the source's deliberate vertical rhythm — a
    designed cover's title/subtitle often sit well below the page top, with large gaps that
    carry no other signal in the semantic model (blocks have no y geometry once reconciled).
    Sets `space_before` (pt) on each qualifying block in place; a non-cover page never calls
    this, so its blocks are unaffected (space_before stays None, unchanged rendering).

    Gap = this block's top y minus the previous block's bottom y. "Bottom" = the max y1 among
    the block's own `source_span_ids` spans (the actual glyph ink); a block with no resolvable
    spans (e.g. a chrome-derived cover paragraph co-opted from the header/footer detector,
    which carries a cluster y but an EMPTY span list) can't anchor a real bottom — rather than
    guess a nominal line height, we simply fall back to that block's own top y (zero measured
    height), which is the simplest option that still lets the NEXT block's gap be measured
    from something reasonable. The very first block instead measures from `top_margin` (the
    document's normal body top margin) since the cover has no real previous block at all.

    `local` is walked in y-order (sorted here) rather than assumed pre-sorted, since it holds
    the analyzer's reading order, not a geometric sort.
    """
    ordered = sorted((t for t in local if t[0] is not None), key=lambda t: t[0])
    if not ordered:
        return
    prev_bottom = top_margin
    for top_y, blk in ordered:
        if prev_bottom is not None:
            gap = top_y - prev_bottom
            if gap > COVER_RHYTHM_MIN_GAP and isinstance(
                blk, (HeadingBlock, ParagraphBlock, ListItemBlock, CaptionBlock)
            ):
                blk.space_before = round(min(COVER_RHYTHM_MAX_GAP, max(0.0, gap)), 1)
        ids = getattr(blk, "source_span_ids", None) or []
        bottoms = [by_id[i].bbox[3] for i in ids if i in by_id]
        prev_bottom = max(bottoms) if bottoms else top_y


def _apply_page_rhythm(
    local: list[tuple[float | None, object]],
    page_index: int,
    margin_top: float | None,
    by_id: dict,
    has_table: bool,
) -> None:
    """Non-cover page: measure a SPARSE body page's deliberate vertical whitespace (a
    disclaimer page whose text starts halfway down, a chart page with a large trailing
    blank run) into `page_space_before` — generalizes `_apply_cover_rhythm`'s span-bbox
    gap measurement (same per-block skip-noise/clamp thresholds: > COVER_RHYTHM_MIN_GAP,
    clamped to COVER_RHYTHM_MAX_GAP) from the page-0 cover to any body page. This stage
    runs before translation (no target_lang to gate on) — it only measures and stores;
    T3 (the renderer) decides whether/how to apply it. Deliberately NOT shared code with
    `_apply_cover_rhythm` (beyond the constants): the cover path is regression-protected
    by its own tests and has no page-level gate, so touching its walk to add one here
    would risk that behavior for no benefit.

    Page gate (ALL must hold, else the page is left untouched — no block's
    `page_space_before` is set):
      - `has_table` is False — a page carrying a TableBlock never qualifies. A rebuilt
        table's row heights don't reproduce the source's real render height, so a gap
        measured next to one can't be trusted.
      - no more than RHYTHM_MAX_TEXT_BLOCKS text blocks (heading/paragraph/list_item/
        caption) on the page — a dense page's gaps are ordinary paragraph-to-paragraph
        leading, not deliberate design whitespace.
      - at least one gap that is itself eligible to be WRITTEN (the first TEXT block's top
        gap from `margin_top`, or a gap ending at a TEXT block) is >= RHYTHM_MIN_GAP_PT.
        Proof a real deliberate gap exists on the page, as opposed to merely uniform (if
        generous) paragraph spacing — a large gap that ends at a figure/table boundary
        (never itself measured below) must not be able to qualify the page on its own.

    Measuring a block's bottom (the "previous bottom" the NEXT block's gap is taken from):
    a text block's own `source_span_ids` give the real glyph-ink bottom; a FigureBlock has
    no such field, but carries `height` from the same tight clipped bbox as its stored top
    y, so its real bottom (`top + height`) is used instead — otherwise a caption sitting a
    true 10pt below a tall chart would measure the whole top-of-figure-to-caption span.
    Any other block with neither spans nor a `height` leaves the baseline UNKNOWN (`None`):
    the following block's gap is then skipped rather than fabricated from a wrong fallback.

    `page_index` is accepted for parity with the call site's per-page loop (and any future
    per-page diagnostics); the measurement itself only needs the page's own blocks/geometry.
    """
    if has_table:
        return
    text_types = (HeadingBlock, ParagraphBlock, ListItemBlock, CaptionBlock)
    ordered = sorted((t for t in local if t[0] is not None), key=lambda t: t[0])
    if not ordered:
        return
    if sum(1 for _, blk in ordered if isinstance(blk, text_types)) > RHYTHM_MAX_TEXT_BLOCKS:
        return

    measured: list[tuple[object, float]] = []  # (block, clamped gap) for qualifying blocks
    gate_gap_seen = False
    prev_bottom = margin_top
    for top_y, blk in ordered:
        is_text = isinstance(blk, text_types)
        if prev_bottom is not None:
            gap = top_y - prev_bottom
            # Gate on exactly the gaps the setter below can ever WRITE (text-block gaps,
            # including the first block's margin-to-text gap) — a big gap that lands on a
            # figure/table boundary is never itself measured, so it must not be able to
            # qualify the whole page and drag along an unrelated (small but > the per-block
            # noise floor) text gap elsewhere on the page.
            if is_text and gap >= RHYTHM_MIN_GAP_PT:
                gate_gap_seen = True
            if is_text and gap > COVER_RHYTHM_MIN_GAP:
                measured.append((blk, round(min(COVER_RHYTHM_MAX_GAP, max(0.0, gap)), 1)))
        ids = getattr(blk, "source_span_ids", None) or []
        bottoms = [by_id[i].bbox[3] for i in ids if i in by_id]
        if bottoms:
            prev_bottom = max(bottoms)
        else:
            # No resolvable spans to anchor a real bottom. A FigureBlock carries no
            # `source_span_ids` field at all (see models.py) — but it DOES carry its own
            # `height`, measured from the same tight clipped bbox as `top_y` (both set at
            # the call site from the figure's real geometry). Reuse it so a caption sitting
            # a true 10pt below a 400pt-tall chart measures that true gap instead of the
            # top-of-figure-to-caption span (~410pt) getting clamped into a giant
            # COVER_RHYTHM_MAX_GAP whitespace. Any OTHER span-less, height-less block (e.g.
            # a chrome-derived paragraph) truly can't be measured — rather than guess (or
            # fall back to its own top, which is what caused the figure bug), leave the
            # baseline UNKNOWN so the NEXT block's gap is skipped rather than fabricated.
            height = getattr(blk, "height", None)
            prev_bottom = (top_y + height) if height is not None else None

    if not gate_gap_seen:
        return
    for blk, gap in measured:
        blk.page_space_before = gap


def _merge_by_y(main: list, extras: list) -> list:
    """Preserve the analyzer's reading order for its blocks, slotting each extra
    (recovered text / divider) in front of the first block at or below its y."""
    extras = sorted(extras, key=lambda t: t[0] if t[0] is not None else 1e9)
    out: list = []
    ei = 0
    for ty, blk in main:
        tref = ty if ty is not None else 1e9
        while ei < len(extras) and (extras[ei][0] if extras[ei][0] is not None else 1e9) <= tref:
            out.append(extras[ei][1])
            ei += 1
        out.append(blk)
    while ei < len(extras):
        out.append(extras[ei][1])
        ei += 1
    return out


def _chrome_side(xc: float, page_w: float) -> str:
    """Classify a chrome item's horizontal position into left/center/right."""
    if not page_w:
        return "center"
    if xc < 0.4 * page_w:
        return "left"
    if xc > 0.6 * page_w:
        return "right"
    return "center"


def _split_chrome_clusters(spans, page_w: float) -> list[tuple[str, float, float]]:
    """Group a chrome block's spans into visually separate runs by horizontal gap.

    The analyzer sometimes returns a page's whole header/footer line as ONE block
    ("Company   …   Confidential   Page 3 of 32"); classifying that by its median x
    buries the side structure and bakes run-on whitespace into one band cell. A gap
    wider than 8% of the page width (24pt when the width is unknown) starts a new
    cluster. Returns (text, x_center, y_center) per cluster, in x order."""
    items = sorted((s for s in spans if getattr(s, "bbox", None)), key=lambda s: s.bbox[0])
    gap = 0.08 * page_w if page_w else 24.0
    clusters: list[list] = []
    cur: list = []
    for s in items:
        if cur and s.bbox[0] - cur[-1].bbox[2] > gap:
            clusters.append(cur)
            cur = []
        cur.append(s)
    if cur:
        clusters.append(cur)
    out: list[tuple[str, float, float]] = []
    for cl in clusters:
        text = " ".join((s.text or "").strip() for s in cl if (s.text or "").strip())
        if not text:
            continue
        x0 = min(s.bbox[0] for s in cl)
        x1 = max(s.bbox[2] for s in cl)
        y = statistics.median((s.bbox[1] + s.bbox[3]) / 2 for s in cl)
        out.append((text, (x0 + x1) / 2, y))
    return out


def _pick_running(items: list, side: str) -> str | None:
    """The chrome text on `side` that repeats across the most pages (≥2). Ignores page
    numbers / tiny fragments so we reproduce the title, not '12'."""
    pages_by_text: dict[str, set] = {}
    for text, sd, pg in items:
        if sd != side:
            continue
        t = (text or "").strip()
        if len(t) < 2 or re.fullmatch(r"[\d\-\.\s/]+", t):  # skip page numbers etc.
            continue
        pages_by_text.setdefault(t, set()).add(pg)
    if not pages_by_text:
        return None
    text, pages = max(pages_by_text.items(), key=lambda kv: len(kv[1]))
    return text if len(pages) >= 2 else None  # must actually repeat


def _dedupe_band_sides(left, center, right):
    """Never render the same band text twice. Mixed analyzer output (merged one-block
    pages vs split left/right pages) can fill CENTER with the concatenation of LEFT
    and RIGHT via per-side voting — prefer the split sides and drop the restatement."""
    def norm(t):
        return re.sub(r"\s+", " ", t or "").strip()

    l, c, r = norm(left), norm(center), norm(right)
    if c and (c == l or c == r or (l and r and l in c and r in c)):
        center = None
    if l and r and l == r:
        right = None
    return left, center, right


def _pick_band(items: list, band_rule: tuple | None) -> RunningBand | None:
    """Build a header/footer band from positioned chrome items: the dominant repeating
    text per side, plus the band's recurring rule (thickness+colour). None if empty."""
    left = _pick_running(items, "left")
    center = _pick_running(items, "center")
    right = _pick_running(items, "right")
    left, center, right = _dedupe_band_sides(left, center, right)
    if not (left or center or right or band_rule):
        return None
    return RunningBand(
        left=left,
        center=center,
        right=right,
        rule=band_rule is not None,
        rule_size=band_rule[0] if band_rule else None,
        rule_color=band_rule[1] if band_rule else None,
    )


def _band_rule(raw: RawDocument, top: bool) -> tuple[float, str] | None:
    """The horizontal rule that recurs at a CONSISTENT position across a majority of pages
    in the header band (top 15%) or footer band (bottom 15%) — a real running rule, not a
    table/figure border that strays near a page edge on a few pages. Returns the rule's
    (thickness_pt, color_hex), or None."""
    n = len(raw.pages)
    if not n:
        return None
    recs: list[tuple[int, float, str]] = []  # (y-bucket, thickness, color)
    for pg in raw.pages:
        if not pg.height:
            continue
        band = [
            (y, th, col) for (y, _x0, _x1, th, col) in pg.hrules
            if (y < 0.15 * pg.height if top else y > 0.85 * pg.height)
        ]
        if not band:
            continue
        y, th, col = min(band) if top else max(band)  # rule nearest the page edge
        recs.append((round(y / 5) * 5, th, col))
    if not recs:
        return None
    yb, count = Counter(b for b, _, _ in recs).most_common(1)[0]
    if count < max(2, 0.5 * n):  # must recur on a majority of pages
        return None
    members = [(th, col) for b, th, col in recs if b == yb]
    size = round(statistics.median([th for th, _ in members]), 2)
    color = Counter([col for _, col in members]).most_common(1)[0][0]
    return (size, color)


def _representative_chrome_page(raw: RawDocument) -> int | None:
    """Index of a body page to capture repeating chrome from. Chrome repeats, so the first
    non-cover page (index 1) is representative; None when the doc has no body page."""
    return 1 if len(raw.pages) >= 2 else None


def _band_strip_bbox(pg: RawPage, top: bool) -> tuple[float, float, float, float] | None:
    """The full-width header (top) / footer (bottom) strip region in PDF points, or None if
    the page has no geometry. Bounded by BAND_STRIP_FRAC of the page height."""
    if not (pg.width and pg.height):
        return None
    band = BAND_STRIP_FRAC * pg.height
    if top:
        return (0.0, 0.0, pg.width, band)
    return (0.0, pg.height - band, pg.width, pg.height)


def _capture_band_image(pdf_path: str, pg: RawPage, page_index: int, asset_dir: str,
                        top: bool) -> str | None:
    """Render the header/footer strip of `pg` with its text glyphs redacted (vector art +
    logo images kept) → a PNG basename, but ONLY if the strip carries real decoration: a
    large coloured/inked area (non-white) OR a thin-but-coloured brand bar (saturated).
    Returns None for a plain text/rule strip or on failure (→ the rule path handles it)."""
    region = _band_strip_bbox(pg, top)
    if region is None:
        return None
    rx0, ry0, rx1, ry1 = region
    # Redact any span that INTERSECTS the strip vertically (not just centred in it): a line
    # straddling the strip edge would otherwise leak half its glyphs into the image. Redacting
    # a span that lies outside the clip is harmless (it isn't rendered there anyway).
    redact = [s.bbox for s in pg.spans if s.bbox[3] > ry0 and s.bbox[1] < ry1]
    name = f"{'hdr' if top else 'ftr'}_band.png"
    try:
        out = figure_overlay.render_redacted_clip(
            pdf_path, page_index, list(region), redact, asset_dir, name)
    except Exception:  # noqa: BLE001 — a degenerate strip just yields no decoration
        return None
    if (_page_nonwhite_frac(out, asset_dir) < BAND_DECORATION_NONWHITE_FRAC
            and _strip_colored_frac(out, asset_dir) < BAND_DECORATION_COLOR_FRAC):
        return None  # plain text/rule strip — keep the rule path, don't anchor a blank image
    return out


_PN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)^page\s+(\d+)\s+of\s+(\d+)$"), "Page {n} of {m}"),
    (re.compile(r"^(\d+)\s*/\s*(\d+)$"), "{n}/{m}"),
    (re.compile(r"^-\s*(\d+)\s*-$"), "- {n} -"),
]


def _page_number_spec(items: list) -> tuple[str, str] | None:
    """(side, format) where page-number chrome recurs (>=2 pages). Verbose forms
    ("Page 3 of 32") carry a format with {n}/{m} placeholders; bare digit/roman runs
    keep the legacy plain PAGE field ("{n}")."""
    by_side: dict[str, list[str]] = {}
    for text, side, _pg in items:
        t = (text or "").strip()
        if not t:
            continue
        for pat, fmt in _PN_PATTERNS:
            if pat.match(t):
                by_side.setdefault(side, []).append(fmt)
                break
        else:
            if re.fullmatch(r"[\divxlcDIVXLC\-\.\s/]+", t):
                by_side.setdefault(side, []).append("{n}")
    best: tuple[str, str, int] | None = None
    for side, fmts in by_side.items():
        if len(fmts) < 2:
            continue
        fmt = Counter(fmts).most_common(1)[0][0]
        if best is None or len(fmts) > best[2]:
            best = (side, fmt, len(fmts))
    return (best[0], best[1]) if best else None


def _guess_title(blocks: list) -> str | None:
    for b in blocks:
        if isinstance(b, HeadingBlock) and b.text:
            return b.text
    return None
