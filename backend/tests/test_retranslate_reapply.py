import os

from models import ParagraphBlock, SemanticDocument
from pipeline import process
from pipeline.text_replace import ReplaceRule
from pipeline.translator import MockTranslator


def _seed(tmp_path):
    storage, doc_id, lang = str(tmp_path), "doc1", "en"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(base, exist_ok=True)
    process._write_json(
        os.path.join(base, "source.json"),
        SemanticDocument(source_lang="ko",
                         blocks=[ParagraphBlock(text="캐빈", source_span_ids=[],
                                                block_id="BLK0")]).model_dump(),
    )
    process.save_translated_model(
        storage, doc_id, lang,
        SemanticDocument(source_lang="ko", target_lang=lang,
                         blocks=[ParagraphBlock(text="Cabin", source_span_ids=[],
                                                block_id="BLK0")]),
    )
    return storage, doc_id, lang


def test_retranslate_reapplies_saved_replacements(tmp_path):
    storage, doc_id, lang = _seed(tmp_path)
    # Source "캐빈" → built-in glossary (캐빈→Cabin) → MockTranslator yields "[en] Cabin".
    # A saved TARGET-side rule (Cabin → Cab) must be re-applied to that fresh translation.
    process.save_replacements(storage, doc_id, lang,
                              [ReplaceRule(find="Cabin", replace="Cab", match="smart")])
    out = process.retranslate_one_segment(storage, doc_id, lang, "BLK0:text", "",
                                          translator=MockTranslator())
    assert out["new_text"] == "[en] Cab"


def test_retranslate_without_saved_rules_unchanged(tmp_path):
    storage, doc_id, lang = _seed(tmp_path)
    out = process.retranslate_one_segment(storage, doc_id, lang, "BLK0:text", "",
                                          translator=MockTranslator())
    assert out["new_text"] == "[en] Cabin"
