# 용어 일괄변경 (Bulk Term Replace in Viewer) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user replace a term across the whole translated document from the OnlyOffice viewer — a multi-row find→replace panel that edits the live `.docx` (preserving manual edits), mirrors the change into the model, and (optionally) remembers each rule so it re-applies on later re-translations.

**Architecture:** A pure matching core (`text_replace.py`) is shared by a run-aware `.docx` editor (`docx_edit.py`), the orchestration in `process.py` (apply to `.docx` → mirror to `translated/edited.{lang}.json` → persist saved rules to `replacements.{lang}.json`), two FastAPI endpoints, and a new right-pane `BulkReplacePanel` mounted as a third viewer mode. Apply reloads the OnlyOffice editor (Community Edition has no in-place connector). Saved rules are a **target-side** replacement list, distinct from the source→target upload glossary, and `retranslate_one_segment` re-applies them so corrected terms stick.

**Tech Stack:** Python 3 / FastAPI / pydantic / python-docx (backend); Next.js / React / TypeScript / Tailwind (frontend). Backend tests: pytest + `fastapi.testclient`. Offline-safe via `MockTranslator` (no `ANTHROPIC_API_KEY`).

**Spec:** [docs/superpowers/specs/2026-06-03-bulk-term-replace-design.md](../specs/2026-06-03-bulk-term-replace-design.md)

> **Backend commands** assume the repo's venv python is active (`backend/.venv`) and are run from the `backend/` directory. **Frontend:** per [frontend/AGENTS.md](../../../frontend/AGENTS.md) this Next.js may differ from defaults — skim `node_modules/next/dist/docs/` before editing; the changes here are standard client components/`fetch` that mirror the existing `RetranslatePanel`/`c/[id]/page.tsx`.

---

## File Structure

**Backend (create):**
- `backend/pipeline/text_replace.py` — `ReplaceRule` model + `compile_rule` / `count_in_text` / `apply_to_text` (pure, no I/O).
- `backend/pipeline/docx_edit.py` — `apply_replacements` / `count_replacements` over a python-docx `Document` (run-aware).

**Backend (modify):**
- `backend/pipeline/process.py` — `translated_path`, `replacements_path`, `load_replacements`, `save_replacements`, `preview_replace`, `bulk_replace`; re-apply saved rules in `retranslate_one_segment`.
- `backend/main.py` — `POST /replace/preview` and `POST /replace`; import `ReplaceRule`, add `ReplaceRequest`.

**Backend (tests, create):** `test_text_replace.py`, `test_docx_edit.py`, `test_bulk_replace.py`, `test_retranslate_reapply.py`, `test_replace_api.py`.

**Frontend (create):** `frontend/src/components/BulkReplacePanel.tsx`.

**Frontend (modify):** `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/app/c/[id]/page.tsx`.

---

## Task 1: `text_replace.py` — pure matching/apply core

**Files:**
- Create: `backend/pipeline/text_replace.py`
- Test: `backend/tests/test_text_replace.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_text_replace.py`:

```python
from pipeline.text_replace import ReplaceRule, apply_to_text, compile_rule, count_in_text


def _rule(find, replace, match="smart"):
    return ReplaceRule(find=find, replace=replace, match=match)


def test_smart_word_boundary_en():
    # "load" must not match inside "download"; smart is case-insensitive.
    text = "Download the load and LOAD it."
    out, counts = apply_to_text(text, [_rule("load", "cargo")], "en")
    assert out == "Download the cargo and cargo it."
    assert counts == {"load": 2}


def test_smart_substring_cjk():
    # Korean has no word boundaries → substring match even in smart mode.
    out, counts = apply_to_text("정격하중과 정격하중", [_rule("정격하중", "정격")], "ko")
    assert out == "정격과 정격"
    assert counts == {"정격하중": 2}


def test_exact_is_case_sensitive_substring():
    out, counts = apply_to_text("Load load", [_rule("Load", "X", "exact")], "en")
    assert out == "X load"
    assert counts == {"Load": 1}


def test_loose_is_case_insensitive_substring():
    # substring (hits inside "download") + case-insensitive.
    out, counts = apply_to_text("download LOAD", [_rule("load", "x", "loose")], "en")
    assert out == "downx x"
    assert counts == {"load": 2}


def test_rules_apply_in_order():
    rules = [_rule("A", "B", "exact"), _rule("B", "C", "exact")]
    out, counts = apply_to_text("A", rules, "en")
    assert out == "C"
    assert counts == {"A": 1, "B": 1}


def test_noop_rules_skipped():
    rules = [_rule("", "x"), _rule("same", "same"), _rule("hit", "done", "exact")]
    out, counts = apply_to_text("same hit", rules, "en")
    assert out == "same done"
    assert counts == {"hit": 1}
    assert compile_rule(_rule("", "x"), "en") is None
    assert compile_rule(_rule("a", "a"), "en") is None


def test_replacement_with_backslash_is_literal():
    out, _ = apply_to_text("see X", [_rule("X", r"\1 path", "exact")], "en")
    assert out == r"see \1 path"


def test_count_in_text():
    pat = compile_rule(_rule("load", "x"), "en")
    assert count_in_text("load the load", pat) == 2
    assert count_in_text("anything", None) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_text_replace.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.text_replace'`

- [ ] **Step 3: Write the implementation**

Create `backend/pipeline/text_replace.py`:

```python
"""Post-translation bulk term replacement — pure matching/apply over plain strings.

The viewer's 용어 일괄변경 lets a user replace a term across the translated document.
This module is the shared, side-effect-free core: compile a rule into a regex, count
matches, and apply a list of rules to a string. docx_edit (the .docx), process (the model
mirror + saved-rule re-apply on re-translation), and the API preview all call it so the
matching semantics stay identical everywhere.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

# Target languages written with spaces → "smart" matching uses word boundaries so
# replacing "load" doesn't hit "download". CJK has no word separators → substring.
_WORD_BOUNDARY_LANGS = {"en", "es", "fr", "de", "vi"}


class ReplaceRule(BaseModel):
    find: str
    replace: str
    match: Literal["smart", "exact", "loose"] = "smart"
    save: bool = True


def compile_rule(rule: ReplaceRule, target_lang: str) -> re.Pattern | None:
    """A compiled pattern for the rule, or None if the rule is a no-op (empty find, or
    find == replace). `smart`/`loose` are case-insensitive; `smart` adds word boundaries
    for space-separated target languages; `exact` is literal + case-sensitive substring."""
    if not rule.find or rule.find == rule.replace:
        return None
    body = re.escape(rule.find)
    flags = re.IGNORECASE if rule.match in ("smart", "loose") else 0
    if rule.match == "smart" and target_lang in _WORD_BOUNDARY_LANGS:
        body = rf"(?<!\w){body}(?!\w)"
    return re.compile(body, flags)


def count_in_text(text: str, pattern: re.Pattern | None) -> int:
    return len(pattern.findall(text)) if pattern else 0


def apply_to_text(
    text: str, rules: list[ReplaceRule], target_lang: str
) -> tuple[str, dict[str, int]]:
    """Apply rules in order (a later rule sees earlier rules' output). Returns the new
    text and {find: replaced_count}. The replacement is treated literally (a `\\1` or
    backslash in the replacement is NOT interpreted as a regex backreference)."""
    counts: dict[str, int] = {}
    for rule in rules:
        pattern = compile_rule(rule, target_lang)
        if pattern is None:
            continue
        text, n = pattern.subn(lambda _m, r=rule: r.replace, text)
        if n:
            counts[rule.find] = counts.get(rule.find, 0) + n
    return text, counts
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_text_replace.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/text_replace.py backend/tests/test_text_replace.py
git commit -m "feat(text_replace): pure rule model + smart/exact/loose matching"
```

---

## Task 2: `docx_edit.py` — run-aware replace over a Document

**Files:**
- Create: `backend/pipeline/docx_edit.py`
- Test: `backend/tests/test_docx_edit.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_docx_edit.py`:

```python
from docx import Document

from pipeline.docx_edit import apply_replacements, count_replacements
from pipeline.text_replace import ReplaceRule


def _rule(find, replace, match="smart"):
    return ReplaceRule(find=find, replace=replace, match=match)


def test_replace_in_single_run_paragraph():
    d = Document()
    d.add_paragraph("Rated Load is high")
    counts = apply_replacements(d, [_rule("Rated Load", "Load Rating")], "en")
    assert d.paragraphs[0].text == "Load Rating is high"
    assert counts == {"Rated Load": 1}


def test_replace_across_run_boundary():
    d = Document()
    p = d.add_paragraph()
    p.add_run("Ra")
    p.add_run("ted Load")  # "Rated" spans two runs → caught by the cross-run pass
    counts = apply_replacements(d, [_rule("Rated", "Nominal")], "en")
    assert p.text == "Nominal Load"
    assert counts == {"Rated": 1}


def test_replace_in_table_cell():
    d = Document()
    t = d.add_table(rows=1, cols=1)
    t.rows[0].cells[0].paragraphs[0].add_run("정격하중")
    counts = apply_replacements(d, [_rule("정격하중", "정격")], "ko")
    assert t.rows[0].cells[0].text == "정격"
    assert counts == {"정격하중": 1}


def test_count_does_not_mutate():
    d = Document()
    d.add_paragraph("load load")
    counts = count_replacements(d, [_rule("load", "x")], "en")
    assert counts == {"load": 2}
    assert d.paragraphs[0].text == "load load"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_docx_edit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.docx_edit'`

- [ ] **Step 3: Write the implementation**

Create `backend/pipeline/docx_edit.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_docx_edit.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/docx_edit.py backend/tests/test_docx_edit.py
git commit -m "feat(docx_edit): run-aware bulk replace over a python-docx Document"
```

---

## Task 3: `process.py` — persistence + bulk_replace + preview

**Files:**
- Modify: `backend/pipeline/process.py` (add helpers near `glossary_path`/`save_doc_glossary`, ≈ lines 248-267, and new functions after `load_doc_glossary`)
- Test: `backend/tests/test_bulk_replace.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_bulk_replace.py`:

```python
import os

from docx import Document

from models import ParagraphBlock, SemanticDocument
from pipeline import process
from pipeline.text_replace import ReplaceRule
from renderers.docx_renderer import render_docx


def _rule(find, replace, save=True, match="smart"):
    return ReplaceRule(find=find, replace=replace, save=save, match=match)


def _seed(tmp_path, text="Rated Load test"):
    storage, doc_id, lang = str(tmp_path), "doc1", "en"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    process._write_json(
        os.path.join(base, "source.json"),
        SemanticDocument(source_lang="ko",
                         blocks=[ParagraphBlock(text="원문", source_span_ids=[])]).model_dump(),
    )
    translated = SemanticDocument(
        source_lang="ko", target_lang=lang,
        blocks=[ParagraphBlock(text=text, source_span_ids=[])],
    )
    process.save_translated_model(storage, doc_id, lang, translated)
    render_docx(translated, process.output_docx_path(storage, doc_id, lang),
                process.asset_dir(storage, doc_id))
    return storage, doc_id, lang


def _docx_text(storage, doc_id, lang):
    d = Document(process.output_docx_path(storage, doc_id, lang))
    return "\n".join(p.text for p in d.paragraphs)


def test_bulk_replace_edits_docx_and_mirrors_model(tmp_path):
    storage, doc_id, lang = _seed(tmp_path)
    res = process.bulk_replace(storage, doc_id, lang, [_rule("Rated Load", "Load Rating")])
    assert res["total"] == 1
    text = _docx_text(storage, doc_id, lang)
    assert "Load Rating" in text and "Rated Load" not in text
    assert process.get_translated_model(storage, doc_id, lang).blocks[0].text == "Load Rating test"


def test_bulk_replace_persists_saved_rules_only(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, "alpha beta")
    process.bulk_replace(storage, doc_id, lang, [
        _rule("alpha", "A", save=True),
        _rule("beta", "B", save=False),
    ])
    saved = process.load_replacements(storage, doc_id, lang)
    assert {r["find"] for r in saved} == {"alpha"}


def test_save_replacements_dedup_later_wins(tmp_path):
    storage, doc_id, lang = str(tmp_path), "doc1", "en"
    os.makedirs(process.doc_dir(storage, doc_id), exist_ok=True)
    process.save_replacements(storage, doc_id, lang, [_rule("x", "1")])
    process.save_replacements(storage, doc_id, lang, [_rule("x", "2")])
    assert process.load_replacements(storage, doc_id, lang) == [
        {"find": "x", "replace": "2", "match": "smart"}
    ]


def test_load_replacements_corrupt_is_empty(tmp_path):
    storage, doc_id, lang = str(tmp_path), "doc1", "en"
    os.makedirs(process.doc_dir(storage, doc_id), exist_ok=True)
    with open(process.replacements_path(storage, doc_id, lang), "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    assert process.load_replacements(storage, doc_id, lang) == []


def test_preview_replace_counts_without_writing(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, "load the load")
    res = process.preview_replace(storage, doc_id, lang, [_rule("load", "cargo")])
    assert res["total"] == 2
    assert "cargo" not in _docx_text(storage, doc_id, lang)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_bulk_replace.py -v`
Expected: FAIL with `AttributeError: module 'pipeline.process' has no attribute 'bulk_replace'`

- [ ] **Step 3: Write the implementation**

In `backend/pipeline/process.py`, add these functions immediately after `load_doc_glossary` (after line 267):

```python
def translated_path(storage_dir: str, doc_id: str, target_lang: str) -> str:
    return os.path.join(doc_dir(storage_dir, doc_id), f"translated.{target_lang}.json")


def replacements_path(storage_dir: str, doc_id: str, target_lang: str) -> str:
    return os.path.join(doc_dir(storage_dir, doc_id), f"replacements.{target_lang}.json")


def load_replacements(storage_dir: str, doc_id: str, target_lang: str) -> list[dict]:
    """Saved post-translation term replacements for this doc/lang, or [] if none/unreadable.
    A corrupt file must not block re-translation — degrade to []."""
    path = replacements_path(storage_dir, doc_id, target_lang)
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def save_replacements(storage_dir: str, doc_id: str, target_lang: str, rules) -> None:
    """Merge `rules` (ReplaceRule list) into the saved set, deduped by `find` (later wins)."""
    by_find = {r["find"]: r for r in load_replacements(storage_dir, doc_id, target_lang)}
    for r in rules:
        by_find[r.find] = {"find": r.find, "replace": r.replace, "match": r.match}
    _write_json(replacements_path(storage_dir, doc_id, target_lang), list(by_find.values()))


def preview_replace(storage_dir: str, doc_id: str, target_lang: str, rules) -> dict:
    """Per-rule occurrence counts over the current .docx (reflects manual editor edits).
    No disk writes. Caller must have ensured the .docx exists."""
    from docx import Document
    from pipeline import docx_edit

    valid = [r for r in rules if r.find and r.find != r.replace]
    document = Document(output_docx_path(storage_dir, doc_id, target_lang))
    counts = docx_edit.count_replacements(document, valid, target_lang)
    results = [{"find": r.find, "replace": r.replace, "count": counts.get(r.find, 0)}
               for r in valid]
    return {"results": results, "total": sum(c["count"] for c in results)}


def bulk_replace(storage_dir: str, doc_id: str, target_lang: str, rules) -> dict:
    """Apply term replacements across the live .docx (preserving manual edits), mirror the
    same change into the translated/edited model(s), and persist rules whose save=True so
    they re-apply on later re-translations. Caller must have ensured the .docx exists.
    Returns {results:[{find,replace,count}], total}."""
    from docx import Document
    from pipeline import docx_edit, text_replace

    valid = [r for r in rules if r.find and r.find != r.replace]

    path = output_docx_path(storage_dir, doc_id, target_lang)
    document = Document(path)
    counts = docx_edit.apply_replacements(document, valid, target_lang)
    document.save(path)

    # Mirror into the model so the 재번역 list + any later re-render stay consistent with
    # the .docx. (source.json is the original language — left untouched.)
    for model_path in (translated_path(storage_dir, doc_id, target_lang),
                       edited_path(storage_dir, doc_id, target_lang)):
        if not os.path.exists(model_path):
            continue
        with open(model_path, encoding="utf-8") as fh:
            model = SemanticDocument.model_validate(json.load(fh))
        for get, set_ in iter_text_fields(model):
            new, _ = text_replace.apply_to_text(get(), valid, target_lang)
            if new != get():
                set_(new)
        _write_json(model_path, model.model_dump())

    saved = [r for r in valid if r.save]
    if saved:
        save_replacements(storage_dir, doc_id, target_lang, saved)

    results = [{"find": r.find, "replace": r.replace, "count": counts.get(r.find, 0)}
               for r in valid]
    return {"results": results, "total": sum(counts.values())}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_bulk_replace.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/process.py backend/tests/test_bulk_replace.py
git commit -m "feat(process): bulk_replace + preview + replacement-rule persistence"
```

---

## Task 4: re-apply saved rules in `retranslate_one_segment`

**Files:**
- Modify: `backend/pipeline/process.py` (`retranslate_one_segment`, lines 308-332)
- Test: `backend/tests/test_retranslate_reapply.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_retranslate_reapply.py`:

```python
import os

from models import ParagraphBlock, SemanticDocument
from pipeline import process
from pipeline.text_replace import ReplaceRule
from pipeline.translator import MockTranslator


def _seed(tmp_path):
    storage, doc_id, lang = str(tmp_path), "doc1", "en"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(base, exist_ok=True)
    process._write_json(
        os.path.join(base, "source.json"),
        SemanticDocument(source_lang="ko",
                         blocks=[ParagraphBlock(text="캐빈", source_span_ids=[])]).model_dump(),
    )
    process.save_translated_model(
        storage, doc_id, lang,
        SemanticDocument(source_lang="ko", target_lang=lang,
                         blocks=[ParagraphBlock(text="cabin", source_span_ids=[])]),
    )
    return storage, doc_id, lang


def test_retranslate_reapplies_saved_replacements(tmp_path):
    storage, doc_id, lang = _seed(tmp_path)
    # MockTranslator echoes "[en] 캐빈" for source "캐빈"; a saved rule must rewrite it.
    process.save_replacements(storage, doc_id, lang,
                              [ReplaceRule(find="캐빈", replace="CAB", match="loose")])
    out = process.retranslate_one_segment(storage, doc_id, lang, 0, "",
                                          translator=MockTranslator())
    assert "CAB" in out["new_text"]
    assert "캐빈" not in out["new_text"]


def test_retranslate_without_saved_rules_unchanged(tmp_path):
    storage, doc_id, lang = _seed(tmp_path)
    out = process.retranslate_one_segment(storage, doc_id, lang, 0, "",
                                          translator=MockTranslator())
    assert out["new_text"] == "[en] 캐빈"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_retranslate_reapply.py -v`
Expected: FAIL — `test_retranslate_reapplies_saved_replacements` fails (`new_text` still contains "캐빈"; saved rules not yet applied).

- [ ] **Step 3: Write the implementation**

In `backend/pipeline/process.py`, edit `retranslate_one_segment`. Find this block (lines 319-323):

```python
    src_text = src_fields[index][0]()
    new_text = translate_segment(
        src_text, source.source_lang, target_lang, guidance, translator=translator,
        extra_glossary=load_doc_glossary(storage_dir, doc_id),
    )
```

Replace it with:

```python
    src_text = src_fields[index][0]()
    new_text = translate_segment(
        src_text, source.source_lang, target_lang, guidance, translator=translator,
        extra_glossary=load_doc_glossary(storage_dir, doc_id),
    )
    # Keep any saved bulk-replace terms applied: a corrected term shouldn't reappear just
    # because this segment was re-translated fresh from the source.
    saved = load_replacements(storage_dir, doc_id, target_lang)
    if saved:
        from pipeline import text_replace
        rules = [text_replace.ReplaceRule(**r) for r in saved]
        new_text, _ = text_replace.apply_to_text(new_text, rules, target_lang)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_retranslate_reapply.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/process.py backend/tests/test_retranslate_reapply.py
git commit -m "feat(process): re-apply saved replacements after a re-translation"
```

---

## Task 5: API endpoints — preview + apply

**Files:**
- Modify: `backend/main.py` (import + `ReplaceRequest` near `RetranslateBody` line 40; endpoints after `render`, ≈ line 334)
- Test: `backend/tests/test_replace_api.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_replace_api.py`:

```python
import os

import pytest
from fastapi.testclient import TestClient

import main
from models import ParagraphBlock, SemanticDocument
from pipeline import process
from renderers.docx_renderer import render_docx


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    return TestClient(main.app)


def _seed(tmp_path, text="Rated Load"):
    storage, doc_id, lang = str(tmp_path), "doc1", "en"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    process._write_json(
        os.path.join(base, "source.json"),
        SemanticDocument(source_lang="ko",
                         blocks=[ParagraphBlock(text="원문", source_span_ids=[])]).model_dump(),
    )
    tr = SemanticDocument(source_lang="ko", target_lang=lang,
                          blocks=[ParagraphBlock(text=text, source_span_ids=[])])
    process.save_translated_model(storage, doc_id, lang, tr)
    render_docx(tr, process.output_docx_path(storage, doc_id, lang),
                process.asset_dir(storage, doc_id))
    return doc_id, lang


def test_preview_then_apply(client, tmp_path):
    doc_id, lang = _seed(tmp_path)
    body = {"rules": [{"find": "Rated Load", "replace": "Load Rating"}]}
    r = client.post(f"/api/documents/{doc_id}/replace/preview?lang={lang}", json=body)
    assert r.status_code == 200
    assert r.json()["total"] == 1
    r2 = client.post(f"/api/documents/{doc_id}/replace?lang={lang}", json=body)
    assert r2.status_code == 200
    assert r2.json()["results"][0]["count"] == 1
    assert process.get_translated_model(str(tmp_path), doc_id, lang).blocks[0].text == "Load Rating"


def test_replace_skips_empty_and_noop_rules(client, tmp_path):
    doc_id, lang = _seed(tmp_path)
    body = {"rules": [{"find": "", "replace": "x"},
                      {"find": "Rated Load", "replace": "Rated Load"}]}
    r = client.post(f"/api/documents/{doc_id}/replace?lang={lang}", json=body)
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_replace_unknown_doc_404(client):
    r = client.post("/api/documents/nope/replace?lang=en", json={"rules": []})
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_replace_api.py -v`
Expected: FAIL with 404/405 (routes not defined) on `test_preview_then_apply`.

- [ ] **Step 3: Write the implementation**

In `backend/main.py`, add `ReplaceRule` to the translator import (line 30):

```python
from pipeline.translator import build_guidance, parse_glossary_csv
from pipeline.text_replace import ReplaceRule
```

Add `ReplaceRequest` right after the `RetranslateBody` class (after line 45):

```python
class ReplaceRequest(BaseModel):
    rules: list[ReplaceRule]
```

Add the two endpoints immediately after the `render` endpoint (after line 334):

```python
@app.post("/api/documents/{doc_id}/replace/preview")
def replace_preview(doc_id: str, body: ReplaceRequest, lang: str):
    """Per-rule occurrence counts for a set of term replacements (no writes)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    _ensure_docx(doc_id, lang)
    try:
        return process.preview_replace(STORAGE, doc_id, lang, body.rules)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Preview failed: {e}")


@app.post("/api/documents/{doc_id}/replace")
def replace_apply(doc_id: str, body: ReplaceRequest, lang: str):
    """Apply bulk term replacements across the document's .docx (preserving manual edits),
    mirror into the model, and persist rules marked save. Returns per-rule replaced counts.
    The caller reloads the editor (Community Edition has no in-place connector)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    _ensure_docx(doc_id, lang)
    try:
        return process.bulk_replace(STORAGE, doc_id, lang, body.rules)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Replace failed: {e}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_replace_api.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full backend suite (no regressions)**

Run: `cd backend && python -m pytest -q`
Expected: PASS — all prior tests (incl. glossary) plus the new ones.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_replace_api.py
git commit -m "feat(api): /replace and /replace/preview endpoints for bulk term replace"
```

---

## Task 6: frontend types + API client

**Files:**
- Modify: `frontend/src/lib/types.ts` (append after the `RetranslateResponse` block, ≈ line 160)
- Modify: `frontend/src/lib/api.ts` (import + two methods)

- [ ] **Step 1: Add the types**

In `frontend/src/lib/types.ts`, append:

```typescript
// POST /api/documents/{id}/replace[/preview]?lang= — 용어 일괄변경 (bulk term replace).
export type MatchMode = "smart" | "exact" | "loose";

export interface ReplaceRule {
  find: string;
  replace: string;
  match: MatchMode;
  save: boolean;
}

export interface ReplaceResult {
  find: string;
  replace: string;
  count: number;
}

export interface ReplaceResponse {
  results: ReplaceResult[];
  total: number;
}
```

- [ ] **Step 2: Add the API methods**

In `frontend/src/lib/api.ts`, extend the type import (lines 1-9) to include the new types:

```typescript
import type {
  EditorConfigResponse,
  HealthResponse,
  JobStatus,
  ReplaceResponse,
  ReplaceRule,
  ResultResponse,
  RetranslateResponse,
  SegmentsResponse,
  StartResponse,
} from "./types";
```

Add these two methods inside the `api` object, right after the `render` method (after line 87):

```typescript
  // Per-rule occurrence counts for a set of replacements (preview before applying).
  replacePreview: (docId: string, lang: string, rules: ReplaceRule[]) =>
    fetch(`/api/documents/${docId}/replace/preview?lang=${encodeURIComponent(lang)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rules }),
    }).then((r) => asJson<ReplaceResponse>(r)),

  // Apply the replacements to the .docx (preserving manual edits) + mirror the model.
  replaceApply: (docId: string, lang: string, rules: ReplaceRule[]) =>
    fetch(`/api/documents/${docId}/replace?lang=${encodeURIComponent(lang)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rules }),
    }).then((r) => asJson<ReplaceResponse>(r)),
```

- [ ] **Step 3: Type-check / build**

Run: `cd frontend && npm run build`
Expected: build succeeds (no type errors). If `next start` is running, this is enough to confirm types compile.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts
git commit -m "feat(web): ReplaceRule types + replace/preview API client methods"
```

---

## Task 7: `BulkReplacePanel` + third viewer mode

**Files:**
- Create: `frontend/src/components/BulkReplacePanel.tsx`
- Modify: `frontend/src/app/c/[id]/page.tsx` (mode union line 24; action bar line 124-132; right drawer lines 212-225; import line 9)

- [ ] **Step 1: Create the panel component**

Create `frontend/src/components/BulkReplacePanel.tsx`:

```tsx
"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import type { MatchMode, ReplaceResult, ReplaceRule } from "@/lib/types";

const MATCHES: { key: MatchMode; label: string }[] = [
  { key: "smart", label: "스마트" },
  { key: "exact", label: "정확히" },
  { key: "loose", label: "느슨하게" },
];

function emptyRule(): ReplaceRule {
  return { find: "", replace: "", match: "smart", save: true };
}

export function BulkReplacePanel({
  docId,
  lang,
  onApplied,
}: {
  docId: string;
  lang: string;
  // Called after a successful apply so the parent reloads the OnlyOffice editor
  // (Community Edition has no connector for in-place edits).
  onApplied: () => void;
}) {
  const [rules, setRules] = useState<ReplaceRule[]>([emptyRule()]);
  const [results, setResults] = useState<ReplaceResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  function update(i: number, patch: Partial<ReplaceRule>) {
    setRules((prev) => prev.map((r, j) => (j === i ? { ...r, ...patch } : r)));
    setResults(null);
  }
  function addRow() {
    setRules((prev) => [...prev, emptyRule()]);
  }
  function removeRow(i: number) {
    setRules((prev) => (prev.length === 1 ? prev : prev.filter((_, j) => j !== i)));
    setResults(null);
  }

  // Drop empty / no-op rows before sending (the backend skips them too).
  const filled = () => rules.filter((r) => r.find.trim() && r.find !== r.replace);
  const countOf = (find: string) => results?.find((r) => r.find === find)?.count;

  async function doPreview() {
    setBusy(true);
    setNote(null);
    try {
      const res = await api.replacePreview(docId, lang, filled());
      setResults(res.results);
      if (res.total === 0) setNote("일치하는 단어가 없습니다.");
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    setBusy(true);
    setNote(null);
    try {
      const res = await api.replaceApply(docId, lang, filled());
      setResults(res.results);
      if (res.total > 0) {
        onApplied();
        setNote(`${res.total}곳을 변경하고 에디터에 반영했습니다.`);
      } else {
        setNote("일치하는 단어가 없어 변경하지 않았습니다.");
      }
    } catch (e) {
      setNote(`변경 실패: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto px-4 py-4">
      <h2 className="slug mb-3 px-1">용어 일괄변경 — 번역 결과에서 단어를 바꿉니다</h2>
      <ul className="space-y-3">
        {rules.map((rule, i) => (
          <li key={i} className="rounded border border-rule p-2">
            <div className="flex items-center gap-1.5">
              <input
                value={rule.find}
                onChange={(e) => update(i, { find: e.target.value })}
                placeholder="찾을 단어"
                className="mono w-full rounded border border-rule p-1.5 text-sm"
              />
              <span className="opacity-50">→</span>
              <input
                value={rule.replace}
                onChange={(e) => update(i, { replace: e.target.value })}
                placeholder="바꿀 단어"
                className="mono w-full rounded border border-rule p-1.5 text-sm"
              />
              {rules.length > 1 && (
                <button className="px-1 text-sm opacity-60" onClick={() => removeRow(i)}>
                  ×
                </button>
              )}
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
              {MATCHES.map((m) => (
                <button
                  key={m.key}
                  onClick={() => update(i, { match: m.key })}
                  className={`mono rounded px-2 py-0.5 text-xs ${
                    rule.match === m.key ? "btn-ink" : "border border-rule"
                  }`}
                >
                  {m.label}
                </button>
              ))}
              <label className="mono ml-auto flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={rule.save}
                  onChange={(e) => update(i, { save: e.target.checked })}
                />
                용어집에 저장
              </label>
            </div>
            {countOf(rule.find) != null && (
              <p className="slug mt-1">{countOf(rule.find)}곳</p>
            )}
          </li>
        ))}
      </ul>
      <button className="mono mt-2 text-xs underline" onClick={addRow}>
        + 행 추가
      </button>
      <div className="mt-3 flex items-center gap-2">
        <button className="btn-ink" disabled={busy} onClick={doPreview}>
          {busy ? "확인 중…" : "미리보기"}
        </button>
        <button className="btn-vermillion" disabled={busy} onClick={apply}>
          적용
        </button>
      </div>
      {note && <p className="slug mt-2">{note}</p>}
    </div>
  );
}
```

- [ ] **Step 2: Import the panel in the viewer**

In `frontend/src/app/c/[id]/page.tsx`, add the import after line 9:

```tsx
import { RetranslatePanel } from "@/components/RetranslatePanel";
import { BulkReplacePanel } from "@/components/BulkReplacePanel";
```

- [ ] **Step 3: Widen the mode union**

Change line 24 from:

```tsx
  const [mode, setMode] = useState<"edit" | "retranslate">("edit");
```

to:

```tsx
  const [mode, setMode] = useState<"edit" | "retranslate" | "replace">("edit");
```

- [ ] **Step 4: Add the third-mode toggle button**

In the action bar, the existing 재번역 모드 button is at lines 125-132. Immediately after its closing `</button>` (after line 132), add:

```tsx
          <button
            className={mode === "replace" ? "btn-vermillion" : "btn-ink"}
            onClick={() => setMode((m) => (m === "replace" ? "edit" : "replace"))}
          >
            {mode === "replace" ? "용어 일괄변경 ●" : "용어 일괄변경"}
          </button>
```

- [ ] **Step 5: Render the right drawer for either mode**

Replace the right-drawer block (lines 212-225) — currently:

```tsx
        {mode === "retranslate" && (
          <div className="flex min-h-0 flex-col border-t border-rule lg:flex-none lg:w-[400px] lg:border-l lg:border-t-0">
            <RetranslatePanel
              docId={doc.doc_id}
              lang={lang}
              connector={connector}
              onActiveSegment={setActiveSeg}
              onApplied={(bm) => {
                setEditorBookmark(bm);
                setEditorReloadKey((k) => k + 1);
              }}
            />
          </div>
        )}
```

with (renders the drawer for retranslate OR replace, choosing the panel):

```tsx
        {mode !== "edit" && (
          <div className="flex min-h-0 flex-col border-t border-rule lg:flex-none lg:w-[400px] lg:border-l lg:border-t-0">
            {mode === "retranslate" ? (
              <RetranslatePanel
                docId={doc.doc_id}
                lang={lang}
                connector={connector}
                onActiveSegment={setActiveSeg}
                onApplied={(bm) => {
                  setEditorBookmark(bm);
                  setEditorReloadKey((k) => k + 1);
                }}
              />
            ) : (
              <BulkReplacePanel
                docId={doc.doc_id}
                lang={lang}
                onApplied={() => setEditorReloadKey((k) => k + 1)}
              />
            )}
          </div>
        )}
```

- [ ] **Step 6: Build**

Run: `cd frontend && npm run build`
Expected: build succeeds (no type/lint errors).

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/BulkReplacePanel.tsx frontend/src/app/c/[id]/page.tsx
git commit -m "feat(web): BulkReplacePanel + third 용어 일괄변경 viewer mode"
```

---

## Final Verification

- [ ] **Backend:** `cd backend && python -m pytest -q` — all green (new suites + existing, incl. glossary).
- [ ] **Frontend:** `cd frontend && npm run build` — clean.
- [ ] **Manual (prod build; dev HMR is broken here — use `npm run build` + `next start`, and the Document Server must be up):**
  1. Process a document, open the viewer (`/c/{id}?lang=en`).
  2. Toggle **용어 일괄변경** → the right panel appears beside the editor (편집/재번역/용어 일괄변경 are mutually exclusive).
  3. Enter a 찾을 단어 → 바꿀 단어 present in the translation, **미리보기** → the row shows a "N곳" count.
  4. **적용** → editor reloads with the term changed everywhere; "M곳을 변경…" note shows.
  5. Add a second row, toggle a row's match mode, uncheck "용어집에 저장" on one row, apply → both applied; only the checked row is saved.
  6. Switch to **재번역 모드**, re-translate a sentence whose source contains a saved-rule term → the result keeps the replaced term (saved rule re-applied).
- [ ] **Branch wrap-up:** use **superpowers:finishing-a-development-branch**.

---

## Notes / Risks

- **Manual edits preserved:** apply edits the live `output.{lang}.docx` (post-edit source of truth) rather than re-rendering from the model. The model is mirrored so the 재번역 list + any later `/render` stay consistent. `source.json` is never touched.
- **Run flattening:** a replacement that crosses run boundaries flattens *only that paragraph's* runs into one (rare; mixed inline formatting within a single paragraph). Within-run matches keep formatting.
- **Saved rules ≠ upload glossary:** saved rules are target-side string replacements in `replacements.{lang}.json`, applied after translation; the upload glossary (`glossary.json`) is source→target, applied during translation. They are intentionally separate stores.
- **Preview accuracy:** counts are computed on the live `.docx` text, so they reflect manual editor edits. Counts can differ from applied counts only in the pathological case where a replacement reintroduces a later rule's `find` — acceptable for v1.
