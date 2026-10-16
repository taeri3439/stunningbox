"""Phase 1D — figure clip robustness: an oversized/out-of-bounds vision bbox is clamped
to the page (so the figure still renders) rather than failing and silently vanishing; a
degenerate bbox raises so the reconciler logs it instead of emitting a phantom figure."""
import os

import fitz
import pytest

from pipeline import extractor


def _make_pdf(tmp_path) -> str:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4-ish
    page.draw_rect(fitz.Rect(100, 100, 300, 300), fill=(0, 0, 0))  # a black square "figure"
    p = str(tmp_path / "t.pdf")
    doc.save(p)
    doc.close()
    return p


def test_render_clip_clamps_out_of_bounds(tmp_path):
    pdf = _make_pdf(tmp_path)
    # bbox spills well past the page edges (x1=900 > 595, y1=1000 > 842)
    ref, tight = extractor.render_clip(pdf, 0, [50, 50, 900, 1000], str(tmp_path), "f.png")
    assert os.path.exists(os.path.join(str(tmp_path), "f.png"))
    assert ref == "f.png"
    # tight box stays within the page bounds (clamped, not off-page)
    assert tight[2] <= 595 + 1 and tight[3] <= 842 + 1


def test_render_clip_rejects_degenerate(tmp_path):
    pdf = _make_pdf(tmp_path)
    with pytest.raises(ValueError):
        extractor.render_clip(pdf, 0, [100, 100, 100, 100], str(tmp_path), "z.png")  # zero area
