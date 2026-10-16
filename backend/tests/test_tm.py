"""Translation-Memory: bilingual upload stores aligned pairs (role='tm_src'); tm_prefill
maps a near-identical source sentence to its approved translation; translate_document reuses
those verbatim and skips the LLM for them."""
import pytest

from models import ParagraphBlock, SemanticDocument, iter_text_fields
from pipeline import embeddings as E
from pipeline import style_memory as SM
from pipeline import translator as T


@pytest.fixture(autouse=True)
def _dev(monkeypatch):
    monkeypatch.setenv("DEV_NO_AUTH", "1")
    SM._reset()
    yield
    SM._reset()


def test_bilingual_ingest_stores_tm_pairs(tmp_path):
    e = E.MockEmbedder(128)
    (tmp_path / "src.txt").write_text(
        "rated load definition here.\nsiren volume definition here.\n", encoding="utf-8")
    (tmp_path / "tgt.txt").write_text(
        "rated load definition english.\nsiren volume definition english.\n", encoding="utf-8")
    SM.ingest_reference("u1", "fire", "ref", str(tmp_path / "tgt.txt"), "en",
                        source_path=str(tmp_path / "src.txt"), lang_src="ko", embedder=e)
    assert SM.domain_has_pairs("u1", "fire")
    pre = SM.tm_prefill("u1", "fire", ["rated load definition here."], "ko", embedder=e)
    assert pre["rated load definition here."] == "rated load definition english."


def test_tm_prefill_empty_without_pairs():
    e = E.MockEmbedder(64)
    SM.index_segments("u1", "fire", "r", ["english only sentence here."], "en", embedder=e)
    assert SM.tm_prefill("u1", "fire", ["english only sentence here."], "ko", embedder=e) == {}


def test_translate_document_tm_prefill_skips_llm():
    captured = {}

    class _Spy:
        def translate(self, texts, sl, tl, gloss, on_done=None, guidance="", style_context=""):
            captured["sent"] = list(texts)
            return [f"LLM:{t}" for t in texts]

    doc = SemanticDocument(source_lang="ko", blocks=[
        ParagraphBlock(text="정격하중"), ParagraphBlock(text="기타 문장입니다")])
    T.translate_document(doc, "en", translator=_Spy(), tm_map={"정격하중": "Rated Load"})
    fields = [g() for g, _ in iter_text_fields(doc)]
    assert captured["sent"] == ["기타 문장입니다"]      # TM-hit not sent to the LLM
    assert fields[0] == "Rated Load"                  # verbatim reuse
    assert fields[1] == "LLM:기타 문장입니다"


def test_translate_document_all_tm_reused():
    class _Spy:
        def translate(self, texts, *a, **k):
            raise AssertionError("LLM should not be called when every segment is TM-reused")

    doc = SemanticDocument(source_lang="ko", blocks=[ParagraphBlock(text="정격하중")])
    T.translate_document(doc, "en", translator=_Spy(), tm_map={"정격하중": "Rated Load"})
    assert [g() for g, _ in iter_text_fields(doc)][0] == "Rated Load"
