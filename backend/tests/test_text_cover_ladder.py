"""End-to-end (cached-analysis) check on the real ladder fixture (a white text cover:
huge title + sparse text + logo, 소방장비기본규격). Skips when the dev storage copy is
absent.

Verified against the real cached fixture (see docs/superpowers/plans/
2026-07-02-text-cover-preservation.md Task 3 debug step):
  - real extracted page-0 spans: 12 (<= COVER_MAX_SPANS=18); max span size 35.97pt
    (>= COVER_DISPLAY_FONT_PT=26.0); n_pages=21 (>=2) -> `_looks_like_text_cover` is
    True, exactly as designed.
  - non-white fraction of the rendered page-0 image: ~0.037, far below
    COVER_PAGE_NONWHITE_FRAC=0.4 (as expected — it's a WHITE cover).
  - the cached page_blocks.json analyzer output for page 0 carries 5 paragraph-type
    blocks (subtitle triple-line, KFS doc number, "Aerial Ladder Vehicle", committee
    line, URL) — more than the designed-cover COVER_MAX_BODY_BLOCKS=3 guard, but within
    the text-cover arm's own COVER_TEXT_MAX_BODY_BLOCKS=8 cap (2026-07-02 follow-up
    fix), so the text-cover arm now fires for this exact fixture and the ladder cover
    is correctly recognized end to end."""
import os

import pytest

STORAGE = os.path.join(os.path.dirname(__file__), "..", "storage")
DOC = "_cover-verify-ladder"


@pytest.mark.skipif(
    not os.path.exists(os.path.join(STORAGE, DOC, "page_blocks.json")),
    reason="dev-local cached fixture not present",
)
def test_ladder_page0_is_recognized_as_a_text_cover_shape():
    """The `_looks_like_text_cover` signal correctly reads the real ladder page 0 as
    cover-shaped: sparse spans + a display-size title."""
    from pipeline import extractor
    from pipeline.reconciler import (
        COVER_DISPLAY_FONT_PT,
        COVER_MAX_SPANS,
        _looks_like_text_cover,
    )

    base = os.path.join(STORAGE, DOC)
    pdf = os.path.join(base, "source.pdf")
    raw = extractor.extract_document(pdf, os.path.join(base, "assets"))
    p0_spans = raw.pages[0].spans
    max_size = max((s.size or 0.0) for s in p0_spans) if p0_spans else 0.0
    print(f"page-0 span count={len(p0_spans)} (max {COVER_MAX_SPANS}), "
          f"max span size={max_size} (need >= {COVER_DISPLAY_FONT_PT})")

    assert len(p0_spans) <= COVER_MAX_SPANS
    assert max_size >= COVER_DISPLAY_FONT_PT
    assert _looks_like_text_cover(p0_spans, len(raw.pages)) is True


@pytest.mark.skipif(
    not os.path.exists(os.path.join(STORAGE, DOC, "page_blocks.json")),
    reason="dev-local cached fixture not present",
)
def test_ladder_cover_fires_end_to_end():
    """The real ladder cover must actually be recognized end to end: page 0's 5
    paragraph-type blocks are within the text-cover arm's COVER_TEXT_MAX_BODY_BLOCKS=8
    cap (added 2026-07-02 so a text cover isn't blocked by the tighter designed-cover
    COVER_MAX_BODY_BLOCKS=3 guard), so `doc.cover_background` is set, the page's own doc
    number ("KFS 0011") survives as page-0 body text, and the rendered cover background
    PNG lands in the fixture's asset dir."""
    from pipeline import process

    base = os.path.join(STORAGE, DOC)
    pdf = os.path.join(base, "source.pdf")
    doc, _report = process.rebuild_source_model(pdf, DOC, os.path.abspath(STORAGE), source_lang="ko")

    assert doc.cover_background, "the ladder cover must be recognized (text-cover arm)"
    assert any(
        getattr(b, "page", None) == 0 and "KFS 0011" in (getattr(b, "text", "") or "")
        for b in doc.blocks
    ), "the cover's doc-number chrome must survive as page-0 body text"
    assert os.path.exists(os.path.join(base, "assets", doc.cover_background)), (
        "the rendered cover background PNG must exist in the fixture's asset dir")
