"""Coloured text must survive as a meaningful colour. The old packed-int near-black filter
classified any low-valued packed RGB (blue 0x0000FF == 255, green 0x00FF00) as ink and dropped
it, so blue/green headings lost their colour. The channel-based check keeps any bright channel."""
from models import RawSpan
from pipeline import reconciler


def _span(color):
    return RawSpan(id="s1", page=0, text="x", bbox=(0, 0, 10, 10), size=10.0, color=color)


def test_is_ink_channel_based():
    assert reconciler._is_ink(0x000000)       # black
    assert reconciler._is_ink(0x303030)       # near-black gray
    assert not reconciler._is_ink(0x0000FF)    # blue (was wrongly treated as ink)
    assert not reconciler._is_ink(0x00FF00)    # green (was wrongly treated as ink)
    assert not reconciler._is_ink(0xFF0000)    # red


def test_dominant_inline_keeps_blue():
    _, color, _ = reconciler._dominant_inline(["s1"], {"s1": _span(0x0000FF)})
    assert color == "0000FF"


def test_dominant_inline_drops_black_ink():
    _, color, _ = reconciler._dominant_inline(["s1"], {"s1": _span(0x000000)})
    assert color is None
