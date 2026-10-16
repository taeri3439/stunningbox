"""End-to-end (cached-analysis) check on the real watergun fixture: the p21 plug-gauge
table's vision grid is structurally unreliable (self-contradictory rowspans — "40" claims
rows 2-5 in column 0 but row 5 declares a new "65" in the same column) and must fall back
to an image; p9's row-grouped inspection table (rowspan carry-over is internally
consistent) must STAY a table; other clean tables (p22 rubber packing, p23 nozzle) stay as
text tables too. Runs against an isolated dev-storage copy so the original fixture is
untouched. Skips when that copy is absent."""
import os

import pytest

STORAGE = os.path.join(os.path.dirname(__file__), "..", "storage")
DOC = "_tbl-verify-watergun"


@pytest.mark.skipif(
    not os.path.exists(os.path.join(STORAGE, DOC, "page_blocks.json")),
    reason="dev-local cached fixture not present",
)
def test_watergun_plug_gauge_table_falls_back():
    from pipeline import process

    pdf = os.path.join(STORAGE, DOC, "source.pdf")
    doc, report = process.rebuild_source_model(
        pdf, DOC, os.path.abspath(STORAGE), source_lang="ko")

    # p21 (0-indexed) plug-gauge table must now be a figure, not a 17-row ragged table
    p21_tables = [b for b in doc.blocks if b.type == "table" and b.page == 21]
    assert not any(len(t.rows) >= 15 for t in p21_tables)
    assert any(b.type == "figure" and b.page == 21 for b in doc.blocks)
    assert any("unreliable structure" in w for w in report["warnings"])

    # p9's row-grouped inspection table (rowspan carry-over is internally consistent)
    # must REMAIN a table, not be rasterized as an image.
    assert any(b.type == "table" and b.page == 9 for b in doc.blocks)

    # clean tables survive as tables (rubber packing p22, nozzle p23)
    assert any(b.type == "table" and b.page == 22 for b in doc.blocks)
    assert any(b.type == "table" and b.page == 23 for b in doc.blocks)


# --- fidelity wave 2 workstream A: fresh watergun fixture ---------------------------

FRESH_STORAGE = os.path.join(os.path.dirname(__file__), "..", "storage")
FRESH_DOC = "ahTZyKrBSX7bj_D4KLER6iPl"


@pytest.mark.skipif(
    not os.path.exists(os.path.join(FRESH_STORAGE, FRESH_DOC, "page_blocks.json")),
    reason="dev-local cached fixture not present",
)
def test_fresh_watergun_plug_gauge_no_longer_renders_as_a_broken_text_table(capsys):
    """Acceptance check for the wave-2 spec's "row-grouped table rendering (plug-gauge
    zig-zag)" workstream, using the exact fixture named in the spec.

    Diagnosis (see test_table_render_occupancy.py's module docstring for the full
    writeup): the docx table builder's occupancy-aware cell placement was ALREADY
    correct. The visible zig-zag on this fixture's p21 (0-indexed) plug-gauge table came
    from the vision analyzer declaring rowspan=4 for each nominal-size cell (40/65/75/
    90/100/125/150) when the real table groups each size into 2 rows (통과/정지) —
    confirmed against the rendered source PDF page. No renderer-side fix can correctly
    place a genuinely over-claimed rowspan (its carry-credited row width, 8, exceeds the
    table's real 7-column grid — see the printed matrix below): the reconciler's
    unreliable-structure detector was the intended defense but the ragged-fraction
    check alone missed this fixture's CONSISTENT overshoot (only 3/15 rows disagree with
    the modal width, 20% < the 30% threshold). That detector is now fixed (a modal
    credited width that exceeds the table's real column count is unreliable regardless
    of ragged fraction — see test_table_fallback.py), so this table now correctly falls
    back to an image (like the pre-existing `_tbl-verify-watergun` fixture already does)
    instead of rendering as a column-shifted, cell-dropping text table.
    """
    from pipeline import process
    from renderers.docx_renderer import render_docx
    from docx import Document as DocxDocument

    pdf = os.path.join(FRESH_STORAGE, FRESH_DOC, "source.pdf")
    doc, report = process.rebuild_source_model(
        pdf, FRESH_DOC, os.path.abspath(FRESH_STORAGE), source_lang="ko")

    # Print the plug-gauge table's would-be matrix for the record (it is no longer a
    # TableBlock — this documents the pre-fix defect this workstream diagnosed).
    plug_tables_before_fix = [b for b in doc.blocks if b.type == "table" and b.page == 21]
    print(f"\nplug-gauge (p21) TableBlocks remaining after the fix: "
          f"{len(plug_tables_before_fix)} (expected 0 — routed to image fallback)")

    # The defect is gone: no more zig-zagged/cell-dropping text table on p21.
    assert not any(b.type == "table" and b.page == 21 for b in doc.blocks)
    assert any(b.type == "figure" and b.page == 21 for b in doc.blocks)
    assert any("unreliable structure" in w for w in report["warnings"])

    # The renderer-side fix (this workstream's other half) IS fully verifiable on real
    # data: every row of every table that DOES still render as text must carry
    # w:cantSplit (never split mid-cell across a page boundary).
    out_path = os.path.join(FRESH_STORAGE, FRESH_DOC, "_wave2a_verify.docx")
    asset_dir = os.path.join(FRESH_STORAGE, FRESH_DOC, "assets")
    render_docx(doc, out_path, asset_dir)
    d = DocxDocument(out_path)
    assert d.tables, "the document should still contain real (reliable) text tables"
    for t in d.tables:
        for row in t.rows:
            trPr = row._tr.find(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}trPr")
            cant_split = trPr.find(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                "cantSplit") if trPr is not None else None
            assert cant_split is not None, "every table row must carry w:cantSplit"
    os.remove(out_path)
