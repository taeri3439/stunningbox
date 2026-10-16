"""In-figure text overlay: capture the text inside a figure, redact it from the
rendered image, and composite the translated text back in place.

Born-digital PDFs only — the in-figure text is real PyMuPDF spans, so we redact the
text layer (keeping vector artwork) instead of OCR/inpainting.
"""
from __future__ import annotations

import os
import sys

import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont

from models import FigureBlock, FigureOverlay, RawSpan, SemanticDocument
from pipeline import extractor

_SCALE = extractor._SCALE  # PDF points -> pixels at RENDER_DPI (shared with the figure clip)


def render_redacted_clip(
    pdf_path: str,
    page_index: int,
    exact_bbox,
    redact_bboxes,
    asset_dir: str,
    name: str,
) -> str:
    """Remove the glyphs in `redact_bboxes` (PDF points), then render EXACTLY `exact_bbox`
    (the figure's tight box — no re-trim, so the PNG origin/size match the overlay coords)
    to a PNG. Vector artwork and images are preserved. Returns the PNG basename.
    """
    os.makedirs(asset_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    try:
        page = doc[page_index]
        for rb in redact_bboxes:
            # fill=None → erase the covered glyphs without painting a fill rectangle.
            page.add_redact_annot(fitz.Rect(rb), fill=None)
        # Keep images and line art; only text is removed.
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        )
        clip = fitz.Rect(exact_bbox) & page.rect
        if clip.is_empty or clip.width < 2 or clip.height < 2:
            raise ValueError(f"degenerate exact_bbox {exact_bbox} on page {page_index}")
        pix = page.get_pixmap(matrix=fitz.Matrix(_SCALE, _SCALE), clip=clip, alpha=False)
        out = os.path.join(asset_dir, name)
        pix.save(out)
        return os.path.basename(out)
    finally:
        doc.close()


def spans_in_region(page_spans, geom) -> list[RawSpan]:
    """Spans whose CENTRE falls inside the figure's tight box `geom` (PDF points)."""
    gx0, gy0, gx1, gy1 = geom
    out = []
    for s in page_spans:
        cx = (s.bbox[0] + s.bbox[2]) / 2
        cy = (s.bbox[1] + s.bbox[3]) / 2
        if gx0 <= cx <= gx1 and gy0 <= cy <= gy1:
            out.append(s)
    return out


def overlays_from_spans(spans, geom, scale: float = _SCALE) -> list[FigureOverlay]:
    """Convert in-figure spans to FigureOverlays in figure-local pixel coords."""
    gx0, gy0 = geom[0], geom[1]
    out = []
    for s in spans:
        out.append(
            FigureOverlay(
                id=s.id,
                source_text=s.text,
                target_text="",
                bbox=(
                    (s.bbox[0] - gx0) * scale,
                    (s.bbox[1] - gy0) * scale,
                    (s.bbox[2] - s.bbox[0]) * scale,
                    (s.bbox[3] - s.bbox[1]) * scale,
                ),
                font_size=s.size * scale,
                color="%06X" % (int(s.color) & 0xFFFFFF),
                align="left",
                rotation=s.rotation,
            )
        )
    return out


def has_translatable_text(overlays) -> bool:
    """True iff some overlay has an alphabetic character — the test that qualifies a figure
    for overlay treatment (a pure number/symbol figure stays opaque)."""
    return any(any(ch.isalpha() for ch in o.source_text) for o in overlays)


# ---------------------------------------------------------------------------
# Font resolver + PIL composite
# ---------------------------------------------------------------------------

MIN_FONT_PX = 6.0          # never shrink below this
SHRINK_FLOOR = 0.55        # shrink to at most this fraction before wrapping

_FONTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "fonts")

FONT_FAMILY = "NanumGothic"  # the family the DS renders AND we measure with


def bundled_cjk_font() -> str | None:
    """Absolute path to the bundled NanumGothic.ttf (the family the DS renders with),
    or None if it isn't present. Used by both the PIL measurer and the group renderer
    so measurement and the docx w:rFonts family agree — zero substitution surprise."""
    p = os.path.join(_FONTS_DIR, "NanumGothic.ttf")
    return p if os.path.exists(p) else None


# Preference order: bundled CJK font (prod parity) → common system CJK fonts →
# DejaVuSans (shipped inside Pillow, always loadable) so tests run with no bundled asset.
_FONT_CANDIDATES = (
    os.path.join(_FONTS_DIR, "NanumGothic.ttf"),
    os.path.join(_FONTS_DIR, "NotoSansKR-Regular.ttf"),
    "C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "DejaVuSans.ttf",
)


def resolve_font(target_lang: str = "") -> str:
    """Path/name of a TrueType font for compositing. Prefers a bundled CJK font; falls
    back to a system CJK font, then DejaVuSans (bundled with Pillow)."""
    for c in _FONT_CANDIDATES:
        if os.path.isabs(c):
            if os.path.exists(c):
                return c
        else:
            try:
                ImageFont.truetype(c, 12)  # name resolvable by Pillow?
                return c
            except OSError:
                continue
    return "DejaVuSans.ttf"


def fit_text(text, font_path, size, max_w, max_h, draw):
    """Choose a font size (and <=2 lines) so `text` fits `max_w`x`max_h`. Returns
    (ImageFont, lines). Shrinks to SHRINK_FLOOR, then wraps on spaces, then shrinks more."""
    size = max(MIN_FONT_PX, float(size))
    floor = max(MIN_FONT_PX, size * SHRINK_FLOOR)

    def font_at(s):
        return ImageFont.truetype(font_path, int(round(s)))

    # 1) shrink (single line) down to the floor
    s = size
    while s > floor:
        f = font_at(s)
        if draw.textlength(text, font=f) <= max_w:
            return f, [text]
        s -= 1.0
    # 2) at the floor, try a 2-line wrap
    f = font_at(floor)
    if draw.textlength(text, font=f) <= max_w:
        return f, [text]
    lines = _wrap_two(text, f, max_w, draw)
    # 3) still too wide -> keep shrinking the wrapped lines
    s = floor
    while s > MIN_FONT_PX:
        f = font_at(s)
        lines = _wrap_two(text, f, max_w, draw)
        if all(draw.textlength(ln, font=f) <= max_w for ln in lines):
            break
        s -= 1.0
    return font_at(max(s, MIN_FONT_PX)), lines


def _wrap_two(text, font, max_w, draw):
    """Greedy split into at most two lines at the space that best balances widths."""
    words = text.split(" ")
    if len(words) < 2:
        return [text]
    best = None
    for i in range(1, len(words)):
        a = " ".join(words[:i])
        b = " ".join(words[i:])
        widest = max(draw.textlength(a, font=font), draw.textlength(b, font=font))
        if widest <= max_w and (best is None or widest < best[0]):
            best = (widest, [a, b])
    if best:
        return best[1]
    # nothing fits cleanly -> split as evenly as possible
    mid = len(words) // 2
    return [" ".join(words[:mid]), " ".join(words[mid:])]


def _median_border_color(img, x0: float, y0: float, x1: float, y1: float):
    """Median colour of a thin ring of pixels just OUTSIDE (x0,y0)-(x1,y1) — the local
    background to inpaint an overlay's box with before drawing its translated text. Median
    (not mean) resists a stray dark line/rule brushing one edge of the ring skewing the
    fill toward it — the same failure mode a flat-average sampler is prone to."""
    w_img, h_img = img.size
    xi0, yi0, xi1, yi1 = int(x0), int(y0), int(x1), int(y1)
    px = img.load()
    pts = []
    for x in range(max(0, xi0 - 3), min(w_img, xi1 + 3), 2):
        if yi0 - 3 >= 0:
            pts.append(px[x, yi0 - 3])
        if yi1 + 3 < h_img:
            pts.append(px[x, yi1 + 3])
    for y in range(max(0, yi0), min(h_img, yi1), 2):
        if xi0 - 3 >= 0:
            pts.append(px[xi0 - 3, y])
        if xi1 + 3 < w_img:
            pts.append(px[xi1 + 3, y])
    if not pts:
        return None

    def _median(i):
        vals = sorted(p[i] for p in pts)
        return vals[len(vals) // 2]

    return tuple(_median(i) for i in range(3))


def composite(base_path, overlays, out_path, target_lang: str = "") -> None:
    """Draw each overlay's translated text onto a copy of the redacted base PNG. Before
    drawing, inpaint the overlay's own bbox with the median colour of its border pixels —
    a defence-in-depth pass so any pixels the upstream erasure step missed (a baked label
    only partially cleared, an OCR fill's tight padding leaving a fringe) don't peek out
    from under the translated text as a "double text" artifact. Skips a zero-area bbox
    (nothing to safely inpaint — risk containment: only ever paints INSIDE the box)."""
    img = Image.open(base_path).convert("RGB")
    orig = img.copy()  # sample border colour from the UNMODIFIED image — an earlier
    #                     overlay's inpaint/text fill must never contaminate a later one's
    #                     sample (same discipline as figure_ocr.fill_mask's `_sample_bg`).
    draw = ImageDraw.Draw(img)
    font_path = resolve_font(target_lang)

    def _fill(hexv):
        h = hexv if (hexv and len(hexv) == 6) else "1B1A17"
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

    for ov in overlays:
        # Collapse newlines / whitespace runs to single spaces — PIL's textlength can't measure
        # multi-line text, and an OCR overlay may carry a multi-line paragraph. fit_text re-wraps
        # by width afterward.
        text = " ".join((ov.target_text or ov.source_text or "").split())
        if not text:
            continue
        x, y, w, h = ov.bbox
        if w > 0 and h > 0 and not ov.rotation:
            bg = _median_border_color(orig, x, y, x + w, y + h)
            if bg is not None:
                draw.rectangle([x, y, x + w, y + h], fill=bg)
        color = _fill(ov.color)
        if ov.rotation:
            _draw_rotated(img, text, font_path, ov.font_size, x, y, w, h, color, ov.rotation)
            continue
        font, lines = fit_text(text, font_path, ov.font_size, max(w, 1.0), max(h, 1.0), draw)
        line_h = (font.size + 2)
        total_h = line_h * len(lines)
        cy = y + max(0.0, (h - total_h) / 2)
        for ln in lines:
            lw = draw.textlength(ln, font=font)
            if ov.align == "center":
                lx = x + max(0.0, (w - lw) / 2)
            elif ov.align == "right":
                lx = x + max(0.0, w - lw)
            else:
                lx = x
            draw.text((lx, cy), ln, font=font, fill=color)
            cy += line_h
    img.save(out_path)


def _draw_rotated(img, text, font_path, size, x, y, w, h, color, rotation) -> None:
    """Render rotated text (90/-90) on a transparent sub-image and paste it at (x, y)."""
    font = ImageFont.truetype(font_path, max(int(round(size)), int(MIN_FONT_PX)))
    tmp_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    tw = int(tmp_draw.textlength(text, font=font)) + 4
    th = int(font.size + 4)
    txt = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    ImageDraw.Draw(txt).text((2, 2), text, font=font, fill=color + (255,))
    rot = txt.rotate(rotation, expand=True)
    img.paste(rot, (int(x), int(y)), rot)


def composite_document(doc: SemanticDocument, asset_dir: str, target_lang: str) -> None:
    """For each overlay figure, composite the translated text onto its base PNG and point
    `image_ref` at a per-language file (`<stem>.<lang>.png`). Opaque figures are untouched.
    Mutates `doc` in place. A figure that fails to composite keeps its source `image_ref`
    (graceful degradation — the untranslated figure still renders)."""
    lang = (target_lang or "out").replace("/", "_")
    for block in doc.blocks:
        if not isinstance(block, FigureBlock):
            continue
        if not block.overlays or not block.base_image_ref:
            continue
        base_path = os.path.join(asset_dir, block.base_image_ref)
        if not os.path.exists(base_path):
            continue
        stem = (block.base_image_ref[:-9] if block.base_image_ref.endswith(".base.png")
                else os.path.splitext(block.base_image_ref)[0])
        out_name = f"{stem}.{lang}.png"
        try:
            composite(base_path, block.overlays, os.path.join(asset_dir, out_name),
                      target_lang=target_lang)
            block.image_ref = out_name
        except Exception as e:  # noqa: BLE001 — keep the source image_ref if compositing fails
            print(
                f"[figure_overlay] WARNING: composite failed for {block.base_image_ref!r}: {e}",
                file=sys.stderr, flush=True,
            )
