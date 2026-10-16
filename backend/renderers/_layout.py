"""Pure layout helpers shared by the DOCX and HTML renderers — font-size scaling, table
column-cram detection, and document-level body metrics. No renderer-specific (python-docx
or HTML) dependencies, so both renderers import the same tuned logic instead of drifting."""
from __future__ import annotations

import re
import statistics
from collections import Counter

from models import ListItemBlock, ParagraphBlock, SemanticDocument

MAX_LINE_PITCH = 1.4  # cap on rendered line pitch (multiple of font size); see docx_renderer
INK_HEX = "1B1A17"    # near-black body/heading ink (hex, no leading '#')


def scaled_pt(size: float | None, body_pt: float | None) -> int | None:
    """Map a source font size onto an 11pt body, preserving the original ratio (clamped
    8..30pt). None when we can't scale (→ keep the default)."""
    if not size or not body_pt:
        return None
    return max(8, min(30, round(11 * size / body_pt)))


def widths_would_cram(rows, fractions: list[float], ncols: int,
                      content_pt: float, font_pt: float) -> bool:
    """A fixed source-proportional width 'crams' a column when it's narrower than that
    column's longest UNBREAKABLE Latin token (forcing an ugly one-glyph-per-line break).
    CJK wraps anywhere so it never crams."""
    longest = [0] * ncols
    for row in rows:
        c = 0
        for cell in row:
            cs = max(1, getattr(cell, "colspan", 1) or 1)
            if cs == 1 and c < ncols:
                runs = re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-/(),]*",
                                  getattr(cell, "text", "") or "")
                longest[c] = max(longest[c], max((len(x) for x in runs), default=0))
            c += cs
    return any(longest[i] * font_pt * 0.58 + 7 > fractions[i] * content_pt + 1
               for i in range(ncols))


def dominant_body_pt(doc: SemanticDocument) -> float | None:
    """Most common font size (pt) among body paragraphs/list items; fallback to any sized
    block, else None (→ no scaling)."""
    sizes = [round(b.font_size, 1) for b in doc.blocks
             if isinstance(b, (ParagraphBlock, ListItemBlock)) and b.font_size]
    if not sizes:
        sizes = [round(b.font_size, 1) for b in doc.blocks if getattr(b, "font_size", None)]
    return Counter(sizes).most_common(1)[0][0] if sizes else None


def body_line_spacing(doc: SemanticDocument) -> float | None:
    """Median line spacing of body paragraphs/list items (used where a block has none)."""
    vals = [b.line_spacing for b in doc.blocks
            if isinstance(b, (ParagraphBlock, ListItemBlock)) and b.line_spacing]
    return round(statistics.median(vals), 2) if vals else None


def content_pt(doc: SemanticDocument) -> float:
    """Usable text width (pt) = page width minus L/R margins. A4 text-width fallback when
    the source carries no geometry (legacy source.json)."""
    if doc.page_width:
        return doc.page_width - (doc.margin_left or 0) - (doc.margin_right or 0)
    return 455.0
