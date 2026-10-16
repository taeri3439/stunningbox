import re
from models import (
    SemanticDocument, HeadingBlock, ParagraphBlock, ListItemBlock, CaptionBlock,
    TableBlock, TableCell, FigureBlock, FigureOverlay, RunningBand, iter_segments,
    build_segment_index_map,
)
from renderers.html_renderer import render_html


def _doc():
    return SemanticDocument(
        running_header=RunningBand(left="HdrL", center="", right=""),
        blocks=[
            HeadingBlock(text="Title"),
            ParagraphBlock(text="One sentence."),
            ParagraphBlock(lines=["Line A", "Line B"]),          # 2 segments
            TableBlock(rows=[[TableCell(text="C1"), TableCell(text="C2")]]),  # 2 segments
            FigureBlock(overlays=[FigureOverlay(id="x1", source_text="Cap", target_text="Cap")]),
        ],
    )


def _doc_with_ids():
    return SemanticDocument(
        blocks=[
            HeadingBlock(text="H", block_id="hdr1"),
            ParagraphBlock(text="P", block_id="para2"),
        ],
    )


def test_index_map_covers_every_iter_segment():
    doc = _doc()
    seg_ids_from_iter = [s[4] for s in iter_segments(doc)]
    m = build_segment_index_map(doc)
    # values are seg-id strings; set must match exactly (1:1 lockstep)
    assert set(m.values()) == set(seg_ids_from_iter)
    assert len(m) == len(seg_ids_from_iter)


def test_rendered_data_seg_values_match_iter_segments_body():
    doc = _doc()
    seg_ids = [s[4] for s in iter_segments(doc)]
    html = render_html(doc, wrap_segments=True)
    found = re.findall(r'data-seg="([^"]+)"', html)
    # Body segments are wrapped; the header band is chrome and not wrapped.
    band_seg_ids = [s[4] for s in iter_segments(doc) if s[3] is None and "band:" in s[4]]
    body_seg_ids = [sid for sid in seg_ids if sid not in set(band_seg_ids)]
    assert set(found) == set(body_seg_ids)


def test_wrap_segments_false_emits_no_seg_spans():
    html = render_html(_doc(), wrap_segments=False)
    assert "data-seg" not in html and 'class="seg"' not in html


def test_block_anchors_use_block_id():
    """Block anchors must be id="blk{block_id}" not position-based."""
    doc = _doc_with_ids()
    html = render_html(doc, anchor_blocks=True)
    assert 'id="blkhdr1"' in html
    assert 'id="blkpara2"' in html
    # No position-based anchors
    assert 'id="blk0"' not in html
    assert 'id="blk1"' not in html


def test_source_and_translated_stamp_matching_seg_ids():
    """data-seg is the stable seg-id derived from block_id, NOT position: the SAME block
    (block_id 'p1') stamps the SAME seg-id in both previews even though it sits at a
    DIFFERENT position in each. A position-based seg-id would differ and fail this."""
    source_doc = SemanticDocument(
        blocks=[ParagraphBlock(text="lead", block_id="lead"),
                ParagraphBlock(text="Source text", block_id="p1")],   # p1 at position 1
    )
    translated_doc = SemanticDocument(
        blocks=[ParagraphBlock(text="Translated text", block_id="p1")],  # p1 at position 0
    )
    src_html = render_html(source_doc, wrap_segments=True)
    tgt_html = render_html(translated_doc, wrap_segments=True)
    assert 'data-seg="p1:text"' in src_html      # seg-id embeds block_id, not position
    assert 'data-seg="p1:text"' in tgt_html
