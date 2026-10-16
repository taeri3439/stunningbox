import os, json
import models
from pipeline import process


def _seed(tmp_path, reorder=False):
    storage, doc_id, lang = str(tmp_path), "d", "en"
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    src = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="원문0", block_id="B0", source_span_ids=["s0"]),
        models.ParagraphBlock(text="원문1", block_id="B1", source_span_ids=["s1"]),
    ])
    tr_blocks = [models.ParagraphBlock(text="t0", block_id="B0", source_span_ids=["s0"]),
                 models.ParagraphBlock(text="t1", block_id="B1", source_span_ids=["s1"])]
    if reorder:
        tr_blocks = tr_blocks[::-1]   # translated reordered relative to source
    tr = models.SemanticDocument(source_lang="ko", blocks=tr_blocks)
    with open(os.path.join(process.doc_dir(storage, doc_id), "source.json"), "w", encoding="utf-8") as fh:
        json.dump(src.model_dump(), fh)
    process.save_translated_model(storage, doc_id, lang, tr)
    return storage, doc_id, lang


def test_build_segments_pairs_by_id_when_unreordered(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, reorder=False)
    segs = process.build_segments(storage, doc_id, lang)
    assert [(s["source"], s["translated"]) for s in segs] == [("원문0", "t0"), ("원문1", "t1")]
    assert segs[0]["id"] == "B0:text" and segs[0]["block_id"] == "B0"


def test_build_segments_pairs_by_id_after_reorder(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, reorder=True)
    segs = process.build_segments(storage, doc_id, lang)
    # translated order is [t1, t0]; each still paired to its OWN source by id
    assert [(s["source"], s["translated"]) for s in segs] == [("원문1", "t1"), ("원문0", "t0")]
