"""Renderer deletion tests (T9 rewrite).

Deletion is now structural: a dropped block is simply absent from the model's
block list. The renderers (docx + html) do not check a deleted flag; they render
whatever is in doc.blocks. Tests verify that absent blocks are not rendered and
that survivors + their bookmarks/anchors appear correctly.
"""
import os
import models
from renderers.docx_renderer import render_docx
from renderers.html_renderer import render_html
from docx import Document
from docx.oxml.ns import qn


def _model_with_drop():
    """A model where block id1 has been structurally dropped (simulating apply_docx_edits)."""
    return models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="KEEPZERO", block_id="id0", source_span_ids=["s0"]),
        # id1 / "GONEONE" is absent — it was dropped by apply_docx_edits_to_model
        models.ParagraphBlock(text="KEEPTWO",  block_id="id2", source_span_ids=["s2"]),
    ])


def test_render_docx_omits_absent_block_and_keeps_survivor_bookmarks(tmp_path):
    """render_docx only renders blocks in doc.blocks; absent blocks produce no output."""
    out = os.path.join(str(tmp_path), "d.docx")
    render_docx(_model_with_drop(), out, str(tmp_path))
    d = Document(out)
    body = d.element.body
    text = "".join(t.text or "" for t in body.iter(qn("w:t")))
    assert "GONEONE" not in text                        # absent block not rendered
    assert "KEEPZERO" in text and "KEEPTWO" in text
    names = {bm.get(qn("w:name")) for bm in body.iter(qn("w:bookmarkStart"))}
    assert "blkid0" in names and "blkid2" in names     # survivor bookmark ids present
    assert "blkid1" not in names                        # absent block has no bookmark


def test_render_html_omits_absent_block(tmp_path):
    """render_html only renders blocks in doc.blocks; absent blocks produce no output."""
    html = render_html(_model_with_drop(), anchor_blocks=True)
    assert "GONEONE" not in html
    assert "KEEPZERO" in html and "KEEPTWO" in html
    # html_renderer uses block_id-based anchors (blk{block_id})
    assert 'id="blkid0"' in html and 'id="blkid2"' in html
    assert 'id="blkid1"' not in html                   # absent block's anchor is absent
