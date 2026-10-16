"""Reference-intake: classify an uploaded reference's bilingual format and extract style
segments + TM pairs accordingly. Language tagging is Hangul-ratio based (robust for ko↔en
technical docs where langdetect trips on KFI/mm/dB). Pairing modes:
  interleaved  — 원문 단락 → 번역 단락 alternating in one file (positional pairing)
  columns      — 2-column CSV / docx table (원문 | 번역)
  sequential   — bilingual but bulk KO then bulk EN (embedding alignment)
  paired_files — separate 원문 + 번역 files (embedding alignment)
  target_only  — translation only (style, no TM)
"""
import pytest

from pipeline import embeddings as E
from pipeline import reference_intake as RI


def test_lang_of():
    assert RI.lang_of("방수총의 형상") == "ko"
    assert RI.lang_of("Firefighting monitor") == "en"
    assert RI.lang_of("2.5 / 90 / 50 %") == "other"    # numbers+symbols only, no letters
    assert RI.lang_of("호칭40 KFI") == "ko"             # Hangul wins even with latin tokens
    # Latin table codes / units / acronyms are NOT a translation → 'other', not 'en'
    assert RI.lang_of("Ac Re Ac Re A B C") == "other"  # 합격/불합격 표 + 열 기호
    assert RI.lang_of("1-S-1 1-S-2") == "other"        # 샘플링검사 코드
    assert RI.lang_of("mm dB Hz KFI TCP") == "other"   # 단위/약어 — no vowel-bearing word
    assert RI.lang_of("Article 1 (Purpose)") == "en"   # real prose still detected


def test_interleaved_pairs_alternating_blocks_and_merges_multiline():
    lines = [
        "방수총의 KFI인증기준",                  # KO title
        "KFI Certification Standards",          # EN title  → pairs with title
        "제1조(목적) 이 기준은 방수총의",         # KO block, line 1
        "형상을 규정한다.",                      # KO block, line 2 (same run → merged)
        "Article 1 (Purpose) This standard",    # EN block, line 1
        "specifies the shape.",                 # EN block, line 2 (same run → merged)
    ]
    style, pairs = RI.pair_interleaved(lines)
    assert len(pairs) == 2
    assert pairs[1][0].startswith("제1조") and pairs[1][1].startswith("Article 1")
    assert "형상을 규정한다." in pairs[1][0]          # multi-line KO merged into one block
    assert any("Article 1" in s for s in style)       # EN blocks become style segments


def test_interleaved_lone_target_block_is_style_not_broken_pair():
    lines = ["Standalone English note here.", "한국어 문장.", "Korean sentence translated."]
    style, pairs = RI.pair_interleaved(lines)
    assert len(pairs) == 1 and pairs[0][0].startswith("한국어")
    assert any("Standalone English" in s for s in style)


def test_columns_csv():
    style, pairs = RI.pair_columns_text("원어,번역어\n방수총,Firefighting monitor\n고정형,Fixed type\n")
    assert dict(pairs) == {"방수총": "Firefighting monitor", "고정형": "Fixed type"}
    assert "Firefighting monitor" in style


def test_sequential_equal_length_positional():
    # bulk KO then bulk EN; equal sentence counts → positional alignment (no embedding)
    lines = ["가나 다라.", "마바 사아.", "Alpha beta.", "Gamma delta."]
    style, pairs = RI.pair_sequential(lines, embedder=E.MockEmbedder(64))
    assert len(pairs) == 2
    assert pairs[0][0] == "가나 다라." and pairs[0][1] == "Alpha beta."


@pytest.fixture
def _txt(tmp_path):
    def write(name, body):
        p = tmp_path / name
        p.write_text(body, encoding="utf-8")
        return str(p)
    return write


def test_classify_modes(_txt):
    interleaved = _txt("i.txt", "한국어 문장.\nEnglish sentence.\n또 다른 문장.\nAnother sentence.\n")
    assert RI.classify(interleaved) == "interleaved"

    target_only = _txt("t.txt", "Only English sentences here.\nAnother English line follows.\n")
    assert RI.classify(target_only) == "target_only"

    source_only = _txt("s.txt", "한국어 원문만 있는 문서.\n번역이 전혀 없습니다.\n")
    assert RI.classify(source_only) == "source_only"

    csv = _txt("c.csv", "원어,번역어\n방수총,Firefighting monitor\n")
    assert RI.classify(csv) == "columns"

    seq = _txt("q.txt", "첫 문장.\n둘째 문장.\n셋째 문장.\nFirst sentence.\nSecond sentence.\nThird sentence.\n")
    assert RI.classify(seq) == "sequential"

    assert RI.classify(target_only, source_path=source_only) == "paired_files"


def test_analyze_interleaved_reports_counts_and_samples(_txt):
    p = _txt("i.txt", "방수총 정의 문장.\nFirefighting monitor definition.\n고정형 정의.\nFixed type definition.\n")
    info = RI.analyze(p)
    assert info["mode"] == "interleaved"
    assert info["pair_count"] == 2 and info["style_count"] >= 2
    assert info["sample_pairs"] and "Firefighting" in info["sample_pairs"][0]["target"]


def test_analyze_source_only_flags_error(_txt):
    p = _txt("s.txt", "한국어 원문만 있는 문서입니다.\n번역이 없어요.\n")
    assert RI.analyze(p)["mode"] == "source_only"


def test_korean_only_with_latin_table_noise_is_source_only(_txt):
    # Real bug: a Korean-only KFI standard whose tables hold Latin codes (Ac/Re sampling,
    # column letters, 1-S-1) must NOT read as bilingual — there is no translation → source_only.
    p = _txt(
        "kfi.txt",
        "소방용 전자음향경보장치의 KFI인정기준 제246호 제정.\n"
        "Ac Re Ac Re Ac Re A B C\n"
        "밑에 적은 첫 번째 샘플링검사 방식을 사용한다.\n"
        "1-S-1 1-S-2\n"
        "시료의 크기가 로트의 크기에 따라 정해진다.\n"
        "A A B C C D E F G H J\n",
    )
    assert RI.classify(p) == "source_only"
    info = RI.analyze(p)
    assert info["mode"] == "source_only"
    assert info["pair_count"] == 0 and not info["has_pairs"]


def test_merge_runs_drops_leading_other_line():
    # a leading number/symbol-only line must not create a spurious run that shifts pairing
    style, pairs = RI.pair_interleaved(["2.5 / 90 %", "한국어 문장.", "English sentence."])
    assert pairs == [("한국어 문장.", "English sentence.")]


def test_target_only_keeps_only_target_language(_txt):
    # mode override 'target_only' on a Korean-only file must yield no style (→ rejected upstream)
    p = _txt("ko.txt", "한국어 원문만 있는 문서입니다.\n번역이 전혀 없습니다.\n")
    style, pairs = RI.extract_for_mode(p, None, "target_only")
    assert style == [] and pairs == []


def test_extract_lines_strips_bom(_txt):
    p = _txt("b.txt", "﻿First English line here.\nSecond line follows.\n")
    assert RI.extract_lines(p)[0] == "First English line here."   # no leading BOM


def test_pairs_preview_interleaved_returns_all_pairs(_txt):
    p = _txt("i.txt", "방수총 정의.\nMonitor definition here.\n고정형 정의.\nFixed type definition.\n또 다른.\nAnother one here.\n")
    info = RI.analyze(p)
    full = RI.pairs_preview(p)
    assert full["mode"] == "interleaved" and full["estimated"] is False
    assert len(full["pairs"]) == info["pair_count"] >= 3      # ALL pairs, not truncated to 4
    assert set(full["pairs"][0]) == {"source", "target"}


def test_pairs_preview_source_only_is_empty(_txt):
    p = _txt("s.txt", "한국어 원문만 있는 문서입니다.\n번역이 없어요.\n")
    full = RI.pairs_preview(p)
    assert full["mode"] == "source_only" and full["pairs"] == []


def test_pairs_preview_sequential_is_estimated(_txt):
    p = _txt("q.txt", "첫 문장.\n둘째 문장.\n셋째 문장.\nFirst sentence here.\nSecond sentence here.\nThird sentence here.\n")
    full = RI.pairs_preview(p)
    assert full["mode"] == "sequential" and full["estimated"] is True
    assert len(full["pairs"]) >= 1


def test_pairs_preview_target_only_has_no_pairs(_txt):
    p = _txt("t.txt", "Only English sentences here.\nAnother English line follows.\n")
    full = RI.pairs_preview(p)
    assert full["mode"] == "target_only" and full["estimated"] is False and full["pairs"] == []
