"""Run-aware bulk term replacement over a python-docx Document.

python-docx splits a paragraph's text across runs (and OnlyOffice fragments them further
on save), so a match can straddle run boundaries. We replace within each run first
(preserving that run's formatting), then catch any cross-run leftover by rewriting the
paragraph's joined text into its first run. Covers body paragraphs, table cells (nested
too), and section header/footer paragraphs.
"""
from __future__ import annotations

from pipeline import text_replace
from pipeline.text_replace import ReplaceRule


def _iter_table_paragraphs(table):
    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                yield p
            for nested in cell.tables:
                yield from _iter_table_paragraphs(nested)


def _iter_paragraphs(document):
    for p in document.paragraphs:
        yield p
    for table in document.tables:
        yield from _iter_table_paragraphs(table)
    for section in document.sections:
        for band in (section.header, section.footer):
            for p in band.paragraphs:
                yield p
            for table in band.tables:
                yield from _iter_table_paragraphs(table)


def _merge(counts: dict, more: dict) -> None:
    for k, v in more.items():
        counts[k] = counts.get(k, 0) + v


def _replace_in_paragraph(paragraph, rules, target_lang) -> dict:
    runs = paragraph.runs
    if not runs:
        return {}
    # The whole-paragraph result is authoritative for BOTH the final text and the counts:
    # computed once on the ORIGINAL joined text, so an expanding replacement (find a
    # substring of replace) is never re-matched and nothing is double-counted.
    original = "".join(run.text for run in runs)
    final, counts = text_replace.apply_to_text(original, rules, target_lang)
    if final == original:
        return {}
    # Prefer per-run replacement so each run keeps its own formatting. If that reproduces
    # the authoritative text, keep it; otherwise a match spanned run boundaries, so flatten
    # the paragraph into its first run.
    for run in runs:
        new, _ = text_replace.apply_to_text(run.text, rules, target_lang)
        if new != run.text:
            run.text = new
    if "".join(run.text for run in runs) != final:
        runs[0].text = final
        for run in runs[1:]:
            run.text = ""
    return counts


def apply_replacements(document, rules: list[ReplaceRule], target_lang: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for paragraph in _iter_paragraphs(document):
        _merge(counts, _replace_in_paragraph(paragraph, rules, target_lang))
    return counts


def count_replacements(document, rules: list[ReplaceRule], target_lang: str) -> dict[str, int]:
    """Per-rule occurrence counts across the document, without mutating it (for preview)."""
    patterns = [(r.find, text_replace.compile_rule(r, target_lang)) for r in rules]
    counts: dict[str, int] = {}
    for paragraph in _iter_paragraphs(document):
        text = paragraph.text
        for find, pattern in patterns:
            n = text_replace.count_in_text(text, pattern)
            if n:
                counts[find] = counts.get(find, 0) + n
    return counts
