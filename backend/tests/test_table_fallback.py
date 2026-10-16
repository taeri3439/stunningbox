"""Unreliable-table detection + image fallback."""
from models import CaptionBlock, FigureBlock, RawDocument, RawPage, RawSpan, TableBlock, TableCell
from pipeline import extractor, reconciler
from pipeline.reconciler import _table_structure_unreliable


def _row(*colspans):
    return [TableCell(text="x", colspan=c) for c in colspans]


def _row_rs(*cells):
    """Build a row from (colspan, rowspan) pairs — for rowspan-carry fixtures."""
    return [TableCell(text="x", colspan=c, rowspan=r) for c, r in cells]


def test_watergun_like_ragged_mix_is_unreliable():
    # 17 rows whose column sums alternate 6/5/7 (the plug-gauge pattern)
    rows = []
    for i in range(17):
        s = (6, 5, 7)[i % 3]
        rows.append(_row(*([1] * s)))
    assert _table_structure_unreliable(rows) is True


def test_row_grouped_table_with_rowspan_carry_is_reliable():
    """Mirrors the real watergun p9 shape: a 2-row header (rowspan=2 label + rowspan=2
    remark column), a rowspan=2 group ("일반검사") and a rowspan=7 group ("특별검사")
    whose member rows carry one fewer own cell because the group label + remark columns
    are still covered by the earlier rowspan. This is a WELL-FORMED row-grouped table,
    not a ragged one — it must stay reliable (False) once rowspan carry-over is credited
    toward each row's effective width."""
    rows = [
        _row_rs((2, 2), (1, 1), (1, 1), (1, 2)),          # header, spans rows 0-1
        _row_rs((1, 1), (1, 1)),                           # header row 2/2 (2 open cols)
        _row_rs((1, 2), (1, 1), (1, 1), (1, 1), (1, 2)),   # "일반검사" group start (rows 2-3)
        _row_rs((1, 1), (1, 1), (1, 1)),                   # group member (label+remark carried)
        _row_rs((1, 7), (1, 1), (1, 1), (1, 1), (1, 7)),   # "특별검사" group start (rows 4-10)
        _row_rs((1, 1), (1, 1), (1, 1)),                   # member (label+remark carried)
        _row_rs((1, 1), (1, 1), (1, 1)),                   # member
        _row_rs((1, 1), (1, 1), (1, 1)),                   # member
        _row_rs((1, 1), (1, 1), (1, 1)),                   # member
        _row_rs((1, 1), (1, 1), (1, 1)),                   # member
        _row_rs((1, 1), (1, 1), (1, 1)),                   # member
    ]
    assert _table_structure_unreliable(rows) is False


def test_stacked_rowspan_groups_last_row_credited():
    """Two stacked rowspan=3 groups (label spans 3 rows, 2 own cells/row) — every row's
    true effective width is 3 ([3,3,3,3,3,3], 0% ragged). The old carry-decrements-before
    counting bug drops credit for the LAST row of every rowspan group (it sees the carry
    already zeroed), producing [3,3,2,3,3,2] = 2/6 = 33% ragged, which trips the 30%
    threshold and wrongly flags this well-formed table as unreliable."""
    rows = [
        _row_rs((1, 3), (1, 1), (1, 1)),   # group 1 start
        _row_rs((1, 1), (1, 1)),           # group 1 member
        _row_rs((1, 1), (1, 1)),           # group 1 last member (bug loses credit here)
        _row_rs((1, 3), (1, 1), (1, 1)),   # group 2 start
        _row_rs((1, 1), (1, 1)),           # group 2 member
        _row_rs((1, 1), (1, 1)),           # group 2 last member (bug loses credit here)
    ]
    assert _table_structure_unreliable(rows) is False


def test_oversized_rowspan_double_claim_is_unreliable():
    """Mirrors the real watergun p21 plug-gauge defect: a nominal-size cell in column 0
    declares rowspan=4 when its group is really 2 rows (통과/정지), so from the third row
    on every row's carry-credited width stabilises at 8 — one column MORE than the
    table's real 7-column grid — while only the first 3/15 rows disagree with that modal
    width (20% ragged, below the 30% threshold). The ragged-fraction check alone misses
    this because the wrongness is CONSISTENT, not ragged; the modal-vs-true-column-count
    check below must still catch it (a modal width that exceeds every row's own natural
    column count proves a rowspan is double-claiming a column another row also claims)."""
    def group(size, rs):
        return [
            _row_rs((1, rs), (1, 1), (1, 1), (1, 1), (1, 1), (1, 1), (1, 1)),  # own row (7)
            _row_rs((1, 1), (1, 1), (1, 1), (1, 1), (1, 1), (1, 1)),           # pair row (6)
        ]

    rows = _row_rs((2, 1), (1, 1), (1, 1), (1, 1), (1, 1), (1, 1))  # header (7)
    rows = [rows]
    for size in (40, 65, 75, 90, 100, 125, 150):
        rows.extend(group(size, rs=4))  # BUG: should be rs=2 (each group is 2 rows)
    assert len(rows) == 15
    assert _table_structure_unreliable(rows) is True


def test_regular_table_is_reliable():
    rows = [_row(1, 1, 1, 1) for _ in range(8)]
    assert _table_structure_unreliable(rows) is False


def test_merged_header_still_reliable():
    # header uses colspans but sums to the same width — not ragged
    rows = [_row(2, 2)] + [_row(1, 1, 1, 1) for _ in range(7)]
    assert _table_structure_unreliable(rows) is False


def test_small_tables_never_fall_back():
    rows = [_row(1, 1), _row(1, 1, 1), _row(1)]
    assert _table_structure_unreliable(rows) is False


def test_mild_raggedness_kept_as_text():
    # 25% ragged (7 of 28) — below the 0.3 threshold
    rows = [_row(*([1] * (7 if i % 4 == 2 else 6))) for i in range(28)]
    assert _table_structure_unreliable(rows) is False


def test_zero_modal_is_unreliable():
    rows = [[] for _ in range(6)]
    assert _table_structure_unreliable(rows) is True


# --- reconcile()-level: ragged table -> FigureBlock (+ CaptionBlock) fallback --------------


def _span(sid, bbox, text="x"):
    return RawSpan(id=sid, page=0, text=text, bbox=bbox, size=10.0)


def _patch_render_clip(monkeypatch):
    # No PyMuPDF grid available (keeps `rows` untouched by the grid-rebuild path) and
    # render_clip returns the caller's own computed name/bbox (no file actually written).
    monkeypatch.setattr(extractor, "detect_table_grids", lambda *a, **k: [])
    monkeypatch.setattr(
        extractor, "render_clip",
        lambda pdf, pi, bbox, ad, name: (name, tuple(bbox)),
    )


def _ragged_table_fixture():
    """One page with a 6-row table whose row cell-counts alternate 2/3 (50% ragged —
    well above the 30% threshold), each cell backed by its own span."""
    spans = []
    rows = []
    for i in range(6):
        ncols = 2 if i % 2 == 0 else 3
        y0, y1 = 100.0 + i * 20, 100.0 + i * 20 + 10
        width = 300.0 / ncols
        cells = []
        for c in range(ncols):
            sid = f"r{i}c{c}"
            x0, x1 = 100.0 + c * width, 100.0 + (c + 1) * width
            spans.append(_span(sid, (x0, y0, x1, y1), text=f"v{i}{c}"))
            cells.append({"text": f"v{i}{c}", "source_span_ids": [sid]})
        rows.append(cells)
    page = RawPage(width=600.0, height=800.0, spans=spans, images=[], render_path="page0.png")
    table_block = {"type": "table", "caption": "표 1", "rows": rows}
    return RawDocument(pages=[page]), [[table_block]]


def _clean_table_fixture():
    """One page with a regular 4x4 table (no raggedness) — the regression guard."""
    spans = []
    rows = []
    for i in range(4):
        cells = []
        for c in range(4):
            sid = f"r{i}c{c}"
            x0, y0 = 100.0 + c * 50, 100.0 + i * 20
            spans.append(_span(sid, (x0, y0, x0 + 50, y0 + 10), text=f"v{i}{c}"))
            cells.append({"text": f"v{i}{c}", "source_span_ids": [sid]})
        rows.append(cells)
    page = RawPage(width=600.0, height=800.0, spans=spans, images=[], render_path="page0.png")
    table_block = {"type": "table", "caption": "표 2", "rows": rows}
    return RawDocument(pages=[page]), [[table_block]]


def test_ragged_table_falls_back_to_image(monkeypatch, tmp_path):
    """A >=30%-ragged vision table reconciles to FigureBlock (+ CaptionBlock) with a
    warning; its spans count as referenced (no coverage-gate misses)."""
    _patch_render_clip(monkeypatch)
    doc_in, page_blocks = _ragged_table_fixture()
    doc, report = reconciler.reconcile(doc_in, page_blocks, "x.pdf", str(tmp_path))

    assert not any(isinstance(b, TableBlock) for b in doc.blocks)
    figs = [b for b in doc.blocks if isinstance(b, FigureBlock)]
    assert len(figs) == 1
    assert figs[0].image_ref == "imgtable_p0_0.png"

    idx = doc.blocks.index(figs[0])
    assert idx > 0 and isinstance(doc.blocks[idx - 1], CaptionBlock)
    assert doc.blocks[idx - 1].text == "표 1"

    assert any("unreliable structure" in w for w in report.warnings)
    assert report.missing_span_ids == []


def test_clean_table_still_reconciles_to_tableblock(monkeypatch, tmp_path):
    """A regular 4x4 vision table (no raggedness) must stay a TableBlock (regression)."""
    _patch_render_clip(monkeypatch)
    doc_in, page_blocks = _clean_table_fixture()
    doc, report = reconciler.reconcile(doc_in, page_blocks, "x.pdf", str(tmp_path))

    assert any(isinstance(b, TableBlock) for b in doc.blocks)
    assert not any(isinstance(b, FigureBlock) for b in doc.blocks)
    assert not any("unreliable structure" in w for w in report.warnings)
    assert report.missing_span_ids == []
