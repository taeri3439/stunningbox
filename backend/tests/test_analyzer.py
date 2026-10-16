"""ClaudeAnalyzer robustness around truncated / malformed tool responses.

Root cause (measured): dense pages (large tables) truncate the emit_structure output at
max_tokens, so the forced tool_use comes back with a non-dict / partial `input`. The old
code did `part.input.get("blocks", [])` unconditionally (crashes on non-dict) and used a
fixed max_tokens=8000 with no truncation signal — silently losing the whole table.
"""
import base64
import os

from models import RawPage
from pipeline.analyzer import ClaudeAnalyzer
from pipeline import analyzer as analyzer_mod

# Smallest valid PNG (1x1) — analyze_page opens the page render before calling the API.
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _page(tmp_path):
    (tmp_path / "page0.png").write_bytes(_PNG_1x1)
    return RawPage(width=100.0, height=100.0, render_path="page0.png", spans=[])


class _Part:
    def __init__(self, tool_input):
        self.type = "tool_use"
        self.name = "emit_structure"
        self.input = tool_input


class _Usage:
    output_tokens = 8000


class _Msg:
    def __init__(self, parts, stop_reason):
        self.content = parts
        self.stop_reason = stop_reason
        self.usage = _Usage()


class _FakeClient:
    """Records the create() kwargs and returns a canned message."""

    def __init__(self, msg):
        self._msg = msg
        self.create_kwargs = None

        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.create_kwargs = kwargs
                return outer._msg

        self.messages = _Messages()


def test_non_dict_tool_input_does_not_crash(tmp_path):
    # Truncated tool_use: the SDK hands back a partial/non-dict input. Must yield [] (not crash).
    client = _FakeClient(_Msg([_Part("{\"blocks\": [")], "max_tokens"))
    out = ClaudeAnalyzer(client=client).analyze_page(_page(tmp_path), str(tmp_path))
    assert out == []


def test_dict_tool_input_returns_blocks(tmp_path):
    blocks = [{"type": "paragraph", "text": "hi"}]
    client = _FakeClient(_Msg([_Part({"blocks": blocks})], "tool_use"))
    out = ClaudeAnalyzer(client=client).analyze_page(_page(tmp_path), str(tmp_path))
    assert out == blocks


def test_max_tokens_raised_above_old_cap(tmp_path):
    # The old 8000 cap truncated dense tables; the configured cap must be well above it.
    assert analyzer_mod._MAX_TOKENS >= 16000
    client = _FakeClient(_Msg([_Part({"blocks": []})], "tool_use"))
    ClaudeAnalyzer(client=client).analyze_page(_page(tmp_path), str(tmp_path))
    assert client.create_kwargs["max_tokens"] == analyzer_mod._MAX_TOKENS


class _SeqClient:
    """Returns canned messages in order and counts the calls."""

    def __init__(self, msgs):
        self._msgs = list(msgs)
        self.calls = 0
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.calls += 1
                return outer._msgs.pop(0)

        self.messages = _Messages()


def test_retries_once_on_empty_non_truncated(tmp_path):
    # A genuine empty return (completed, not truncated) is a vision miss -> retry once.
    blocks = [{"type": "paragraph", "text": "hi"}]
    client = _SeqClient([_Msg([_Part({"blocks": []})], "end_turn"),
                         _Msg([_Part({"blocks": blocks})], "tool_use")])
    out = ClaudeAnalyzer(client=client).analyze_page(_page(tmp_path), str(tmp_path))
    assert out == blocks
    assert client.calls == 2


def test_no_retry_when_truncated(tmp_path):
    # Empty because truncated (max_tokens) -> a retry can't help, so don't spend it.
    client = _SeqClient([_Msg([_Part("{partial")], "max_tokens"),
                         _Msg([_Part({"blocks": [{"type": "paragraph", "text": "x"}]})], "tool_use")])
    out = ClaudeAnalyzer(client=client).analyze_page(_page(tmp_path), str(tmp_path))
    assert out == []
    assert client.calls == 1


def test_stringified_blocks_are_parsed(tmp_path):
    # "blocks" sometimes comes back as a JSON STRING, not an array. Recover it by PARSING
    # (the string holds the real structure) instead of discarding the whole page.
    client = _FakeClient(_Msg([_Part({"blocks": '[{"type":"paragraph","text":"x"}]'})], "tool_use"))
    out = ClaudeAnalyzer(client=client).analyze_page(_page(tmp_path), str(tmp_path))
    assert out == [{"type": "paragraph", "text": "x"}]


def test_malformed_stringified_blocks_recover_prefix(tmp_path):
    # The stringified blocks are often malformed partway (the model leaves quotes inside the
    # Korean text unescaped, e.g. "방수총"). Recover the leading blocks that DO parse rather
    # than losing the whole page to the text-recovery net (which mangles e.g. a right-aligned
    # revision-date block into split paragraphs).
    blob = ('[{"type":"heading","text":"Title"},'
            '{"type":"paragraph","text":"KFIS 019 2004. 5. 30. Established"},'
            '{"type":"paragraph","text":"bad "broken" quotes here"},'
            '{"type":"paragraph","text":"lost after the break"}]')
    client = _FakeClient(_Msg([_Part({"blocks": blob})], "tool_use"))
    out = ClaudeAnalyzer(client=client).analyze_page(_page(tmp_path), str(tmp_path))
    assert out == [{"type": "heading", "text": "Title"},
                   {"type": "paragraph", "text": "KFIS 019 2004. 5. 30. Established"}]


def test_garbage_string_blocks_yield_empty(tmp_path):
    # A truly unparseable blob (no leading JSON object) recovers nothing -> [] -> retry/recovery.
    client = _FakeClient(_Msg([_Part({"blocks": "not json at all"})], "end_turn"))
    out = ClaudeAnalyzer(client=client).analyze_page(_page(tmp_path), str(tmp_path))
    assert out == []
