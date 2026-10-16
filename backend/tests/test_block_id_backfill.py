import os, json, uuid
import models
from pipeline import process


def test_ensure_block_ids_assigns_and_mirrors():
    src = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="a"), models.ParagraphBlock(text="b")])
    tr = src.model_copy(deep=True)
    changed = process.ensure_block_ids(src, tr)
    assert changed is True
    assert all(b.block_id for b in src.blocks)
    assert [b.block_id for b in src.blocks] == [b.block_id for b in tr.blocks]  # mirrored by position
    # idempotent
    assert process.ensure_block_ids(src, tr) is False


def test_load_source_model_backfills(tmp_path):
    storage, doc_id = str(tmp_path), "d"
    os.makedirs(process.doc_dir(storage, doc_id), exist_ok=True)
    # persist an OLD model with no block_id
    old = models.SemanticDocument(source_lang="ko", blocks=[models.ParagraphBlock(text="x")])
    raw = old.model_dump(); raw["blocks"][0].pop("block_id", None)
    with open(os.path.join(process.doc_dir(storage, doc_id), "source.json"), "w", encoding="utf-8") as fh:
        json.dump(raw, fh)
    loaded = process.load_source_model(storage, doc_id)
    assert loaded.blocks[0].block_id is not None              # backfilled on load
    # and persisted: a re-load sees the same id
    assert process.load_source_model(storage, doc_id).blocks[0].block_id == loaded.blocks[0].block_id
