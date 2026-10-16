"""tpages/tsegments/tfind routes (DS conversion monkeypatched)."""
import fitz
import pytest
from fastapi.testclient import TestClient

import main
from pipeline import ds_convert


@pytest.fixture()
def doc(tmp_path, monkeypatch):
    """A storage dir with one doc whose 'conversion' writes a synthetic PDF."""
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    doc_dir = tmp_path / "d1"
    doc_dir.mkdir()

    def fake_ensure_docx(doc_id, lang):
        p = doc_dir / f"output.{lang}.docx"
        p.write_bytes(b"docx")
        return str(p)

    def fake_ensure_pdf(storage, doc_id, lang):
        out = ds_convert.pdf_path(storage, doc_id, lang)
        pdf = fitz.open()
        page = pdf.new_page(width=595, height=842)
        page.insert_text((72, 72), "Hello translated world", fontsize=12)
        pdf.save(out)
        pdf.close()
        return out

    monkeypatch.setattr(main, "_ensure_docx", fake_ensure_docx)
    monkeypatch.setattr(main.ds_convert, "ensure_pdf", fake_ensure_pdf)
    # /tsegments's segment source, monkeypatched: 2 entries (one on-page, one not).
    monkeypatch.setattr(main, "_tpage_segments", lambda doc_id, lang: [
        {"id": "seg1", "text": "Hello translated world"},
        {"id": "seg2", "text": "not on the page"},
    ])
    return TestClient(main.app)


def test_tpages_manifest(doc):
    r = doc.get("/api/documents/d1/tpages?lang=en")
    assert r.status_code == 200
    m = r.json()
    assert m["pages"][0]["url"].startswith("/api/documents/d1/tpages/0.png")
    assert m["page_w"] > 0


def test_tpage_png_served(doc):
    doc.get("/api/documents/d1/tpages?lang=en")
    r = doc.get("/api/documents/d1/tpages/0.png?lang=en")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"


def test_tsegments_join_ids(doc):
    r = doc.get("/api/documents/d1/tsegments?lang=en")
    assert r.status_code == 200
    segs = {s["id"]: s["bbox"] for s in r.json()["segments"]}
    assert segs["seg1"] is not None and 0 <= segs["seg1"]["x"] <= 1
    assert segs["seg2"] is None


def test_tsegments_excludes_band_from_alignment(doc, monkeypatch):
    """A running-header/footer band segment (seg id 'band:...') that textually
    SHADOWS a body segment must not consume the PDF word-stream's only occurrence
    before the body segment gets a chance to align — bands repeat verbatim on
    every page of the converted PDF, so align_words' single forward cursor would
    otherwise anchor the band first and strand the cursor, collapsing the match
    rate for every body segment that follows (see test_tpages_integration.py's
    comment on the real-document 13/120 regression this caused)."""
    monkeypatch.setattr(main, "_tpage_segments", lambda doc_id, lang: [
        {"id": "band:header:left", "text": "Hello translated world"},
        {"id": "seg1", "text": "Hello translated world"},
        {"id": "seg2", "text": "not on the page"},
    ])
    r = doc.get("/api/documents/d1/tsegments?lang=en")
    assert r.status_code == 200
    segments = r.json()["segments"]
    assert [s["id"] for s in segments] == ["band:header:left", "seg1", "seg2"]
    by_id = {s["id"]: s["bbox"] for s in segments}
    assert by_id["band:header:left"] is None
    assert by_id["seg1"] is not None and 0 <= by_id["seg1"]["x"] <= 1
    assert by_id["seg2"] is None


def test_tfind(doc):
    doc.get("/api/documents/d1/tpages?lang=en")
    r = doc.post("/api/documents/d1/tfind?lang=en", json={"terms": ["translated"]})
    assert r.status_code == 200
    assert len(r.json()["hits"]["translated"]) == 1


def test_ds_failure_is_503(doc, monkeypatch):
    def boom(storage, doc_id, lang):
        raise ds_convert.DsConvertError(-3)
    monkeypatch.setattr(main.ds_convert, "ensure_pdf", boom)
    r = doc.get("/api/documents/d1/tpages?lang=en")
    assert r.status_code == 503


def test_tpages_unknown_doc_is_404(doc):
    r = doc.get("/api/documents/nope/tpages?lang=en")
    assert r.status_code == 404


# --- lang validation / path-traversal guard --------------------------------

def test_tpage_png_traversal_rejected(doc, tmp_path):
    """lang must never reach the on-disk path unvalidated: 'en/../../x' would
    otherwise escape tpages.{lang}/ into a sibling directory under STORAGE."""
    secret_dir = tmp_path / "x"
    secret_dir.mkdir()
    (secret_dir / "tpage0.png").write_bytes(b"SECRET-OTHER-DOC")
    r = doc.get("/api/documents/d1/tpages/0.png", params={"lang": "en/../../x"})
    assert r.status_code == 400
    assert r.content != b"SECRET-OTHER-DOC"


def test_tpages_bad_lang_is_400(doc):
    r = doc.get("/api/documents/d1/tpages", params={"lang": "en/../../x"})
    assert r.status_code == 400


def test_tsegments_bad_lang_is_400(doc):
    r = doc.get("/api/documents/d1/tsegments", params={"lang": "en/../../x"})
    assert r.status_code == 400


def test_tfind_bad_lang_is_400(doc):
    r = doc.post(
        "/api/documents/d1/tfind", params={"lang": "en/../../x"}, json={"terms": ["x"]}
    )
    assert r.status_code == 400


# --- GET /tversion — cheap docx-version signal (pure stat, no conversion) -----------------
# The translated pane's freshness (tpages/tsegments) is keyed on onlyoffice.document_key(),
# which hashes path+mtime+size of output.{lang}.docx. tversion exposes that same value
# WITHOUT calling _ensure_docx or ds_convert — so the frontend can cheaply poll "did the
# docx change yet?" instead of polling segment text (which never changes for formatting-only
# edits, and which lags a late-landing OnlyOffice save past a short poll's timeout).

def test_tversion_returns_string(doc, tmp_path):
    (tmp_path / "d1" / "output.en.docx").write_bytes(b"hello")
    r = doc.get("/api/documents/d1/tversion?lang=en")
    assert r.status_code == 200
    version = r.json()["version"]
    assert isinstance(version, str) and version


def test_tversion_changes_after_rewrite(doc, tmp_path):
    docx = tmp_path / "d1" / "output.en.docx"
    docx.write_bytes(b"hello")
    v1 = doc.get("/api/documents/d1/tversion?lang=en").json()["version"]
    # Different length (not just mtime) guarantees a different document_key seed even if the
    # filesystem's mtime resolution is coarser than the time this test takes to run.
    docx.write_bytes(b"hello world, this is longer now")
    v2 = doc.get("/api/documents/d1/tversion?lang=en").json()["version"]
    assert v1 != v2


def test_tversion_null_when_docx_missing(doc):
    r = doc.get("/api/documents/d1/tversion?lang=en")
    assert r.status_code == 200
    assert r.json()["version"] is None


def test_tversion_bad_lang_is_400(doc):
    r = doc.get("/api/documents/d1/tversion", params={"lang": "en/../../x"})
    assert r.status_code == 400


def test_tversion_unknown_doc_is_404(doc):
    r = doc.get("/api/documents/nope/tversion?lang=en")
    assert r.status_code == 404


def test_tversion_does_not_convert(doc, monkeypatch):
    """Must stay a pure stat: never touch _ensure_docx or ds_convert.ensure_pdf."""
    def boom(*a, **k):
        raise AssertionError("tversion must not trigger docx/pdf conversion")
    monkeypatch.setattr(main, "_ensure_docx", boom)
    monkeypatch.setattr(main.ds_convert, "ensure_pdf", boom)
    r = doc.get("/api/documents/d1/tversion?lang=en")
    assert r.status_code == 200
    assert r.json()["version"] is None  # no docx written in this test → null, but no crash either
