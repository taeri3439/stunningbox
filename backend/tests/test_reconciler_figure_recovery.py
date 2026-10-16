"""A flowchart/diagram captured as a figure keeps its box-label text baked into the rendered
image. Those text spans are left UNASSIGNED by the analyzer, so the recovery net used to
re-emit them as loose paragraphs — echoing the figure's internal labels as floating
translated text AND inflating the page count. Spans whose centre falls inside a figure's
clipped region must be treated as covered (by the image), never recovered as text."""
from models import FigureBlock, ParagraphBlock, RawDocument, RawPage, RawSpan
from pipeline import extractor, reconciler


def _span(sid, bbox, text):
    return RawSpan(id=sid, page=0, text=text, bbox=bbox, size=10.0)


def _patch(monkeypatch):
    monkeypatch.setattr(extractor, "detect_table_grids", lambda *a, **k: [])
    monkeypatch.setattr(extractor, "render_clip",
                        lambda pdf, pi, bbox, ad, name: ("fig.png", tuple(bbox)))


def test_figure_internal_spans_not_recovered(tmp_path, monkeypatch):
    _patch(monkeypatch)
    inside = _span("p0b5l0s0", (120, 220, 220, 235), "서류검토")        # within the figure bbox
    outside = _span("p0b9l0s0", (100, 700, 320, 715), "footnote text")  # below the figure
    page = RawPage(width=600.0, height=800.0,
                   spans=[inside, outside], images=[], render_path="page0.png")
    blocks = [[{"type": "figure", "figure_bbox": [100, 200, 520, 600],
                "source_span_ids": []}]]
    doc, report = reconciler.reconcile(
        RawDocument(pages=[page]), blocks, "x.pdf", str(tmp_path))

    paras = [b for b in doc.blocks if isinstance(b, ParagraphBlock)]
    assert any(isinstance(b, FigureBlock) for b in doc.blocks)
    # the in-figure label is NOT echoed as a loose paragraph...
    assert all("서류검토" not in (p.text or "") for p in paras)
    # ...but real out-of-figure text is still recovered (never silently dropped)
    assert any("footnote" in (p.text or "") for p in paras)
    # the in-figure span is accounted for (by the image), not reported missing
    assert "서류검토" not in report.missing_texts


def test_analyzer_text_block_inside_figure_dropped(tmp_path, monkeypatch):
    # The analyzer sometimes transcribes a flowchart's box labels as their OWN paragraph/
    # caption blocks IN ADDITION to emitting the figure image (observed on the 방수총 test-
    # sequence flowchart). Those duplicate the image's content as floating text — drop the
    # text blocks that sit wholly inside the figure; keep blocks outside it.
    _patch(monkeypatch)
    inside_lbl = _span("p0b3l0s0", (150, 250, 250, 265), "서류검토")    # a flowchart box label
    inside_cap = _span("p0b4l0s0", (150, 210, 240, 225), "(1)기능")     # the column caption
    outside = _span("p0b9l0s0", (100, 700, 320, 715), "real body paragraph")
    page = RawPage(width=600.0, height=800.0,
                   spans=[inside_lbl, inside_cap, outside], images=[], render_path="page0.png")
    blocks = [[
        {"type": "figure", "figure_bbox": [100, 200, 520, 600], "source_span_ids": []},
        {"type": "caption", "text": "(1)기능", "source_span_ids": ["p0b4l0s0"]},
        {"type": "paragraph", "text": "서류검토", "source_span_ids": ["p0b3l0s0"]},
        {"type": "paragraph", "text": "real body paragraph", "source_span_ids": ["p0b9l0s0"]},
    ]]
    doc, _ = reconciler.reconcile(RawDocument(pages=[page]), blocks, "x.pdf", str(tmp_path))
    texts = " ".join(getattr(b, "text", "") or "" for b in doc.blocks)
    assert any(isinstance(b, FigureBlock) for b in doc.blocks)
    assert "서류검토" not in texts          # analyzer-emitted in-figure label dropped
    assert "(1)기능" not in texts           # analyzer-emitted in-figure caption dropped
    assert "real body paragraph" in texts   # text outside the figure is kept


def test_text_beside_figure_still_recovered(tmp_path, monkeypatch):
    # A span level with the figure but to its SIDE (wrap text) is outside the bbox -> kept.
    _patch(monkeypatch)
    beside = _span("p0b5l0s0", (540, 300, 595, 315), "side note")  # right of the figure bbox
    page = RawPage(width=600.0, height=800.0, spans=[beside], images=[],
                   render_path="page0.png")
    blocks = [[{"type": "figure", "figure_bbox": [100, 200, 520, 600],
                "source_span_ids": []}]]
    doc, _ = reconciler.reconcile(RawDocument(pages=[page]), blocks, "x.pdf", str(tmp_path))
    paras = [b for b in doc.blocks if isinstance(b, ParagraphBlock)]
    assert any("side note" in (p.text or "") for p in paras)


# --- span-LESS in-figure leak (workstream C: baked-in labels, e.g. watergun nozzle "접합부") --
#
# Investigation on the real watergun doc (backend/storage/ahTZyKrBSX7bj_D4KLER6iPl,
# page_blocks.json page 23, the [별도 5]/Appendix-5 standard-nozzle drawing): the analyzer's
# block for that figure is
#   {"type": "figure", "figure_bbox": [56, 95, 590, 380], "has_text": true}
# with NO separate text block for "접합부" anywhere on the page — no text span on that page
# contains "접합" either (confirmed against page_blocks.json + the raw PDF spans). The
# `has_text` flag routes it through the figure-OCR path (pipeline/figure_ocr.py), which
# already turns a baked label into a proper FigureOverlay instead of floating body text —
# so THIS exact document doesn't currently reproduce the leak. The architectural gap the
# spec describes is nonetheless real: if the vision analyzer ever transcribes a baked-in
# label as its OWN span-less paragraph/caption/heading/list_item block (instead of, or in
# addition to, flagging the figure's has_text), nothing stopped it from being emitted as
# a detached, translated body-text line duplicating the figure's own baked text. The tests
# below reproduce that block SHAPE synthetically (a span-less text block using the real
# "접합부" label, sitting adjacent to a figure) and pin the fix: such a block is dropped,
# while legitimate span-less blocks elsewhere on the page are left alone.


def test_spanless_text_block_after_figure_dropped(tmp_path, monkeypatch):
    _patch(monkeypatch)
    real = _span("p0b9l0s0", (100, 700, 320, 715), "real body paragraph")
    page = RawPage(width=600.0, height=800.0, spans=[real], images=[],
                   render_path="page0.png")
    blocks = [[
        {"type": "figure", "figure_bbox": [56, 95, 590, 380], "has_text": True},
        # the vision-hallucinated label: no source_span_ids resolve to a real span at all
        {"type": "paragraph", "text": "접합부", "source_span_ids": []},
        {"type": "paragraph", "text": "real body paragraph", "source_span_ids": ["p0b9l0s0"]},
    ]]
    doc, _ = reconciler.reconcile(RawDocument(pages=[page]), blocks, "x.pdf", str(tmp_path))
    texts = " ".join(getattr(b, "text", "") or "" for b in doc.blocks)
    assert any(isinstance(b, FigureBlock) for b in doc.blocks)
    assert "접합부" not in texts             # the baked-label leak is dropped
    assert "real body paragraph" in texts    # unrelated span-backed text is kept


def test_spanless_heading_before_figure_dropped(tmp_path, monkeypatch):
    # Same leak, but on the OTHER side (analyzer emits the label BEFORE the figure) and as
    # a heading — the spec explicitly calls out heading/paragraph/caption/list_item alike.
    _patch(monkeypatch)
    page = RawPage(width=600.0, height=800.0, spans=[], images=[], render_path="page0.png")
    blocks = [[
        {"type": "heading", "text": "접합부", "source_span_ids": ["ghost_id_not_a_real_span"]},
        {"type": "figure", "figure_bbox": [56, 95, 590, 380], "has_text": True},
    ]]
    doc, _ = reconciler.reconcile(RawDocument(pages=[page]), blocks, "x.pdf", str(tmp_path))
    texts = " ".join(getattr(b, "text", "") or "" for b in doc.blocks)
    assert any(isinstance(b, FigureBlock) for b in doc.blocks)
    assert "접합부" not in texts   # hallucinated ids (none resolve) count as span-less too


def test_spanless_text_block_not_adjacent_to_figure_kept(tmp_path, monkeypatch):
    # A span-less block that is NOT next to a figure is left alone — the rule must not
    # reach for every ungrounded block on a page, only the ones sitting right next to a
    # figure in the analyzer's own reading order.
    _patch(monkeypatch)
    real = _span("p0b1l0s0", (100, 100, 320, 115), "spacer paragraph")
    page = RawPage(width=600.0, height=800.0, spans=[real], images=[], render_path="page0.png")
    blocks = [[
        {"type": "figure", "figure_bbox": [56, 95, 590, 380], "has_text": True},
        {"type": "paragraph", "text": "spacer paragraph", "source_span_ids": ["p0b1l0s0"]},
        {"type": "paragraph", "text": "unrelated span-less note", "source_span_ids": []},
    ]]
    doc, _ = reconciler.reconcile(RawDocument(pages=[page]), blocks, "x.pdf", str(tmp_path))
    texts = " ".join(getattr(b, "text", "") or "" for b in doc.blocks)
    assert "unrelated span-less note" in texts
