"""White text-cover detection (sparse page-0 with a display-size title)."""
from types import SimpleNamespace

import fitz

import pipeline.reconciler as reconciler_mod
from pipeline import extractor, figure_overlay
from pipeline.reconciler import _looks_like_text_cover, reconcile


def _span(size, text="x"):
    return SimpleNamespace(text=text, size=size, bbox=(0, 0, 10, 10))


def test_sparse_page_with_display_title_is_cover():
    spans = [_span(42.0), _span(16.0), _span(12.0), _span(11.0)]
    assert _looks_like_text_cover(spans, n_pages=21) is True


def test_many_spans_is_not_cover():
    spans = [_span(12.0) for _ in range(30)] + [_span(42.0)]
    assert _looks_like_text_cover(spans, n_pages=21) is False


def test_no_display_font_is_not_cover():
    spans = [_span(14.0), _span(12.0)]
    assert _looks_like_text_cover(spans, n_pages=21) is False


def test_single_page_doc_is_not_cover():
    spans = [_span(42.0)]
    assert _looks_like_text_cover(spans, n_pages=1) is False


def test_empty_page_is_not_cover():
    assert _looks_like_text_cover([], n_pages=5) is False


# --------------------------------------------------------------------------- #
# Reconcile-level: page-0 chrome kept as translated body text on a text cover.
# --------------------------------------------------------------------------- #

_TITLE = "COVER TITLE"  # stand-in display title (PyMuPDF's default font can't shape the
# real doc's Korean glyphs in a synthetic fixture) — only its 42pt size matters.


def _text_cover_pdf(tmp_path):
    doc = fitz.open()
    p0 = doc.new_page(width=600, height=800)
    p0.insert_text((480, 30), "KFS 0011", fontsize=10)  # top-right chrome
    p0.insert_text((60, 200), _TITLE, fontsize=42)  # display-size title
    p1 = doc.new_page(width=600, height=800)
    p1.insert_text((60, 60), "Body paragraph text on page two.", fontsize=11)
    path = str(tmp_path / "textcover.pdf")
    doc.save(path)
    doc.close()
    return path


def test_reconcile_text_cover_keeps_chrome_as_body(tmp_path, monkeypatch):
    pdf = _text_cover_pdf(tmp_path)
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(pdf, ad)
    monkeypatch.setattr(reconciler_mod, "_page_nonwhite_frac", lambda *a, **k: 0.05)
    monkeypatch.setattr(figure_overlay, "render_redacted_clip", lambda *a, **k: "cover_bg.png")

    chrome_ids = [s.id for s in raw.pages[0].spans if s.text.strip() == "KFS 0011"]
    heading_ids = [s.id for s in raw.pages[0].spans if _TITLE in s.text]
    body_ids = [s.id for s in raw.pages[1].spans]
    assert chrome_ids and heading_ids and body_ids  # fixture sanity

    page_blocks = [
        [
            {"type": "chrome", "text": "KFS 0011", "source_span_ids": chrome_ids},
            {"type": "heading", "text": _TITLE, "source_span_ids": heading_ids},
        ],
        [
            {"type": "paragraph", "text": "Body paragraph text on page two.",
             "source_span_ids": body_ids},
        ],
    ]
    doc, report = reconcile(raw, page_blocks, pdf, ad, source_lang="ko")

    assert doc.cover_background == "cover_bg.png"
    chrome_paras = [
        b for b in doc.blocks
        if getattr(b, "type", None) == "paragraph" and b.page == 0 and b.text == "KFS 0011"
    ]
    assert chrome_paras, "chrome kept as a page-0 body paragraph"
    assert chrome_paras[0].align == "right"
    assert not report.missing_span_ids, "chrome span ids stay accounted for (referenced)"


def _five_paragraph_text_cover_pdf(tmp_path):
    """Mirrors the real ladder cover shape: a display-size title plus FIVE short
    subtitle/doc-number/committee-line paragraphs — more than COVER_MAX_BODY_BLOCKS (3)
    but within COVER_TEXT_MAX_BODY_BLOCKS (8), and still sparse enough (well under
    COVER_MAX_SPANS) to read as a text cover."""
    doc = fitz.open()
    p0 = doc.new_page(width=600, height=800)
    p0.insert_text((60, 100), _TITLE, fontsize=42)  # display-size title
    for i, t in enumerate(["Subtitle line", "KFS 0011", "Aerial Ladder Vehicle",
                           "Standards Committee", "http://example.org"]):
        p0.insert_text((60, 250 + i * 30), t, fontsize=11)
    p1 = doc.new_page(width=600, height=800)
    p1.insert_text((60, 60), "Body paragraph text on page two.", fontsize=11)
    path = str(tmp_path / "fiveparacover.pdf")
    doc.save(path)
    doc.close()
    return path


def test_reconcile_five_paragraph_cover_fires_via_text_cover_arm(tmp_path, monkeypatch):
    """A real ladder-style cover — a display title plus 5 short paragraph blocks — has
    more body blocks than a bare designed cover allows (3), but is still recognized as a
    cover via the text-cover arm's own (looser) COVER_TEXT_MAX_BODY_BLOCKS cap."""
    pdf = _five_paragraph_text_cover_pdf(tmp_path)
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(pdf, ad)
    monkeypatch.setattr(reconciler_mod, "_page_nonwhite_frac", lambda *a, **k: 0.05)
    monkeypatch.setattr(figure_overlay, "render_redacted_clip", lambda *a, **k: "cover_bg.png")

    heading_ids = [s.id for s in raw.pages[0].spans if _TITLE in s.text]
    para_texts = ["Subtitle line", "KFS 0011", "Aerial Ladder Vehicle",
                  "Standards Committee", "http://example.org"]
    para_blocks = [
        {"type": "paragraph", "text": t,
         "source_span_ids": [s.id for s in raw.pages[0].spans if s.text.strip() == t]}
        for t in para_texts
    ]
    body_ids = [s.id for s in raw.pages[1].spans]
    assert heading_ids and all(b["source_span_ids"] for b in para_blocks) and body_ids

    page_blocks = [
        [{"type": "heading", "text": _TITLE, "source_span_ids": heading_ids}] + para_blocks,
        [{"type": "paragraph", "text": "Body paragraph text on page two.",
          "source_span_ids": body_ids}],
    ]
    doc, _report = reconcile(raw, page_blocks, pdf, ad, source_lang="ko")

    assert doc.cover_background == "cover_bg.png", (
        "5 body blocks (> COVER_MAX_BODY_BLOCKS but <= COVER_TEXT_MAX_BODY_BLOCKS) "
        "must still fire the text-cover arm"
    )


def _content_page0_pdf(tmp_path):
    doc = fitz.open()
    p0 = doc.new_page(width=300, height=400)
    p0.insert_text((230, 25), "KFS 0011", fontsize=10)
    for i, t in enumerate(["Paragraph one.", "Paragraph two.", "Paragraph three.",
                           "Paragraph four."]):
        p0.insert_text((40, 100 + i * 30), t, fontsize=11)
    p1 = doc.new_page(width=300, height=400)
    p1.insert_text((40, 60), "Body paragraph text on page two.", fontsize=11)
    path = str(tmp_path / "content.pdf")
    doc.save(path)
    doc.close()
    return path


def test_reconcile_chrome_not_kept_as_body_without_cover(tmp_path, monkeypatch):
    # Content-heavy page 0 (many body blocks) is never a cover — the pre-existing
    # COVER_MAX_BODY_BLOCKS guard stands, so page-0 chrome behaves as before (recorded
    # for band voting only, not also emitted as a body paragraph).
    pdf = _content_page0_pdf(tmp_path)
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(pdf, ad)
    monkeypatch.setattr(reconciler_mod, "_page_nonwhite_frac", lambda *a, **k: 0.05)

    chrome_ids = [s.id for s in raw.pages[0].spans if s.text.strip() == "KFS 0011"]
    para_texts = ["Paragraph one.", "Paragraph two.", "Paragraph three.", "Paragraph four."]
    para_blocks = [
        {"type": "paragraph", "text": t,
         "source_span_ids": [s.id for s in raw.pages[0].spans if s.text.strip() == t]}
        for t in para_texts
    ]
    body_ids = [s.id for s in raw.pages[1].spans]
    assert chrome_ids and all(b["source_span_ids"] for b in para_blocks) and body_ids

    page_blocks = [
        [{"type": "chrome", "text": "KFS 0011", "source_span_ids": chrome_ids}] + para_blocks,
        [{"type": "paragraph", "text": "Body paragraph text on page two.",
          "source_span_ids": body_ids}],
    ]
    doc, _report = reconcile(raw, page_blocks, pdf, ad, source_lang="ko")

    assert doc.cover_background is None, "content-heavy page 0 is not a cover"
    chrome_paras = [
        b for b in doc.blocks
        if getattr(b, "type", None) == "paragraph" and b.page == 0 and b.text == "KFS 0011"
    ]
    assert not chrome_paras, "no cover -> chrome is not emitted as body text (unchanged)"
