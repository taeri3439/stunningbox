"""A single malformed table merge must never abort the whole document render.

A detected-grid table whose last cell in a row carries a rowspan trips the renderer's
"extend the last cell to the row edge" heuristic into an L-shaped (non-rectangular) merge,
which python-docx rejects with InvalidSpanError. Before the guard this 500'd /editor-config
and blanked the viewer; now the merge is skipped and the document still renders."""
from models import SemanticDocument, TableBlock, TableCell
from renderers.docx_renderer import render_docx


def test_render_survives_nonrectangular_merge(tmp_path):
    rows = [
        [TableCell(text="hdr", colspan=3)],                      # row 0 -> ncols = 3
        [TableCell(text="A"), TableCell(text="B", rowspan=2)],   # row 1: B(rowspan2) is last;
        [TableCell(text="C")],                                   # the edge-extend would be L-shaped
    ]
    out = str(tmp_path / "t.docx")
    render_docx(SemanticDocument(blocks=[TableBlock(rows=rows)]), out, str(tmp_path))
    assert (tmp_path / "t.docx").exists()
