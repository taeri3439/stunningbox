"""The translated DOCX is a REFLOWABLE document viewed in a continuous editor. It must NOT
inject a hard page break at every source-page boundary: once the translation expands the
text (English ~1.5x Korean) each source page overflows, the leftover lines land on a near-
empty forced page, and the page count balloons (a 24p source rendered to 36p). Word should
paginate naturally; per-block style/structure is what preserves fidelity, not forced breaks.
"""
import os

from docx import Document

from models import ParagraphBlock, SemanticDocument
from renderers.docx_renderer import render_docx


def test_no_forced_page_break_between_source_pages(tmp_path):
    doc = SemanticDocument(blocks=[
        ParagraphBlock(text="content of source page zero", page=0),
        ParagraphBlock(text="content of source page one", page=1),
        ParagraphBlock(text="content of source page two", page=2),
    ])
    out = os.path.join(str(tmp_path), "out.docx")
    render_docx(doc, out, str(tmp_path))
    xml = Document(out).element.xml
    assert xml.count('w:type="page"') == 0  # no hard page breaks injected
