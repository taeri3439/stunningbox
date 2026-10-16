"""Sparse-page vertical rhythm (Task 2 of the template-fidelity feature).

Generalizes the existing cover-rhythm measurement (`_apply_cover_rhythm`, which records
a designed cover's deliberate title/subtitle spacing into `space_before`) to ordinary
body pages. A disclaimer page whose text starts halfway down, or a chart page with a
large trailing blank run, carries no y-geometry once reconciled into the semantic model
either — this measures that gap into `page_space_before` (T1's field) so a later stage
(T3, the renderer) can reproduce it. This module only measures + stores; it never touches
`space_before` and never runs on the page-0 cover path (regression-protected by
test_cover_rhythm.py, exercised again here from the page-rhythm side).

Gate (see `_apply_page_rhythm`'s docstring for the full rationale): a page only gets
measured when it has <= RHYTHM_MAX_TEXT_BLOCKS text blocks, no TableBlock, and at least
one gap >= RHYTHM_MIN_GAP_PT (40pt) somewhere on the page.
"""
import pipeline.reconciler as reconciler_mod
from models import FigureBlock, RawDocument, RawPage, RawSpan
from pipeline import extractor, figure_overlay
from pipeline.reconciler import reconcile


def _span(sid, bbox, text="x", size=10.0):
    return RawSpan(id=sid, page=0, text=text, bbox=bbox, size=size)


def _reconcile(raw, page_blocks, tmp_path):
    return reconcile(raw, page_blocks, str(tmp_path / "x.pdf"), str(tmp_path), source_lang="ko")


# --------------------------------------------------------------------------- #
# Sparse page: measured into page_space_before
# --------------------------------------------------------------------------- #


def test_sparse_page_first_block_measured_and_later_block_above_noise_floor(tmp_path):
    """3 text blocks on a 400pt-tall page; the first starts at y=200 (halfway down). A
    second page anchors the document's estimated top margin at 50pt (its own paragraph
    sits right at that margin) so the first block's gap is measured against a realistic
    baseline, not one dragged up by the sparse page's own content."""
    spans_p0 = [
        _span("pA", (100, 200, 300, 212), text="Block A"),
        # gap from A's bottom (212) to B's top (232) = 20pt (>6pt noise floor, no table,
        # <=8 text blocks — qualifies once the page-level gate below is satisfied).
        _span("pB", (100, 232, 300, 244), text="Block B"),
        # gap from B's bottom (244) to C's top (248) = 4pt (<=6pt noise floor) — must NOT
        # be measured even though the page passes its gate.
        _span("pC", (100, 248, 300, 260), text="Block C."),
    ]
    spans_p1 = [_span("anchor", (100, 50, 300, 62), text="Anchor")]
    raw = RawDocument(pages=[
        RawPage(width=600, height=400, spans=spans_p0),
        RawPage(width=600, height=400, spans=spans_p1),
    ])
    page_blocks = [
        [
            {"type": "paragraph", "text": "Block A", "source_span_ids": ["pA"]},
            {"type": "paragraph", "text": "Block B", "source_span_ids": ["pB"]},
            {"type": "paragraph", "text": "Block C.", "source_span_ids": ["pC"]},
        ],
        [{"type": "paragraph", "text": "Anchor", "source_span_ids": ["anchor"]}],
    ]
    doc, _report = _reconcile(raw, page_blocks, tmp_path)
    assert doc.cover_background is None  # fixture sanity: not a cover

    page0 = [b for b in doc.blocks if getattr(b, "page", None) == 0]
    a = next(b for b in page0 if b.text == "Block A")
    b = next(b for b in page0 if b.text == "Block B")
    c = next(b for b in page0 if b.text == "Block C.")

    # margin_top is estimated at 50pt (the anchor page's own paragraph sits there); A's
    # measured gap = 200 - 50 = 150pt.
    assert a.page_space_before is not None
    assert 149.0 < a.page_space_before < 151.0

    # B's inter-block gap (20pt) clears the 6pt noise floor.
    assert b.page_space_before is not None
    assert 19.0 < b.page_space_before < 21.0

    # C's inter-block gap (4pt) does not.
    assert c.page_space_before is None


def test_dense_page_gate_fails_nothing_measured(tmp_path):
    """9 text blocks (> RHYTHM_MAX_TEXT_BLOCKS) on one page — even though the first block
    sits far below the others (a large, gate-worthy gap on its own), the page-wide text-
    block-count cap must suppress ALL measurement on the page."""
    spans = [_span("p0", (100, 300, 300, 312), text="Block 0")]
    for i in range(1, 9):
        top = 312 + (i - 1) * 14
        spans.append(_span(f"p{i}", (100, top, 300, top + 12), text=f"Block {i}"))
    raw = RawDocument(pages=[RawPage(width=600, height=800, spans=spans)])
    page_blocks = [[
        {"type": "paragraph", "text": f"Block {i}", "source_span_ids": [f"p{i}"]}
        for i in range(9)
    ]]
    doc, _report = _reconcile(raw, page_blocks, tmp_path)

    page0 = [b for b in doc.blocks if getattr(b, "page", None) == 0]
    assert len(page0) == 9  # fixture sanity: every span was assigned, none recovered
    assert all(b.page_space_before is None for b in page0)


def test_page_with_tableblock_nothing_measured(tmp_path):
    """A page carrying a TableBlock never gets ANY block measured — including an
    unrelated sparse paragraph with a clearly gate-worthy gap from the page's estimated
    top margin — since a rebuilt table's row heights aren't a reliable anchor for a
    neighboring gap."""
    table_rows = [
        [{"text": "h1", "source_span_ids": ["t00"]}, {"text": "h2", "source_span_ids": ["t01"]}],
        [{"text": "v1", "source_span_ids": ["t10"]}, {"text": "v2", "source_span_ids": ["t11"]}],
    ]
    spans = [
        _span("p1", (100, 200, 300, 212), text="Lonely paragraph"),
        _span("t00", (100, 300, 200, 312), text="h1"),
        _span("t01", (200, 300, 300, 312), text="h2"),
        _span("t10", (100, 314, 200, 326), text="v1"),
        _span("t11", (200, 314, 300, 326), text="v2"),
    ]
    raw = RawDocument(pages=[RawPage(width=600, height=500, spans=spans)])
    page_blocks = [[
        {"type": "paragraph", "text": "Lonely paragraph", "source_span_ids": ["p1"]},
        {"type": "table", "rows": table_rows},
    ]]
    doc, _report = _reconcile(raw, page_blocks, tmp_path)

    page0 = [b for b in doc.blocks if getattr(b, "page", None) == 0]
    para = next(b for b in page0 if getattr(b, "text", None) == "Lonely paragraph")
    assert para.page_space_before is None


def test_sparse_page_all_gaps_below_min_gate_fails(tmp_path):
    """3 text blocks, sparse (<=8) and table-free, but every gap on the page (the first
    block's gap from the estimated margin, and both inter-block gaps) is well under the
    40pt page-level gate — nothing gets measured, even though the inter-block gaps (12pt,
    26pt) individually clear the 6pt per-block noise floor."""
    spans = [
        _span("pA", (100, 100, 300, 112), text="Block A"),
        _span("pB", (100, 132, 300, 144), text="Block B"),  # gap from A's bottom = 20pt
        _span("pC", (100, 170, 300, 182), text="Block C."),  # gap from B's bottom = 26pt
    ]
    raw = RawDocument(pages=[RawPage(width=600, height=400, spans=spans)])
    page_blocks = [[
        {"type": "paragraph", "text": "Block A", "source_span_ids": ["pA"]},
        {"type": "paragraph", "text": "Block B", "source_span_ids": ["pB"]},
        {"type": "paragraph", "text": "Block C.", "source_span_ids": ["pC"]},
    ]]
    doc, _report = _reconcile(raw, page_blocks, tmp_path)

    page0 = [b for b in doc.blocks if getattr(b, "page", None) == 0]
    assert all(b.page_space_before is None for b in page0)


# --------------------------------------------------------------------------- #
# Figure geometry: a FigureBlock's real bottom, not its top, anchors the next
# block's gap (Finding 1) — and the page-level gate only fires on gaps that are
# actually eligible to be written (Finding 2).
# --------------------------------------------------------------------------- #


def test_figure_then_caption_measures_true_gap_never_giant_whitespace(tmp_path, monkeypatch):
    """A caption sitting a true 10pt below a 400pt-tall figure must never measure the
    ~410pt top-of-figure-to-caption span (the old bug: FigureBlock has no
    `source_span_ids` field at all, so the walk's prev-bottom fell back to the figure's
    TOP y instead of its real bottom) — that measured a giant gap, clamped to
    COVER_RHYTHM_MAX_GAP (300pt), on exactly the target scenario (a chart page with a
    caption underneath it). The heading's own genuine 200pt margin-to-text gap is what
    legitimately qualifies the page-level gate here, independent of the figure."""
    monkeypatch.setattr(extractor, "render_clip",
                         lambda pdf, pi, bbox, ad, name: (name, tuple(bbox)))
    heading_span = _span("h1", (100, 250, 300, 265), text="Chart Title")
    caption_span = _span("c1", (100, 680, 300, 695), text="Figure 1: caption")
    anchor_span = _span("anchor", (100, 50, 300, 62), text="Anchor")
    raw = RawDocument(pages=[
        RawPage(width=600, height=1000, spans=[heading_span, caption_span]),
        RawPage(width=600, height=1000, spans=[anchor_span]),
    ])
    page_blocks = [
        [
            {"type": "heading", "text": "Chart Title", "source_span_ids": ["h1"]},
            # A 400pt-tall figure (bbox height = 670 - 270); the caption sits only 10pt
            # below its true bottom (670), not below its top (270).
            {"type": "figure", "figure_bbox": [100, 270, 500, 670], "source_span_ids": []},
            {"type": "caption", "text": "Figure 1: caption", "source_span_ids": ["c1"]},
        ],
        [{"type": "paragraph", "text": "Anchor", "source_span_ids": ["anchor"]}],
    ]
    doc, _report = _reconcile(raw, page_blocks, tmp_path)

    page0 = [b for b in doc.blocks if getattr(b, "page", None) == 0]
    assert any(isinstance(b, FigureBlock) for b in page0)  # fixture sanity: figure survived
    caption = next(b for b in page0 if getattr(b, "text", None) == "Figure 1: caption")

    # Never the old giant-whitespace bug (~410pt gap clamped to 300pt).
    assert caption.page_space_before != 300.0
    # The true measured gap (~10pt, figure bottom = top + height) — must never be huge.
    assert caption.page_space_before is not None
    assert 9.0 < caption.page_space_before < 11.0


def test_gate_ignores_figure_boundary_gap_page_does_not_qualify(tmp_path, monkeypatch):
    """A page whose ONLY >=40pt gap sits between the page margin and a FIGURE (not a text
    block) must not qualify the page-level gate. The gate must only consider gaps that are
    actually eligible to be WRITTEN (text-block gaps, per the setter's own isinstance
    check) — an unrelated large margin-to-figure gap must not drag along an unrelated
    small (but > the 6pt noise floor) text gap elsewhere on the page. Here the caption's
    own real gap below the figure is 20pt: > the per-block noise floor, but < the 40pt
    page-level gate on its own — it must stay unmeasured."""
    monkeypatch.setattr(extractor, "render_clip",
                         lambda pdf, pi, bbox, ad, name: (name, tuple(bbox)))
    caption_span = _span("c1", (100, 280, 300, 292), text="Cap")
    anchor_span = _span("anchor", (100, 50, 300, 62), text="Anchor")
    raw = RawDocument(pages=[
        RawPage(width=600, height=500, spans=[caption_span]),
        RawPage(width=600, height=500, spans=[anchor_span]),
    ])
    page_blocks = [
        [
            # margin(50) -> figure top(200) = 150pt gap: figure-boundary, not text-eligible.
            {"type": "figure", "figure_bbox": [100, 200, 500, 260], "source_span_ids": []},
            # figure bottom(260) -> caption top(280) = 20pt: real, but under the 40pt gate.
            {"type": "caption", "text": "Cap", "source_span_ids": ["c1"]},
        ],
        [{"type": "paragraph", "text": "Anchor", "source_span_ids": ["anchor"]}],
    ]
    doc, _report = _reconcile(raw, page_blocks, tmp_path)

    page0 = [b for b in doc.blocks if getattr(b, "page", None) == 0]
    assert any(isinstance(b, FigureBlock) for b in page0)  # fixture sanity: figure survived
    caption = next(b for b in page0 if getattr(b, "text", None) == "Cap")
    assert caption.page_space_before is None


# --------------------------------------------------------------------------- #
# Cover page: existing space_before path fires; page_space_before must not
# --------------------------------------------------------------------------- #


def test_cover_page_keeps_space_before_and_never_gets_page_space_before(tmp_path, monkeypatch):
    """A detected text cover (page 0) must keep going through `_apply_cover_rhythm`
    exactly as before (space_before set) and must NOT also pick up page_space_before —
    the renderer would otherwise double-space it once T3 lands."""
    title_span = _span("title", (60, 250, 400, 300), text="COVER TITLE", size=42.0)
    sub_span = _span("sub", (60, 500, 300, 514), text="Subtitle line.", size=14.0)
    body_span = _span("body", (60, 60, 300, 72), text="Body paragraph text on page two.")
    raw = RawDocument(pages=[
        RawPage(width=600, height=800, spans=[title_span, sub_span]),
        RawPage(width=600, height=800, spans=[body_span]),
    ])
    page_blocks = [
        [
            {"type": "heading", "text": "COVER TITLE", "source_span_ids": ["title"]},
            {"type": "paragraph", "text": "Subtitle line.", "source_span_ids": ["sub"]},
        ],
        [{"type": "paragraph", "text": "Body paragraph text on page two.",
          "source_span_ids": ["body"]}],
    ]

    monkeypatch.setattr(reconciler_mod, "_page_nonwhite_frac", lambda *a, **k: 0.05)
    monkeypatch.setattr(figure_overlay, "render_redacted_clip", lambda *a, **k: "cover_bg.png")
    doc, _report = _reconcile(raw, page_blocks, tmp_path)

    assert doc.cover_background == "cover_bg.png"  # fixture sanity: cover was detected

    page0 = [b for b in doc.blocks if getattr(b, "page", None) == 0]
    title = next(b for b in page0 if getattr(b, "text", "") == "COVER TITLE")
    subtitle = next(b for b in page0 if getattr(b, "text", "") == "Subtitle line.")

    assert subtitle.space_before is not None  # existing cover-rhythm behavior, unchanged
    assert subtitle.page_space_before is None
    assert title.page_space_before is None
