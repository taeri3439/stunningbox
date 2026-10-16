import os
import models
from renderers.docx_renderer import render_docx
from pipeline.docx_sync import read_block_lines
from docx import Document
from docx.oxml.ns import qn


def test_read_returns_ordered_entries_with_none_for_new_paragraph(tmp_path):
    m = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="zero", block_id="B0", source_span_ids=["s"]),
        models.ParagraphBlock(text="one", block_id="B1", source_span_ids=["s"]),
    ])
    out = os.path.join(str(tmp_path), "d.docx")
    render_docx(m, out, str(tmp_path))
    # simulate a user-typed new paragraph (no bookmark) inserted between B0 and B1
    d = Document(out)
    body = d.element.body
    ps = [p for p in body.iter(qn("w:p"))
          if any((bm.get(qn("w:name")) or "").startswith("blk") for bm in p.iter(qn("w:bookmarkStart")))]
    new_p = body.makeelement(qn("w:p"), {})
    r = new_p.makeelement(qn("w:r"), {}); t = new_p.makeelement(qn("w:t"), {})
    t.text = "BRANDNEW"; r.append(t); new_p.append(r)
    ps[0].addnext(new_p)                          # after B0's paragraph
    d.save(out)

    entries = read_block_lines(out)
    assert entries == [("B0", ["zero"]), (None, ["BRANDNEW"]), ("B1", ["one"])]
