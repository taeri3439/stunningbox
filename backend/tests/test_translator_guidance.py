from pipeline import translator as T


def test_build_guidance_combines_preset_and_custom():
    g = T.build_guidance("concise", "keep legal terms verbatim")
    assert "concise" in g.lower()
    assert "keep legal terms verbatim" in g
    assert T.build_guidance(None, None) == ""
    assert T.build_guidance("bogus", None) == ""        # unknown preset ignored
    assert T.build_guidance(None, "  only custom ") == "only custom"


def test_mock_translate_threads_guidance():
    out = T.MockTranslator().translate(
        ["원문"], "ko", "en", {}, guidance="be very formal"
    )
    assert out[0].startswith("[en]")
    assert "be very formal" in out[0]                   # guidance is visible to callers


def test_translate_segment_guided_and_atomic():
    tr = T.MockTranslator()
    assert T.translate_segment("정격하중", "ko", "en", "concise", tr) != "정격하중"
    # atomic / empty segments are returned unchanged (no wasted translation)
    assert T.translate_segment("12 960", "ko", "en", "", tr) == "12 960"
    assert T.translate_segment("   ", "ko", "en", "", tr) == "   "
