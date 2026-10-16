import fitz

from pipeline.extractor import (
    extract_document,
    extract_span_bboxes,
    segment_source_bbox,
)


def test_segment_source_bbox_unions_and_fractions():
    span_map = {"a": (0, (10.0, 20.0, 30.0, 40.0)),
                "b": (0, (30.0, 20.0, 50.0, 45.0))}
    page_sizes = [(100.0, 200.0)]
    bbox = segment_source_bbox(["a", "b"], span_map, page_sizes)
    assert bbox == {"page": 0, "x": 0.1, "y": 0.1, "w": 0.4, "h": 0.125}


def test_segment_source_bbox_missing_and_degenerate():
    assert segment_source_bbox(["zzz"], {"a": (0, (1, 1, 2, 2))}, [(10, 10)]) is None
    assert segment_source_bbox([], {}, []) is None
    assert segment_source_bbox(["a"], {"a": (0, (1, 1, 2, 2))}, [(0, 0)]) is None


def test_extract_span_bboxes_ids_match_scheme(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=200, height=300)
    page.insert_text((50, 100), "Hello world")
    p = str(tmp_path / "s.pdf")
    doc.save(p)
    doc.close()

    span_map, page_sizes = extract_span_bboxes(p)
    assert page_sizes == [(200.0, 300.0)]
    assert span_map                                            # at least one span found
    sid, (pno, box) = next(iter(span_map.items()))
    assert sid.startswith("p0b") and "l" in sid and "s" in sid  # extractor id scheme
    assert pno == 0 and len(box) == 4


def test_span_ids_match_extract_document(tmp_path):
    # The whole feature relies on extract_span_bboxes producing the SAME ids as
    # extract_document (which is what populated the model's source_span_ids). Pin it.
    doc = fitz.open()
    page = doc.new_page(width=200, height=300)
    page.insert_text((50, 100), "Hello world")
    page.insert_text((50, 150), "두 번째 줄입니다")
    p = str(tmp_path / "s.pdf")
    doc.save(p)
    doc.close()

    raw = extract_document(p, str(tmp_path / "assets"))
    full_ids = {s.id for pg in raw.pages for s in pg.spans}
    span_map, _ = extract_span_bboxes(p)
    assert set(span_map) == full_ids and full_ids   # identical, non-empty id sets
