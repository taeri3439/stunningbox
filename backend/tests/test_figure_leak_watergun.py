"""Fidelity wave 2, workstream C: in-figure baked text ("접합부"/"Joint") must never leak
as a detached body-text line near the [별도 5]/Appendix-5 standard-nozzle drawing.

Runs against an isolated dev-storage copy (`_fig-verify-watergun`, copied from the fresh
watergun fixture named in the spec) so the original fixture is untouched. Skips when that
copy is absent (dev-local only — `backend/storage/` is gitignored).

Investigation finding (see the report + test_reconciler_figure_recovery.py's docstring for
the full writeup): on this exact fixture, page_blocks.json's block for the nozzle page is

    {"type": "figure", "figure_bbox": [56, 95, 590, 380], "has_text": true}

with NO separate "접합부" text block anywhere on the page (and no text span on that page
contains "접합" either — the label is baked into the drawing). `has_text` already routes it
through the figure-OCR path (pipeline/figure_ocr.py), which turns a baked label into a
FigureOverlay instead of floating body text — so this fixture's CURRENT cached analysis
does not, by itself, exercise the new span-less/figure-adjacent drop rule. This test is
therefore primarily a regression guard (no leak on real data, pinned by an assertion) and a
holistic check on the nozzle page's shape; the rule itself is exercised directly by the
synthetic fixtures in test_reconciler_figure_recovery.py.
"""
import os

import pytest

STORAGE = os.path.join(os.path.dirname(__file__), "..", "storage")
DOC = "_fig-verify-watergun"
NOZZLE_PAGE = 23  # 0-indexed: "[별도 5] 표준노즐(직사형)" / "[Appendix 5] Standard Nozzle"


@pytest.mark.skipif(
    not os.path.exists(os.path.join(STORAGE, DOC, "page_blocks.json")),
    reason="dev-local cached fixture not present",
)
def test_watergun_nozzle_page_has_no_baked_label_leak():
    from pipeline import process

    pdf = os.path.join(STORAGE, DOC, "source.pdf")
    doc, report = process.rebuild_source_model(
        pdf, DOC, os.path.abspath(STORAGE), source_lang="ko")

    nozzle_blocks = [b for b in doc.blocks if getattr(b, "page", None) == NOZZLE_PAGE]
    assert nozzle_blocks, "the nozzle page should still contribute blocks"
    assert any(b.type == "figure" for b in nozzle_blocks), \
        "the nozzle drawing should still render as a figure"

    # No body text block on the nozzle page carries the baked-in label (source-language
    # "접합부", or, if translated, "Joint") — whether the analyzer routed it through the
    # figure's own has_text/OCR path (the case on this fixture) or, hypothetically, emitted
    # it as a stray span-less block (the general leak this workstream's rule guards against).
    for b in nozzle_blocks:
        if b.type == "figure":
            continue  # the drawing's own (possibly OCR'd) content is not a body-text leak
        text = getattr(b, "text", "") or ""
        assert "접합부" not in text and "Joint" not in text, (
            f"baked-in figure label leaked as a body {b.type} block: {text!r}"
        )

    # Sanity: report.json round-trips cleanly (rebuild_source_model persisted it) — a
    # crash-free run on real data end to end.
    assert isinstance(report.get("warnings"), list)
