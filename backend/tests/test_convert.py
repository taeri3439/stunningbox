import os
from pipeline import convert


def test_needs_conversion_by_extension():
    # needs_conversion = "to_docx will run LibreOffice on it" → only .doc.
    # .hwp goes via hwp_to_html + the native extractor (LibreOffice can't read HWP).
    assert convert.needs_conversion("a.doc") is True
    assert convert.needs_conversion("a.DOC") is True
    assert convert.needs_conversion("a.docx") is False
    assert convert.needs_conversion("a.pdf") is False
    assert convert.needs_conversion("a.hwp") is False


def test_to_docx_rejects_hwp():
    # HWP is not a to_docx input; it must be routed through hwp_to_html + hwp_extractor.
    import pytest
    with pytest.raises(ValueError):
        convert.to_docx("a.hwp", "/out")


def test_to_docx_is_noop_for_docx(tmp_path):
    src = tmp_path / "already.docx"
    src.write_bytes(b"PK\x03\x04")  # not opened, just must exist
    out = convert.to_docx(str(src), str(tmp_path / "out"))
    assert out == str(src)


def test_to_docx_rejects_unknown_format(tmp_path):
    src = tmp_path / "weird.rtf"
    src.write_bytes(b"x")
    import pytest
    with pytest.raises(ValueError):
        convert.to_docx(str(src), str(tmp_path / "out"))


def test_soffice_cmd_builds_isolated_profile_url():
    cmd = convert.soffice_cmd("/in/a.doc", "/out", "/tmp/prof", soffice="soffice")
    assert cmd[0] == "soffice"
    assert "--headless" in cmd and "docx" in cmd
    assert "--outdir" in cmd and "/out" in cmd
    assert cmd[-1] == "/in/a.doc"
    assert any(a.startswith("-env:UserInstallation=file:///tmp/prof") for a in cmd)


def test_soffice_cmd_target_pdf():
    cmd = convert.soffice_cmd("/in/index.html", "/out", "/tmp/p", target="pdf")
    i = cmd.index("--convert-to")
    assert cmd[i + 1] == "pdf"
