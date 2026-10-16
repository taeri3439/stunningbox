"""Page-number chrome: recognize 'Page N of M' / 'N/M' / '- N -' / bare digits and
render PAGE (+ NUMPAGES) fields in the band."""
import docx as _docx
from pipeline.reconciler import _page_number_spec
from models import RunningBand, SemanticDocument
from renderers.docx_renderer import render_docx


def test_page_n_of_m_detected():
    items = [(f"Page {i} of 32", "right", i) for i in range(1, 6)]
    assert _page_number_spec(items) == ("right", "Page {n} of {m}")


def test_bare_digits_legacy_still_detected():
    items = [(str(i), "center", i) for i in (2, 3, 4)]
    assert _page_number_spec(items) == ("center", "{n}")


def test_slash_format():
    items = [(f"{i}/20", "right", i) for i in (1, 2, 3)]
    assert _page_number_spec(items) == ("right", "{n}/{m}")


def test_non_number_chrome_ignored():
    items = [("Confidential", "center", 0), ("Confidential", "center", 1)]
    assert _page_number_spec(items) is None


def test_single_hit_not_enough():
    assert _page_number_spec([("Page 1 of 2", "right", 0)]) is None


def test_docx_renders_page_and_numpages_fields(tmp_path):
    doc = SemanticDocument(
        source_lang="en",
        blocks=[],
        running_footer=RunningBand(left="Acme", page_number="right",
                                   page_number_format="Page {n} of {m}"),
    )
    out = str(tmp_path / "o.docx")
    render_docx(doc, out, str(tmp_path))
    d = _docx.Document(out)
    xml = d.sections[0].footer._element.xml
    assert "PAGE" in xml and "NUMPAGES" in xml
    assert "Page " in xml and " of " in xml  # literal chrome around the fields
