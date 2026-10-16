"""Emit an overlay figure as an inline OOXML group: a base picture + one editable
text box per overlay, pre-fitted so no renderer re-wraps.

Text boxes are pre-fitted with the SAME PIL `fit_text` algorithm the PNG composite
uses (`pipeline.figure_overlay.fit_text`), then emitted as fixed-size, pre-wrapped,
exactly-line-spaced `wps:wsp` shapes so Word never re-flows them differently from
the pixel-accurate reference render. The declared font size is floor-quantized to
half-points (`sz_half_pt <= measured size`) so Word can only render text SMALLER
than what was measured, never overflow the fitted box. Rotated overlays (targeting
DS-style sideways axis labels) use `<a:xfrm rot="...">` center-rotation: the shape's
un-rotated footprint is the fitted box's (h, w) swap around the same bbox midpoint,
matching how Word interprets `rot` (rotate about the shape's own center after
laying out the un-rotated footprint).

This math was pixel-verified against the real DS render (spike v4, 3.89% residual
= anti-aliasing only) — see
docs/superpowers/specs/2026-07-09-editable-figure-text-design.md for the reference
implementation this module ports from and why each discipline (pre-fit, font
unification, floor-quantize, center-rotation) is required.
"""
from __future__ import annotations

import math

from PIL import Image, ImageDraw

from models import FigureBlock, FigureOverlay
from pipeline.extractor import RENDER_DPI
from pipeline.figure_overlay import FONT_FAMILY, bundled_cjk_font, fit_text, resolve_font

PX_TO_PT = 72.0 / RENDER_DPI

# Private-use-area bullet glyphs some PDF font subsets emit in place of a real
# bullet character; normalized to a plain bullet before measuring/emitting so the
# fitted-text box math (and the rendered docx) never carries an unmapped PUA
# codepoint that most fonts (including the bundled CJK font) can't glyph.
_BULLET_MAP = {"": "•", "": "•"}

_NSDECL = (
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _norm(text: str) -> str:
    """Collapse whitespace runs to single spaces (fit_text can't measure multi-line
    text) and map known PUA bullet glyphs to a plain bullet."""
    t = " ".join((text or "").split())
    for k, v in _BULLET_MAP.items():
        t = t.replace(k, v)
    return t


def _textbox_xml(oi: int, ov: FigureOverlay, scale: float, font_path: str, draw: ImageDraw.ImageDraw) -> str:
    """Pre-fit `ov`'s text with PIL (same algorithm as the PNG composite), then emit
    a fixed-size, pre-wrapped, exactly-line-spaced `wps:wsp` shape. Returns "" if the
    overlay's normalized text is empty (nothing to render)."""
    text = _norm(ov.target_text or ov.source_text)
    if not text:
        return ""
    x, y, w, h = ov.bbox
    rot = int(ov.rotation or 0)

    rot_attr = ""
    if rot in (90, -90):
        # xfrm center-rotation: footprint (w,h) is the ROTATED box -> the unrotated
        # shape swaps to (h,w) about the same midpoint.
        font, lines = fit_text(text, font_path, ov.font_size, max(h, 1.0), max(w, 1.0), draw)
        cx, cy = x + w / 2.0, y + h / 2.0
        box_w, box_h = h, w
        off_x, off_y = int((cx - box_w / 2.0) * scale), int((cy - box_h / 2.0) * scale)
        ext_w, ext_h = max(int(box_w * scale), 1), max(int(box_h * scale), 1)
        rot_attr = f' rot="{((-rot) % 360) * 60000}"'
    else:
        font, lines = fit_text(text, font_path, ov.font_size, max(w, 1.0), max(h, 1.0), draw)
        off_x, off_y = int(x * scale), int(y * scale)
        ext_w, ext_h = max(int(w * scale), 1), max(int(h * scale), 1)

    fitted_px = font.size
    sz_half_pt = max(2, math.floor(fitted_px * PX_TO_PT * 2))  # floor: declared <= measured
    # composite line pitch: font.size + 2 px -> exact OOXML line rule in twips
    line_twips = max(20, round((fitted_px + 2) * PX_TO_PT * 20))
    color = _esc(ov.color or "1B1A17")
    jc = {"left": "left", "center": "center", "right": "right"}.get(ov.align or "left", "left")

    runs = []
    for li, ln in enumerate(lines):
        br = "<w:br/>" if li else ""
        runs.append(
            f'<w:r><w:rPr><w:rFonts w:ascii="{FONT_FAMILY}" w:hAnsi="{FONT_FAMILY}" w:eastAsia="{FONT_FAMILY}"/>'
            f'<w:color w:val="{color}"/><w:sz w:val="{sz_half_pt}"/><w:szCs w:val="{sz_half_pt}"/></w:rPr>'
            f'{br}<w:t xml:space="preserve">{_esc(ln)}</w:t></w:r>'
        )
    return f"""
      <wps:wsp>
        <wps:cNvPr id="{100 + oi}" name="ov{oi}"/>
        <wps:cNvSpPr txBox="1"/>
        <wps:spPr>
          <a:xfrm{rot_attr}><a:off x="{off_x}" y="{off_y}"/><a:ext cx="{ext_w}" cy="{ext_h}"/></a:xfrm>
          <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
          <a:noFill/><a:ln><a:noFill/></a:ln>
        </wps:spPr>
        <wps:txbx>
          <w:txbxContent>
            <w:p>
              <w:pPr><w:spacing w:before="0" w:after="0" w:line="{line_twips}" w:lineRule="exact"/><w:jc w:val="{jc}"/></w:pPr>
              {''.join(runs)}
            </w:p>
          </w:txbxContent>
        </wps:txbx>
        <wps:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0" anchor="ctr"><a:noAutofit/></wps:bodyPr>
      </wps:wsp>"""


def build_figure_group_xml(
    block: FigureBlock, base_rid: str, emu_w: int, emu_h: int, px_w: int, px_h: int
) -> str:
    """Return the full `<w:drawing>...</w:drawing>` string for an inline OOXML group:
    a base picture (embedding `base_rid`, sized `emu_w`x`emu_h`) plus one editable
    text-box shape per non-empty overlay in `block.overlays`, pre-fitted at
    `scale = emu_w / px_w` EMU per figure-local pixel."""
    scale = emu_w / px_w
    font_path = bundled_cjk_font() or resolve_font("ko")
    draw = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    boxes = "".join(
        _textbox_xml(oi, ov, scale, font_path, draw) for oi, ov in enumerate(block.overlays)
    )

    return f"""<w:drawing {_NSDECL}>
  <wp:inline distT="0" distB="0" distL="0" distR="0">
    <wp:extent cx="{emu_w}" cy="{emu_h}"/>
    <wp:docPr id="10" name="fig{_esc(block.block_id)}"/>
    <a:graphic>
      <a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup">
        <wpg:wgp>
          <wpg:cNvGrpSpPr/>
          <wpg:grpSpPr>
            <a:xfrm><a:off x="0" y="0"/><a:ext cx="{emu_w}" cy="{emu_h}"/><a:chOff x="0" y="0"/><a:chExt cx="{emu_w}" cy="{emu_h}"/></a:xfrm>
          </wpg:grpSpPr>
          <pic:pic>
            <pic:nvPicPr><pic:cNvPr id="11" name="base"/><pic:cNvPicPr/></pic:nvPicPr>
            <pic:blipFill><a:blip r:embed="{_esc(base_rid)}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>
            <pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{emu_w}" cy="{emu_h}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>
          </pic:pic>
          {boxes}
        </wpg:wgp>
      </a:graphicData>
    </a:graphic>
  </wp:inline>
</w:drawing>"""
