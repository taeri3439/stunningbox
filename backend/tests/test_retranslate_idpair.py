"""Task 5: retranslate_one_segment resolves by seg-id, not positional index.

The key correctness guarantee: after a reorder (translated order != source order)
a retranslate call with a seg-id must write to the CORRECT translated field — the
one matching that id — not the field at the same positional index.
"""
import json
import os

from models import ParagraphBlock, SemanticDocument
from pipeline import process


class _Echo:
    """Minimal translator: returns the source text prefixed with 'RT:' so we can
    distinguish it from MockTranslator's '[en] …' prefix."""
    def translate(self, texts, source_lang, target_lang, glossary, on_done=None,
                  guidance="", style_context=""):
        return ["RT:" + t for t in texts]


def _seed_reordered(tmp_path):
    """Seed:
    - source.json: [B0 "원문0", B1 "원문1"]  (block_ids B0, B1)
    - translated.en.json: [B1 "번역1", B0 "번역0"]  (REORDERED — B1 first)

    A positional implementation would write to translated block at index 0
    (which is B1), but the correct implementation writes to the field with
    matching seg-id (B0 → translated block at index 1).
    """
    storage, doc_id, lang = str(tmp_path), "doc1", "en"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(base, exist_ok=True)

    source = SemanticDocument(
        source_lang="ko",
        blocks=[
            ParagraphBlock(text="원문0", source_span_ids=[], block_id="B0"),
            ParagraphBlock(text="원문1", source_span_ids=[], block_id="B1"),
        ],
    )
    with open(os.path.join(base, "source.json"), "w", encoding="utf-8") as fh:
        json.dump(source.model_dump(), fh, ensure_ascii=False)

    # Translated is REORDERED: B1 first, B0 second
    translated = SemanticDocument(
        source_lang="ko",
        target_lang=lang,
        blocks=[
            ParagraphBlock(text="번역1", source_span_ids=[], block_id="B1"),
            ParagraphBlock(text="번역0", source_span_ids=[], block_id="B0"),
        ],
    )
    process.save_translated_model(storage, doc_id, lang, translated)

    return storage, doc_id, lang


def test_retranslate_by_id_after_reorder(tmp_path):
    """Retranslating "B0:text" must update B0's translated field, even though
    B0 sits at index 1 in the translated model (not index 0)."""
    storage, doc_id, lang = _seed_reordered(tmp_path)

    res = process.retranslate_one_segment(
        storage, doc_id, lang, "B0:text", "", translator=_Echo()
    )

    assert res["id"] == "B0:text"

    # Verify the write landed on B0's translated field (index 1 in translated)
    tr = process.get_translated_model(storage, doc_id, lang)
    b0 = next(b for b in tr.blocks if b.block_id == "B0")
    assert b0.text == "RT:원문0", f"Expected 'RT:원문0', got {b0.text!r}"

    # B1 must be untouched
    b1 = next(b for b in tr.blocks if b.block_id == "B1")
    assert b1.text == "번역1", f"B1 was unexpectedly modified: {b1.text!r}"


def test_retranslate_id_not_found_raises(tmp_path):
    """Unknown seg-id raises IndexError."""
    storage, doc_id, lang = _seed_reordered(tmp_path)
    try:
        process.retranslate_one_segment(
            storage, doc_id, lang, "NOSUCH:text", "", translator=_Echo()
        )
        assert False, "expected IndexError"
    except IndexError:
        pass
