"""Tests for apply_docx_edits_to_model: inserting user-added paragraphs.

A paragraph in the docx with no blk bookmark (a user addition) becomes a new
first-class ParagraphBlock with a fresh uuid block_id and source_span_ids=[].
"""
import os
import models
from pipeline.docx_sync import apply_docx_edits_to_model
from renderers.docx_renderer import render_docx
from docx import Document
from docx.oxml.ns import qn


def _insert_new_paragraph_after(path, after_block_id, text):
    d = Document(path); body = d.element.body
    target = None
    for p in body.iterchildren(qn("w:p")):
        if any((bm.get(qn("w:name")) or "") == f"blk{after_block_id}" for bm in p.iter(qn("w:bookmarkStart"))):
            target = p; break
    np = body.makeelement(qn("w:p"), {}); r = np.makeelement(qn("w:r"), {}); t = np.makeelement(qn("w:t"), {})
    t.text = text; r.append(t); np.append(r); target.addnext(np); d.save(path)


def _reorder_paragraphs(path):
    """Swap the first two w:p that carry blk bookmarks."""
    d = Document(path)
    body = d.element.body
    ps = [p for p in body.iterchildren(qn("w:p")) if any(
        (bm.get(qn("w:name")) or "").startswith("blk")
        for bm in p.iter(qn("w:bookmarkStart")))]
    if len(ps) >= 2:
        a, b = ps[0], ps[1]
        a.addprevious(b)  # move b before a
    d.save(path)


def _delete_paragraph_by_bookmark(path, name):
    """Simulate an editor deletion: remove the w:p that contains bookmark `name`."""
    d = Document(path)
    for bm in list(d.element.body.iter(qn("w:bookmarkStart"))):
        if bm.get(qn("w:name")) == name:
            p = bm
            while p is not None and p.tag != qn("w:p"):
                p = p.getparent()
            if p is not None:
                p.getparent().remove(p)
            break
    d.save(path)


def test_new_paragraph_inserted_as_block(tmp_path):
    """A bookmark-less paragraph between two bookmarked blocks becomes a new first-class ParagraphBlock."""
    m = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="zero", block_id="B0", source_span_ids=["s"]),
        models.ParagraphBlock(text="one",  block_id="B1", source_span_ids=["s"]),
    ])
    out = os.path.join(str(tmp_path), "d.docx"); render_docx(m, out, str(tmp_path))
    _insert_new_paragraph_after(out, "B0", "ADDED")

    synced = apply_docx_edits_to_model(m, out)

    texts = [b.text for b in synced.blocks]
    assert texts == ["zero", "ADDED", "one"]                     # inserted at the right position
    added = synced.blocks[1]
    assert added.block_id and added.block_id not in ("B0", "B1") # fresh id
    assert added.source_span_ids == []


def test_compose_add_reorder_delete(tmp_path):
    """Compose: add a new paragraph, reorder two blocks, and delete one — all in a single docx."""
    m = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="alpha", block_id="A", source_span_ids=["s"]),
        models.ParagraphBlock(text="beta",  block_id="B", source_span_ids=["s"]),
        models.ParagraphBlock(text="gamma", block_id="C", source_span_ids=["s"]),
    ])
    out = os.path.join(str(tmp_path), "d.docx"); render_docx(m, out, str(tmp_path))

    # Insert "NEW" between A and B (after A)
    _insert_new_paragraph_after(out, "A", "NEW")
    # Swap A and B (B moves before A; NEW is between them — after A in original positions)
    _reorder_paragraphs(out)  # swaps first two blk-bookmarked: A and B → B, A
    # Delete C
    _delete_paragraph_by_bookmark(out, "blkC")

    synced = apply_docx_edits_to_model(m, out)

    block_ids = [b.block_id for b in synced.blocks]
    # B comes first, then A; NEW (None-entry) is between A and B in original doc order
    # After reorder: docx order is B, A, NEW → that's what we get back
    assert "B" in block_ids
    assert "A" in block_ids
    assert "C" not in block_ids        # deleted
    # One new block with fresh id
    new_ids = [bid for bid in block_ids if bid not in ("A", "B", "C")]
    assert len(new_ids) == 1
    new_block = next(b for b in synced.blocks if b.block_id not in ("A", "B", "C"))
    assert new_block.text == "NEW"
    assert new_block.source_span_ids == []
