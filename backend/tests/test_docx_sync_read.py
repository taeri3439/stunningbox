# backend/tests/test_docx_sync_read.py
import os
from models import SemanticDocument, HeadingBlock, ParagraphBlock, ListItemBlock, CaptionBlock
from renderers.docx_renderer import render_docx
from pipeline.docx_sync import read_block_lines


def _render(tmp_path, doc):
    out = os.path.join(str(tmp_path), "out.docx")
    render_docx(doc, out, str(tmp_path))
    return out


def test_read_single_and_multiline_blocks(tmp_path):
    doc = SemanticDocument(blocks=[
        HeadingBlock(level=1, marker="1.", text="제목", block_id="h0"),           # blkh0 → "1. 제목"
        ParagraphBlock(text="한 줄 문단.", block_id="p1"),                          # blkp1
        ParagraphBlock(lines=["첫째 줄", "둘째 줄"], block_id="p2"),               # blkp2 → 2 lines via <w:br/>
        ListItemBlock(marker="-", text="항목", block_id="l3"),                    # blkl3 → "- 항목"
        CaptionBlock(text="그림 1", block_id="c4"),                               # blkc4
    ])
    out = _render(tmp_path, doc)
    entries = read_block_lines(out)
    by_id = {bid: lines for bid, lines in entries if bid is not None}
    ordered = [bid for bid, _ in entries if bid is not None]
    assert ordered == ["h0", "p1", "p2", "l3", "c4"]
    assert by_id["h0"] == ["1. 제목"]          # marker still baked in at this layer (stripped in apply)
    assert by_id["p1"] == ["한 줄 문단."]
    assert by_id["p2"] == ["첫째 줄", "둘째 줄"]  # split on <w:br/>
    assert by_id["l3"] == ["- 항목"]
    assert by_id["c4"] == ["그림 1"]
