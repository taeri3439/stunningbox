"""Style-context threading: the retrieved style block must reach the LLM system prompt for
both full-document translation and single-segment re-translation, and be a no-op when empty."""
from types import SimpleNamespace

from models import ParagraphBlock, SemanticDocument
from pipeline import translator as T


def test_mock_translate_threads_style_context():
    out = T.MockTranslator().translate(["원문"], "ko", "en", {}, style_context="some style block")
    assert "[s]" in out[0]
    assert "[s]" not in T.MockTranslator().translate(["원문"], "ko", "en", {})[0]


def test_openai_translate_injects_style_into_system(monkeypatch):
    captured = {}

    def _fake_tool_call(**kw):
        captured.update(kw)
        return {"translations": ["X"]}, None

    monkeypatch.setattr(T, "openai_tool_call", _fake_tool_call)
    style = "\n\nStyle reference — match ...:\n- Rated load shall not exceed"
    T.OpenAITranslator().translate(["원문"], "ko", "en", {"가": "A"}, style_context=style)
    assert "Style reference" in captured["system"]
    assert "Rated load shall not exceed" in captured["system"]


def test_claude_translate_injects_style_into_system():
    captured = {}

    class _Client:
        @property
        def messages(self):
            class _M:
                def create(self, **kw):
                    captured.update(kw)
                    return SimpleNamespace(content=[SimpleNamespace(
                        type="tool_use", input={"translations": ["X"]})])
            return _M()

    style = "\n\nStyle reference — match ...:\n- Rated load shall not exceed"
    T.ClaudeTranslator(client=_Client()).translate(
        ["원문"], "ko", "en", {"가": "A"}, style_context=style)
    assert "Style reference" in captured["system"]
    assert "Rated load shall not exceed" in captured["system"]


class _Spy:
    def __init__(self):
        self.kw = None

    def translate(self, texts, sl, tl, glossary, on_done=None, guidance="", style_context=""):
        self.kw = {"guidance": guidance, "style_context": style_context}
        return [f"T:{t}" for t in texts]


def test_translate_document_passes_style_context():
    spy = _Spy()
    doc = SemanticDocument(source_lang="ko", blocks=[ParagraphBlock(text="본문 문장입니다")])
    T.translate_document(doc, "en", translator=spy, style_context="SC-BLOCK")
    assert spy.kw["style_context"] == "SC-BLOCK"


def test_translate_segment_passes_style_context():
    spy = _Spy()
    T.translate_segment("본문 문장입니다", "ko", "en", translator=spy, style_context="SC-SEG")
    assert spy.kw["style_context"] == "SC-SEG"
