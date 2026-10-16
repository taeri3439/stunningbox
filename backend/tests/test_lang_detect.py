"""Language auto-detection + default translation direction."""
from pipeline import lang_detect as L


def test_detect_korean():
    assert L.detect_lang("이 규격은 소방활동에 사용되는 소방사다리차의 구조와 성능기준을 규정한다.") == "ko"


def test_detect_korean_with_english_terms():
    # Hangul guard: a Korean doc with English technical terms still detects as Korean.
    assert L.detect_lang(
        "이 표준은 AUC 비교와 MRHD 값에 근거한 노출 배수(Multiples of Exposure)를 설명한다."
    ) == "ko"


def test_detect_english():
    assert L.detect_lang(
        "This specification defines the structure and performance criteria for "
        "aerial ladder vehicles used in firefighting operations."
    ) == "en"


def test_detect_japanese():
    assert L.detect_lang("この規格は消防活動に使用されるはしご車の構造を規定します。") == "ja"


def test_detect_empty_defaults_english():
    assert L.detect_lang("") == "en"
    assert L.detect_lang("   12.5  ±4%  ") == "en"  # no script signal


def test_hwp_shortcut():
    assert L.detect_lang("", is_hwp=True) == "ko"
    assert L.detect_lang("anything", is_hwp=True) == "ko"


def test_default_direction():
    assert L.default_direction("ko") == ("ko", "en")
    assert L.default_direction("en") == ("en", "ko")
    assert L.default_direction("ja") == ("ja", "ko")
    assert L.default_direction("zz") == ("en", "ko")  # unknown source -> en -> ko


def test_sample_text_docx(tmp_path):
    from docx import Document
    d = Document()
    d.add_paragraph("Nonclinical Overview and Pharmacology summary.")
    p = str(tmp_path / "s.docx")
    d.save(p)
    assert "Nonclinical Overview" in L.sample_text(p)


def test_sample_text_unknown_ext_empty(tmp_path):
    f = tmp_path / "x.hwp"
    f.write_bytes(b"\x00\x01")
    assert L.sample_text(str(f)) == ""  # .hwp not sampled here -> "" (caller defaults ko)
