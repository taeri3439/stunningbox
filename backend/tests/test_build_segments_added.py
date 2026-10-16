import os, json
import models
from pipeline import process


def _seed_with_added(tmp_path):
    storage, doc_id, lang = str(tmp_path), "d", "en"
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    src = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="원문0", block_id="B0", source_span_ids=["s0"])])
    with open(os.path.join(process.doc_dir(storage, doc_id), "source.json"), "w", encoding="utf-8") as fh:
        json.dump(src.model_dump(), fh)
    tr = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="t0", block_id="B0", source_span_ids=["s0"]),
        models.ParagraphBlock(text="ADDED translation", block_id="NEW1", source_span_ids=[])])
    process.save_translated_model(storage, doc_id, lang, tr)
    return storage, doc_id, lang


def test_added_segment_emitted_with_empty_source(tmp_path):
    storage, doc_id, lang = _seed_with_added(tmp_path)
    segs = process.build_segments(storage, doc_id, lang)
    by_id = {s["id"]: s for s in segs}
    assert by_id["B0:text"]["added"] is False and by_id["B0:text"]["source"] == "원문0"
    added = by_id["NEW1:text"]
    assert added["added"] is True and added["source"] == "" and added["translated"] == "ADDED translation"
