"""Stage 1 — semantic structure analysis.

The analyzer decides *what each thing is* (heading / paragraph / list item / table /
figure / caption / repeating chrome), groups spans into logical blocks, fixes reading
order, and separates literal markers (4.1.1, 가)) from translatable text. It does NOT
re-derive geometry — that came from the extractor.

Two implementations share one interface:
  * ClaudeAnalyzer   — vision + text, the real path (uses ANTHROPIC_API_KEY)
  * HeuristicAnalyzer — offline fallback (font-size + marker regex) for tests / no key
"""
from __future__ import annotations

import base64
import json
import os
import re
import sys
from typing import Protocol

from models import RawPage, RawSpan
from pipeline.llm_provider import openai_model, openai_tool_call, resolve_llm_provider

# A page block as emitted by the analyzer (pre-reconciliation). Kept as plain dicts
# so both implementations and the JSON tool-schema speak the same shape.
#   common: {"type", "source_span_ids": [...]}
#   heading:   + "level", "marker"(opt), "text"
#   paragraph: + "text"
#   list_item: + "marker", "level", "text"
#   caption:   + "text"
#   chrome:    + "text"            (repeating header/footer; dropped downstream)
#   figure:    + "figure_bbox": [x0,y0,x1,y1]   (clipped to an image downstream)
#   table:     + "rows": [[{"text","rowspan","colspan","is_header","source_span_ids"}]]

BLOCK_TYPES = ["heading", "paragraph", "list_item", "table", "figure", "caption", "chrome"]


class Analyzer(Protocol):
    def analyze_page(self, page: RawPage, asset_dir: str) -> list[dict]:
        ...


# --------------------------------------------------------------------------- #
# Claude (vision) analyzer
# --------------------------------------------------------------------------- #

# Structure analysis is a layout/classification task (not translation), so a faster,
# cheaper tier is plenty and keeps the per-page latency low. Override via env.
ANALYZER_MODEL = os.environ.get("ANALYZER_MODEL", "claude-sonnet-4-6")

# Structure emission for a dense page (a large table rebuilt as rows x cells, each carrying
# its span ids) is token-heavy. The old fixed 8000 cap truncated such pages mid-tool-call,
# leaving an incomplete / non-dict tool_use input — the whole table was then silently lost
# and its spans scattered as loose paragraphs. Raise the cap well above 8000 (a cap only
# bills for tokens actually emitted, so this is free for ordinary pages) and surface any
# remaining truncation instead of failing silently. Tunable via env.
# 16000 is ~2x the densest observed need (a full 9-column AQL grid measured at 8431 output
# tokens, which the old 8000 cap clipped) and stays under the SDK's non-streaming ceiling.
_MAX_TOKENS = int(os.environ.get("ANALYZER_MAX_TOKENS", "16000"))

_SYSTEM = """You are a document-structure analyst. You receive a rendered page image \
and the exact text spans extracted from that page (with ids and positions). Your job \
is to reconstruct the page's LOGICAL structure as an ordered list of blocks.

Rules:
- Use the span text VERBATIM. Never translate, paraphrase, fix, or invent text.
- Every span id must be assigned to exactly one block (except pure decoration).
- Separate literal numbering/markers (e.g. "4.", "4.1.1", "1)", "가)") into the
  `marker` field; put the remaining sentence in `text`. Markers are structural.
- Classify repeating page headers/footers and page numbers as type "chrome".
- For tables, reconstruct the real grid (rows x cells) with rowspan/colspan and mark
  header cells. Put each cell's spans in its source_span_ids. For each cell, set
  `shading` to its background fill as RRGGBB hex (omit when the cell is white/unshaded)
  and set `align`/`valign` to the cell text's horizontal/vertical alignment.
- For a vertically-stacked text label (a row-spanning cell whose characters each sit on
  their own line at the same x-position, e.g. a tall left-hand label), merge ALL those
  spans into that one cell: set vertical=true, put EVERY character's id in
  source_span_ids, and set text to the concatenated label. Never leave such a cell empty.
- For figures/diagrams/graphs, emit a "figure" block with figure_bbox = the pixel-ish
  region in PDF points [x0,y0,x1,y1] covering the drawing; do not put its label spans
  into text.
- For a "figure" block, set `has_text` true if the figure/diagram contains readable text
  worth translating (chart labels, callouts, a screenshot's words); false for a logo, photo,
  or pure graphic with no translatable text.
- Output reading order top-to-bottom, left-to-right.
"""


def _tool_schema() -> dict:
    cell = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "rowspan": {"type": "integer", "default": 1},
            "colspan": {"type": "integer", "default": 1},
            "is_header": {"type": "boolean", "default": False},
            "shading": {"type": "string",
                        "description": "cell background fill as RRGGBB hex; omit if none/white"},
            "align": {"type": "string", "enum": ["left", "center", "right"]},
            "valign": {"type": "string", "enum": ["top", "center", "bottom"]},
            "vertical": {"type": "boolean", "default": False,
                         "description": "text written vertically (stacked single characters) "
                                        "-- render rotated 90 degrees"},
            "source_span_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["text"],
    }
    block = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": BLOCK_TYPES},
            "level": {"type": "integer"},
            "marker": {"type": "string"},
            "text": {"type": "string"},
            "rows": {"type": "array", "items": {"type": "array", "items": cell}},
            "figure_bbox": {
                "type": "array",
                "items": {"type": "number"},
                "minItems": 4,
                "maxItems": 4,
            },
            "has_text": {
                "type": "boolean",
                "description": "for a figure block: true if the figure contains readable text "
                               "worth translating (labels/sentences), false for logos/photos/"
                               "pure graphics",
            },
            "source_span_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["type"],
    }
    return {
        "type": "object",
        "properties": {"blocks": {"type": "array", "items": block}},
        "required": ["blocks"],
    }


def _recover_json_array_prefix(s: str) -> list[dict]:
    """Parse a malformed/truncated JSON array object-by-object, returning the LEADING objects
    that parse cleanly (stop at the first that doesn't). The model frequently serialises the
    `blocks` array as a string and leaves quotes inside the Korean text unescaped, so the blob
    breaks partway — recovering the valid prefix keeps the page's leading structure (title,
    revision dates, headings) instead of dropping the whole page to the text-recovery net."""
    dec = json.JSONDecoder()
    s = s.lstrip()
    if s.startswith("["):
        s = s[1:]
    out: list[dict] = []
    while True:
        s = s.lstrip().lstrip(",").lstrip()
        if not s or s[0] == "]":
            break
        try:
            obj, idx = dec.raw_decode(s)
        except ValueError:
            break
        if isinstance(obj, dict):
            out.append(obj)
        s = s[idx:]
    return out


def _coerce_blocks(value) -> list[dict]:
    """Normalise the tool's `blocks` field to a list. Usually it's already an array; sometimes
    the model serialises it as a JSON STRING (occasionally malformed). Parse the string — full
    if it's valid, else the recoverable prefix — rather than discarding real structure."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except ValueError:
            return _recover_json_array_prefix(value)
    return []


class ClaudeAnalyzer:
    def __init__(self, client=None, model: str = ANALYZER_MODEL):
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def analyze_page(self, page: RawPage, asset_dir: str) -> list[dict]:
        img_path = os.path.join(asset_dir, page.render_path)
        with open(img_path, "rb") as fh:
            img_b64 = base64.standard_b64encode(fh.read()).decode()

        spans_payload = [
            {
                "id": s.id,
                "text": s.text,
                "bbox": [round(v, 1) for v in s.bbox],
                "size": s.size,
                "bold": s.bold,
            }
            for s in page.spans
        ]

        tool = {
            "name": "emit_structure",
            "description": "Return the page's logical block structure.",
            "input_schema": _tool_schema(),
        }
        content = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
            },
            {
                "type": "text",
                "text": "Page spans (JSON):\n" + json.dumps(spans_payload, ensure_ascii=False),
            },
        ]
        blocks, stop_reason = self._emit(tool, content)
        if not blocks and stop_reason != "max_tokens":
            # A genuinely empty result (the vision model returned no structure — a miss, not a
            # truncation) drops the whole page to the reconciler's text-recovery net, losing
            # its structure. Vision is stochastic, so retry once before giving up.
            blocks, stop_reason = self._emit(tool, content)
        if stop_reason == "max_tokens":
            print(
                f"[analyzer] WARNING: structure output truncated at max_tokens "
                f"({_MAX_TOKENS}) on page render={page.render_path!r}; a large table on this "
                f"page may be incomplete. Raise ANALYZER_MAX_TOKENS if this recurs.",
                file=sys.stderr,
                flush=True,
            )
        return blocks

    def _emit(self, tool: dict, content: list) -> tuple[list[dict], str | None]:
        """One emit_structure call -> (blocks, stop_reason). blocks is [] when the tool_use
        is absent or its input is partial/non-dict (a truncated call)."""
        msg = self.client.messages.create(
            model=self.model,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM,
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit_structure"},
            messages=[{"role": "user", "content": content}],
        )
        blocks: list[dict] = []
        for part in msg.content:
            if part.type == "tool_use" and part.name == "emit_structure":
                # The input can be partial/non-dict on truncation, or "blocks" can come back as
                # a stringified (sometimes malformed) JSON blob instead of an array. Coerce it:
                # parse the string and recover what we can, so a serialisation quirk doesn't
                # drop the page's whole structure (→ blank/mangled recovery output).
                if isinstance(part.input, dict):
                    blocks = _coerce_blocks(part.input.get("blocks", []))
        return blocks, msg.stop_reason


class OpenAIAnalyzer:
    """GPT vision + spans JSON (same tool schema as ClaudeAnalyzer)."""

    def __init__(self, client=None, model: str | None = None):
        self._client = client
        self.model = model or openai_model(
            dedicated_env="OPENAI_ANALYZER_MODEL",
            shared_env="ANALYZER_MODEL",
            default="gpt-4o",
        )

    def analyze_page(self, page: RawPage, asset_dir: str) -> list[dict]:
        img_path = os.path.join(asset_dir, page.render_path)
        with open(img_path, "rb") as fh:
            img_b64 = base64.standard_b64encode(fh.read()).decode()

        spans_payload = [
            {
                "id": s.id,
                "text": s.text,
                "bbox": [round(v, 1) for v in s.bbox],
                "size": s.size,
                "bold": s.bold,
            }
            for s in page.spans
        ]
        tool = {
            "name": "emit_structure",
            "description": "Return the page's logical block structure.",
            "parameters": _tool_schema(),
        }
        user_content = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{img_b64}"},
            },
            {
                "type": "text",
                "text": "Page spans (JSON):\n" + json.dumps(spans_payload, ensure_ascii=False),
            },
        ]
        blocks, stop_reason = self._emit(tool, user_content)
        if not blocks and stop_reason != "length":
            blocks, stop_reason = self._emit(tool, user_content)
        if stop_reason == "length":
            print(
                f"[analyzer] WARNING: structure output truncated at max_tokens "
                f"({_MAX_TOKENS}) on page render={page.render_path!r}; a large table on this "
                f"page may be incomplete. Raise ANALYZER_MAX_TOKENS if this recurs.",
                file=sys.stderr,
                flush=True,
            )
        return blocks

    def _emit(self, tool: dict, user_content: list) -> tuple[list[dict], str | None]:
        parsed, finish = openai_tool_call(
            model=self.model,
            system=_SYSTEM,
            user_content=user_content,
            tool_name=tool["name"],
            tool_description=tool["description"],
            parameters=tool["parameters"],
            max_tokens=_MAX_TOKENS,
        )
        blocks: list[dict] = []
        if isinstance(parsed, dict):
            blocks = _coerce_blocks(parsed.get("blocks", []))
        return blocks, finish


# --------------------------------------------------------------------------- #
# Heuristic analyzer (offline fallback)
# --------------------------------------------------------------------------- #

# Leading markers common in KR standards docs: 4.  4.1  4.1.1  1)  가)  ① etc.
_MARKER_RE = re.compile(
    r"^\s*("
    r"\d+(?:\.\d+)*\.?"          # 4   4.1   4.1.1   4.
    r"|\d+\)"                    # 1)
    r"|[가-힣]\)"                # 가)
    r"|[①-⑮]"                   # circled numbers
    r")\s+(.*)$"
)


class HeuristicAnalyzer:
    """Cheap structure guess from font size + markers. Lossless on text by design:
    every span becomes (part of) exactly one block."""

    def analyze_page(self, page: RawPage, asset_dir: str) -> list[dict]:
        if not page.spans:
            return []
        sizes = sorted({round(s.size) for s in page.spans})
        body = max(set(round(s.size) for s in page.spans), key=lambda z: sum(
            1 for s in page.spans if round(s.size) == z))
        big = max(sizes)

        # group consecutive spans on the same text line (similar y) into one block
        blocks: list[dict] = []
        page_h = page.height
        for s in page.spans:
            y0 = s.bbox[1]
            text = s.text.strip()
            ids = [s.id]
            # chrome: very top/bottom band
            if y0 < page_h * 0.06 or y0 > page_h * 0.94:
                blocks.append({"type": "chrome", "text": text, "source_span_ids": ids})
                continue
            m = _MARKER_RE.match(s.text)
            if m:
                marker, rest = m.group(1), m.group(2).strip()
                if round(s.size) >= big and not rest_is_long(rest):
                    blocks.append({"type": "heading", "level": 1, "marker": marker,
                                   "text": rest, "source_span_ids": ids})
                else:
                    blocks.append({"type": "list_item", "marker": marker, "level": 0,
                                   "text": rest, "source_span_ids": ids})
            elif round(s.size) >= big:
                blocks.append({"type": "heading", "level": 1, "text": text,
                               "source_span_ids": ids})
            else:
                blocks.append({"type": "paragraph", "text": text, "source_span_ids": ids})
        return blocks


def rest_is_long(rest: str) -> bool:
    return len(rest) > 40


# --------------------------------------------------------------------------- #


def get_analyzer(mode: str | None = None) -> Analyzer:
    mode = (mode or os.environ.get("ANALYZER_MODE", "claude")).lower()
    if mode == "heuristic":
        return HeuristicAnalyzer()
    provider = resolve_llm_provider()
    if provider == "openai":
        return OpenAIAnalyzer()
    if provider == "anthropic":
        return ClaudeAnalyzer()
    return HeuristicAnalyzer()
