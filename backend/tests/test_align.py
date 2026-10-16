"""Sentence alignment for bilingual reference uploads. Equal-length → positional; otherwise
a cosine Needleman-Wunsch keeps the monotonic 1:1 matches and drops unmatched sentences.
Driven with MockEmbedder via shared tokens (it can't model cross-lingual semantics)."""
from pipeline import embeddings as E
from pipeline.align import align_segments


def test_equal_length_aligns_positionally():
    e = E.MockEmbedder(64)
    pairs = align_segments(["a one", "b two"], ["x one", "y two"], e)
    assert pairs == [("a one", "x one"), ("b two", "y two")]


def test_unequal_length_drops_unmatched():
    e = E.MockEmbedder(128)
    src = ["rated load", "siren volume", "extra unmatched line"]
    tgt = ["rated load value", "siren volume value"]
    d = dict(align_segments(src, tgt, e))
    assert d["rated load"] == "rated load value"
    assert d["siren volume"] == "siren volume value"
    assert "extra unmatched line" not in d        # no partner above the floor → dropped


def test_empty_inputs():
    e = E.MockEmbedder(32)
    assert align_segments([], ["x"], e) == []
    assert align_segments(["x"], [], e) == []
