"""The `lang`/`target_lang` query param flows into on-disk filenames. These guard against
path traversal (an owner of doc A using lang='../<B>/output' to read/overwrite doc B)."""
import pytest

from pipeline import process


@pytest.mark.parametrize(
    "builder",
    [process.output_docx_path, process.translated_path, process.edited_path, process.replacements_path],
)
@pytest.mark.parametrize(
    "bad_lang",
    ["../../other/output", "..%2f..%2fx", "../x", "a/b", "a.b", "en/../../etc/passwd", "..\\..\\x"],
)
def test_path_builders_reject_traversal(builder, bad_lang):
    with pytest.raises(ValueError):
        builder("/storage", "doc1", bad_lang)


def test_path_builders_accept_normal_lang():
    assert process.output_docx_path("/storage", "doc1", "en").endswith("output.en.docx")
    assert process.translated_path("/storage", "doc1", "ko").endswith("translated.ko.json")
    assert process.edited_path("/storage", "doc1", "en").endswith("edited.en.json")
    assert process.replacements_path("/storage", "doc1", "ja").endswith("replacements.ja.json")


def test_output_docx_path_none_uses_src():
    # No target language → the constant "src" suffix (not user input), still safe.
    assert process.output_docx_path("/storage", "doc1", None).endswith("output.src.docx")
