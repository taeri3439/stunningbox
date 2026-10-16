"""A table's source-proportional column widths are kept ONLY while they still fit the
translated text. A column that's narrow in the source (e.g. 호칭 = "40") but holds a long
translated label after translation ("Nominal Classification" / "NO-GO (For Inspection
Only)") would, under fixed widths, break one glyph per line. Such tables fall back to Word
autofit; tables whose content still fits keep the source's exact column lines."""
import os

from docx import Document

from models import SemanticDocument, TableBlock, TableCell
from renderers.docx_renderer import render_docx


def _render(block, tmp_path):
    out = os.path.join(str(tmp_path), "t.docx")
    render_docx(SemanticDocument(blocks=[block]), out, str(tmp_path))
    return Document(out).element.xml


def test_cramming_table_falls_back_to_autofit(tmp_path):
    # col 0 is 5% wide but holds long Latin words -> fixed widths would cram it -> autofit.
    rows = [
        [TableCell(text="Nominal Classification"), TableCell(text="Effective Diameter")],
        [TableCell(text="NO-GO (For Inspection Only)"), TableCell(text="48.971")],
    ]
    xml = _render(TableBlock(rows=rows, col_widths=[0.05, 0.95]), tmp_path)
    assert 'w:type="fixed"' not in xml  # the cramming fixed widths were NOT forced



def test_fitting_table_keeps_fixed_widths(tmp_path):
    # short content fits the proportions -> keep the source's exact column lines (fixed).
    rows = [
        [TableCell(text="검사항목"), TableCell(text="생산"), TableCell(text="비고")],
        [TableCell(text="1. 구조"), TableCell(text="○"), TableCell(text="")],
    ]
    xml = _render(TableBlock(rows=rows, col_widths=[0.5, 0.25, 0.25]), tmp_path)
    assert 'w:type="fixed"' in xml


def test_numeric_table_keeps_fixed_widths(tmp_path):
    # short numbers/codes (no long Latin word) fit even narrowish columns -> stay fixed.
    rows = [
        [TableCell(text="0.65"), TableCell(text="1.0"), TableCell(text="Ac Re")],
        [TableCell(text="2"), TableCell(text="3"), TableCell(text="5 6")],
    ]
    xml = _render(TableBlock(rows=rows, col_widths=[0.34, 0.33, 0.33]), tmp_path)
    assert 'w:type="fixed"' in xml
