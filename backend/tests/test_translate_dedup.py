"""Identical source segments must translate identically (translated once)."""
import models
from pipeline.translator import translate_document


class SpyTranslator:
    """Returns 'T<call#>:<text>' so drifting duplicates would be visible; records calls."""

    def __init__(self):
        self.calls = []

    def translate(self, texts, source_lang, target_lang, glossary, on_done=None, style_context=""):
        self.calls.append(list(texts))
        return [f"T:{t}" for t in texts]


def _doc(texts):
    return models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text=t, source_span_ids=[f"s{i}"]) for i, t in enumerate(texts)
    ])


def test_duplicates_sent_once_and_fanned_out():
    spy = SpyTranslator()
    doc = _doc(["통과 (공 용)", "정지", "통과 (공 용)", "통과 (공 용)"])
    out = translate_document(doc, "en", translator=spy)
    sent = [t for call in spy.calls for t in call]
    assert sent == ["통과 (공 용)", "정지"]          # unique, first-seen order
    got = [b.text for b in out.blocks]
    assert got == ["T:통과 (공 용)", "T:정지", "T:통과 (공 용)", "T:통과 (공 용)"]


def test_tm_hits_apply_to_all_occurrences():
    spy = SpyTranslator()
    doc = _doc(["A", "B", "A"])
    out = translate_document(doc, "en", translator=spy, tm_map={"A": "TM-A"})
    sent = [t for call in spy.calls for t in call]
    assert sent == ["B"]                              # TM text never reaches the LLM
    assert [b.text for b in out.blocks] == ["TM-A", "T:B", "TM-A"]


def test_all_tm_reports_completion(monkeypatch):
    spy = SpyTranslator()
    doc = _doc(["A", "A"])
    seen = []
    out = translate_document(doc, "en", translator=spy, tm_map={"A": "TM-A"},
                             on_progress=lambda st, k, n: seen.append((st, k, n)))
    assert spy.calls == [] or spy.calls == [[]]
    assert [b.text for b in out.blocks] == ["TM-A", "TM-A"]
    assert ("translate", 1, 1) in seen
