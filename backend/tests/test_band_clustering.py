"""Chrome span clustering: split a merged header/footer block into side runs by x-gap."""
from types import SimpleNamespace

from pipeline.reconciler import _split_chrome_clusters


def _span(text, x0, x1, y0=10.0, y1=20.0):
    return SimpleNamespace(text=text, bbox=(x0, y0, x1, y1))


def test_wide_gap_splits_into_two_clusters():
    spans = [_span("Sun Pharmaceutical Industries Ltd.", 50, 220),
             _span("Confidential", 400, 470)]
    out = _split_chrome_clusters(spans, page_w=612.0)
    assert [t for t, _, _ in out] == ["Sun Pharmaceutical Industries Ltd.", "Confidential"]
    # x-centers: 135 (< 0.4*612=244.8 -> left) and 435 (> 0.6*612=367.2 -> right)
    assert out[0][1] < 0.4 * 612 and out[1][1] > 0.6 * 612  # left, right


def test_adjacent_spans_stay_one_cluster():
    spans = [_span("2.7.2 Summary of", 50, 150), _span("Clinical Pharmacology", 155, 300)]
    out = _split_chrome_clusters(spans, page_w=612.0)
    assert len(out) == 1
    assert out[0][0] == "2.7.2 Summary of Clinical Pharmacology"


def test_three_way_split_left_center_right():
    spans = [_span("Company", 40, 120), _span("Confidential", 280, 340), _span("Page 3 of 32", 500, 580)]
    out = _split_chrome_clusters(spans, page_w=612.0)
    assert [t for t, _, _ in out] == ["Company", "Confidential", "Page 3 of 32"]


def test_empty_and_bboxless_spans_skipped():
    spans = [SimpleNamespace(text="x", bbox=None), _span("  ", 10, 20)]
    assert _split_chrome_clusters(spans, page_w=612.0) == []


def test_no_page_width_uses_point_fallback_gap():
    spans = [_span("A", 0, 10), _span("B", 60, 70)]  # 50pt gap > 24pt fallback
    out = _split_chrome_clusters(spans, page_w=0)
    assert [t for t, _, _ in out] == ["A", "B"]
