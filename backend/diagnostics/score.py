"""Vision scorer: one Claude call per page pair -> structured per-dimension findings.

Copies the analyzer's forced-tool idiom (base64 image blocks + tool_choice). The prompt
framing is load-bearing: the RIGHT image is a TRANSLATION, so text wording/length WILL
differ — that must NEVER be penalised. Only FORMAT fidelity is scored.
"""
from __future__ import annotations

import base64
import os
from typing import Optional

from diagnostics import schema
from diagnostics.pair import PagePair

DIAG_MODEL = os.environ.get("DIAG_MODEL", "claude-sonnet-4-6")


def _b64(path: str) -> str:
    with open(path, "rb") as fh:
        return base64.standard_b64encode(fh.read()).decode()


def _prompt(structural_notes: list[str]) -> str:
    lines = "\n".join(f"- {d}: {schema.RUBRIC[d]}" for d in schema.DIMENSIONS)
    extra = ""
    if structural_notes:
        extra = (
            "\n\nDeterministic structural check already found (treat as ground truth, "
            "weight the relevant dimensions accordingly):\n"
            + "\n".join(f"- {n}" for n in structural_notes)
        )
    return f"""You are auditing FORMAT fidelity of a document translation.

The FIRST image is the ORIGINAL page (source language). The SECOND image is OUR \
TRANSLATED rendering of the SAME page.

CRITICAL: the text wording AND length WILL DIFFER (it is translated and reflowed). This \
is EXPECTED and must NEVER be penalised. Do NOT score text equality, language, spelling, \
or line wrapping that is caused purely by translation length. Score ONLY the visual / \
structural FORMAT fidelity of the rendering relative to the original.

Score each dimension below 0-4 (or null if the original page has no instance of it):
{schema.SCORE_RUBRIC}

Dimensions:
{lines}{extra}

Return one finding per dimension via the emit_fidelity tool. In `observation` state the \
concrete difference; in `evidence` cite where on the page (e.g. 'heading row', 'body \
throughout', 'table on lower half')."""


def score_pair(pair: PagePair, structural_notes: Optional[list[str]] = None,
               client=None, model: str = DIAG_MODEL) -> schema.PageFinding:
    """Score one page pair. Sends the source and rendered PNGs as two labelled image
    blocks. Returns a PageFinding with severities/page_score computed deterministically."""
    if client is None:
        import anthropic

        client = anthropic.Anthropic()

    tool = {
        "name": "emit_fidelityn   4w3eujitnhiuow3ethnouiwehoiwetioedxv i cizimii one format-fidelity finding per dimension.",
        "input_schema": schema.tool_schema(),
    }
    msg = client.messages.create(
        model=model,
        max_tokens=4000,
        temperature=0, 
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _prompt(structural_notes or [])},
                    {"type": "text", "text": "ORIGINAL page:"},
                    {"type": "image", "source": {"type": "base64",
                     "media_type": "image/png", "data": _b64(pair.source_png)}},
                    {"type": "text", "text": "TRANSLATED rendering:"},
                    {"type": "image", "source": {"type": "base64",
                     "media_type": "image/png", "data": _b64(pair.rendered_png)}},
                ],
            }
        ],
        tools=[tool],
        tool_choice={"type": "tool", "name": "emit_fidelity"},
    )
    raw = {}
    for part in msg.content:
        if part.type == "tool_use" and part.name == "emit_fidelity":
            raw = part.input
            break
    finding = schema.PageFinding(
        page_index=pair.index,
        dimensions=[schema.DimensionFinding(**d) for d in raw.get("dimensions", [])],
        notes=raw.get("notes", ""),
    )
    return finding.recompute()
