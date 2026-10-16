"""Translator robustness: a chunk whose model output count mismatches must recover
granularly (split + retry) instead of falling the whole chunk back to the source
language; long dot-leaders are collapsed before sending."""
import json
from types import SimpleNamespace

from pipeline.translator import ClaudeTranslator, _collapse_leaders


def test_collapse_leaders():
    assert _collapse_leaders("Title .................. 12") == "Title ... 12"
    assert _collapse_leaders("middle···········dots 3") == "middle...dots 3"
    assert _collapse_leaders("no leader here") == "no leader here"


class _FakeClient:
    """Mimics a model that DROPS segments on big chunks (returns a too-short array) but
    translates correctly once the chunk is split to <=2 segments."""

    def __init__(self):
        self.sizes = []

    @property
    def messages(self):
        client = self

        class _M:
            def create(self, **kw):
                arr = json.loads(kw["messages"][0]["content"].split("\n", 1)[1])
                client.sizes.append(len(arr))
                translated = ["KO:" + a for a in arr]
                if len(arr) > 2:                      # simulate dropped segments
                    translated = translated[:1]
                return SimpleNamespace(
                    content=[SimpleNamespace(type="tool_use",
                                             input={"translations": translated})])
        return _M()


def test_translate_chunk_recovers_on_mismatch():
    t = ClaudeTranslator(client=_FakeClient())
    chunk = ["a", "b", "c", "d"]
    out = t._translate_chunk(chunk, "sys", {"name": "emit_translations"})
    assert len(out) == 4
    # every segment is translated (none echoed back in the source language)
    assert out == ["KO:a", "KO:b", "KO:c", "KO:d"]


def test_translate_chunk_single_segment_fallback():
    # A size-1 chunk that still mismatches falls back to identity (last resort), not a crash.
    class _Empty:
        @property
        def messages(self):
            class _M:
                def create(self, **kw):
                    return SimpleNamespace(content=[SimpleNamespace(
                        type="tool_use", input={"translations": []})])
            return _M()
    t = ClaudeTranslator(client=_Empty())
    assert t._translate_chunk(["x"], "sys", {"name": "t"}) == ["x"]
