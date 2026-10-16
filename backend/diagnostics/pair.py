"""Align source and rendered pages and build side-by-side composites.

Alignment is positional (index 0..min-1). The rendered DOCX reflows and paginates
differently from the source (translated text expands/contracts), so a page-count
mismatch is the EXPECTED reflow signal — recorded, never treated as an error.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from PIL import Image


@dataclass
class PagePair:
    index: int
    source_png: str
    rendered_png: str


def build_pairs(source_pngs: list[str], rendered_pngs: list[str]) -> list[PagePair]:
    """Pair pages by index over the overlap. Surplus pages on either side are reported
    separately via `page_count_mismatch`."""
    n = min(len(source_pngs), len(rendered_pngs))
    return [PagePair(i, source_pngs[i], rendered_pngs[i]) for i in range(n)]


def page_count_mismatch(source_pngs: list[str], rendered_pngs: list[str]) -> Optional[dict]:
    """Describe a page-count difference (reflow signal). None when counts match."""
    ns, nr = len(source_pngs), len(rendered_pngs)
    if ns == nr:
        return None
    gap = abs(ns - nr)
    # A small drift (±1-2 on a long doc) is benign reflow; a large gap means structure
    # is collapsing or exploding — flag it louder.
    severity = "high" if gap > max(1, 0.25 * ns) else "medium"
    return {"source_pages": ns, "rendered_pages": nr, "gap": gap, "severity": severity}


def side_by_side(src_png: str, rendered_png: str, out_png: str, target_h: int = 1100,
                 gap: int = 24) -> str:
    """Compose source (left) | divider | rendered (right) into one PNG, both scaled to
    `target_h`. Human-skimmable AND fed to the vision scorer so it sees both halves."""
    os.makedirs(os.path.dirname(out_png), exist_ok=True)
    left = _scaled(src_png, target_h)
    right = _scaled(rendered_png, target_h)
    w = left.width + gap + right.width
    canvas = Image.new("RGB", (w, target_h), "white")
    canvas.paste(left, (0, 0))
    canvas.paste(right, (left.width + gap, 0))
    canvas.save(out_png)
    return out_png


def _scaled(path: str, target_h: int) -> Image.Image:
    im = Image.open(path).convert("RGB")
    if im.height != target_h:
        w = max(1, round(im.width * target_h / im.height))
        im = im.resize((w, target_h))
    return im
