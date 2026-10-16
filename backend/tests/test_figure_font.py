import os

from pipeline import figure_overlay


def test_bundled_cjk_font_is_nanum():
    p = figure_overlay.bundled_cjk_font()
    assert p is not None and os.path.exists(p)
    assert os.path.basename(p) == "NanumGothic.ttf"


def test_font_family_constant():
    assert figure_overlay.FONT_FAMILY == "NanumGothic"


def test_resolve_font_prefers_bundled_nanum():
    assert figure_overlay.resolve_font("ko") == figure_overlay.bundled_cjk_font()
