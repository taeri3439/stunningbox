"""Vision OCR for text baked into images (no born-digital span layer).

A second overlay source for the figure-overlay pipeline: read the visible text in an opaque
figure, classify each region translate-vs-keep, and emit FigureOverlays for the translatable
ones plus a simple-fill base image (original text erased). Generative inpainting is out of v1.

Two implementations share one interface (mirrors analyzer/translator):
  * ClaudeOcr — vision (uses ANTHROPIC_API_KEY)
  * MockOcr   — deterministic, injectable regions, for tests / offline
"""
from __future__ import annotations

import base64
import os
import shutil
from dataclasses import dataclass

from PIL import Image, ImageDraw

from models import FigureOverlay
from pipeline.analyzer import ANALYZER_MODEL
from pipeline.llm_provider import openai_model, openai_tool_call, resolve_llm_provider

# Keep regions that must NOT be translated (left untouched in the image).
KEEP_KINDS = {"keep"}


@dataclass
class OcrRegion:
    text: str
    bbox: tuple[float, float, float, float]  # (x, y, w, h) in the IMAGE's pixels
    kind: str  # "translate" | "keep"


_OCR_SYSTEM = """You read the visible text in an image and return each text region.
For each region give the text verbatim, its pixel bounding box [x, y, w, h] (top-left origin),
and a `kind`:
- "translate": meaningful content text (titles, labels, sentences) that should be translated.
- "keep": text that must stay as-is — brand/logo wordmarks, URLs, email addresses, product
  codes/SKUs, and bare units/symbols.
Return every readable text region. Do not invent text."""


def _ocr_schema() -> dict:
    region = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "bbox": {"type": "array", "items": {"type": "number"},
                     "minItems": 4, "maxItems": 4,
                     "description": "[x, y, w, h] in image pixels, top-left origin"},
            "kind": {"type": "string", "enum": ["translate", "keep"]},
        },
        "required": ["text", "bbox", "kind"],
    }
    return {"type": "object",
            "properties": {"regions": {"type": "array", "items": region}},
            "required": ["regions"]}


class MockOcr:
    """Returns a fixed, injected region list — deterministic, no network."""

    def __init__(self, regions: list[OcrRegion] | None = None):
        self._regions = regions or []

    def ocr_classify(self, image_path: str) -> list[OcrRegion]:
        return list(self._regions)


class ClaudeOcr:
    def __init__(self, client=None, model: str = ANALYZER_MODEL):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def ocr_classify(self, image_path: str) -> list[OcrRegion]:
        with open(image_path, "rb") as fh:
            b64 = base64.standard_b64encode(fh.read()).decode()
        tool = {"name": "emit_text_regions",
                "description": "Return the visible text regions in the image.",
                "input_schema": _ocr_schema()}
        content = [
            {"type": "image",
             "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": "List every visible text region."},
        ]
        msg = self.client.messages.create(
            model=self.model, max_tokens=2000, system=_OCR_SYSTEM, tools=[tool],
            tool_choice={"type": "tool", "name": "emit_text_regions"},
            messages=[{"role": "user", "content": content}],
        )
        out: list[OcrRegion] = []
        for part in msg.content:
            if part.type == "tool_use" and part.name == "emit_text_regions" \
                    and isinstance(part.input, dict):
                for r in part.input.get("regions", []):
                    bb = r.get("bbox")
                    if isinstance(r.get("text"), str) and isinstance(bb, list) and len(bb) == 4:
                        out.append(OcrRegion(text=r["text"], bbox=tuple(float(v) for v in bb),
                                             kind=r.get("kind", "translate")))
        return out


class OpenAIOcr:
    def __init__(self, client=None, model: str | None = None):
        self._client = client
        self.model = model or openai_model(
            dedicated_env="OPENAI_ANALYZER_MODEL",
            shared_env="ANALYZER_MODEL",
            default="gpt-4o",
        )

    def ocr_classify(self, image_path: str) -> list[OcrRegion]:
        with open(image_path, "rb") as fh:
            b64 = base64.standard_b64encode(fh.read()).decode()
        schema = _ocr_schema()
        parsed, _finish = openai_tool_call(
            model=self.model,
            system=_OCR_SYSTEM,
            user_content=[
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                },
                {"type": "text", "text": "List every visible text region."},
            ],
            tool_name="emit_text_regions",
            tool_description="Return the visible text regions in the image.",
            parameters=schema,
            max_tokens=2000,
        )
        out: list[OcrRegion] = []
        if isinstance(parsed, dict):
            for r in parsed.get("regions", []):
                bb = r.get("bbox")
                if isinstance(r.get("text"), str) and isinstance(bb, list) and len(bb) == 4:
                    out.append(
                        OcrRegion(
                            text=r["text"],
                            bbox=tuple(float(v) for v in bb),
                            kind=r.get("kind", "translate"),
                        )
                    )
        return out


# A translatable overlay's font size is estimated from its box height (most of the box is the
# glyph body); shrink slightly so the rendered text doesn't overshoot the original line.
_FONT_FROM_HEIGHT = 0.8


def overlays_from_regions(regions) -> list[FigureOverlay]:
    """Translate-regions → FigureOverlays (figure-image pixel coords). keep-regions are dropped
    (left untouched in the image). bbox/font are approximate (vision OCR).

    ids are `ocr_0, ocr_1, …`, unique WITHIN a figure (the re-translation layer addresses
    overlays scoped by the figure's block id, so the same id across figures is fine)."""
    out: list[FigureOverlay] = []
    j = 0
    for r in regions:
        if r.kind in KEEP_KINDS:
            continue
        out.append(
            FigureOverlay(
                id=f"ocr_{j}",
                source_text=r.text,
                target_text="",
                bbox=tuple(float(v) for v in r.bbox),
                font_size=max(6.0, r.bbox[3] * _FONT_FROM_HEIGHT),
                color=None,   # ink default (composite falls back to ink)
                align="left",
                rotation=0,
            )
        )
        j += 1
    return out


def get_ocr():
    """Vision OCR when an LLM key is configured, else MockOcr (offline)."""
    provider = resolve_llm_provider()
    if provider == "openai":
        return OpenAIOcr()
    if provider == "anthropic":
        return ClaudeOcr()
    return MockOcr()


def _sample_bg(img, x0: int, y0: int, x1: int, y1: int):
    """Average colour of a thin ring just outside the box — the local background to fill with."""
    w, h = img.size
    px = img.load()
    pts = []
    for x in range(max(0, x0 - 3), min(w, x1 + 3), 2):
        if y0 - 3 >= 0:
            pts.append(px[x, y0 - 3])
        if y1 + 3 < h:
            pts.append(px[x, y1 + 3])
    for y in range(max(0, y0), min(h, y1), 2):
        if x0 - 3 >= 0:
            pts.append(px[x0 - 3, y])
        if x1 + 3 < w:
            pts.append(px[x1 + 3, y])
    if not pts:
        return (255, 255, 255)
    n = len(pts)
    return tuple(sum(p[i] for p in pts) // n for i in range(3))


def fill_mask(image_path: str, regions, out_path: str) -> None:
    """Erase each TRANSLATE region by filling its (padded) box with the sampled local background;
    keep-regions are left untouched. Writes the base image to out_path. (v1: flat-fill only.)

    Background is sampled from an UNMODIFIED copy of the image, so an earlier box's fill can never
    contaminate a later box's sample. Adjacent text whose sample ring overlaps a neighbour's
    glyphs is an accepted v1 limitation (generative inpainting would address it later)."""
    translate = [r for r in regions if r.kind not in KEEP_KINDS]
    if not translate:  # nothing to erase — pass the image through unchanged
        if os.path.abspath(image_path) != os.path.abspath(out_path):
            shutil.copy2(image_path, out_path)
        return
    img = Image.open(image_path).convert("RGB")
    orig = img.copy()  # sample bg from the ORIGINAL, never from boxes we've already filled
    draw = ImageDraw.Draw(img)
    w, h = img.size
    pad = 2
    for r in translate:
        x, y, bw, bh = r.bbox
        x0, y0, x1, y1 = int(x), int(y), int(x + bw), int(y + bh)
        rx0, ry0, rx1, ry1 = max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)
        if rx1 <= rx0 or ry1 <= ry0:  # box off-image / degenerate after clamping — skip, don't crash
            continue
        bg = _sample_bg(orig, x0, y0, x1, y1)
        draw.rectangle([rx0, ry0, rx1, ry1], fill=bg)
    img.save(out_path)
