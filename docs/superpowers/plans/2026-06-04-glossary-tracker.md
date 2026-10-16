# 용어 관리 (Glossary Tracking & Management) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the `용어 일괄변경` viewer mode into `용어 관리`: an editable glossary list with an applied/missed audit, a highlighted read-only translated HTML preview, source-block highlighting (🟢 applied / 🔴 missed), inline 번역어 editing (reuses `bulk_replace`), and add-term — with the existing find→replace kept as a secondary section.

**Architecture:** A pure audit (`glossary_audit`) pairs source↔translation segments (reusing `build_segments`) and classifies each glossary entry. `render_html` gains opt-in term highlighting + block anchors for the preview. `process` exposes a lazy `glossary_report` plus `update_glossary_term`/`add_glossary_term` (reusing the merged `bulk_replace` and `retranslate_one_segment` engines). Three new endpoints feed a restructured `manage` viewer mode whose center swaps the OnlyOffice editor for the highlighted preview.

**Tech Stack:** Python 3 / FastAPI / pydantic / python-docx (backend); Next.js / React / TypeScript / Tailwind (frontend). Backend tests: pytest + `fastapi.testclient`, offline via `MockTranslator`.

**Spec:** [docs/superpowers/specs/2026-06-04-glossary-tracker-design.md](../specs/2026-06-04-glossary-tracker-design.md)

> **Backend commands** run from the repo root using the project venv: `backend/.venv/Scripts/python.exe -m pytest backend/tests/<file> -v`. **Frontend:** per [frontend/AGENTS.md](../../../frontend/AGENTS.md) this Next.js may differ from defaults — skim `node_modules/next/dist/docs/` before editing; the changes mirror existing components (`RetranslatePanel`, `BulkReplacePanel`, `OnlyOfficeEditor`) and the `/view` HTMLResponse pattern.

---

## File Structure

**Backend (create):** `backend/pipeline/glossary_audit.py` — pure per-term classification.
**Backend (modify):**
- `backend/pipeline/text_replace.py` — add `compile_terms(terms, lang)` (combined alternation pattern, matching parity).
- `backend/renderers/html_renderer.py` — `render_html` gains `highlight_pattern` + `anchor_blocks`.
- `backend/pipeline/process.py` — `glossary_report`, `update_glossary_term`, `add_glossary_term`, `apply_glossary_term`.
- `backend/main.py` — `GET /glossary-report`, `GET /preview`, `POST /glossary/term`.

**Frontend (modify):** `frontend/src/lib/types.ts`, `frontend/src/lib/api.ts`, `frontend/src/app/c/[id]/page.tsx`.
**Frontend (create):** `frontend/src/components/TranslatedPreview.tsx`, `frontend/src/components/GlossaryManagePanel.tsx`.

---

## Task 1: `glossary_audit.py` — pure per-term classification

**Files:**
- Create: `backend/pipeline/glossary_audit.py`
- Test: `backend/tests/test_glossary_audit.py`

- [ ] **Step 1: Write the failing test** — create `backend/tests/test_glossary_audit.py`:

```python
from pipeline.glossary_audit import audit_glossary


def _seg(index, source, translated, block=None, bbox=None):
    return {"index": index, "source": source, "translated": translated,
            "source_bbox": bbox, "block": block}


def test_applied_partial_missed_absent():
    segments = [
        _seg(0, "정격하중 표", "Rated Load table", block=0),   # applied
        _seg(1, "정격하중 재확인", "Capacity recheck", block=1),  # source has 원어, target missing → missed
        _seg(2, "캐빈 점검", "Cabin check", block=2),           # applied
        _seg(3, "무관한 문장", "Unrelated sentence", block=3),
    ]
    glossary = {"정격하중": "Rated Load", "캐빈": "Cabin", "스태빌라이저": "Stabilizer"}
    out = {t["source"]: t for t in audit_glossary(segments, glossary, "ko", "en")}

    assert out["정격하중"]["status"] == "partial"
    assert out["정격하중"]["source_count"] == 2
    assert out["정격하중"]["applied_count"] == 1
    # locations carry per-occurrence applied flag + block for navigation
    locs = {l["block"]: l["applied"] for l in out["정격하중"]["locations"]}
    assert locs == {0: True, 1: False}

    assert out["캐빈"]["status"] == "applied"
    assert out["스태빌라이저"]["status"] == "absent"
    assert out["스태빌라이저"]["source_count"] == 0


def test_english_word_boundary_and_case():
    # target "Load" must not count inside "Download"; case-insensitive.
    segments = [_seg(0, "하중", "the load is high", block=0),
                _seg(1, "하중", "Download the file", block=1)]
    out = audit_glossary([*segments], {"하중": "Load"}, "ko", "en")[0]
    assert out["status"] == "partial"  # seg0 applied (load), seg1 missed (only Download)
    assert out["applied_count"] == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_glossary_audit.py -v`
Expected: FAIL `ModuleNotFoundError: No module named 'pipeline.glossary_audit'`

- [ ] **Step 3: Implement** — create `backend/pipeline/glossary_audit.py`:

```python
"""Post-translation glossary application audit (deterministic, inference-based).

No translator instrumentation tells us which glossary entries were honored, so we infer
per segment: a glossary entry {원어: 번역어} is "applied" in a segment when the 원어 occurs
in that segment's SOURCE and the 번역어 occurs in its aligned TRANSLATION. Matching reuses
text_replace (word-boundary for space-separated langs, substring for CJK, case-insensitive)
so it agrees with the bulk-replace/preview features.
"""
from __future__ import annotations

from pipeline.text_replace import ReplaceRule, compile_rule, count_in_text


def audit_glossary(segments: list[dict], glossary: dict[str, str],
                   source_lang: str, target_lang: str) -> list[dict]:
    """segments: build_segments() output ({index, source, translated, source_bbox, block}).
    Returns one record per glossary entry with status + per-occurrence locations."""
    out: list[dict] = []
    for src_term, tgt_term in glossary.items():
        src_pat = compile_rule(ReplaceRule(find=src_term, replace=""), source_lang)
        tgt_pat = compile_rule(ReplaceRule(find=tgt_term, replace=""), target_lang)
        locations: list[dict] = []
        for seg in segments:
            if src_pat and count_in_text(seg["source"], src_pat):
                applied = bool(tgt_pat and count_in_text(seg["translated"], tgt_pat))
                locations.append({
                    "segment_index": seg["index"], "block": seg.get("block"),
                    "source_bbox": seg.get("source_bbox"), "applied": applied,
                })
        source_count = len(locations)
        applied_count = sum(1 for loc in locations if loc["applied"])
        if source_count == 0:
            status = "absent"
        elif applied_count == source_count:
            status = "applied"
        elif applied_count == 0:
            status = "missed"
        else:
            status = "partial"
        out.append({"source": src_term, "target": tgt_term,
                    "source_count": source_count, "applied_count": applied_count,
                    "status": status, "locations": locations})
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_glossary_audit.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/glossary_audit.py backend/tests/test_glossary_audit.py
git commit -m "feat(glossary_audit): per-term applied/missed/absent classification

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: `render_html` highlighting + anchors (+ `compile_terms`)

**Files:**
- Modify: `backend/pipeline/text_replace.py` (add `compile_terms`)
- Modify: `backend/renderers/html_renderer.py`
- Test: `backend/tests/test_render_html_highlight.py`

- [ ] **Step 1: Write the failing test** — create `backend/tests/test_render_html_highlight.py`:

```python
from models import HeadingBlock, ParagraphBlock, SemanticDocument
from pipeline.text_replace import compile_terms
from renderers.html_renderer import render_html


def _doc():
    return SemanticDocument(source_lang="ko", target_lang="en", blocks=[
        HeadingBlock(level=1, text="Rated Load section", source_span_ids=[]),
        ParagraphBlock(text="the load and Download", source_span_ids=[]),
    ])


def test_no_options_is_unchanged_regression():
    html = render_html(_doc())
    assert "<mark" not in html
    assert 'id="blk0"' not in html
    assert "Rated Load section" in html


def test_highlight_and_anchors():
    pat = compile_terms(["Rated Load", "load"], "en")
    html = render_html(_doc(), highlight_pattern=pat, anchor_blocks=True)
    assert '<h1 id="blk0">' in html
    assert '<p id="blk1">' in html
    assert "<mark" in html
    # "Rated Load" highlighted; "load" highlighted but NOT inside "Download"
    assert "<mark class=\"gloss\">Rated Load</mark>" in html
    assert "<mark class=\"gloss\">load</mark>" in html
    assert "Down<mark" not in html  # word boundary respected


def test_compile_terms_empty_is_none():
    assert compile_terms([], "en") is None
    assert compile_terms(["", None], "en") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_render_html_highlight.py -v`
Expected: FAIL — `ImportError: cannot import name 'compile_terms'` (and `render_html` has no `highlight_pattern`).

- [ ] **Step 3a: Add `compile_terms`** — in `backend/pipeline/text_replace.py`, append after `count_in_text`:

```python
def compile_terms(terms, lang: str) -> "re.Pattern | None":
    """One case-insensitive alternation matching ANY of `terms` (longest first so a longer
    term wins over a contained shorter one). Word boundaries for space-separated langs,
    substring for CJK — same rule as compile_rule. Returns None if no usable terms."""
    usable = [t for t in terms if t]
    if not usable:
        return None
    body = "|".join(re.escape(t) for t in sorted(set(usable), key=len, reverse=True))
    if lang in _WORD_BOUNDARY_LANGS:
        body = rf"(?<!\w)(?:{body})(?!\w)"
    return re.compile(body, re.IGNORECASE)
```

- [ ] **Step 3b: Add highlight + anchors to `render_html`** — in `backend/renderers/html_renderer.py`:

Add an import + a `_mark` helper near the top (after the existing imports):

```python
import re


def _mark(text: str, pattern: "re.Pattern | None") -> str:
    """Escape `text`; if a pattern is given, wrap each match in <mark class="gloss">."""
    if not pattern:
        return escape(text)
    out, last = [], 0
    for m in pattern.finditer(text):
        out.append(escape(text[last:m.start()]))
        out.append(f'<mark class="gloss">{escape(m.group(0))}</mark>')
        last = m.end()
    out.append(escape(text[last:]))
    return "".join(out)
```

Change the `render_html` signature and body. Replace the signature line and the block loop:

```python
def render_html(doc: SemanticDocument, asset_url_prefix: str = "assets",
                highlight_pattern: "re.Pattern | None" = None,
                anchor_blocks: bool = False) -> str:
    parts: list[str] = []
    prev_page: int | None = None
    for i, block in enumerate(doc.blocks):
        # Insert a page break whenever the source PDF page changes (not before page 1).
        page = getattr(block, "page", None)
        if page is not None and prev_page is not None and page != prev_page:
            parts.append('<div data-page-break="true"></div>')
        if page is not None:
            prev_page = page

        anchor = f' id="blk{i}"' if anchor_blocks else ""
        if isinstance(block, HeadingBlock):
            lvl = min(max(block.level, 1), 6)
            marker = f'<span class="marker">{escape(block.marker)}</span> ' if block.marker else ""
            parts.append(f"<h{lvl}{anchor}>{marker}{_mark(block.text, highlight_pattern)}</h{lvl}>")
        elif isinstance(block, ListItemBlock):
            indent = block.level * 1.5
            parts.append(
                f'<div class="li"{anchor} style="margin-left:{indent}em">'
                f'<span class="marker">{escape(block.marker)}</span> '
                f"{_mark(block.text, highlight_pattern)}</div>"
            )
        elif isinstance(block, ParagraphBlock):
            parts.append(f"<p{anchor}>{_mark(block.text, highlight_pattern)}</p>")
        elif isinstance(block, TableBlock):
            parts.append(_render_table(block, highlight_pattern, anchor))
        elif isinstance(block, FigureBlock):
            if block.image_ref:
                parts.append(
                    f'<figure{anchor}><img src="{asset_url_prefix}/{escape(block.image_ref)}" '
                    f'alt="figure"/></figure>'
                )
        elif isinstance(block, CaptionBlock):
            parts.append(f'<p class="caption"{anchor}>{_mark(block.text, highlight_pattern)}</p>')
```

Update `_render_table` to accept the pattern + anchor and mark cell text:

```python
def _render_table(block: TableBlock, highlight_pattern: "re.Pattern | None" = None,
                  anchor: str = "") -> str:
    rows_html = []
    for row in block.rows:
        cells = []
        for cell in row:
            tag = "th" if cell.is_header else "td"
            attrs = ""
            if cell.rowspan > 1:
                attrs += f' rowspan="{cell.rowspan}"'
            if cell.colspan > 1:
                attrs += f' colspan="{cell.colspan}"'
            cells.append(f"<{tag}{attrs}>{_mark(cell.text, highlight_pattern)}</{tag}>")
        rows_html.append("<tr>" + "".join(cells) + "</tr>")
    caption = f"<caption>{escape(block.caption)}</caption>" if block.caption else ""
    return f"<table{anchor}>{caption}{''.join(rows_html)}</table>"
```

Add a style rule for the mark inside the `<style>` block (after the `.marker` line):

```python
  mark.gloss {{ background: #fde68a; color: inherit; padding: 0 .05em; border-radius: 2px; }}
```

- [ ] **Step 4: Run to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_render_html_highlight.py backend/tests/test_text_replace.py -v`
Expected: PASS (new highlight tests + existing text_replace tests still green).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/text_replace.py backend/renderers/html_renderer.py backend/tests/test_render_html_highlight.py
git commit -m "feat(render): opt-in term highlight + block anchors; compile_terms helper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `process.py` — report + term update/add/apply

**Files:**
- Modify: `backend/pipeline/process.py` (add after `build_segments`/`retranslate_one_segment`)
- Test: `backend/tests/test_glossary_manage.py`

Relevant existing helpers (already present): `load_source_model`, `build_segments`, `load_doc_glossary`, `save_doc_glossary`, `bulk_replace`, `retranslate_one_segment`, `iter_text_fields` (imported at top of process.py).

- [ ] **Step 1: Write the failing test** — create `backend/tests/test_glossary_manage.py`:

```python
import os

from docx import Document

from models import ParagraphBlock, SemanticDocument
from pipeline import process
from pipeline.translator import MockTranslator
from renderers.docx_renderer import render_docx


def _seed(tmp_path, source_text, translated_text, glossary):
    storage, doc_id, lang = str(tmp_path), "doc1", "en"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    process._write_json(os.path.join(base, "source.json"),
        SemanticDocument(source_lang="ko",
            blocks=[ParagraphBlock(text=source_text, source_span_ids=[])]).model_dump())
    tr = SemanticDocument(source_lang="ko", target_lang=lang,
        blocks=[ParagraphBlock(text=translated_text, source_span_ids=[])])
    process.save_translated_model(storage, doc_id, lang, tr)
    render_docx(tr, process.output_docx_path(storage, doc_id, lang),
                process.asset_dir(storage, doc_id))
    process.save_doc_glossary(storage, doc_id, glossary)
    return storage, doc_id, lang


def test_glossary_report_classifies(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, "정격하중 점검", "Rated Load check",
                                  {"정격하중": "Rated Load", "캐빈": "Cabin"})
    terms = {t["source"]: t for t in process.glossary_report(storage, doc_id, lang)}
    assert terms["정격하중"]["status"] == "applied"
    assert terms["캐빈"]["status"] == "absent"


def test_glossary_report_empty_when_no_glossary(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, "x", "y", {})
    assert process.glossary_report(storage, doc_id, lang) == []


def test_update_glossary_term_replaces_and_saves(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, "정격하중", "Rated Load here",
                                  {"정격하중": "Rated Load"})
    res = process.update_glossary_term(storage, doc_id, "정격하중", "Load Rating", lang)
    assert res["replaced"] == 1
    # glossary.json updated
    assert process.load_doc_glossary(storage, doc_id)["정격하중"] == "Load Rating"
    # translation unified
    assert process.get_translated_model(storage, doc_id, lang).blocks[0].text == "Load Rating here"


def test_add_glossary_term_registers(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, "캐빈", "Cabin", {})
    process.add_glossary_term(storage, doc_id, "캐빈", "Cab")
    assert process.load_doc_glossary(storage, doc_id) == {"캐빈": "Cab"}


def test_apply_glossary_term_retranslates_affected(tmp_path):
    storage, doc_id, lang = _seed(tmp_path, "캐빈", "old", {"캐빈": "Cab"})
    res = process.apply_glossary_term(storage, doc_id, "캐빈", lang,
                                      translator=MockTranslator())
    assert res["retranslated"] == 1
    # MockTranslator applies glossary 캐빈→Cab → translation now contains "Cab"
    assert "Cab" in process.get_translated_model(storage, doc_id, lang).blocks[0].text
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_glossary_manage.py -v`
Expected: FAIL `AttributeError: module 'pipeline.process' has no attribute 'glossary_report'`

- [ ] **Step 3: Implement** — in `backend/pipeline/process.py`, add after `retranslate_one_segment`:

```python
def glossary_report(storage_dir: str, doc_id: str, target_lang: str) -> list[dict]:
    """Per-term applied/missed audit of the uploaded glossary against the current
    translation. Lazy (no disk writes); [] when no glossary was uploaded."""
    from pipeline import glossary_audit

    glossary = load_doc_glossary(storage_dir, doc_id)
    if not glossary:
        return []
    segments = build_segments(storage_dir, doc_id, target_lang)
    source = load_source_model(storage_dir, doc_id)
    return glossary_audit.audit_glossary(segments, glossary, source.source_lang, target_lang)


def update_glossary_term(storage_dir: str, doc_id: str, source: str,
                         new_target: str, target_lang: str) -> dict:
    """Change an existing glossary entry's 번역어 and unify it in the current translation
    (replace old_target → new_target via bulk_replace). Updates glossary.json."""
    from pipeline.text_replace import ReplaceRule

    glossary = load_doc_glossary(storage_dir, doc_id)
    old_target = glossary.get(source)
    replaced = 0
    if old_target and old_target != new_target:
        res = bulk_replace(storage_dir, doc_id, target_lang,
                           [ReplaceRule(find=old_target, replace=new_target,
                                        match="smart", save=False)])
        replaced = res["total"]
    glossary[source] = new_target
    save_doc_glossary(storage_dir, doc_id, glossary)
    return {"replaced": replaced}


def add_glossary_term(storage_dir: str, doc_id: str, source: str, target: str) -> None:
    """Register a new glossary entry (applies on next (re)translation, or via
    apply_glossary_term)."""
    glossary = load_doc_glossary(storage_dir, doc_id)
    glossary[source] = target
    save_doc_glossary(storage_dir, doc_id, glossary)


def apply_glossary_term(storage_dir: str, doc_id: str, source_term: str,
                        target_lang: str, translator=None) -> dict:
    """Re-translate the segments whose SOURCE contains source_term, so the (now updated)
    glossary is applied. Returns {retranslated: n}."""
    from pipeline.text_replace import ReplaceRule, compile_rule, count_in_text

    source = load_source_model(storage_dir, doc_id)
    pat = compile_rule(ReplaceRule(find=source_term, replace=""), source.source_lang)
    n = 0
    if pat:
        for idx, (get, _set) in enumerate(iter_text_fields(source)):
            if count_in_text(get(), pat):
                retranslate_one_segment(storage_dir, doc_id, target_lang, idx, "",
                                        translator=translator)
                n += 1
    return {"retranslated": n}
```

- [ ] **Step 4: Run to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_glossary_manage.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Full suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/process.py backend/tests/test_glossary_manage.py
git commit -m "feat(process): glossary_report + update/add/apply term (reuse bulk_replace/retranslate)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: `main.py` — report, preview, term endpoints

**Files:**
- Modify: `backend/main.py`
- Test: `backend/tests/test_glossary_manage_api.py`

- [ ] **Step 1: Write the failing test** — create `backend/tests/test_glossary_manage_api.py`:

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


def _seed(tmp_path, glossary):
    storage, doc_id, lang = str(tmp_path), "doc1", "en"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(process.asset_dir(storage, doc_id), exist_ok=True)
    process._write_json(os.path.join(base, "source.json"),
        SemanticDocument(source_lang="ko",
            blocks=[ParagraphBlock(text="정격하중 점검", source_span_ids=[])]).model_dump())
    tr = SemanticDocument(source_lang="ko", target_lang=lang,
        blocks=[ParagraphBlock(text="Rated Load check", source_span_ids=[])])
    process.save_translated_model(storage, doc_id, lang, tr)
    render_docx(tr, process.output_docx_path(storage, doc_id, lang),
                process.asset_dir(storage, doc_id))
    process.save_doc_glossary(storage, doc_id, glossary)
    return doc_id, lang


def test_glossary_report_route(client, tmp_path):
    doc_id, lang = _seed(tmp_path, {"정격하중": "Rated Load"})
    r = client.get(f"/api/documents/{doc_id}/glossary-report?lang={lang}")
    assert r.status_code == 200
    assert r.json()["terms"][0]["status"] == "applied"


def test_preview_route_highlights(client, tmp_path):
    doc_id, lang = _seed(tmp_path, {"정격하중": "Rated Load"})
    r = client.get(f"/api/documents/{doc_id}/preview?lang={lang}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "<mark" in r.text and "Rated Load" in r.text


def test_update_term_route(client, tmp_path):
    doc_id, lang = _seed(tmp_path, {"정격하중": "Rated Load"})
    r = client.post(f"/api/documents/{doc_id}/glossary/term?lang={lang}",
                    json={"source": "정격하중", "target": "Load Rating"})
    assert r.status_code == 200
    assert r.json()["status"] == "updated"
    assert process.load_doc_glossary(str(tmp_path), doc_id)["정격하중"] == "Load Rating"


def test_add_term_route(client, tmp_path):
    doc_id, lang = _seed(tmp_path, {})
    r = client.post(f"/api/documents/{doc_id}/glossary/term?lang={lang}",
                    json={"source": "캐빈", "target": "Cabin"})
    assert r.status_code == 200
    assert r.json()["status"] == "added"
    assert process.load_doc_glossary(str(tmp_path), doc_id) == {"캐빈": "Cabin"}


def test_add_term_apply_retranslates_and_rerenders(client, tmp_path):
    doc_id, lang = _seed(tmp_path, {})  # source paragraph contains "정격하중"
    r = client.post(f"/api/documents/{doc_id}/glossary/term?lang={lang}",
                    json={"source": "정격하중", "target": "Cap-X", "apply": True})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "added" and body["retranslated"] == 1
    # MockTranslator applies the new (user-wins) glossary → translation contains "Cap-X"
    assert "Cap-X" in process.get_translated_model(str(tmp_path), doc_id, lang).blocks[0].text
```

- [ ] **Step 2: Run to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_glossary_manage_api.py -v`
Expected: FAIL (routes 404).

- [ ] **Step 3: Implement** — in `backend/main.py`:

Add a body model after the existing `ReplaceRequest` class:

```python
class GlossaryTermBody(BaseModel):
    source: str
    target: str
    apply: bool = False
```

Add three endpoints after the `replace_apply` endpoint:

```python
@app.get("/api/documents/{doc_id}/glossary-report")
def glossary_report(doc_id: str, lang: str):
    """Per-term applied/missed audit of the uploaded glossary vs the current translation."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    try:
        return {"lang": lang, "terms": process.glossary_report(STORAGE, doc_id, lang)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Glossary report failed: {e}")


@app.get("/api/documents/{doc_id}/preview", response_class=HTMLResponse)
def preview(doc_id: str, lang: str | None = None):
    """Read-only translated HTML with uploaded-glossary 번역어 highlighted + block anchors
    (for the 용어 관리 view; the OnlyOffice editor can't be scripted in CE)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    from pipeline.text_replace import compile_terms

    doc = process.get_export_model(STORAGE, doc_id, lang)
    eff_lang = lang or doc.source_lang
    glossary = process.load_doc_glossary(STORAGE, doc_id)
    pattern = compile_terms(list(glossary.values()), eff_lang) if glossary else None
    return render_html(doc, asset_url_prefix=_assets_prefix(doc_id),
                       highlight_pattern=pattern, anchor_blocks=True)


@app.post("/api/documents/{doc_id}/glossary/term")
def glossary_term(doc_id: str, body: GlossaryTermBody, lang: str):
    """Edit an existing glossary term (unifies the current translation) or add a new one
    (optionally re-translating the affected segments to apply it now)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    source, target = body.source.strip(), body.target.strip()
    if not source or not target:
        raise HTTPException(422, "source and target are required")
    _ensure_docx(doc_id, lang)  # update path edits the .docx via bulk_replace — ensure it exists
    try:
        if source in process.load_doc_glossary(STORAGE, doc_id):
            res = process.update_glossary_term(STORAGE, doc_id, source, target, lang)
            return {"status": "updated", **res}  # bulk_replace already edited the .docx
        process.add_glossary_term(STORAGE, doc_id, source, target)
        if body.apply:
            res = process.apply_glossary_term(STORAGE, doc_id, source, lang)
            # apply re-translates into translated.{lang}.json ONLY → re-render the docx so the
            # OnlyOffice editor reflects it (the 용어 관리 preview is model-based, already fresh).
            out = process.output_docx_path(STORAGE, doc_id, lang)
            render_docx(process.get_export_model(STORAGE, doc_id, lang), out,
                        process.asset_dir(STORAGE, doc_id))
            return {"status": "added", **res}
        return {"status": "added"}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Glossary term update failed: {e}")
```

(`render_html`, `render_docx`, and `HTMLResponse` are already imported in main.py — `from renderers.html_renderer import render_html`, `from renderers.docx_renderer import render_docx`, `from fastapi.responses import ... HTMLResponse`.)

- [ ] **Step 4: Run to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_glossary_manage_api.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Full suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_glossary_manage_api.py
git commit -m "feat(api): /glossary-report, /preview, /glossary/term endpoints

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: frontend types + API client

**Files:**
- Modify: `frontend/src/lib/types.ts` (append)
- Modify: `frontend/src/lib/api.ts` (import + methods)

- [ ] **Step 1: Add types** — append to `frontend/src/lib/types.ts`:

```typescript
// GET /api/documents/{id}/glossary-report?lang= — 용어 관리 적용 추적.
export type GlossaryTermStatus = "applied" | "partial" | "missed" | "absent";

export interface GlossaryLocation {
  segment_index: number;
  block: number | null; // doc.blocks index → preview anchor "blk{n}" / editor bookmark
  source_bbox: SourceBBox | null; // PDF only; null for HWP/office
  applied: boolean;
}

export interface GlossaryTermReport {
  source: string;
  target: string;
  source_count: number;
  applied_count: number;
  status: GlossaryTermStatus;
  locations: GlossaryLocation[];
}

export interface GlossaryReportResponse {
  lang: string;
  terms: GlossaryTermReport[];
}
```

- [ ] **Step 2: Add API methods** — in `frontend/src/lib/api.ts`, extend the type import block to include `GlossaryReportResponse`, and add these methods inside the `api` object (after `replaceApply`, before `downloadUrl`):

```typescript
  // 용어 관리: per-term applied/missed audit of the uploaded glossary.
  glossaryReport: (docId: string, lang: string) =>
    fetch(`/api/documents/${docId}/glossary-report?lang=${encodeURIComponent(lang)}`).then(
      (r) => asJson<GlossaryReportResponse>(r),
    ),

  // Read-only translated HTML (glossary terms highlighted) — iframe src for the preview.
  previewUrl: (docId: string, lang: string) =>
    `/api/documents/${docId}/preview?lang=${encodeURIComponent(lang)}`,

  // Edit an existing term (unifies translation) or add a new one (apply=re-translate now).
  saveGlossaryTerm: (
    docId: string,
    lang: string,
    body: { source: string; target: string; apply?: boolean },
  ) =>
    fetch(`/api/documents/${docId}/glossary/term?lang=${encodeURIComponent(lang)}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => asJson<{ status: string; replaced?: number; retranslated?: number }>(r)),
```

The import block becomes:

```typescript
import type {
  EditorConfigResponse,
  GlossaryReportResponse,
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

- [ ] **Step 3: Build**

Run: `cd frontend; npm run build`
Expected: build succeeds, no type errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts
git commit -m "feat(web): glossary report types + report/preview/term API methods

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: `TranslatedPreview` component

**Files:**
- Create: `frontend/src/components/TranslatedPreview.tsx`

- [ ] **Step 1: Create the component** — `frontend/src/components/TranslatedPreview.tsx`:

```tsx
"use client";

import { useEffect, useRef } from "react";
import { api } from "@/lib/api";

// Read-only highlighted translation (served HTML) for 용어 관리 mode, since the OnlyOffice
// editor can't be scripted in Community Edition. `reloadKey` bumps force a refresh after an
// apply; `jumpBlock` scrolls the iframe to a block anchor without a full reload.
export function TranslatedPreview({
  docId,
  lang,
  reloadKey,
  jumpBlock,
}: {
  docId: string;
  lang: string;
  reloadKey: number;
  jumpBlock: number | null;
}) {
  const ref = useRef<HTMLIFrameElement | null>(null);

  // Same-origin (/api proxy) → we can set the inner hash to scroll to #blk{n}.
  useEffect(() => {
    if (jumpBlock == null) return;
    const win = ref.current?.contentWindow;
    if (win) win.location.hash = `blk${jumpBlock}`;
  }, [jumpBlock, reloadKey]);

  return (
    <iframe
      ref={ref}
      key={reloadKey}
      src={`${api.previewUrl(docId, lang)}&v=${reloadKey}`}
      title="번역본 미리보기 (용어 하이라이트)"
      className="sheet min-h-0 w-full flex-1 bg-white"
    />
  );
}
```

- [ ] **Step 2: Build**

Run: `cd frontend; npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/TranslatedPreview.tsx
git commit -m "feat(web): TranslatedPreview (highlighted read-only translation iframe)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: `GlossaryManagePanel` component

**Files:**
- Create: `frontend/src/components/GlossaryManagePanel.tsx`

This panel: loads the report, lists terms (inline-editable 번역어, status dot, click→jump), adds terms, and embeds the existing `BulkReplacePanel` as a collapsible 직접 교체 section. All applies call `onChanged()` so the parent refreshes preview + report + editor.

- [ ] **Step 1: Create the component** — `frontend/src/components/GlossaryManagePanel.tsx`:

```tsx
"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { GlossaryLocation, GlossaryTermReport } from "@/lib/types";
import { BulkReplacePanel } from "@/components/BulkReplacePanel";

const DOT: Record<string, string> = {
  applied: "🟢",
  partial: "🟡",
  missed: "🔴",
  absent: "⚪",
};

export function GlossaryManagePanel({
  docId,
  lang,
  reloadKey,
  onJump,
  onChanged,
}: {
  docId: string;
  lang: string;
  reloadKey: number; // bump to re-fetch the report after an apply
  onJump: (loc: GlossaryLocation) => void;
  onChanged: () => void; // after any glossary/replace apply: refresh preview + editor + report
}) {
  const [terms, setTerms] = useState<GlossaryTermReport[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({}); // source → edited target
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [newSource, setNewSource] = useState("");
  const [newTarget, setNewTarget] = useState("");
  const [showReplace, setShowReplace] = useState(false);

  useEffect(() => {
    api
      .glossaryReport(docId, lang)
      .then((r) => setTerms(r.terms))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [docId, lang, reloadKey]);

  async function saveEdit(source: string) {
    const target = (drafts[source] ?? "").trim();
    if (!target) return;
    setBusy(source);
    setNote(null);
    try {
      await api.saveGlossaryTerm(docId, lang, { source, target });
      onChanged();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function addTerm(applyNow: boolean) {
    const source = newSource.trim();
    const target = newTarget.trim();
    if (!source || !target) return;
    setBusy("__add__");
    setNote(null);
    try {
      await api.saveGlossaryTerm(docId, lang, { source, target, apply: applyNow });
      setNewSource("");
      setNewTarget("");
      onChanged();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (error) {
    return (
      <p className="mono p-6 text-sm" style={{ color: "var(--vermillion-deep)" }}>
        용어집 오류 — {error}
      </p>
    );
  }

  const summary = terms.reduce(
    (a, t) => {
      a[t.status] = (a[t.status] ?? 0) + 1;
      return a;
    },
    {} as Record<string, number>,
  );

  return (
    <div className="min-h-0 flex-1 overflow-auto px-4 py-4">
      <h2 className="slug mb-2 px-1">용어 관리 — 업로드 용어집</h2>
      {terms.length > 0 && (
        <p className="slug mb-3 px-1">
          적용 {summary.applied ?? 0} · 부분 {summary.partial ?? 0} · 누락 {summary.missed ?? 0} · 미등장 {summary.absent ?? 0}
        </p>
      )}

      {terms.length === 0 && (
        <p className="slug mb-3 px-1">업로드된 용어집이 없습니다. 아래에서 용어를 추가할 수 있습니다.</p>
      )}

      <ul className="space-y-2">
        {terms.map((t) => (
          <li key={t.source} className="rounded border border-rule p-2">
            <div className="flex items-center gap-2">
              <span title={t.status}>{DOT[t.status] ?? "⚪"}</span>
              <span className="mono flex-1 text-xs opacity-70">{t.source}</span>
              <span className="slug">
                {t.applied_count}/{t.source_count}
              </span>
            </div>
            <div className="mt-1.5 flex items-center gap-1.5">
              <input
                value={drafts[t.source] ?? t.target}
                onChange={(e) => setDrafts((d) => ({ ...d, [t.source]: e.target.value }))}
                aria-label={`${t.source} 번역어`}
                className="mono w-full rounded border border-rule p-1.5 text-sm"
              />
              <button
                className="btn-ink"
                disabled={busy === t.source}
                onClick={() => saveEdit(t.source)}
              >
                {busy === t.source ? "…" : "수정"}
              </button>
            </div>
            {t.locations.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1">
                {t.locations.map((loc, i) => (
                  <button
                    key={i}
                    onClick={() => onJump(loc)}
                    className={`mono rounded px-1.5 py-0.5 text-xs ${
                      loc.applied ? "border border-rule" : "btn-vermillion"
                    }`}
                    title={loc.applied ? "적용 위치" : "누락 위치"}
                  >
                    {loc.applied ? "●" : "○"} #{loc.segment_index}
                  </button>
                ))}
              </div>
            )}
          </li>
        ))}
      </ul>

      <div className="mt-4 rounded border border-rule p-2">
        <p className="slug mb-1.5">+ 용어 추가</p>
        <div className="flex items-center gap-1.5">
          <input
            value={newSource}
            onChange={(e) => setNewSource(e.target.value)}
            placeholder="원어"
            aria-label="추가할 원어"
            className="mono w-full rounded border border-rule p-1.5 text-sm"
          />
          <span className="opacity-50">→</span>
          <input
            value={newTarget}
            onChange={(e) => setNewTarget(e.target.value)}
            placeholder="번역어"
            aria-label="추가할 번역어"
            className="mono w-full rounded border border-rule p-1.5 text-sm"
          />
        </div>
        <div className="mt-1.5 flex items-center gap-2">
          <button className="btn-ink" disabled={busy === "__add__"} onClick={() => addTerm(false)}>
            등록
          </button>
          <button
            className="btn-vermillion"
            disabled={busy === "__add__"}
            onClick={() => addTerm(true)}
          >
            등록 + 지금 적용(재번역)
          </button>
        </div>
      </div>

      <div className="mt-4">
        <button className="mono text-xs underline" onClick={() => setShowReplace((s) => !s)}>
          {showReplace ? "직접 교체 닫기" : "직접 교체 (임의 단어)"}
        </button>
        {showReplace && (
          <div className="mt-2 border-t border-rule pt-2">
            <BulkReplacePanel docId={docId} lang={lang} onApplied={onChanged} />
          </div>
        )}
      </div>

      {note && <p className="slug mt-2">{note}</p>}
    </div>
  );
}
```

- [ ] **Step 2: Build**

Run: `cd frontend; npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/GlossaryManagePanel.tsx
git commit -m "feat(web): GlossaryManagePanel (term audit list, inline edit, add, direct-replace section)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: viewer wiring — `replace` mode → `manage`, center swap, source multi-highlight

**Files:**
- Modify: `frontend/src/app/c/[id]/page.tsx`

- [ ] **Step 1: Imports + state** — In `frontend/src/app/c/[id]/page.tsx`:

Update the type import (line 7) to add `GlossaryTermReport`:

```tsx
import type { GlossaryTermReport, ResultResponse, Segment } from "@/lib/types";
```

Replace the panel imports (lines 9-10) with (drop `BulkReplacePanel` — it's now only used inside `GlossaryManagePanel`):

```tsx
import { RetranslatePanel } from "@/components/RetranslatePanel";
import { GlossaryManagePanel } from "@/components/GlossaryManagePanel";
import { TranslatedPreview } from "@/components/TranslatedPreview";
```

Replace the `mode`/`activeSeg` state (lines 25-26) with (add report-refresh + glossary-highlight state):

```tsx
  const [mode, setMode] = useState<"edit" | "retranslate" | "manage">("edit");
  const [activeSeg, setActiveSeg] = useState<Segment | null>(null);
  // 용어 관리: bump to refresh preview + re-fetch the glossary report after an apply.
  const [manageKey, setManageKey] = useState(0);
  // 용어 관리: the block to scroll the highlighted preview to.
  const [jumpBlock, setJumpBlock] = useState<number | null>(null);
  // 용어 관리: glossary report so the original (left) pane can outline terms 🟢/🔴.
  const [glossaryReport, setGlossaryReport] = useState<GlossaryTermReport[]>([]);
```

- [ ] **Step 2: Fetch the report for source highlight (manage mode)** — add this effect after the existing `activeSeg` effect (after line 47):

```tsx
  // 용어 관리: load the report so the original (left) pane can outline glossary terms.
  useEffect(() => {
    if (mode !== "manage") return;
    api
      .glossaryReport(id, lang)
      .then((r) => setGlossaryReport(r.terms))
      .catch(() => setGlossaryReport([]));
  }, [mode, id, lang, manageKey]);
```

- [ ] **Step 3: Rename the toggle button** — replace the `용어 일괄변경` button (lines 134-139) with:

```tsx
          <button
            className={mode === "manage" ? "btn-vermillion" : "btn-ink"}
            onClick={() => setMode((m) => (m === "manage" ? "edit" : "manage"))}
          >
            {mode === "manage" ? "용어 관리 ●" : "용어 관리"}
          </button>
```

- [ ] **Step 4: Source-pane multi-highlight (manage mode)** — replace the single-highlight block inside the page-image loop (lines 181-196) with one that draws the retranslate highlight AND, in manage mode, every glossary location on this page:

```tsx
                    {mode === "retranslate" &&
                      activeSeg?.source_bbox &&
                      activeSeg.source_bbox.page === i && (
                        <div
                          id="active-source-highlight"
                          className="pointer-events-none absolute"
                          style={{
                            left: `${activeSeg.source_bbox.x * 100}%`,
                            top: `${activeSeg.source_bbox.y * 100}%`,
                            width: `${activeSeg.source_bbox.w * 100}%`,
                            height: `${activeSeg.source_bbox.h * 100}%`,
                            background: "rgba(214, 91, 56, 0.28)",
                            outline: "1.5px solid var(--vermillion-deep)",
                          }}
                        />
                      )}
                    {mode === "manage" &&
                      glossaryReport.flatMap((t) =>
                        t.locations
                          .filter((loc) => loc.source_bbox && loc.source_bbox.page === i)
                          .map((loc, k) => (
                            <div
                              key={`${t.source}-${k}`}
                              className="pointer-events-none absolute"
                              style={{
                                left: `${loc.source_bbox!.x * 100}%`,
                                top: `${loc.source_bbox!.y * 100}%`,
                                width: `${loc.source_bbox!.w * 100}%`,
                                height: `${loc.source_bbox!.h * 100}%`,
                                background: loc.applied
                                  ? "rgba(22, 163, 74, 0.22)"
                                  : "rgba(214, 91, 56, 0.28)",
                                outline: loc.applied
                                  ? "1.5px solid rgba(22,163,74,0.9)"
                                  : "1.5px solid var(--vermillion-deep)",
                              }}
                            />
                          )),
                      )}
```

- [ ] **Step 5: Center swap + right panel** — replace the center editor block (lines 207-216) and the right-panel block (lines 218-240).

Center: render the preview in manage mode, else the editor:

```tsx
        {/* middle: editor (edit/retranslate) or highlighted preview (manage) */}
        <div className="relative flex flex-1 min-h-0 flex-col">
          {mode === "manage" ? (
            <TranslatedPreview docId={doc.doc_id} lang={lang} reloadKey={manageKey} jumpBlock={jumpBlock} />
          ) : (
            <OnlyOfficeEditor
              docId={doc.doc_id}
              lang={lang}
              onConnector={setConnector}
              reloadKey={editorReloadKey}
              bookmark={editorBookmark}
            />
          )}
        </div>

        {/* far right: toggled drawer */}
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
              <GlossaryManagePanel
                docId={doc.doc_id}
                lang={lang}
                reloadKey={manageKey}
                onJump={(loc) => {
                  // primary "where": scroll the highlighted preview to the block. (The left
                  // pane already outlines every glossary location, so no per-click scroll.)
                  if (loc.block != null) setJumpBlock(loc.block);
                }}
                onChanged={() => {
                  // an apply changed the .docx + model → refresh preview & report, and
                  // mark the editor stale so it reloads when the user returns to 편집.
                  setManageKey((k) => k + 1);
                  setEditorReloadKey((k) => k + 1);
                }}
              />
            )}
          </div>
        )}
```

(The existing `activeSeg`/`mode` scroll effect stays unchanged — it serves 재번역 mode only. In 용어 관리 mode every glossary location is already outlined in the left pane, and clicking a chip scrolls the center preview; a per-click left-pane scroll is out of v1 scope.)

- [ ] **Step 6: Build**

Run: `cd frontend; npm run build`
Expected: build succeeds, no type/lint errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/c/[id]/page.tsx
git commit -m "feat(web): 용어 관리 viewer mode — preview swap, glossary source highlight, jump

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final Verification

- [ ] **Backend:** `backend/.venv/Scripts/python.exe -m pytest backend/tests -q` — all green (new suites + existing).
- [ ] **Frontend:** `cd frontend; npm run build` — clean.
- [ ] **Manual (prod build; dev HMR broken; Document Server up):**
  1. Upload a doc **with a glossary CSV**, open the viewer.
  2. Toggle **용어 관리** → center becomes the highlighted preview (번역어 🟡 marked); left original outlines glossary terms 🟢적용/🔴누락; right panel lists terms with status dots + 적용/총 counts.
  3. Click a term's location chip → preview scrolls to that block (left pane best-effort).
  4. Edit a term's 번역어 → 수정 → preview + list refresh, term unified; switch to 편집 → OnlyOffice shows the change.
  5. **+ 용어 추가** (등록 / 등록+지금 적용) → registered; with apply, affected sentences re-translated and reflected.
  6. Open **직접 교체** sub-section → find→replace still works (apply refreshes preview).
  7. Upload a doc **without** a glossary → 용어 관리 shows "업로드된 용어집이 없습니다" + add still works. Word upload → no left highlight, list status only.
- [ ] **Branch wrap-up:** use **superpowers:finishing-a-development-branch**.

---

## Notes / Risks

- **CE constraint:** OnlyOffice can't be scripted in Community Edition, so the translated highlight lives in a separate read-only HTML preview shown only in 용어 관리 mode; editing happens in 편집 mode. The preview is model-based, so a manual OnlyOffice edit not yet mirrored to the model won't appear there (acceptable role split).
- **Source highlight is block-level** (`source_bbox`), colored by applied/missed; word-precise boxes are out of v1 scope (the color is per-segment, which word boxes can't cheaply carry).
- **`update_glossary_term`** only unifies occurrences where the *old* 번역어 is present (applied segments); missed segments stay 🔴 until re-translated. `apply_glossary_term` (add → 지금 적용) re-translates affected segments and can cost multiple API calls — hence it's an explicit button.
- **Matching is inference** (false positive/negative possible); the UI shows status as a dot/indicator, not a guarantee.
- This branch reworks the just-merged `용어 일괄변경` mode (`replace` → `manage`); `BulkReplacePanel` is reused unchanged, embedded as the 직접 교체 sub-section.
