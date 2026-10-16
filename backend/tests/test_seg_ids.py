import models


def _doc():
    d = models.SemanticDocument(source_lang="ko", blocks=[
        models.HeadingBlock(text="h", block_id="H"),
        models.ParagraphBlock(text="", lines=["a", "b"], block_id="P"),
    ])
    return d


def test_iter_segments_yields_seg_ids():
    segs = list(models.iter_segments(_doc()))
    assert [s[4] for s in segs] == ["H:text", "P:line:0", "P:line:1"]   # seg_id is 5th
    assert [s[3] for s in segs] == ["H", "P", "P"]                       # block_id is 4th


def test_index_map_and_iter_segments_lockstep():
    d = _doc()
    seg_ids_iter = [s[4] for s in models.iter_segments(d)]
    m = models.build_segment_index_map(d)
    assert set(m.values()) == set(seg_ids_iter)                          # same set
    assert len(m) == len(seg_ids_iter)                                   # 1:1
