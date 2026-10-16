"""Task 6: bookmarks use block_id; read_block_lines returns ordered (block_id|None, lines) entries."""
import os
import models
from renderers.docx_renderer import render_docx
from pipeline.docx_sync import read_block_lines


def test_bookmarks_use_block_id_and_read_back_in_order(tmp_path):
    m = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="zero", block_id="B0", source_span_ids=["s"]),
        models.ParagraphBlock(text="one", block_id="B1", source_span_ids=["s"]),
    ])
    out = os.path.join(str(tmp_path), "d.docx")
    render_docx(m, out, str(tmp_path))
    entries = read_block_lines(out)
    by_id = {bid: lines for bid, lines in entries if bid is not None}
    ordered = [bid for bid, _ in entries if bid is not None]
    assert ordered == ["B0", "B1"]
    assert by_id["B0"] == ["zero"] and by_id["B1"] == ["one"]
