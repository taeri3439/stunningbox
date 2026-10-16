"""Image-table handling: when a page image covers a detected table's region, the table is
really a rendered graphic (e.g. a chemical-structure column) and must be emitted as a figure
(so the drawings survive) rather than a text grid with empty cells. A normal text table with
no covering image must stay a table (regression guard)."""
from models import FigureBlock, RawDocument, RawImage, RawPage, RawSpan, TableBlock
from pipeline import extractor, reconciler


def _span(sid, bbox, text="x"):
    return RawSpan(id=sid, page=0, text=text, bbox=bbox, size=10.0)


def _doc(images):
    spans = [_span("s1", (100, 100, 150, 110), "Name"), _span("s2", (100, 150, 150, 160), "Parent")]
    page = RawPage(width=600.0, height=800.0, spans=spans, images=images, render_path="page0.png")
    return RawDocument(pages=[page])


def _table_blocks():
    # 2 rows, 2 cols; the 2nd column (the "structure" column) cells are empty (the drawings).
    return [[{"type": "table", "rows": [
        [{"text": "Name", "source_span_ids": ["s1"]}, {"text": "", "source_span_ids": []}],
        [{"text": "Parent", "source_span_ids": ["s2"]}, {"text": "", "source_span_ids": []}],
    ]}]]


def _patch(monkeypatch):
    monkeypatch.setattr(extractor, "detect_table_grids", lambda *a, **k: [])
    monkeypatch.setattr(extractor, "render_clip",
                        lambda pdf, pi, bbox, ad, name: ("clip.png", tuple(bbox)))


def test_covering_image_geometry():
    img = RawImage(id="i0", page=0, bbox=(90, 90, 520, 620), path="p0img0.png")
    page = RawPage(width=600.0, height=800.0, images=[img])
    assert reconciler._covering_image(page, (100, 100, 150, 160)) is img  # image contains table
    assert reconciler._covering_image(page, (530, 100, 580, 160)) is None  # disjoint region


def test_image_table_becomes_figure(tmp_path, monkeypatch):
    _patch(monkeypatch)
    img = RawImage(id="i0", page=0, bbox=(90, 90, 520, 620), path="p0img0.png")
    doc, _ = reconciler.reconcile(_doc([img]), _table_blocks(), "x.pdf", str(tmp_path))
    assert any(isinstance(b, FigureBlock) for b in doc.blocks)
    assert not any(isinstance(b, TableBlock) for b in doc.blocks)


def test_normal_table_stays_table(tmp_path, monkeypatch):
    _patch(monkeypatch)
    doc, _ = reconciler.reconcile(_doc([]), _table_blocks(), "x.pdf", str(tmp_path))  # no image
    assert any(isinstance(b, TableBlock) for b in doc.blocks)
    assert not any(isinstance(b, FigureBlock) for b in doc.blocks)


def test_spanless_image_table_becomes_figure(tmp_path, monkeypatch):
    # A fully-graphic table (e.g. the chemical-structure table): the vision model read the
    # text off the image, so every cell has NO source_span_ids. With a substantial page image
    # present, it must render as a figure, not a grid of empty cells.
    _patch(monkeypatch)
    img = RawImage(id="i0", page=0, bbox=(72, 117, 508, 619), path="p0img0.png")  # ~46% of page
    page = RawPage(width=600.0, height=800.0, spans=[], images=[img], render_path="page0.png")
    blocks = [[{"type": "table", "rows": [
        [{"text": "Name", "source_span_ids": []}, {"text": "", "source_span_ids": []}],
        [{"text": "Parent", "source_span_ids": []}, {"text": "", "source_span_ids": []}],
    ]}]]
    doc, _ = reconciler.reconcile(RawDocument(pages=[page]), blocks, "x.pdf", str(tmp_path))
    assert any(isinstance(b, FigureBlock) for b in doc.blocks)
    assert not any(isinstance(b, TableBlock) for b in doc.blocks)
