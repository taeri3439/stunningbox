"""build_segments deletion tests (T9 rewrite).

Deletion is now structural: a deleted block is simply absent from translated.blocks.
build_segments iterates over what is in translated.blocks, so a dropped block's
segment naturally does not appear — no tombstone flag needed.
"""
import os, json
import models
from pipeline import process


def _seed(tmp_path, include_middle=True):
    storage, doc_id, lang = str(tmp_path), "d", "en"
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    src = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="원문0", source_span_ids=["s0"], block_id="b0"),
        models.ParagraphBlock(text="원문1", source_span_ids=["s1"], block_id="b1"),
        models.ParagraphBlock(text="원문2", source_span_ids=["s2"], block_id="b2"),
    ])
    with open(os.path.join(process.doc_dir(storage, doc_id), "source.json"), "w", encoding="utf-8") as fh:
        json.dump(src.model_dump(), fh)

    # Translated model: optionally omit the middle block (simulating a structural drop)
    tr_blocks = [
        models.ParagraphBlock(text="trans0", source_span_ids=["s0"], block_id="b0"),
        models.ParagraphBlock(text="trans2", source_span_ids=["s2"], block_id="b2"),
    ]
    if include_middle:
        tr_blocks.insert(1, models.ParagraphBlock(text="trans1", source_span_ids=["s1"], block_id="b1"))
    tr = models.SemanticDocument(source_lang="ko", blocks=tr_blocks)
    process.save_translated_model(storage, doc_id, lang, tr)
    return storage, doc_id, lang


def test_build_segments_omits_dropped_block(tmp_path):
    """A block absent from translated.blocks produces no segment in build_segments output."""
    storage, doc_id, lang = _seed(tmp_path, include_middle=False)
    segs = process.build_segments(storage, doc_id, lang)
    block_ids = {s["block_id"] for s in segs}
    assert "b1" not in block_ids           # dropped block's segment is absent
    assert "b0" in block_ids
    assert "b2" in block_ids


def test_build_segments_baseline_no_deletion(tmp_path):
    """When all blocks are present, all segments appear in build_segments output."""
    storage, doc_id, lang = _seed(tmp_path, include_middle=True)
    segs = process.build_segments(storage, doc_id, lang)
    block_ids = {s["block_id"] for s in segs}
    assert {"b0", "b1", "b2"}.issubset(block_ids)
