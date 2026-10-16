"""Test that get_export_model backfills block_ids on legacy edited.{lang}.json files."""
import json
import os

import models
from pipeline import process


def _make_storage(tmp_path):
    storage = str(tmp_path)
    doc_id = "doc1"
    os.makedirs(process.doc_dir(storage, doc_id), exist_ok=True)
    return storage, doc_id


def test_get_export_model_backfills_edited_block_ids(tmp_path):
    """edited.{lang}.json without block_ids must have them filled and persisted."""
    storage, doc_id = _make_storage(tmp_path)
    lang = "en"

    # Write source.json (with block_ids already assigned)
    source = models.SemanticDocument(
        source_lang="ko",
        blocks=[
            models.ParagraphBlock(text="안녕"),
            models.ParagraphBlock(text="세계"),
        ],
    )
    process.ensure_block_ids(source)
    src_path = os.path.join(process.doc_dir(storage, doc_id), "source.json")
    with open(src_path, "w", encoding="utf-8") as fh:
        json.dump(source.model_dump(), fh, ensure_ascii=False)

    # Write edited.en.json WITHOUT block_ids (simulates a legacy file)
    edited_raw = {
        "source_lang": "ko",
        "blocks": [
            {"type": "paragraph", "text": "Hello", "segments": []},
            {"type": "paragraph", "text": "World", "segments": []},
        ],
    }
    ep = process.edited_path(storage, doc_id, lang)
    with open(ep, "w", encoding="utf-8") as fh:
        json.dump(edited_raw, fh, ensure_ascii=False)

    result = process.get_export_model(storage, doc_id, lang)

    # All returned blocks must have a block_id
    assert all(b.block_id for b in result.blocks), "returned model blocks must all have block_id"

    # IDs must match the source by position (mirrored)
    for src_blk, res_blk in zip(source.blocks, result.blocks):
        assert res_blk.block_id == src_blk.block_id, "block_id must be mirrored from source by position"

    # Re-loading the persisted edited file must yield the same ids (stable)
    result2 = process.get_export_model(storage, doc_id, lang)
    for r1, r2 in zip(result.blocks, result2.blocks):
        assert r1.block_id == r2.block_id, "block_ids must be stable across repeated loads"


def test_get_export_model_edited_already_has_ids_unchanged(tmp_path):
    """When the edited file already has block_ids, ensure_block_ids is a no-op."""
    storage, doc_id = _make_storage(tmp_path)
    lang = "en"

    source = models.SemanticDocument(
        source_lang="ko",
        blocks=[models.ParagraphBlock(text="안녕")],
    )
    process.ensure_block_ids(source)
    src_path = os.path.join(process.doc_dir(storage, doc_id), "source.json")
    with open(src_path, "w", encoding="utf-8") as fh:
        json.dump(source.model_dump(), fh, ensure_ascii=False)

    # Write edited file that already has the matching block_id
    edited = models.SemanticDocument(
        source_lang="ko",
        blocks=[models.ParagraphBlock(text="Hello", block_id=source.blocks[0].block_id)],
    )
    ep = process.edited_path(storage, doc_id, lang)
    with open(ep, "w", encoding="utf-8") as fh:
        json.dump(edited.model_dump(), fh, ensure_ascii=False)

    result = process.get_export_model(storage, doc_id, lang)
    assert result.blocks[0].block_id == source.blocks[0].block_id
