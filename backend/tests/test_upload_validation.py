from main import _ext_ok


def test_allowed_extensions():
    assert _ext_ok("a.pdf") is True
    assert _ext_ok("a.PDF") is True
    assert _ext_ok("report.docx") is True
    assert _ext_ok("legacy.doc") is True
    assert _ext_ok("hangul.hwp") is True


def test_rejected_extensions():
    assert _ext_ok("note.txt") is False
    assert _ext_ok("image.png") is False
    assert _ext_ok("") is False
    # .hwpx is deferred until a converter is validated.
    assert _ext_ok("hangul.hwpx") is False
