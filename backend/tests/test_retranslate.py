import json
import os

from models import ParagraphBlock, SemanticDocument
from pipeline import process
from pipeline.translator import MockTranslator


def _seed(tmp_path):
    """A 2-paragraph Korean source doc on disk (no source.pdf -> bbox is null)."""
    storage, doc_id = str(tmp_path), "doc1"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(base, exist_ok=True)
    doc = SemanticDocument(
        source_lang="ko",
        blocks=[
            ParagraphBlock(text="첫 문장", source_span_ids=["p0b0l0s0"], block_id="BLK0"),
            ParagraphBlock(text="둘째 문장", source_span_ids=["p0b1l0s0"], block_id="BLK1"),
        ],
    )
    with open(os.path.join(base, "source.json"), "w", encoding="utf-8") as fh:
        json.dump(doc.model_dump(), fh, ensure_ascii=False)
    return storage, doc_id


def test_build_segments_pairs_source_and_translation(tmp_path):
    storage, doc_id = _seed(tmp_path)
    segs = process.build_segments(storage, doc_id, "en", translator=MockTranslator())
    assert [s["index"] for s in segs] == [0, 1]
    assert [s["source"] for s in segs] == ["첫 문장", "둘째 문장"]
    assert all(s["translated"].startswith("[en]") for s in segs)
    assert all(s["source_bbox"] is None for s in segs)         # no source.pdf in tmp
    assert all(isinstance(s["block_id"], str) for s in segs)    # auto-assigned uuid hex by load_source_model


def test_retranslate_one_segment_updates_cache(tmp_path):
    storage, doc_id = _seed(tmp_path)
    segs = process.build_segments(storage, doc_id, "en", translator=MockTranslator())  # warms cache
    seg_id = segs[1]["id"]  # seg-id for the second segment ("둘째 문장")
    res = process.retranslate_one_segment(
        storage, doc_id, "en", seg_id, guidance="be concise",
        translator=MockTranslator(),
    )
    assert res["id"] == seg_id
    assert "[g:be concise]" in res["new_text"]
    assert res["old_text"] != res["new_text"]
    # the change is persisted to translated.en.json at that block only
    cache = os.path.join(process.doc_dir(storage, doc_id), "translated.en.json")
    with open(cache, encoding="utf-8") as fh:
        model = json.load(fh)
    assert model["blocks"][1]["text"] == res["new_text"]
    assert "[g:" not in model["blocks"][0]["text"]             # first block untouched


def test_retranslate_index_out_of_range(tmp_path):
    storage, doc_id = _seed(tmp_path)
    try:
        process.retranslate_one_segment(
            storage, doc_id, "en", "NOSUCHID:text", guidance="", translator=MockTranslator()
        )
        assert False, "expected IndexError"
    except IndexError:
        pass
