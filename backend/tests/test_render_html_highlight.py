from models import HeadingBlock, ParagraphBlock, SemanticDocument
from pipeline.text_replace import compile_terms
from renderers.html_renderer import render_html


def _doc():
    return SemanticDocument(source_lang="ko", target_lang="en", blocks=[
        HeadingBlock(level=1, text="Rated Load section", source_span_ids=[]),
        ParagraphBlock(text="the load and Download", source_span_ids=[]),
    ])


def test_no_options_is_unchanged_regression():
    html = render_html(_doc())
    assert "<mark" not in html
    assert 'id="blk0"' not in html
    assert "Rated Load section" in html


def test_highlight_and_anchors():
    pat = compile_terms(["Rated Load", "load"], "en")
    # Blocks without block_id get no anchor even with anchor_blocks=True
    html = render_html(_doc(), highlight_pattern=pat, anchor_blocks=True)
    assert '<h1>' in html  # no block_id → no anchor
    assert '<p>' in html
    assert "<mark" in html
    assert "<mark class=\"gloss\">Rated Load</mark>" in html
    assert "<mark class=\"gloss\">load</mark>" in html
    assert "Down<mark" not in html  # word boundary respected


def test_compile_terms_empty_is_none():
    assert compile_terms([], "en") is None
    assert compile_terms(["", None], "en") is None
