"""Stage 0 — deterministic extraction with PyMuPDF.

This is the *ground truth*: text + geometry pulled losslessly and cheaply. No LLM
here. The analyzer later interprets this; the reconciler verifies nothing is lost.
"""
from __future__ import annotations

import os
import re

import fitz  # PyMuPDF

from models import RawDocument, RawImage, RawPage, RawSpan

# Render DPI for page images handed to the vision model and for figure clips.
RENDER_DPI = 150
_SCALE = RENDER_DPI / 72.0  # PDF points -> pixels at RENDER_DPI

_BOLD_FLAG = 1 << 4


def extract_document(pdf_path: str, asset_dir: str) -> RawDocument:
    """Extract spans + embedded images and render each page to a PNG."""
    os.makedirs(asset_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    pages: list[RawPage] = []

    for pno in range(doc.page_count):
        page = doc[pno]
        rect = page.rect
        raw_page = RawPage(width=rect.width, height=rect.height)

        # --- text spans ---
        text_dict = page.get_text("dict")
        for bno, block in enumerate(text_dict.get("blocks", [])):
            if block.get("type") != 0:  # 0 = text block
                continue
            for lno, line in enumerate(block.get("lines", [])):
                rot = _dir_to_rotation(line.get("dir"))
                for sno, span in enumerate(line.get("spans", [])):
                    text = span.get("text", "")
                    if not text.strip():
                        continue
                    font = span.get("font", "")
                    flags = span.get("flags", 0)
                    bold = bool(flags & _BOLD_FLAG) or "bold" in font.lower()
                    raw_page.spans.append(
                        RawSpan(
                            id=f"p{pno}b{bno}l{lno}s{sno}",
                            page=pno,
                            text=text,
                            bbox=tuple(span["bbox"]),
                            font=font,
                            size=round(span.get("size", 0.0), 2),
                            bold=bold,
                            color=span.get("color", 0),
                            rotation=rot,
                        )
                    )

        # --- embedded raster images (logos/photos); vector figures are handled
        #     later by clipping the page render at analyzer-provided bboxes) ---
        for ino, info in enumerate(page.get_image_info(xrefs=True)):
            bbox = info.get("bbox")
            if not bbox:
                continue
            img_id = f"p{pno}img{ino}"
            out = os.path.join(asset_dir, f"{img_id}.png")
            try:
                _clip_to_png(page, fitz.Rect(bbox), out)
            except Exception:
                continue
            raw_page.images.append(
                RawImage(id=img_id, page=pno, bbox=tuple(bbox), path=os.path.basename(out))
            )

        # --- horizontal rules (vector lines / thin rects) ---
        raw_page.hrules = _horizontal_rules(page, rect.width)

        # --- full-page render for vision analysis ---
        render_name = f"page{pno}.png"
        pix = page.get_pixmap(dpi=RENDER_DPI)
        pix.save(os.path.join(asset_dir, render_name))
        raw_page.render_path = render_name

        pages.append(raw_page)

    doc.close()
    return RawDocument(pages=pages)


# A rule must span at least this fraction of the page width to count (filters out the
# short strokes that make up underlines of individual words, tick marks, etc.).
_MIN_RULE_FRAC = 0.30
_MAX_RULE_THICKNESS = 5.0  # pt; thicker bands are shapes/fills, not rules
_DEFAULT_RULE_COLOR = "1B1A17"  # ink, when a rule carries no explicit colour


def _rgb_hex(c) -> str | None:
    """PyMuPDF colour (r,g,b floats 0..1) → 'RRGGBB'. None if unset."""
    if not c:
        return None
    try:
        return "".join(f"{max(0, min(255, round(v * 255))):02X}" for v in c[:3])
    except (TypeError, ValueError):
        return None


def _dir_to_rotation(d) -> int:
    """PyMuPDF line 'dir' unit vector → text rotation in degrees (0 | 90 | -90).
    Horizontal (incl. 180°) → 0; vertical reads-up → 90; reads-down → -90."""
    if not d:
        return 0
    dx, dy = d[0], d[1]
    if abs(dx) >= abs(dy):
        return 0
    return 90 if dy < 0 else -90


def _horizontal_rules(page: "fitz.Page", page_w: float):
    """Find horizontal rules (section separators, heading underlines) among the page's
    vector drawings, capturing each rule's thickness and colour so it renders true to the
    source. Returns deduped [(y, x0, x1, thickness_pt, color_hex)] top-to-bottom.

    A rule is any wide-but-thin drawing — a stroked line (thickness = stroke width) or a
    thin filled bar (thickness = box height). Table borders are not filtered here — the
    reconciler drops rules inside a detected table/figure region."""
    rules: list[tuple] = []
    try:
        drawings = page.get_drawings()
    except Exception:  # noqa: BLE001 — never let a drawing quirk break extraction
        return rules
    for d in drawings:
        r = d.get("rect")
        if r is None or r.width < _MIN_RULE_FRAC * page_w:
            continue
        thickness = max(r.height, d.get("width") or 0.0)  # fill height OR stroke width
        if thickness > _MAX_RULE_THICKNESS:  # too tall → a box/figure, not a rule
            continue
        color = _rgb_hex(d.get("color") or d.get("fill")) or _DEFAULT_RULE_COLOR
        y = round((r.y0 + r.y1) / 2, 1)
        rules.append((y, round(r.x0, 1), round(r.x1, 1), round(thickness, 2), color))
    rules.sort()
    deduped: list[tuple] = []
    for rule in rules:
        if deduped and abs(deduped[-1][0] - rule[0]) < 3:  # same rule drawn twice
            if rule[3] > deduped[-1][3]:  # keep the thicker representation
                deduped[-1] = rule
            continue
        deduped.append(rule)
    return deduped


def _collapse(vals, tol: float = 3.0) -> list[float]:
    """Sorted unique values with near-duplicates merged into one grid line."""
    out: list[float] = []
    for v in sorted(vals):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _grid_span(a: float, b: float, lines: list[float]) -> int:
    """How many grid cells the coordinate range a..b covers (cell edges == grid lines)."""
    lo = min(range(len(lines)), key=lambda i: abs(lines[i] - a))
    hi = min(range(len(lines)), key=lambda i: abs(lines[i] - b))
    return max(1, hi - lo)


def _table_structure(t, xs: list[float]):
    """Rebuild a PyMuPDF table as rows of {text, colspan, rowspan} — origin cells only,
    span-covered positions omitted (the renderer's convention). Carries the real merges
    (a wide "개정내용" cell becomes one colspan-3 cell) that the vision grid flattens."""
    ys = _collapse(e for cell in t.cells if cell for e in (cell[1], cell[3]))
    try:
        ext = t.extract()
    except Exception:  # noqa: BLE001
        return None
    rows = []
    for ri, row in enumerate(t.rows):
        cells = []
        for ci, cell in enumerate(row.cells):
            if cell is None:  # covered by a row/col span originating elsewhere
                continue
            text = (ext[ri][ci] if ri < len(ext) and ci < len(ext[ri]) else "") or ""
            cells.append({
                "text": re.sub(r"\s+", " ", text).strip(),
                "colspan": _grid_span(cell[0], cell[2], xs),
                "rowspan": _grid_span(cell[1], cell[3], ys),
            })
        rows.append(cells)
    return rows


def detect_table_grids(pdf_path: str, page_index: int, tol: float = 3.0):
    """Table geometry the vision analyzer can't see: for each table PyMuPDF finds on the
    page return (bbox, [vertical-line x positions], rebuilt-rows). The reconciler matches
    these to its (vision-built) tables to recover real column widths, and — for tables whose
    physical column count differs from the vision grid — to rebuild the table from the real
    cell structure. Empty on any failure so a detection miss never breaks reconciliation."""
    try:
        doc = fitz.open(pdf_path)
    except Exception:  # noqa: BLE001
        return []
    try:
        if page_index < 0 or page_index >= doc.page_count:
            return []
        out = []
        for t in doc[page_index].find_tables().tables:
            bounds = _collapse(
                (e for cell in t.cells if cell for e in (cell[0], cell[2])), tol)
            if len(bounds) >= 2:
                out.append((tuple(t.bbox), bounds, _table_structure(t, bounds)))
        return out
    except Exception:  # noqa: BLE001 — find_tables can throw on odd pages
        return []
    finally:
        doc.close()


def render_clip(pdf_path: str, page_index: int, bbox, asset_dir: str, name: str):
    """Clip-render a page region to PNG and TIGHTEN it to the actual content.

    The analyzer's figure_bbox is a vision-estimated (and systematically over-sized) box,
    so the raw clip carries wide empty margins. We render it, auto-trim the white border
    (content-agnostic: vector strokes, rasters, and figure labels are all ink), crop to
    that, and return the tightened box so the renderer can size/centre the image truly.

    Returns (basename, tight_bbox) where tight_bbox is in PDF points; tight_bbox falls
    back to the input bbox if trimming finds nothing.
    """
    os.makedirs(asset_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    page = doc[page_index]
    out = os.path.join(asset_dir, name)
    # Vision bboxes are estimates and often spill past the page edge (or arrive degenerate).
    # Clamp to the page so a slightly-oversized box is fixed rather than failing the clip
    # and silently dropping the figure.
    clip = fitz.Rect(bbox) & page.rect
    if clip.is_empty or clip.width < 2 or clip.height < 2:
        doc.close()
        raise ValueError(f"degenerate figure bbox {bbox} on page {page_index}")
    pix = page.get_pixmap(matrix=fitz.Matrix(_SCALE, _SCALE), clip=clip, alpha=False)
    tight = _trim_and_save(pix, [clip.x0, clip.y0, clip.x1, clip.y1], out)
    doc.close()
    return os.path.basename(out), tight


def _trim_and_save(pix: "fitz.Pixmap", bbox, out_path: str):
    """Auto-trim white margins from a rendered region; save the cropped PNG. Returns the
    trimmed box in PDF points (input bbox if trimming yields nothing)."""
    from PIL import Image, ImageChops

    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    diff = ImageChops.difference(img, Image.new("RGB", img.size, (255, 255, 255)))
    diff = diff.point(lambda p: 255 if p > 12 else 0)  # ignore faint anti-aliasing
    box = diff.getbbox()
    if not box:  # nothing but white → keep the original region as-is
        img.save(out_path)
        return tuple(bbox)
    pad = int(round(2.0 * _SCALE))  # ~2pt breathing room
    px0 = max(0, box[0] - pad)
    py0 = max(0, box[1] - pad)
    px1 = min(img.width, box[2] + pad)
    py1 = min(img.height, box[3] + pad)
    img.crop((px0, py0, px1, py1)).save(out_path)
    return (
        round(bbox[0] + px0 / _SCALE, 1),
        round(bbox[1] + py0 / _SCALE, 1),
        round(bbox[0] + px1 / _SCALE, 1),
        round(bbox[1] + py1 / _SCALE, 1),
    )


def _clip_to_png(page: "fitz.Page", clip: "fitz.Rect", out_path: str) -> None:
    pix = page.get_pixmap(matrix=fitz.Matrix(_SCALE, _SCALE), clip=clip, alpha=False)
    pix.save(out_path)


def extract_span_bboxes(pdf_path: str):
    """Map each text span's id -> (page_index, bbox) plus per-page (width, height) in PDF
    points, using the SAME id scheme as extract_document (p{pno}b{bno}l{lno}s{sno}). No
    rendering — cheap geometry only, for the re-translation highlight overlays."""
    doc = fitz.open(pdf_path)
    spans: dict[str, tuple] = {}
    page_sizes: list[tuple[float, float]] = []
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            page_sizes.append((page.rect.width, page.rect.height))
            text_dict = page.get_text("dict")
            for bno, block in enumerate(text_dict.get("blocks", [])):
                if block.get("type") != 0:
                    continue
                for lno, line in enumerate(block.get("lines", [])):
                    for sno, span in enumerate(line.get("spans", [])):
                        if not span.get("text", "").strip():
                            continue
                        sid = f"p{pno}b{bno}l{lno}s{sno}"
                        spans[sid] = (pno, tuple(span["bbox"]))
    finally:
        doc.close()
    return spans, page_sizes


def segment_source_bbox(span_ids, span_map, page_sizes):
    """Union bbox of a segment's spans (on their first page) as page fractions 0..1:
    {page, x, y, w, h}. None if no span is located or the page size is degenerate."""
    located = [span_map[s] for s in span_ids if s in span_map]
    if not located:
        return None
    page = located[0][0]
    boxes = [b for p, b in located if p == page]
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    if page >= len(page_sizes):
        return None
    pw, ph = page_sizes[page]
    if pw <= 0 or ph <= 0:
        return None
    return {
        "page": page,
        "x": round(x0 / pw, 4),
        "y": round(y0 / ph, 4),
        "w": round((x1 - x0) / pw, 4),
        "h": round((y1 - y0) / ph, 4),
    }
