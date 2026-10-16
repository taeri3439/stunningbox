from types import SimpleNamespace
from pipeline.reconciler import _dominant_style

def _s(font, bold=False, size=13.0):
    return SimpleNamespace(font=font, bold=bold, size=size, color=0, text="X", bbox=(0,0,1,1))

def test_heading_medium_demi_recovered_as_bold():
    spans = [_s("FranklinGothic-MediumCon"), _s("FranklinGothic-DemiCond")]
    by_id = {str(i): s for i, s in enumerate(spans)}
    _, bold = _dominant_style(list(by_id), by_id, heading=True)
    assert bold is True

def test_body_medium_not_forced_bold():
    spans = [_s("SomeFont-Medium"), _s("SomeFont-Medium")]
    by_id = {str(i): s for i, s in enumerate(spans)}
    _, bold = _dominant_style(list(by_id), by_id, heading=False)
    assert bold is False  # body path unchanged: no bold flag, name has no literal "bold"

def test_regular_heading_not_forced_bold():
    spans = [_s("FranklinGothic-Book"), _s("FranklinGothic-Book")]
    by_id = {str(i): s for i, s in enumerate(spans)}
    _, bold = _dominant_style(list(by_id), by_id, heading=True)
    assert bold is False

def test_flag_bold_still_wins():
    spans = [_s("Arial", bold=True), _s("Arial", bold=True)]
    by_id = {str(i): s for i, s in enumerate(spans)}
    _, bold = _dominant_style(list(by_id), by_id, heading=True)
    assert bold is True
