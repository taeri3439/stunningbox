from models import ParagraphBlock, SemanticDocument
from pipeline.translator import (
    MockTranslator, parse_glossary_csv, translate_document, translate_segment,
)


def test_parse_glossary_csv_header_blank_and_dupes():
    text = (
        "원어,번역어\n"          # header → skipped
        "정격하중,Rated Load\n"
        " 캐빈 , Cabin \n"        # trimmed
        "\n"                       # blank → skipped
        "오타\n"                   # 1 column → skipped
        "정격하중,RATED-2\n"       # later dup wins
    )
    g = parse_glossary_csv(text)
    assert g == {"정격하중": "RATED-2", "캐빈": "Cabin"}


def test_parse_glossary_csv_quoted_comma():
    g = parse_glossary_csv('term,"a, b, c"\n')
    assert g == {"term": "a, b, c"}


def test_parse_glossary_csv_strips_bom():
    # Excel "CSV UTF-8" prepends a BOM; the header must still be recognized + skipped
    # (not turned into a junk "﻿원어" entry).
    g = parse_glossary_csv("﻿원어,번역어\n정격하중,Rated Load\n")
    assert g == {"정격하중": "Rated Load"}


def test_translate_document_user_glossary_overrides_builtin():
    # built-in ko-en.json maps 정격하중 -> "Rated Load"; the user glossary must win.
    doc = SemanticDocument(
        source_lang="ko", blocks=[ParagraphBlock(text="정격하중", source_span_ids=[])]
    )
    translate_document(
        doc, "en", extra_glossary={"정격하중": "RATED-LOAD-X"}, translator=MockTranslator()
    )
    assert "RATED-LOAD-X" in doc.blocks[0].text
    assert "Rated Load" not in doc.blocks[0].text


def test_translate_segment_uses_extra_glossary():
    out = translate_segment(
        "캐빈", "ko", "en", translator=MockTranslator(), extra_glossary={"캐빈": "CAB-X"}
    )
    assert "CAB-X" in out
