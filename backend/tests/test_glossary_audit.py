from pipeline.glossary_audit import audit_glossary


def _seg(index, source, translated, block_id=None, bbox=None):
    return {"index": index, "source": source, "translated": translated,
            "source_bbox": bbox, "block_id": block_id}


def test_applied_partial_missed_absent():
    segments = [
        _seg(0, "정격하중 표", "Rated Load table", block_id="b0"),   # applied
        _seg(1, "정격하중 재확인", "Capacity recheck", block_id="b1"),  # source has 원어, target missing → missed
        _seg(2, "캐빈 점검", "Cabin check", block_id="b2"),           # applied
        _seg(3, "무관한 문장", "Unrelated sentence", block_id="b3"),
    ]
    glossary = {"정격하중": "Rated Load", "캐빈": "Cabin", "스태빌라이저": "Stabilizer"}
    out = {t["source"]: t for t in audit_glossary(segments, glossary, "ko", "en")}

    assert out["정격하중"]["status"] == "partial"
    assert out["정격하중"]["source_count"] == 2
    assert out["정격하중"]["applied_count"] == 1
    locs = {l["block_id"]: l["applied"] for l in out["정격하중"]["locations"]}
    assert locs == {"b0": True, "b1": False}

    assert out["캐빈"]["status"] == "applied"
    assert out["스태빌라이저"]["status"] == "absent"
    assert out["스태빌라이저"]["source_count"] == 0


def test_english_word_boundary_and_case():
    # target "Load" must not count inside "Download"; case-insensitive.
    segments = [_seg(0, "하중", "the load is high", block_id="b0"),
                _seg(1, "하중", "Download the file", block_id="b1")]
    out = audit_glossary([*segments], {"하중": "Load"}, "ko", "en")[0]
    assert out["status"] == "partial"  # seg0 applied (load), seg1 missed (only Download)
    assert out["applied_count"] == 1


def test_empty_glossary_returns_empty():
    assert audit_glossary([_seg(0, "hello", "bonjour")], {}, "en", "fr") == []


def test_count_is_per_segment_not_per_token():
    # 원어 appears twice in one segment → counts as ONE location/segment.
    segs = [_seg(0, "하중 하중", "load load", block_id="b0")]
    out = audit_glossary(segs, {"하중": "load"}, "ko", "en")[0]
    assert out["source_count"] == 1
    assert out["applied_count"] == 1
    assert out["status"] == "applied"
