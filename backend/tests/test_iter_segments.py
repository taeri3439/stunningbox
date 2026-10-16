from models import (
    HeadingBlock, ParagraphBlock, RunningBand, SemanticDocument, TableBlock, TableCell,
    iter_segments, iter_text_fields,
)


def _doc():
    return SemanticDocument(
        source_lang="ko",
        blocks=[
            HeadingBlock(level=1, text="제목", source_span_ids=["p0b1l0s0"]),
            ParagraphBlock(text="문단 하나", source_span_ids=["p0b2l0s0", "p0b2l0s1"]),
            TableBlock(rows=[[TableCell(text="셀", source_span_ids=["p1b0l0s0"])]]),
        ],
    )


def test_iter_segments_carries_span_ids_and_seg_id():
    segs = list(iter_segments(_doc()))
    texts = [g() for g, _, _, _, _ in segs]
    assert texts == ["제목", "문단 하나", "셀"]
    assert [ids for _, _, ids, _, _ in segs] == [
        ["p0b1l0s0"], ["p0b2l0s0", "p0b2l0s1"], ["p1b0l0s0"],
    ]
    # block_id is None (no stable ids assigned in this test doc); seg_id uses positional fallback
    assert [b for _, _, _, b, _ in segs] == [None, None, None]
    # seg_id positional fallback: block at position 0 → "0:text", pos 1 → "1:text", pos 2 → "2:cell:0"
    assert [s for _, _, _, _, s in segs] == ["0:text", "1:text", "2:cell:0"]


def test_iter_text_fields_still_two_tuple_and_aligned():
    doc = _doc()
    fields = list(iter_text_fields(doc))
    assert all(len(f) == 2 for f in fields)                      # (getter, setter)
    assert [g() for g, _ in fields] == [g() for g, _, _, _, _ in iter_segments(doc)]
    # setter still mutates the model in place
    fields[0][1]("NEW")
    assert doc.blocks[0].text == "NEW"


def test_running_band_fields_have_empty_span_ids_and_no_block():
    # Header/footer band fields are chrome with no source spans -> span_ids == [], block None.
    doc = SemanticDocument(
        source_lang="ko",
        running_header=RunningBand(left="좌", center="", right="우"),
        blocks=[],
    )
    segs = list(iter_segments(doc))
    assert [g() for g, _, _, _, _ in segs] == ["좌", "우"]  # empty center field is skipped
    assert all(ids == [] and blk is None for _, _, ids, blk, _ in segs)
    # band seg_ids use "band:{which}:{attr}" format
    assert [s for _, _, _, _, s in segs] == ["band:header:left", "band:header:right"]
