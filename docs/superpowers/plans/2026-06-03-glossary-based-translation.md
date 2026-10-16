# 용어집 기반 번역 (Glossary-Based Translation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let a user upload a per-document glossary CSV on the upload screen (with a downloadable template); the document's translation merges it over the built-in domain glossary (user wins on conflict).

**Architecture:** A new `parse_glossary_csv` + an `extra_glossary` merge in the translator; per-doc glossary persisted at `storage/{doc_id}/glossary.json` and loaded by the process layer into both `translate_document` (full translate) and `translate_segment` (re-translate). The upload endpoint parses + stores the CSV and returns the term count; a template endpoint serves a fill-in CSV. The frontend adds an optional glossary file input + a "download template" link.

**Tech Stack:** Python / FastAPI / `csv` module (backend); Next.js / TypeScript (frontend).

**Branch:** `glossarybasedtranslation2026-06-03` (already checked out).

**Spec:** [docs/superpowers/specs/2026-06-03-glossary-based-translation-design.md](../specs/2026-06-03-glossary-based-translation-design.md)

**Backend test runner:** `cd backend; ./.venv/Scripts/python.exe -m pytest ...` (Windows venv). Frontend has no JS test runner → verify with `cd frontend; npm run build`.

**Commit trailer (every commit):**
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 1: Translator — CSV parse + glossary merge

**Files:**
- Modify: `backend/pipeline/translator.py`
- Test: `backend/tests/test_glossary.py`

- [ ] **Step 1: Write the failing test** — create `backend/tests/test_glossary.py`:
```python
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
```

- [ ] **Step 2: Run it — expect failure**

Run: `cd backend; ./.venv/Scripts/python.exe -m pytest tests/test_glossary.py -q`
Expected: FAIL (`cannot import name 'parse_glossary_csv'`).

- [ ] **Step 3: Implement** — in `backend/pipeline/translator.py`:

Add `import csv` and `import io` to the imports at the top (next to the existing `import json`, `import re`).

Add the parser + a header constant after `CHUNK_SIZE = 80` (≈L47):
```python
# Cells that look like a header row (skipped when first row of an uploaded glossary CSV).
_GLOSSARY_HEADER = {
    "원어", "번역어", "원문", "번역", "source", "target", "term", "translation",
    "source term", "target term",
}


def parse_glossary_csv(text: str) -> dict[str, str]:
    """Parse a user glossary CSV ('source,target' per row) into {source: target}. Skips
    blank rows, rows with < 2 columns, and an optional header row. Later rows win on a
    duplicate source term. Values are trimmed."""
    out: dict[str, str] = {}
    for ri, row in enumerate(csv.reader(io.StringIO(text))):
        if len(row) < 2:
            continue
        src, tgt = row[0].strip(), row[1].strip()
        if not src or not tgt:
            continue
        if ri == 0 and (src.lower() in _GLOSSARY_HEADER or tgt.lower() in _GLOSSARY_HEADER):
            continue  # header row
        out[src] = tgt
    return out
```

In `translate_document` (≈L50): add an `extra_glossary` parameter and merge it over the built-in (user wins). Change the signature:
```python
def translate_document(
    doc: SemanticDocument,
    target_lang: str,
    source_lang: str | None = None,
    translator=None,
    on_progress: Optional[ProgressFn] = None,
    extra_glossary: dict | None = None,
) -> SemanticDocument:
```
and change the glossary line inside the `if payload:` block from
`glossary = load_glossary(source_lang, target_lang)` to:
```python
        glossary = {**load_glossary(source_lang, target_lang), **(extra_glossary or {})}
```

In `translate_segment` (≈L… after `build_guidance`): add `extra_glossary` and merge. Change the signature:
```python
def translate_segment(
    source_text: str,
    source_lang: str,
    target_lang: str,
    guidance: str = "",
    translator=None,
    extra_glossary: dict | None = None,
) -> str:
```
and change `glossary = load_glossary(source_lang, target_lang)` to:
```python
    glossary = {**load_glossary(source_lang, target_lang), **(extra_glossary or {})}
```

- [ ] **Step 4: Run tests — expect pass**

Run: `cd backend; ./.venv/Scripts/python.exe -m pytest tests/test_glossary.py -q` → 4 passed.

- [ ] **Step 5: Full suite** — `cd backend; ./.venv/Scripts/python.exe -m pytest -q` → all pass (was 103; +4 → 107).

- [ ] **Step 6: Commit**
```bash
git add backend/pipeline/translator.py backend/tests/test_glossary.py
git commit -m "feat(translator): parse_glossary_csv + extra_glossary merge (user over built-in)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Process — per-doc glossary load/save + wiring

**Files:**
- Modify: `backend/pipeline/process.py`
- Test: `backend/tests/test_doc_glossary.py`

- [ ] **Step 1: Write the failing test** — create `backend/tests/test_doc_glossary.py`:
```python
import json
import os

from models import ParagraphBlock, SemanticDocument
from pipeline import process
from pipeline.translator import MockTranslator


def _seed(tmp_path, glossary=None):
    storage, doc_id = str(tmp_path), "doc1"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(base, exist_ok=True)
    doc = SemanticDocument(
        source_lang="ko", blocks=[ParagraphBlock(text="정격하중", source_span_ids=[])]
    )
    with open(os.path.join(base, "source.json"), "w", encoding="utf-8") as fh:
        json.dump(doc.model_dump(), fh, ensure_ascii=False)
    if glossary is not None:
        process.save_doc_glossary(storage, doc_id, glossary)
    return storage, doc_id


def test_save_and_load_doc_glossary(tmp_path):
    storage, doc_id = _seed(tmp_path, {"정격하중": "RATED-X"})
    assert process.load_doc_glossary(storage, doc_id) == {"정격하중": "RATED-X"}


def test_load_doc_glossary_missing_is_empty(tmp_path):
    storage, doc_id = _seed(tmp_path)
    assert process.load_doc_glossary(storage, doc_id) == {}


def test_get_translated_model_applies_doc_glossary(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)  # → MockTranslator offline
    storage, doc_id = _seed(tmp_path, {"정격하중": "RATED-LOAD-X"})
    model = process.get_translated_model(storage, doc_id, "en")
    assert "RATED-LOAD-X" in model.blocks[0].text
```

- [ ] **Step 2: Run it — expect failure**

Run: `cd backend; ./.venv/Scripts/python.exe -m pytest tests/test_doc_glossary.py -q`
Expected: FAIL (`module 'pipeline.process' has no attribute 'save_doc_glossary'`).

- [ ] **Step 3: Implement** — in `backend/pipeline/process.py`:

Add helpers near `save_translated_model` (after it):
```python
def glossary_path(storage_dir: str, doc_id: str) -> str:
    return os.path.join(doc_dir(storage_dir, doc_id), "glossary.json")


def save_doc_glossary(storage_dir: str, doc_id: str, mapping: dict) -> None:
    """Persist the per-document user glossary ({source: target})."""
    _write_json(glossary_path(storage_dir, doc_id), mapping)


def load_doc_glossary(storage_dir: str, doc_id: str) -> dict:
    """The per-document user glossary, or {} if none was uploaded."""
    path = glossary_path(storage_dir, doc_id)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}
```

In `get_translated_model`, change the translate call from
`translated = translate_document(source, target_lang, on_progress=on_progress)` to:
```python
    translated = translate_document(
        source, target_lang, on_progress=on_progress,
        extra_glossary=load_doc_glossary(storage_dir, doc_id),
    )
```

In `retranslate_one_segment`, change the `translate_segment(...)` call to pass the doc glossary:
```python
    new_text = translate_segment(
        src_text, source.source_lang, target_lang, guidance, translator=translator,
        extra_glossary=load_doc_glossary(storage_dir, doc_id),
    )
```

- [ ] **Step 4: Run tests — expect pass**

Run: `cd backend; ./.venv/Scripts/python.exe -m pytest tests/test_doc_glossary.py -q` → 3 passed.

- [ ] **Step 5: Full suite** — `cd backend; ./.venv/Scripts/python.exe -m pytest -q` → all pass (+3 → 110).

- [ ] **Step 6: Commit**
```bash
git add backend/pipeline/process.py backend/tests/test_doc_glossary.py
git commit -m "feat(process): per-doc glossary load/save, wired into translate + retranslate

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: API — upload accepts glossary; template endpoint

**Files:**
- Modify: `backend/main.py`
- Test: `backend/tests/test_glossary_api.py`

- [ ] **Step 1: Write the failing test** — create `backend/tests/test_glossary_api.py`:
```python
import os

import pytest
from fastapi.testclient import TestClient

import main
from pipeline import process


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    return TestClient(main.app)


def test_glossary_template_download(client):
    r = client.get("/api/glossary/template")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers.get("content-disposition", "")
    body = r.content.decode("utf-8-sig")
    assert body.startswith("원어,번역어")
    assert "Aerial Ladder Vehicle" in body


def test_upload_saves_glossary(client, tmp_path):
    csv = "원어,번역어\n정격하중,RATED-X\n캐빈,CAB-X\n"
    r = client.post(
        "/api/documents",
        files={
            "pdf": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf"),
            "glossary": ("g.csv", csv.encode("utf-8-sig"), "text/csv"),
        },
        data={"source_lang": "ko", "target_lang": "en"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["glossary_terms"] == 2
    saved = process.load_doc_glossary(str(tmp_path), body["doc_id"])
    assert saved == {"정격하중": "RATED-X", "캐빈": "CAB-X"}


def test_upload_without_glossary(client):
    r = client.post(
        "/api/documents",
        files={"pdf": ("doc.pdf", b"%PDF-1.4 fake", "application/pdf")},
        data={"source_lang": "ko", "target_lang": "en"},
    )
    assert r.status_code == 200
    assert r.json()["glossary_terms"] == 0
```

- [ ] **Step 2: Run it — expect failure**

Run: `cd backend; ./.venv/Scripts/python.exe -m pytest tests/test_glossary_api.py -q`
Expected: FAIL (404 on template / missing `glossary_terms`).

- [ ] **Step 3: Implement** — in `backend/main.py`:

Add `Response` to the responses import:
```python
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
```
Extend the translator import to include the parser:
```python
from pipeline.translator import build_guidance, parse_glossary_csv
```

Add the template constant + endpoint (near the other endpoints, e.g. after `health`):
```python
_GLOSSARY_TEMPLATE = (
    "원어,번역어\r\n"
    "소방사다리차,Aerial Ladder Vehicle\r\n"
    "정격하중,Rated Load\r\n"
)


@app.get("/api/glossary/template")
def glossary_template():
    """A fill-in CSV template for user glossaries (utf-8-sig so Excel reads Korean)."""
    data = ("﻿" + _GLOSSARY_TEMPLATE).encode("utf-8")
    return Response(
        content=data,
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="glossary-template.csv"'},
    )
```

Change the `upload` endpoint signature to accept an optional glossary and parse/save it. The current signature is `async def upload(pdf: UploadFile = File(...), source_lang: str = Form("ko"), target_lang: str = Form("en")):`. Change to:
```python
async def upload(
    pdf: UploadFile = File(...),
    source_lang: str = Form("ko"),
    target_lang: str = Form("en"),
    glossary: UploadFile | None = File(None),
):
```
After the block that writes `src_path` (i.e. after `fh.write(await pdf.read())`), before `jobs.start(...)`, add:
```python
    glossary_terms = 0
    if glossary is not None:
        raw = await glossary.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        mapping = parse_glossary_csv(text)
        if mapping:
            process.save_doc_glossary(STORAGE, doc_id, mapping)
        glossary_terms = len(mapping)
```
And change the return to include the count:
```python
    return {
        "doc_id": doc_id,
        "source_lang": source_lang,
        "target_lang": target_lang,
        "glossary_terms": glossary_terms,
    }
```

- [ ] **Step 4: Run tests — expect pass**

Run: `cd backend; ./.venv/Scripts/python.exe -m pytest tests/test_glossary_api.py -q` → 3 passed.
(The background `_run_job` thread will fail on the fake PDF; that's fine — it's a daemon and the assertions only touch the synchronous response + saved glossary.)

- [ ] **Step 5: Full suite** — `cd backend; ./.venv/Scripts/python.exe -m pytest -q` → all pass (+3 → 113).

- [ ] **Step 6: Commit**
```bash
git add backend/main.py backend/tests/test_glossary_api.py
git commit -m "feat(api): upload accepts a glossary CSV; add glossary template endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Frontend — glossary upload input + template link

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/app/page.tsx`

> Verify with `cd frontend; npm run build` (no JS test runner). Commit even without a unit test.

- [ ] **Step 1: types — add the upload response field**

In `frontend/src/lib/types.ts`, the `StartResponse` interface gains an optional count. Change:
```ts
export interface StartResponse {
  doc_id: string;
  source_lang: string;
  target_lang: string;
}
```
to:
```ts
export interface StartResponse {
  doc_id: string;
  source_lang: string;
  target_lang: string;
  glossary_terms?: number;
}
```

- [ ] **Step 2: api — `upload` accepts an optional glossary file**

In `frontend/src/lib/api.ts`, change the `upload` method to:
```ts
  upload: (file: File, sourceLang: string, targetLang: string, glossary?: File | null) => {
    const fd = new FormData();
    fd.append("pdf", file);
    fd.append("source_lang", sourceLang);
    fd.append("target_lang", targetLang);
    if (glossary) fd.append("glossary", glossary);
    return fetch("/api/documents", { method: "POST", body: fd }).then((r) =>
      asJson<StartResponse>(r),
    );
  },
```

- [ ] **Step 3: page.tsx — glossary input + template link**

In `frontend/src/app/page.tsx`:

Add state next to the existing `file` state (`const [file, setFile] = useState<File | null>(null);`):
```tsx
  const [glossaryFile, setGlossaryFile] = useState<File | null>(null);
```

Pass it in `onStart`'s upload call — change
`const { doc_id } = await api.upload(file, sourceLang, targetLang);` to:
```tsx
      const { doc_id } = await api.upload(file, sourceLang, targetLang, glossaryFile);
```

Add the glossary input + template link as a new `<label>` block inside the `.ticket` div, right AFTER the document-file `</label>` (before the `<div className="flex flex-wrap items-end ...">` language row):
```tsx
            <label className="flex flex-col gap-2">
              <span className="slug flex items-center justify-between">
                용어집 (CSV · 선택)
                <a
                  href="/api/glossary/template"
                  download
                  className="mono text-[0.72rem] underline"
                  style={{ color: "var(--vermillion)" }}
                >
                  양식 다운로드
                </a>
              </span>
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => setGlossaryFile(e.target.files?.[0] ?? null)}
                className="mono text-[0.8rem] file:mr-3 file:border file:border-rule-strong file:bg-paper file:px-3 file:py-1.5 file:text-[0.72rem]"
              />
              {glossaryFile && (
                <span className="slug" style={{ color: "var(--vermillion)" }}>
                  {glossaryFile.name}
                </span>
              )}
            </label>
```

- [ ] **Step 4: Build**

Run: `cd frontend; npm run build`
Expected: compiles, no type errors.

- [ ] **Step 5: Commit**
```bash
git add frontend/src/lib/api.ts frontend/src/lib/types.ts frontend/src/app/page.tsx
git commit -m "feat(web): glossary CSV upload on the intake screen + template download link

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Integration verification (manual)

- [ ] **Step 1:** Backend on :8001 (or restart with these changes), frontend prod build + start.
- [ ] **Step 2:** On the upload screen, click "양식 다운로드" → a `glossary-template.csv` downloads with header `원어,번역어` + examples; opens in Excel with Korean intact.
- [ ] **Step 3:** Fill a couple terms (e.g. a term whose built-in translation you want to override), upload it with a document, translate.
- [ ] **Step 4:** Confirm the translated output uses the user's term (overriding the built-in), and that re-translate mode also honors it.
- [ ] **Step 5:** Upload a doc WITHOUT a glossary → still works (built-in only).
- [ ] **Step 6:** `cd backend; ./.venv/Scripts/python.exe -m pytest -q` → all green.

---

## Self-Review

**Spec coverage:** CSV parse (Task 1), merge user-over-built-in (Task 1), per-doc store + wire into translate & retranslate (Task 2), upload accepts+saves glossary + term count (Task 3), template endpoint (Task 3), frontend input + template link (Task 4). All spec items covered.

**Type/name consistency:** `parse_glossary_csv`, `extra_glossary` (translate_document & translate_segment), `glossary_path`/`save_doc_glossary`/`load_doc_glossary`, `glossary_terms` (API + StartResponse), `glossary` form field (frontend ↔ backend), `/api/glossary/template`. Consistent across tasks.

**Placeholder scan:** none — every step has concrete code/commands.

**Note:** built-in glossary (`backend/glossary/ko-en.json`) already maps `정격하중 → Rated Load`, which the override tests rely on; if that file changes, update the test fixtures.
