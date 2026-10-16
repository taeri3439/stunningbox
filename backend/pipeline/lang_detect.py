"""Detect a document's main language from a cheap text sample, to default the upload
translation direction: a non-Korean document → translate to Korean, a Korean document →
translate to English. Script heuristics decide the unambiguous CJK cases (Hangul/kana/
Han); langdetect disambiguates Latin-script languages. Best-effort: unknown → English.
"""
from __future__ import annotations

import os
import re

SUPPORTED = ("ko", "en", "ja", "zh", "es", "fr", "de", "vi")
_SAMPLE_CHARS = 4000
_SAMPLE_PAGES = 2

_HANGUL = re.compile(r"[가-힣]")
_KANA = re.compile(r"[぀-ヿ]")      # hiragana + katakana
_HAN = re.compile(r"[一-鿿]")        # CJK unified ideographs
_LATIN = re.compile(r"[A-Za-z]")


def sample_text(path: str) -> str:
    """A cheap text sample for detection — no vision/render. Returns "" for formats we
    don't sample here (e.g. .hwp/.doc), letting the caller fall back to a default."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf":
            import fitz

            doc = fitz.open(path)
            try:
                parts = [doc[i].get_text()
                         for i in range(min(_SAMPLE_PAGES, doc.page_count))]
            finally:
                doc.close()
            return "".join(parts)[:_SAMPLE_CHARS]
        if ext == ".docx":
            from docx import Document

            d = Document(path)
            out: list[str] = []
            total = 0
            for p in d.paragraphs:
                out.append(p.text)
                total += len(p.text)
                if total > _SAMPLE_CHARS:
                    break
            return "\n".join(out)[:_SAMPLE_CHARS]
    except Exception:  # noqa: BLE001 — detection is best-effort, never fatal
        return ""
    return ""  # .hwp / .doc / unknown → handled by detect_lang's defaults


def _ratio(pat: re.Pattern, text: str, denom: int) -> float:
    return len(pat.findall(text)) / denom if denom else 0.0


def detect_lang(text: str, *, is_hwp: bool = False) -> str:
    """Main language of the sample as a supported code; defaults to 'en' when unknown.

    HWP is a Korean word-processor format → 'ko' without extraction. Hangul is unambiguous
    and the key case, so a meaningful Hangul ratio forces 'ko' even when the sample also
    carries English technical terms (where langdetect can misfire)."""
    if is_hwp:
        return "ko"
    t = text or ""
    scripted = (len(_HANGUL.findall(t)) + len(_LATIN.findall(t))
                + len(_KANA.findall(t)) + len(_HAN.findall(t)))
    if scripted >= 20:  # enough signal for the script heuristics
        if _ratio(_HANGUL, t, scripted) >= 0.15:
            return "ko"
        if _ratio(_KANA, t, scripted) >= 0.05:
            return "ja"
        if not _KANA.search(t) and _ratio(_HAN, t, scripted) >= 0.30:
            return "zh"
    code = _langdetect(t)
    return code if code in SUPPORTED else "en"


def _langdetect(text: str) -> str | None:
    if len(text.strip()) < 10:
        return None
    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 0  # deterministic
        code = detect(text).lower()
    except Exception:  # noqa: BLE001
        return None
    return "zh" if code.startswith("zh") else code


def default_direction(source: str) -> tuple[str, str]:
    """(source, target): Korean → English; anything else → Korean."""
    src = source if source in SUPPORTED else "en"
    return src, ("en" if src == "ko" else "ko")
