"""Classify a bilingual reference upload's format and extract style segments + TM pairs.

A "style reference" can arrive in several shapes (see docs); the backend only needs to end
up with target-language *style* sentences and, when both languages are present and pairable,
source↔target *TM pairs*. This module detects the shape and extracts both.

Language tagging is **Hangul-ratio based**, not langdetect: KFI/HWP technical text is full of
latin tokens (KFI, mm, dB, TCP, "호칭40") embedded in Korean, which fools language detectors —
but "has any Hangul → ko, else has latin → en" separates source/target near-perfectly here.

Modes: interleaved · columns · sequential · paired_files · target_only · source_only(error).
(ko↔en assumed; generalising to other pairs would replace the Hangul check in `lang_of`.)
"""
from __future__ import annotations

import csv
import io
import os
import re
import tempfile

from pipeline.embeddings import get_embedder

_HANGUL = re.compile(r"[가-힣]")
_LATIN_WORD = re.compile(r"[A-Za-z]{3,}")   # a 3+ letter run — a candidate real word
_LOWER_VOWEL = re.compile(r"[aeiou]")        # lowercase vowel ⇒ prose, not an ACRONYM (KFI, TCP)
_SENT = re.compile(r"(?<=[.!?。…])\s+")
_HEADER = {"원어", "번역어", "원문", "번역", "source", "target", "term", "translation",
           "source term", "target term"}


def _has_latin_prose(text: str) -> bool:
    """True only if the text has a genuine word-like Latin token — a 3+ letter run with a
    *lowercase* vowel. The lowercase-vowel rule separates real prose ('Article', 'shall') from
    the Latin that pervades Korean technical docs but is NOT a translation: 'Ac'/'Re' (합격/불합격),
    'A B C' (열 기호), '1-S-1' (샘플링 코드), 'mm'/'dB' (단위), and all-caps acronyms 'KFI'/'TCP'."""
    return any(_LOWER_VOWEL.search(tok) for tok in _LATIN_WORD.findall(text or ""))


def lang_of(text: str) -> str:
    """'ko' if any Hangul; else 'en' ONLY if there is real Latin prose (see `_has_latin_prose`),
    else 'other'. The prose gate stops a Korean-only standard — riddled with sampling-table codes
    ('Ac Re'), column letters ('A B C'), and units — from being misread as a bilingual file with
    an English translation (which produced junk TM pairs and a spurious 'interleaved' result)."""
    text = text or ""
    if _HANGUL.search(text):
        return "ko"
    if _has_latin_prose(text):
        return "en"
    return "other"


# --------------------------------------------------------------------------- #
# Extraction (plain text — no vision; PDFs via PyMuPDF blocks)
# --------------------------------------------------------------------------- #


def extract_lines(path: str) -> list[str]:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".txt", ".md", ".csv"):
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read().lstrip("﻿").splitlines()   # drop a leading UTF-8 BOM
    if ext == ".pdf":
        import fitz
        out: list[str] = []
        with fitz.open(path) as d:
            for page in d:
                for blk in page.get_text("blocks"):  # (x0,y0,x1,y1,text,no,type)
                    out.extend(blk[4].splitlines())
        return out
    if ext in (".docx", ".doc"):
        import docx
        d = docx.Document(path)
        out = [p.text for p in d.paragraphs]
        for tbl in d.tables:
            for row in tbl.rows:
                out.extend(cell.text for cell in row.cells)
        return out
    if ext == ".hwp":   # hwp5html reads binary .hwp only — .hwpx (zip) is not supported
        from models import iter_text_fields
        from pipeline import hwp_extractor
        doc = hwp_extractor.extract_hwp(path, tempfile.mkdtemp())
        return [get() for get, _ in iter_text_fields(doc)]
    return []


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    parts = _SENT.split(text)
    return [p.strip() for p in (parts if len(parts) > 1 else [text]) if p.strip()]


def _style_segments(blocks, min_len: int = 8) -> list[str]:
    """Flatten text blocks into deduped, sentence-sized target segments for style retrieval."""
    out, seen = [], set()
    for b in blocks:
        for s in _sentences(b):
            key = s.lower()
            if len(s) >= min_len and key not in seen:
                seen.add(key)
                out.append(s)
    return out


def _tag(lines) -> list[tuple[str, str]]:
    out = []
    for ln in lines:
        s = (ln or "").strip().lstrip("﻿").strip()   # drop a stray BOM (str.strip won't)
        if s:
            out.append((lang_of(s), s))
    return out


def _merge_runs(tagged) -> list[tuple[str, str]]:
    """Merge adjacent same-language lines into one block (a paragraph can span lines); a
    number/symbol-only line ('other') attaches to the current run. A leading 'other' line
    (no run yet) is dropped, not started as its own run — keeps positional pairing aligned."""
    runs: list[tuple[str, str]] = []
    for lang, ln in tagged:
        if runs and (lang == runs[-1][0] or lang == "other"):
            runs[-1] = (runs[-1][0], runs[-1][1] + " " + ln)
        elif lang != "other":
            runs.append((lang, ln))
    return runs


# --------------------------------------------------------------------------- #
# Pairing modes
# --------------------------------------------------------------------------- #


def pair_interleaved(lines):
    """#2 — 원문 단락 → 번역 단락 alternating in one file. Pair each source run with the
    immediately-following target run (positional). Returns (style_segments, pairs)."""
    runs = _merge_runs(_tag(lines))
    tgt_blocks, pairs = [], []
    for i, (lang, text) in enumerate(runs):
        if lang == "en":
            tgt_blocks.append(text)
        if lang == "ko" and i + 1 < len(runs) and runs[i + 1][0] == "en":
            pairs.append((text.strip(), runs[i + 1][1].strip()))
    return _style_segments(tgt_blocks), pairs


def pair_sequential(lines, embedder=None):
    """#5 — bilingual but bulk source then bulk target. Split by language, sentence-segment,
    and align by embedding (positional when counts match). Returns (style_segments, pairs)."""
    from pipeline.align import align_segments
    tagged = _tag(lines)
    src = _sentences(" ".join(t for lang, t in tagged if lang == "ko"))
    tgt = _sentences(" ".join(t for lang, t in tagged if lang == "en"))
    pairs = align_segments(src, tgt, embedder or get_embedder())
    return _style_segments([t for lang, t in tagged if lang == "en"]), pairs


def pair_columns_text(text: str):
    """2-column CSV text ('원문,번역' per row) → (style_segments, pairs)."""
    text = text.lstrip("﻿")
    pairs = []
    for ri, row in enumerate(csv.reader(io.StringIO(text))):
        if len(row) < 2:
            continue
        s, t = row[0].strip(), row[1].strip()
        if not s or not t:
            continue
        if ri == 0 and s.lower() in _HEADER and t.lower() in _HEADER:
            continue
        pairs.append((s, t))
    return _style_segments([t for _, t in pairs]), pairs


def pair_columns(path: str):
    """#3 — CSV/2-column. .csv parsed directly; .docx 2-column tables (col0 ko, col1 en)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".csv", ".txt"):
        with open(path, encoding="utf-8", errors="replace") as fh:
            return pair_columns_text(fh.read())
    if ext in (".docx", ".doc"):
        import docx
        pairs = []
        for tbl in docx.Document(path).tables:
            if len(tbl.columns) < 2:
                continue
            for row in tbl.rows:
                cells = [c.text.strip() for c in row.cells]
                if (len(cells) >= 2 and cells[0] and cells[1]
                        and lang_of(cells[0]) == "ko" and lang_of(cells[1]) == "en"):
                    pairs.append((cells[0], cells[1]))
        return _style_segments([t for _, t in pairs]), pairs
    return [], []


def pair_files(target_path: str, source_path: str, embedder=None):
    """#4 — separate 원문 + 번역 files, aligned by embedding. Returns (style_segments, pairs)."""
    from pipeline.align import align_segments
    tgt = _style_segments(extract_lines(target_path))
    src = _style_segments(extract_lines(source_path))
    return tgt, align_segments(src, tgt, embedder or get_embedder())


# --------------------------------------------------------------------------- #
# Classification + dispatch
# --------------------------------------------------------------------------- #


def classify(target_path: str, source_path: str | None = None) -> str:
    if source_path:
        return "paired_files"
    ext = os.path.splitext(target_path)[1].lower()
    if ext == ".csv":
        return "columns"
    tagged = _tag(extract_lines(target_path))
    n_ko = sum(1 for l, _ in tagged if l == "ko")
    n_en = sum(1 for l, _ in tagged if l == "en")
    if n_en == 0:
        return "source_only" if n_ko else "target_only"
    if n_ko == 0:
        return "target_only"
    if ext in (".docx", ".doc"):
        _, cpairs = pair_columns(target_path)
        if len(cpairs) >= max(3, 0.5 * min(n_ko, n_en)):
            return "columns"
    runs = _merge_runs(tagged)
    ko_runs = sum(1 for l, _ in runs if l == "ko")
    alt = sum(1 for i in range(len(runs) - 1) if runs[i][0] == "ko" and runs[i + 1][0] == "en")
    if ko_runs and alt >= 0.6 * ko_runs and len(runs) >= 4:
        return "interleaved"
    return "sequential"


def extract_for_mode(target_path, source_path, mode, embedder=None):
    """Run the chosen pairing mode → (style_segments, pairs). Raises on source_only."""
    if mode == "source_only":
        raise ValueError("번역 내용이 없는 파일이에요 — 레퍼런스로 쓰려면 원문과 번역이 함께 있는 파일을 올려주세요.")
    if mode == "columns":
        return pair_columns(target_path)
    if mode == "interleaved":
        return pair_interleaved(extract_lines(target_path))
    if mode == "sequential":
        return pair_sequential(extract_lines(target_path), embedder)
    if mode == "paired_files":
        return pair_files(target_path, source_path, embedder)
    # target_only (default) — keep only target-language lines, so a mis-applied override on a
    # source-only file yields nothing (→ rejected upstream) instead of storing 원문 as 번역.
    return _style_segments([ln for ln in extract_lines(target_path) if lang_of(ln) != "ko"]), []


def extract_style_only(target_path, source_path, mode) -> list[str]:
    """The target-language *style* segments for `mode`, WITHOUT any embedding/alignment. Cheap
    and never network-bound, so the style half of a reference can always be persisted even when
    pair indexing (which embeds) later fails. Raises on source_only (nothing to learn)."""
    if mode == "source_only":
        raise ValueError("번역 내용이 없는 파일이에요 — 레퍼런스로 쓰려면 원문과 번역이 함께 있는 파일을 올려주세요.")
    if mode == "columns":
        return pair_columns(target_path)[0]
    if mode == "interleaved":
        return pair_interleaved(extract_lines(target_path))[0]
    if mode == "sequential":
        tagged = _tag(extract_lines(target_path))
        return _style_segments([t for l, t in tagged if l == "en"])
    if mode == "paired_files":
        return _style_segments(extract_lines(target_path))
    return _style_segments([ln for ln in extract_lines(target_path) if lang_of(ln) != "ko"])


def extract_pairs_only(target_path, source_path, mode, embedder=None) -> list[tuple[str, str]]:
    """The 원문↔번역 pairs for `mode` (may embed for the alignment modes sequential/paired_files).
    Best-effort: callers run this AFTER the style half is stored, treating failure as non-fatal."""
    if mode == "columns":
        return pair_columns(target_path)[1]
    if mode == "interleaved":
        return pair_interleaved(extract_lines(target_path))[1]
    if mode == "sequential":
        return pair_sequential(extract_lines(target_path), embedder)[1]
    if mode == "paired_files":
        return pair_files(target_path, source_path, embedder)[1]
    return []  # target_only / source_only carry no pairs


def _extract_preview(target_path, source_path, mode):
    """(style_segments, pairs, estimated) for `mode` WITHOUT embedding. sequential/paired_files
    are positional estimates here — real alignment happens at ingest."""
    if mode == "paired_files" and not source_path:
        raise ValueError("paired_files 모드에는 원문 파일이 필요합니다.")
    if mode in ("interleaved", "columns", "target_only"):
        style, pairs = extract_for_mode(target_path, source_path, mode)
        return style, pairs, False
    if mode == "paired_files":
        src = _style_segments(extract_lines(source_path))
        tgt = _style_segments(extract_lines(target_path))
    else:  # sequential
        tagged = _tag(extract_lines(target_path))
        src = _sentences(" ".join(t for l, t in tagged if l == "ko"))
        tgt = _sentences(" ".join(t for l, t in tagged if l == "en"))
    n = min(len(src), len(tgt))
    return tgt, [(src[i], tgt[i]) for i in range(n)], True


def analyze(target_path: str, source_path: str | None = None) -> dict:
    """Detect the format and preview what would be extracted — WITHOUT embedding (cheap).
    For alignment modes (sequential/paired) pair_count is an estimate and samples are positional;
    the real alignment runs at ingest time."""
    mode = classify(target_path, source_path)
    if mode == "source_only":
        return {"mode": mode, "style_count": 0, "pair_count": 0, "sample_pairs": [],
                "sample_style": [], "has_pairs": False, "estimated": False,
                "message": "번역 내용이 없는 파일이에요 — 레퍼런스로 쓰려면 원문과 번역이 함께 있는 파일을 올려주세요."}
    style, pairs, estimated = _extract_preview(target_path, source_path, mode)
    return {
        "mode": mode, "style_count": len(style), "pair_count": len(pairs),
        "sample_pairs": [{"source": s, "target": t} for s, t in pairs[:4]],
        "sample_style": style[:4], "has_pairs": bool(pairs), "estimated": estimated,
    }


def pairs_preview(target_path: str, source_path: str | None = None, mode: str | None = None) -> dict:
    """ALL 원문↔번역 pairs for one file, WITHOUT embedding. `mode` overrides auto-detection.
    sequential/paired_files are positional estimates (estimated=True)."""
    m = mode or classify(target_path, source_path)
    if m == "source_only":
        return {"mode": m, "estimated": False, "pairs": []}
    _, pairs, estimated = _extract_preview(target_path, source_path, m)  # style not needed for the pairs view
    return {"mode": m, "estimated": estimated,
            "pairs": [{"source": s, "target": t} for s, t in pairs]}
