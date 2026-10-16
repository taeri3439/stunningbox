"""Cross-side band dedup: a center that restates split left/right must not render twice."""
from pipeline.reconciler import _dedupe_band_sides, _pick_band


def test_center_concatenation_of_left_right_dropped():
    l, c, r = _dedupe_band_sides(
        "2.7.2 Summary of Clinical Pharmacology Studies",
        "2.7.2 Summary of Clinical Pharmacology Studies   CB-03-01 (clascoterone) cream 1%",
        "CB-03-01 (clascoterone) cream 1%")
    assert c is None and l and r


def test_center_equal_to_left_dropped():
    l, c, r = _dedupe_band_sides("Confidential", "Confidential", None)
    assert c is None and l == "Confidential"


def test_distinct_sides_kept():
    l, c, r = _dedupe_band_sides("Left", "Middle", "Right")
    assert (l, c, r) == ("Left", "Middle", "Right")


def test_left_equal_right_keeps_one():
    l, c, r = _dedupe_band_sides("Same", None, "Same")
    assert l == "Same" and r is None


def test_pick_band_applies_dedup():
    # pages alternate: merged center block vs split left/right — voting fills all 3 sides
    items = []
    for pg in (0, 2, 4):
        items += [("Alpha Corp", "left", pg), ("Page Title", "right", pg)]
    for pg in (1, 3, 5):
        items += [("Alpha Corp Page Title", "center", pg)]
    band = _pick_band(items, None)
    assert band.left == "Alpha Corp" and band.right == "Page Title" and band.center is None
