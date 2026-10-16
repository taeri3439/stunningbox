"""Single source of truth for the diagnosis: scoring dimensions, the 0-4 rubric, the
severity mapping, the pydantic result models, and the forced-tool JSON schema used by
the vision scorer. `render`, `score`, and `aggregate` all import from here.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# Fixed fidelity dimensions. These deliberately cover the model's known blind spots
# (no font family, no colour, no italic/underline, no cell shading, no columns) so the
# biggest gaps surface. Each is scored 0-4, or null when the page has no instance.
DIMENSIONS = [
    "font_family",       # serif/sans + specific family match
    "text_color",        # text colour vs source (vs everything forced near-black)
    "emphasis",          # bold / italic / underline presence on the right runs
    "alignment",         # left/center/right/justify per block
    "indent",            # left indentation depth
    "line_spacing",      # leading / paragraph spacing
    "table_structure",   # grid shape, merges, header row present
    "table_shading",     # cell fills + border weight
    "figure_placement",  # figure present, position, relative size
    "figure_wrap",       # text wraps beside figure vs stacked above/below
    "column_layout",     # multi-column body preserved vs flattened
    "linebreak_fidelity",# structural breaks (lists/legends/addresses/TOC) — NOT body wrap
    "header_footer",     # running header/footer reproduced
    "heading_hierarchy", # heading levels/sizes tier correctly
    "overall_layout",    # gestalt: does the page read like the same document
]

SEVERITIES = ["none", "low", "medium", "high"]

# One human-readable line per dimension; embedded into the scorer prompt and emitted to
# the report so a reader knows exactly what each score means.
RUBRIC = {
    "font_family": "Does the rendering use the same font family/character (serif vs sans, weight feel) as the source?",
    "text_color": "Do coloured texts (headings, emphasis) keep their source colour, rather than all going near-black?",
    "emphasis": "Are bold/italic/underlined runs emphasised in the same places as the source?",
    "alignment": "Is each block's horizontal alignment (left/center/right/justify) the same?",
    "indent": "Are left indents / nesting depths reproduced?",
    "line_spacing": "Is line/paragraph spacing visually comparable (not cramped or ballooned beyond translation length)?",
    "table_structure": "Are tables rendered AS tables with the same grid shape, merges, and header row?",
    "table_shading": "Do cells keep their background fills and border weights?",
    "figure_placement": "Is each figure present, in the same position and at a comparable relative size?",
    "figure_wrap": "Where text wrapped beside a figure in the source, does it still wrap beside it (not stacked)?",
    "column_layout": "Is multi-column body text preserved as columns rather than flattened to one column?",
    "linebreak_fidelity": "Are STRUCTURAL line breaks (lists, legends, addresses, TOC) preserved, ignoring pure body reflow?",
    "header_footer": "Is the running header/footer reproduced (text + rule)?",
    "heading_hierarchy": "Do heading levels read in the same size/weight tiers as the source?",
    "overall_layout": "Overall, does the page read like the same document at a glance?",
}

# 0-4 scale, FORMAT fidelity only (text wording/length differences are translation and
# must never be penalised).
SCORE_RUBRIC = (
    "4 = indistinguishable from source · 3 = minor deviation, reading impression "
    "unchanged · 2 = noticeable, a reviewer would flag it · 1 = major, changes the "
    "page's character/structure · 0 = absent or wrong (e.g. a table rendered as plain "
    "paragraphs, a figure missing). Use null when the source page has no instance of "
    "the dimension (e.g. no tables, no figures)."
)


def severity_for(score: Optional[int]) -> str:
    """Deterministic score -> severity. We derive severity ourselves (never trust the
    model's), so the mapping is consistent across the whole report."""
    if score is None:
        return "none"
    if score >= 4:
        return "none"
    if score == 3:
        return "low"
    if score == 2:
        return "medium"
    return "high"  # 0 or 1


class DimensionFinding(BaseModel):
    dimension: str
    score: Optional[int] = None  # 0..4, or None = not applicable on this page
    severity: str = "none"
    observation: str = ""
    evidence: str = ""


class StructuralCheck(BaseModel):
    """No-API ground-truth backstop derived from source.json + the rendered docx."""

    source_tables: int = 0
    rendered_tables: int = 0
    source_figures: int = 0
    rendered_figures: int = 0
    source_headings: int = 0
    notes: list[str] = Field(default_factory=list)


class PageFinding(BaseModel):
    doc_id: str = ""
    page_index: int = 0
    pair_png: str = ""  # relative path to the side-by-side composite
    dimensions: list[DimensionFinding] = Field(default_factory=list)
    page_score: Optional[float] = None  # mean of non-null dimension scores
    notes: str = ""

    def recompute(self) -> "PageFinding":
        """Fill severities from scores and the mean page_score."""
        scored = []
        for d in self.dimensions:
            d.severity = severity_for(d.score)
            if d.score is not None:
                scored.append(d.score)
        self.page_score = round(sum(scored) / len(scored), 2) if scored else None
        return self


def tool_schema() -> dict:
    """input_schema for the forced `emit_fidelity` tool. The model returns one finding
    per dimension; we compute severity ourselves from the score."""
    finding = {
        "type": "object",
        "properties": {
            "dimension": {"type": "string", "enum": DIMENSIONS},
            "score": {"type": ["integer", "null"], "minimum": 0, "maximum": 4},
            "observation": {"type": "string"},
            "evidence": {"type": "string"},
        },
        "required": ["dimension", "score", "observation"],
    }
    return {
        "type": "object",
        "properties": {
            "dimensions": {"type": "array", "items": finding},
            "notes": {"type": "string"},
        },
        "required": ["dimensions"],
    }
