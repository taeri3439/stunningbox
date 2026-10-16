from pipeline.proper_noun_audit import verify, audit, MockPnAuditor, get_pn_auditor


def _seg(index, source, translated, block_id=None, bbox=None):
    return {"index": index, "source": source, "translated": translated,
            "source_bbox": bbox, "block_id": block_id}


def test_verify_flags_translated_proper_noun():
    segs = [_seg(0, "스터닝박스는 회사다.", "StunningBox is a company.", block_id="b3")]
    cands = [{"source_term": "스터닝박스", "translated_form": "StunningBox",
              "category": "brand", "segment_indexes": [0]}]
    out = verify(cands, segs, {}, "ko", "en")
    assert len(out) == 1
    r = out[0]
    assert r["source"] == "스터닝박스" and r["translated"] == "StunningBox"
    assert r["category"] == "brand" and r["count"] == 1
    assert r["locations"][0] == {"segment_index": 0, "block_id": "b3",
                                 "source_bbox": None, "applied": False}


def test_verify_excludes_glossary_term():
    segs = [_seg(0, "스터닝박스는 회사다.", "StunningBox is a company.")]
    cands = [{"source_term": "스터닝박스", "translated_form": "StunningBox", "segment_indexes": [0]}]
    assert verify(cands, segs, {"스터닝박스": "StunningBox"}, "ko", "en") == []


def test_verify_excludes_kept_verbatim():
    # source form survives verbatim in the translation → kept, not translated → drop.
    segs = [_seg(0, "스터닝박스는 회사다.", "스터닝박스 is a company.")]
    cands = [{"source_term": "스터닝박스", "translated_form": "스터닝박스", "segment_indexes": [0]}]
    assert verify(cands, segs, {}, "ko", "en") == []


def test_verify_resolves_sparse_indexes_and_falls_back():
    segs = [_seg(5, "아크경보기 정의", "Arc detector definition", block_id="b2")]
    cands = [{"source_term": "아크경보기", "translated_form": "Arc detector", "segment_indexes": [5]}]
    assert verify(cands, segs, {}, "ko", "en")[0]["locations"][0]["segment_index"] == 5
    # unknown index → fall back to a full scan and still find it
    c2 = [{"source_term": "아크경보기", "translated_form": "Arc detector", "segment_indexes": [99]}]
    assert verify(c2, segs, {}, "ko", "en")[0]["count"] == 1


def test_audit_uses_injected_mock_auditor():
    segs = [_seg(0, "스터닝박스 제품", "StunningBox product")]
    mock = MockPnAuditor([{"source_term": "스터닝박스", "translated_form": "StunningBox",
                           "segment_indexes": [0]}])
    out = audit(segs, {}, "ko", "en", auditor=mock)
    assert out[0]["source"] == "스터닝박스"


def test_get_pn_auditor_offline(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(get_pn_auditor(), MockPnAuditor)
