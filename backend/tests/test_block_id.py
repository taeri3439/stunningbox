from models import (HeadingBlock, ParagraphBlock, TableBlock, FigureBlock,
                    DividerBlock, SemanticDocument, make_seg_id)


def test_blocks_have_block_id_default_none():
    for B in (HeadingBlock, ParagraphBlock, TableBlock, FigureBlock, DividerBlock):
        assert B().block_id is None


def test_block_id_settable_and_old_json_loads():
    assert ParagraphBlock(block_id="abc").block_id == "abc"
    doc = SemanticDocument.model_validate({"source_lang": "ko",
        "blocks": [{"type": "paragraph", "text": "x"}]})
    assert doc.blocks[0].block_id is None


def test_make_seg_id():
    assert make_seg_id("B", "text") == "B:text"
    assert make_seg_id("B", "line", 3) == "B:line:3"
    assert make_seg_id("B", "cellline", 2, 1) == "B:cellline:2:1"
