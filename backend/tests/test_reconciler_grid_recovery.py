"""Vision-missed table recovery: when PyMuPDF detects a real grid that the vision analyzer
left unstructured (no table/figure, spans unassigned), the reconciler rebuilds the table from
the grid — but never over a region the vision pass already accounted for."""
from models import ParagraphBlock, RawDocument, RawPage, RawSpan, TableBlock
from pipeline import extractor, reconciler

# (bbox, vertical-line bounds, struct rows) as detect_table_grids returns; 2 columns x 2 rows.
GRID = (
    (70.0, 150.0, 520.0, 400.0),
    [70.0, 295.0, 520.0],
    [
        [{"text": "항목", "colspan": 1, "rowspan": 1}, {"text": "치명결점", "colspan": 1, "rowspan": 1}],
        [{"text": "구조", "colspan": 1, "rowspan": 1}, {"text": "○", "colspan": 1, "rowspan": 1}],
    ],
)


def _page(spans):
    return RawPage(width=600.0, height=800.0, spans=spans, images=[], render_path="p0.png")


def test_recovers_vision_missed_table(tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "detect_table_grids", lambda *a, **k: [GRID])
    # Spans sit inside the grid region and the vision pass emitted nothing -> all unassigned.
    spans = [RawSpan(id="s1", page=0, text="항목", bbox=(80, 160, 120, 175), size=10.0),
             RawSpan(id="s2", page=0, text="○", bbox=(300, 160, 320, 175), size=10.0)]
    doc, _ = reconciler.reconcile(RawDocument(pages=[_page(spans)]), [[]], "x.pdf", str(tmp_path))
    assert any(isinstance(b, TableBlock) for b in doc.blocks)


def test_no_recovery_when_vision_already_covered(tmp_path, monkeypatch):
    monkeypatch.setattr(extractor, "detect_table_grids", lambda *a, **k: [GRID])
    spans = [RawSpan(id="s1", page=0, text="ordinary prose", bbox=(80, 160, 400, 175), size=10.0)]
    # The vision pass classified this region's span as a paragraph -> it's assigned, so a
    # grid-detector misfire here must NOT be turned into a table.
    pb = [[{"type": "paragraph", "text": "ordinary prose", "source_span_ids": ["s1"]}]]
    doc, _ = reconciler.reconcile(RawDocument(pages=[_page(spans)]), pb, "x.pdf", str(tmp_path))
    assert not any(isinstance(b, TableBlock) for b in doc.blocks)
    assert any(isinstance(b, ParagraphBlock) for b in doc.blocks)
