import os

import fitz
from PIL import Image

from models import FigureBlock, FigureOverlay, SemanticDocument
from pipeline import extractor, figure_overlay
from pipeline.reconciler import reconcile


def _chart_pdf(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.draw_rect(fitz.Rect(40, 40, 260, 160), color=(0, 0, 0), width=2)  # vector art
    page.insert_text((60, 90), "North America", fontsize=12)               # in-figure text
    p = str(tmp_path / "chart.pdf")
    doc.save(p); doc.close()
    return p


def test_reconcile_builds_overlay_figure(tmp_path):
    pdf = _chart_pdf(tmp_path)
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(pdf, ad)
    # one analyzer block: a figure covering the artwork+text region
    page_blocks = [[{"type": "figure", "figure_bbox": [30, 30, 270, 180],
                     "source_span_ids": []}]]
    doc, _report = reconcile(raw, page_blocks, pdf, ad, source_lang="en")

    figs = [b for b in doc.blocks if b.type == "figure"]
    assert len(figs) == 1
    fig = figs[0]
    assert fig.base_image_ref and (tmp_path / "assets" / fig.base_image_ref).exists()
    assert any(o.source_text.strip() == "North America" for o in fig.overlays)


def test_reconcile_opaque_figure_has_no_overlays(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=300, height=300)
    page.draw_rect(fitz.Rect(40, 40, 260, 160), fill=(0.2, 0.2, 0.2))  # pure graphic, no text
    p = str(tmp_path / "logo.pdf")
    doc.save(p); doc.close()
    ad = str(tmp_path / "assets")
    raw = extractor.extract_document(p, ad)
    page_blocks = [[{"type": "figure", "figure_bbox": [30, 30, 270, 180],
                     "source_span_ids": []}]]
    out, _ = reconcile(raw, page_blocks, p, ad, source_lang="en")
    fig = [b for b in out.blocks if b.type == "figure"][0]
    assert fig.overlays == []
    assert fig.base_image_ref == ""


def test_composite_document_sets_per_lang_image_ref(tmp_path):
    ad = str(tmp_path)
    Image.new("RGB", (160, 80), (255, 255, 255)).save(os.path.join(ad, "fig.base.png"))
    fb = FigureBlock(
        image_ref="fig.png", base_image_ref="fig.base.png",
        overlays=[FigureOverlay(id="o1", source_text="North America",
                                target_text="북미", bbox=(8.0, 8.0, 100.0, 16.0),
                                font_size=14.0)],
    )
    doc = SemanticDocument(source_lang="en", target_lang="ko", blocks=[fb])

    figure_overlay.composite_document(doc, ad, "ko")

    assert doc.blocks[0].image_ref == "fig.ko.png"
    assert os.path.exists(os.path.join(ad, "fig.ko.png"))


def test_composite_document_skips_opaque(tmp_path):
    fb = FigureBlock(image_ref="fig.png")  # no overlays / no base
    doc = SemanticDocument(blocks=[fb])
    figure_overlay.composite_document(doc, str(tmp_path), "ko")
    assert doc.blocks[0].image_ref == "fig.png"  # untouched


def test_get_translated_model_composites_overlay(tmp_path, monkeypatch):
    import json
    from pipeline import process
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # force MockTranslator
    storage = str(tmp_path)
    doc_id = "doc1"
    base = process.doc_dir(storage, doc_id)
    ad = process.asset_dir(storage, doc_id)
    os.makedirs(base, exist_ok=True)
    os.makedirs(ad, exist_ok=True)
    Image.new("RGB", (160, 80), (255, 255, 255)).save(os.path.join(ad, "fig.base.png"))

    source = SemanticDocument(
        source_lang="en", blocks=[FigureBlock(
            image_ref="fig.png", base_image_ref="fig.base.png",
            overlays=[FigureOverlay(id="o1", source_text="North America",
                                    bbox=(8.0, 8.0, 100.0, 16.0), font_size=14.0)])])
    with open(os.path.join(base, "source.json"), "w", encoding="utf-8") as fh:
        json.dump(source.model_dump(), fh)

    translated = process.get_translated_model(storage, doc_id, "ko")
    fig = [b for b in translated.blocks if b.type == "figure"][0]
    assert fig.overlays[0].target_text  # translated (MockTranslator), not empty
    assert fig.image_ref == "fig.ko.png"
    assert os.path.exists(os.path.join(ad, "fig.ko.png"))
