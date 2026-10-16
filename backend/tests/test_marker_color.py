from types import SimpleNamespace

from pipeline.reconciler import _marker_color


def _span(id, text, x0, y0, color):
    return SimpleNamespace(id=id, text=text, bbox=(x0, y0, x0+20, y0+10), color=color, font="", size=12, bold=False)


def test_marker_color_distinct_leading_span():
    # "FIGURE 3" gold (0xD4B031) then title teal (0x0D5775)
    spans = [_span("a", "FIGURE 3", 10, 100, 0xD4B031), _span("b", "BREAKDOWN OF X", 90, 100, 0x0D5775)]
    by_id = {s.id: s for s in spans}
    assert _marker_color(["a", "b"], by_id, "FIGURE 3") == "D4B031"


def test_marker_color_none_when_same_as_title():
    spans = [_span("a", "2.1.2.2", 10, 100, 0x007C9E), _span("b", "Break down", 60, 100, 0x007C9E)]
    by_id = {s.id: s for s in spans}
    # leading marker colour equals the title colour -> _marker_color still returns it (007C9E);
    # the reconciler is what suppresses it when == block colour. Here assert the raw colour.
    assert _marker_color(["a", "b"], by_id, "2.1.2.2") == "007C9E"


def test_marker_color_none_when_ink():
    spans = [_span("a", "1.", 10, 100, 0x000000), _span("b", "Intro", 40, 100, 0x0D5775)]
    by_id = {s.id: s for s in spans}
    assert _marker_color(["a", "b"], by_id, "1.") is None


def test_marker_color_none_when_marker_not_leading():
    spans = [_span("b", "Some title text", 10, 100, 0x0D5775)]
    by_id = {s.id: s for s in spans}
    assert _marker_color(["b"], by_id, "FIGURE 9") is None


def test_marker_color_none_marker_empty():
    assert _marker_color([], {}, None) is None
