"""End-to-end (cached-analysis) check on the real pharma fixture: rebuilt bands are
deduplicated and page numbers detected. Skips when the dev storage copy is absent."""
import os

import pytest

STORAGE = os.path.join(os.path.dirname(__file__), "..", "storage")
DOC = "_hf-verify-pharma"


@pytest.mark.skipif(
    not os.path.exists(os.path.join(STORAGE, DOC, "page_blocks.json")),
    reason="dev-local cached fixture not present",
)
def test_pharma_bands_dedup_and_page_number():
    from pipeline import process

    pdf = os.path.join(STORAGE, DOC, "source.pdf")
    doc, _report = process.rebuild_source_model(pdf, DOC, os.path.abspath(STORAGE), source_lang="ko")
    h = doc.running_header
    assert h is not None
    sides = [s for s in (h.left, h.center, h.right) if s]
    # no side text may contain another side's text (the duplication bug)
    for a in sides:
        for b in sides:
            if a is not b:
                assert a not in b
    f = doc.running_footer
    assert f is not None and f.page_number
    assert f.page_number_format == "Page {n} of {m}"
