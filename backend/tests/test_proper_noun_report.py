from types import SimpleNamespace

from pipeline import process
from pipeline.proper_noun_audit import MockPnAuditor


def test_proper_noun_report_wires_segments_glossary_source(monkeypatch):
    monkeypatch.setattr(process, "load_doc_glossary", lambda *a: {})
    monkeypatch.setattr(process, "build_segments",
                        lambda *a, **k: [{"index": 0, "source": "스터닝박스 제품",
                                          "translated": "StunningBox product",
                                          "source_bbox": None, "block_id": "b1"}])
    monkeypatch.setattr(process, "load_source_model",
                        lambda *a: SimpleNamespace(source_lang="ko"))
    mock = MockPnAuditor([{"source_term": "스터닝박스", "translated_form": "StunningBox",
                           "segment_indexes": [0]}])
    out = process.proper_noun_report("S", "doc1", "en", auditor=mock)
    assert out[0]["source"] == "스터닝박스" and out[0]["count"] == 1


def test_proper_noun_report_empty_glossary_does_not_short_circuit(monkeypatch):
    monkeypatch.setattr(process, "load_doc_glossary", lambda *a: {})  # no glossary
    monkeypatch.setattr(process, "build_segments",
                        lambda *a, **k: [{"index": 0, "source": "x", "translated": "y",
                                          "source_bbox": None, "block_id": None}])
    monkeypatch.setattr(process, "load_source_model",
                        lambda *a: SimpleNamespace(source_lang="ko"))
    # auditor finds nothing → empty list, but the function still RAN the audit (no early return).
    out = process.proper_noun_report("S", "doc1", "en", auditor=MockPnAuditor([]))
    assert out == []
