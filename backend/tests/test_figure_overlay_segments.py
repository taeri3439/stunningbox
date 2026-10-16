from models import (FigureBlock, FigureOverlay, ParagraphBlock, SemanticDocument,
                    collect_texts, iter_segments, iter_text_fields)


def _doc_with_overlay():
    fb = FigureBlock(
        image_ref="f.png", base_image_ref="f.base.png",
        overlays=[
            FigureOverlay(id="o1", source_text="North America", font_size=10.0),
            FigureOverlay(id="o2", source_text="Europe", font_size=10.0),
        ],
    )
    return SemanticDocument(source_lang="en", blocks=[fb])


def test_overlays_are_translatable_segments():
    doc = _doc_with_overlay()
    assert collect_texts(doc) == ["North America", "Europe"]  # getter → source_text
    for get, set_ in iter_text_fields(doc):
        set_(get() + " [t]")                                   # setter → target_text
    fb = doc.blocks[0]
    assert fb.overlays[0].target_text == "North America [t]"
    assert fb.overlays[0].source_text == "North America"       # source preserved
    assert fb.overlays[1].target_text == "Europe [t]"


def test_opaque_figure_yields_nothing():
    doc = SemanticDocument(blocks=[FigureBlock(image_ref="f.png"),
                                   ParagraphBlock(text="body")])
    assert collect_texts(doc) == ["body"]  # no overlays → only the paragraph


def test_overlay_segment_carries_span_id_and_seg_id():
    doc = _doc_with_overlay()
    segs = list(iter_segments(doc))
    # 2 overlays on block 0 → span_ids = [overlay id], block_id = None (no stable id),
    # seg_id uses positional fallback "0:overlay:0" and "0:overlay:1"
    assert segs[0][2] == ["o1"] and segs[0][3] is None and segs[0][4] == "0:overlay:0"
    assert segs[1][2] == ["o2"] and segs[1][3] is None and segs[1][4] == "0:overlay:1"
