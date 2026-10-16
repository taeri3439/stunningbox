# 재번역 모드 (Partial Re-translation Mode) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user toggle a "재번역 모드" on the compare screen, pick a sentence from a source↔translation list, re-translate just that sentence with a chosen direction (간략/상세/격식/의역 + custom prompt), see the picked sentence highlighted in both the original (left) and OnlyOffice (right) panes, and apply the new translation in-place.

**Architecture:** Re-translation works at the leaf-text-field (sentence/line/cell) granularity the pipeline already uses. The backend gains two endpoints (`GET /segments`, `POST /retranslate-segment`) whose logic lives in `process.py` (thin HTTP wrappers in `main.py`), plus a `guidance` hook in the translator and a deterministic span-id→bbox helper in the extractor (reusing the persisted `source.pdf`). The `.docx` stays the single source of truth: apply replaces text in the live OnlyOffice editor via its connector (autosave persists it), and the per-segment translation is also written back into `translated.{lang}.json` so the list stays consistent on re-entry. The frontend adds a mode toggle, a `RetranslatePanel`, connector helpers (search/replace + highlight), and a fractional overlay box on the left page image.

**Tech Stack:** Python / FastAPI / PyMuPDF (fitz) / python-docx (backend); Next.js (React, client component) / TypeScript (frontend); OnlyOffice Document Server connector API (`createConnector` → `callCommand` / `executeMethod`).

**Branch:** `partialretranslate-2026-06-03` (already checked out).

**Spec:** [docs/superpowers/specs/2026-06-03-partial-retranslate-mode-design.md](../specs/2026-06-03-partial-retranslate-mode-design.md)

---

## File Structure

**Backend (create/modify):**
- Modify `backend/pipeline/translator.py` — `PRESET_GUIDANCE`, `build_guidance()`, `translate_segment()`, `guidance=` on `.translate()`.
- Modify `backend/models.py` — `iter_segments()` (yields span_ids); `iter_text_fields()` delegates to it.
- Modify `backend/pipeline/extractor.py` — `extract_span_bboxes()`, `segment_source_bbox()`.
- Modify `backend/pipeline/process.py` — `save_translated_model()`, `build_segments()`, `retranslate_one_segment()`.
- Modify `backend/main.py` — `RetranslateBody`, `GET /segments`, `POST /retranslate-segment`.
- Create `backend/tests/test_translator_guidance.py`, `backend/tests/test_iter_segments.py`, `backend/tests/test_segment_bbox.py`, `backend/tests/test_retranslate.py`.

**Frontend (create/modify):**
- Modify `frontend/src/lib/types.ts` — `SourceBBox`, `Segment`, `RetranslateResponse`.
- Modify `frontend/src/lib/api.ts` — `segments()`, `retranslateSegment()`.
- Create `frontend/src/lib/onlyofficeConnector.ts` — `searchAndReplace()`, `highlightInEditor()`.
- Modify `frontend/src/components/OnlyOfficeEditor.tsx` — expose connector via `onConnector` prop.
- Create `frontend/src/components/RetranslatePanel.tsx` — the sentence list + guidance + apply UI.
- Modify `frontend/src/app/c/[id]/page.tsx` — mode toggle, keep editor mounted/hidden, left overlay.

**Note (Next.js):** [frontend/AGENTS.md](../../../frontend/AGENTS.md) warns this Next.js differs from training data — before writing frontend code, read the relevant guide under `node_modules/next/dist/docs/`. The frontend has no JS test runner, so frontend task verification is `npm run build` (type-checks + compiles) plus the described manual check.

**Commit trailer:** every commit message in this plan MUST end with:
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## Task 1: Connector spike (GATE — manual, do before any frontend apply/highlight work)

**Why:** The editor config sets `customization.plugins:false` ([backend/onlyoffice.py:102](../../../backend/onlyoffice.py#L102)). This disables the *plugins tab*, not necessarily the automation connector — but we must confirm on the running Community DS that `createConnector()`, `executeMethod("SearchAndReplace", …)`, and `callCommand(… Api.GetDocument().Search/Select …)` actually work, and learn the exact return semantics. This gates Tasks 8–10.

**Files:** none committed (temporary, reverted at end).

- [ ] **Step 1: Start the stack against the running Document Server**

Backend (existing port 8000) and DS (PUBLIC_URL `http://localhost:8080`) must be up. Build + serve the frontend (memory: dev HMR is broken here):

Run:
```
cd frontend; npm run build; npm run start
```
Open a previously-processed doc, e.g. `http://localhost:3000/c/501c95260b57?lang=en`, and wait for the editor to load.

- [ ] **Step 2: Temporarily wire a connector probe into the editor**

In [frontend/src/components/OnlyOfficeEditor.tsx](../../../frontend/src/components/OnlyOfficeEditor.tsx), inside the `new window.DocsAPI.DocEditor(id, {...})` config, add an `onDocumentReady` event that probes the connector. Add this temporarily to the `events` object:

```ts
events: {
  onError: (e: any) =>
    setError(typeof e?.data === "string" ? e.data : "편집기 오류"),
  onDocumentReady: undefined, // placeholder to avoid typo; see real key below
  onDocumentReady: () => {
    try {
      const connector = editorRef.current.createConnector();
      // 1) replace probe
      connector.executeMethod(
        "SearchAndReplace",
        [{ searchString: "the", replaceString: "THE", matchCase: false }],
        (res: unknown) => console.log("[spike] SearchAndReplace cb:", res),
      );
      // 2) search + select probe. Data is handed to the serialized command via Asc.scope
      //    (the documented, injection-safe mechanism — NEVER build the command from an
      //    interpolated string). Closures do NOT survive callCommand.
      (window as any).Asc.scope.needle = "the";
      connector.callCommand(
        function () {
          var oDocument = Api.GetDocument();
          var aRanges = oDocument.Search(Asc.scope.needle, false);
          if (aRanges && aRanges.length) { aRanges[0].Select(); return aRanges.length; }
          return 0;
        },
        (res: unknown) => console.log("[spike] Search/Select cb:", res),
      );
    } catch (err) {
      console.error("[spike] connector failed:", err);
    }
  },
}
```
(Remove the bogus `onDocumentReady: undefined` line — it is only here to flag that the real key is exactly `onDocumentReady`. For the probe to type-check under `next build`, also add `declare const Api: any; declare const Asc: any;` at the top of the file — removed on revert.)

- [ ] **Step 3: Observe and record results**

Reload the page. In DevTools console check:
- `[spike] SearchAndReplace cb:` fires AND the word "the" visibly becomes "THE" in the editor → **replace works**.
- `[spike] Search/Select cb:` fires (ideally logging a count) AND the first "the" becomes selected/scrolled into view → **highlight + Asc.scope data-passing works**.
- Note whether the `callCommand` callback receives the command's return value (the count). This decides whether "not found" is detectable for the apply fallback.
- If `(window as any).Asc` is undefined in the host page (so `Asc.scope` can't carry data), record it: the right-pane highlight is then not available with this wiring — keep apply via `executeMethod` and drop the in-editor highlight (the left-pane PDF overlay is unaffected).

- [ ] **Step 4: Decide and revert**

Decision:
- ✅ Both work → proceed with the plan as written (Tasks 8–10 use these exact calls).
- ⚠️ `executeMethod` works but `callCommand` return value is not delivered → keep apply; for highlight, ignore return (assume found, user confirms visually).
- ❌ `createConnector` unavailable / nothing works → switch frontend apply to the **copy fallback** (Task 9 alt) and **drop the right-pane highlight** (left-pane PDF overlay still works, it is backend-only). Record this in the plan before continuing.

Revert the temporary probe (`git checkout -- frontend/src/components/OnlyOfficeEditor.tsx`). No commit for this task.

---

## Task 2: Translator guidance hook

**Files:**
- Modify: `backend/pipeline/translator.py`
- Test: `backend/tests/test_translator_guidance.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_translator_guidance.py`:
```python
from pipeline import translator as T


def test_build_guidance_combines_preset_and_custom():
    g = T.build_guidance("concise", "keep legal terms verbatim")
    assert "concise" in g.lower()
    assert "keep legal terms verbatim" in g
    assert T.build_guidance(None, None) == ""
    assert T.build_guidance("bogus", None) == ""        # unknown preset ignored
    assert T.build_guidance(None, "  only custom ") == "only custom"


def test_mock_translate_threads_guidance():
    out = T.MockTranslator().translate(
        ["원문"], "ko", "en", {}, guidance="be very formal"
    )
    assert out[0].startswith("[en]")
    assert "be very formal" in out[0]                   # guidance is visible to callers


def test_translate_segment_guided_and_atomic():
    tr = T.MockTranslator()
    assert T.translate_segment("정격하중", "ko", "en", "concise", tr) != "정격하중"
    # atomic / empty segments are returned unchanged (no wasted translation)
    assert T.translate_segment("12 960 N", "ko", "en", "", tr) == "12 960 N"
    assert T.translate_segment("   ", "ko", "en", "", tr) == "   "
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; python -m pytest tests/test_translator_guidance.py -v`
Expected: FAIL (`AttributeError: module 'pipeline.translator' has no attribute 'build_guidance'`).

- [ ] **Step 3: Implement**

In `backend/pipeline/translator.py`, after the `CHUNK_SIZE = 80` line (≈L47), add:
```python
# Re-translation direction presets (재번역 모드). Appended to the translator system prompt
# so a single sentence can be re-translated "more concise", "more formal", etc.
PRESET_GUIDANCE = {
    "concise": "Make the translation more concise — keep only the essential meaning.",
    "detailed": "Make the translation more detailed and explanatory; make implicit "
                "meaning explicit.",
    "formal": "Use a formal, written register suitable for an official document.",
    "natural": "Translate idiomatically and naturally rather than literally, while "
               "preserving the meaning.",
}


def build_guidance(preset: str | None, custom: str | None) -> str:
    """Combine an optional direction preset with an optional free-text instruction."""
    parts = []
    if preset and preset in PRESET_GUIDANCE:
        parts.append(PRESET_GUIDANCE[preset])
    if custom and custom.strip():
        parts.append(custom.strip())
    return " ".join(parts)


def translate_segment(
    source_text: str,
    source_lang: str,
    target_lang: str,
    guidance: str = "",
    translator=None,
) -> str:
    """Re-translate a single segment with optional direction guidance. Empty / atomic
    (pure number/symbol) segments are returned unchanged."""
    if not source_text.strip() or _is_atomic(source_text):
        return source_text
    translator = translator or get_translator()
    glossary = load_glossary(source_lang, target_lang)
    out = translator.translate(
        [source_text], source_lang, target_lang, glossary, guidance=guidance
    )
    return out[0] if out else source_text
```

Change `MockTranslator.translate` signature + body (≈L122) to:
```python
    def translate(self, texts, source_lang, target_lang, glossary, on_done=None, guidance=""):
        out = []
        for t in texts:
            for src, dst in glossary.items():
                t = t.replace(src, dst)
            tag = f"[{target_lang}]" + (f"[g:{guidance}]" if guidance else "")
            out.append(f"{tag} {t}")
        if on_done:
            on_done()
        return out
```

Change `ClaudeTranslator.translate` signature (≈L142) to accept guidance and append it to `system`:
```python
    def translate(self, texts, source_lang, target_lang, glossary, on_done=None, guidance=""):
        src = LANG_NAMES.get(source_lang, source_lang)
        tgt = LANG_NAMES.get(target_lang, target_lang)
        gloss_lines = "\n".join(f"  {k} => {v}" for k, v in glossary.items())
        system = (
            f"You are a professional technical translator ({src} -> {tgt}). "
            "Translate each input segment faithfully. Keep all numbers, units, codes, "
            "and symbols EXACTLY as-is. Do not add or drop segments. Preserve meaning "
            "and register for an engineering standard."
            + (f"\nUse this glossary strictly:\n{gloss_lines}" if glossary else "")
            + (f"\nAdditional instruction for this request: {guidance}" if guidance else "")
        )
```
(The rest of `ClaudeTranslator.translate` — the `tool`, chunking, `parallel_map` — is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend; python -m pytest tests/test_translator_guidance.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite (no regressions in the existing translate path)**

Run: `cd backend; python -m pytest -q`
Expected: all previously-passing tests still pass (the `guidance=""` default keeps `translate_document`'s existing call working).

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/translator.py backend/tests/test_translator_guidance.py
git commit -m "feat(translator): guidance hook + translate_segment for re-translation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: `iter_segments` (span-ids alongside each segment)

**Files:**
- Modify: `backend/models.py:267-296` (the `iter_text_fields` generator)
- Test: `backend/tests/test_iter_segments.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_iter_segments.py`:
```python
from models import (
    HeadingBlock, ParagraphBlock, SemanticDocument, TableBlock, TableCell,
    iter_segments, iter_text_fields,
)


def _doc():
    return SemanticDocument(
        source_lang="ko",
        blocks=[
            HeadingBlock(level=1, text="제목", source_span_ids=["p0b1l0s0"]),
            ParagraphBlock(text="문단 하나", source_span_ids=["p0b2l0s0", "p0b2l0s1"]),
            TableBlock(rows=[[TableCell(text="셀", source_span_ids=["p1b0l0s0"])]]),
        ],
    )


def test_iter_segments_carries_span_ids():
    segs = list(iter_segments(_doc()))
    texts = [g() for g, _, _ in segs]
    assert texts == ["제목", "문단 하나", "셀"]
    assert [ids for _, _, ids in segs] == [
        ["p0b1l0s0"], ["p0b2l0s0", "p0b2l0s1"], ["p1b0l0s0"],
    ]


def test_iter_text_fields_still_two_tuple_and_aligned():
    doc = _doc()
    fields = list(iter_text_fields(doc))
    assert all(len(f) == 2 for f in fields)                      # (getter, setter)
    assert [g() for g, _ in fields] == [g() for g, _, _ in iter_segments(doc)]
    # setter still mutates the model in place
    fields[0][1]("NEW")
    assert doc.blocks[0].text == "NEW"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; python -m pytest tests/test_iter_segments.py -v`
Expected: FAIL (`ImportError: cannot import name 'iter_segments'`).

- [ ] **Step 3: Implement**

In `backend/models.py`, replace the whole `iter_text_fields` function (L267-296) with `iter_segments` plus a thin `iter_text_fields` delegate:
```python
def iter_segments(doc: SemanticDocument):
    """Yield (getter, setter, span_ids) for every translatable string in the model.

    span_ids is the owning block/cell's source_span_ids (block-level granularity) — used
    to locate the segment's bbox on the original page for the re-translation highlight.
    Running-band fields have no source spans, so they yield []. Markers and image refs
    are intentionally excluded — they are structural.
    """
    for band in (doc.running_header, doc.running_footer):
        if band:
            for attr in ("left", "center", "right"):
                if getattr(band, attr):
                    get, set_ = _field_accessor(band, attr)
                    yield get, set_, []
    for block in doc.blocks:
        if isinstance(block, HeadingBlock):
            get, set_ = _field_accessor(block, "text")
            yield get, set_, block.source_span_ids
        elif isinstance(block, (ParagraphBlock, ListItemBlock, CaptionBlock)):
            # Preserved multi-line blocks translate line-by-line; others as one text.
            if block.lines:
                for i in range(len(block.lines)):
                    get, set_ = _list_accessor(block.lines, i)
                    yield get, set_, block.source_span_ids
            else:
                get, set_ = _field_accessor(block, "text")
                yield get, set_, block.source_span_ids
        elif isinstance(block, TableBlock):
            if block.caption is not None:
                get, set_ = _field_accessor(block, "caption")
                yield get, set_, block.source_span_ids
            for row in block.rows:
                for cell in row:
                    if cell.lines:
                        for i in range(len(cell.lines)):
                            get, set_ = _list_accessor(cell.lines, i)
                            yield get, set_, cell.source_span_ids
                    else:
                        get, set_ = _field_accessor(cell, "text")
                        yield get, set_, cell.source_span_ids


def iter_text_fields(doc: SemanticDocument):
    """(getter, setter) pairs for every translatable string — see iter_segments."""
    for get, set_, _ in iter_segments(doc):
        yield get, set_
```
(`_field_accessor` / `_list_accessor` are defined later in the module; that is fine — they resolve at call time.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; python -m pytest tests/test_iter_segments.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite (translate_document + collect_texts depend on iter_text_fields)**

Run: `cd backend; python -m pytest -q`
Expected: all pass (iter_text_fields contract unchanged).

- [ ] **Step 6: Commit**

```bash
git add backend/models.py backend/tests/test_iter_segments.py
git commit -m "feat(models): iter_segments carries source_span_ids; iter_text_fields delegates

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Span-id → bbox helpers (extractor)

**Files:**
- Modify: `backend/pipeline/extractor.py`
- Test: `backend/tests/test_segment_bbox.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_segment_bbox.py`:
```python
import fitz

from pipeline.extractor import extract_span_bboxes, segment_source_bbox


def test_segment_source_bbox_unions_and_fractions():
    span_map = {"a": (0, (10.0, 20.0, 30.0, 40.0)),
                "b": (0, (30.0, 20.0, 50.0, 45.0))}
    page_sizes = [(100.0, 200.0)]
    bbox = segment_source_bbox(["a", "b"], span_map, page_sizes)
    assert bbox == {"page": 0, "x": 0.1, "y": 0.1, "w": 0.4, "h": 0.125}


def test_segment_source_bbox_missing_and_degenerate():
    assert segment_source_bbox(["zzz"], {"a": (0, (1, 1, 2, 2))}, [(10, 10)]) is None
    assert segment_source_bbox([], {}, []) is None
    assert segment_source_bbox(["a"], {"a": (0, (1, 1, 2, 2))}, [(0, 0)]) is None


def test_extract_span_bboxes_ids_match_scheme(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=200, height=300)
    page.insert_text((50, 100), "Hello world")
    p = str(tmp_path / "s.pdf")
    doc.save(p)
    doc.close()

    span_map, page_sizes = extract_span_bboxes(p)
    assert page_sizes == [(200.0, 300.0)]
    assert span_map                                            # at least one span found
    sid, (pno, box) = next(iter(span_map.items()))
    assert sid.startswith("p0b") and "l" in sid and "s" in sid  # extractor id scheme
    assert pno == 0 and len(box) == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; python -m pytest tests/test_segment_bbox.py -v`
Expected: FAIL (`ImportError: cannot import name 'extract_span_bboxes'`).

- [ ] **Step 3: Implement**

In `backend/pipeline/extractor.py`, after `_clip_to_png` (end of file), add:
```python
def extract_span_bboxes(pdf_path: str):
    """Map each text span's id -> (page_index, bbox) plus per-page (width, height) in PDF
    points, using the SAME id scheme as extract_document (p{pno}b{bno}l{lno}s{sno}). No
    rendering — cheap geometry only, for the re-translation highlight overlays."""
    doc = fitz.open(pdf_path)
    spans: dict[str, tuple] = {}
    page_sizes: list[tuple[float, float]] = []
    try:
        for pno in range(doc.page_count):
            page = doc[pno]
            page_sizes.append((page.rect.width, page.rect.height))
            text_dict = page.get_text("dict")
            for bno, block in enumerate(text_dict.get("blocks", [])):
                if block.get("type") != 0:
                    continue
                for lno, line in enumerate(block.get("lines", [])):
                    for sno, span in enumerate(line.get("spans", [])):
                        if not span.get("text", "").strip():
                            continue
                        sid = f"p{pno}b{bno}l{lno}s{sno}"
                        spans[sid] = (pno, tuple(span["bbox"]))
    finally:
        doc.close()
    return spans, page_sizes


def segment_source_bbox(span_ids, span_map, page_sizes):
    """Union bbox of a segment's spans (on their first page) as page fractions 0..1:
    {page, x, y, w, h}. None if no span is located or the page size is degenerate."""
    located = [span_map[s] for s in span_ids if s in span_map]
    if not located:
        return None
    page = located[0][0]
    boxes = [b for p, b in located if p == page]
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    if page >= len(page_sizes):
        return None
    pw, ph = page_sizes[page]
    if pw <= 0 or ph <= 0:
        return None
    return {
        "page": page,
        "x": round(x0 / pw, 4),
        "y": round(y0 / ph, 4),
        "w": round((x1 - x0) / pw, 4),
        "h": round((y1 - y0) / ph, 4),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; python -m pytest tests/test_segment_bbox.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/extractor.py backend/tests/test_segment_bbox.py
git commit -m "feat(extractor): deterministic span-id->bbox helpers for highlight overlays

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Process layer — build_segments, retranslate_one_segment, save_translated_model

**Files:**
- Modify: `backend/pipeline/process.py`
- Test: `backend/tests/test_retranslate.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_retranslate.py`:
```python
import json
import os

from models import ParagraphBlock, SemanticDocument
from pipeline import process
from pipeline.translator import MockTranslator


def _seed(tmp_path):
    """A 2-paragraph Korean source doc on disk (no source.pdf -> bbox is null)."""
    storage, doc_id = str(tmp_path), "doc1"
    base = process.doc_dir(storage, doc_id)
    os.makedirs(base, exist_ok=True)
    doc = SemanticDocument(
        source_lang="ko",
        blocks=[
            ParagraphBlock(text="첫 문장", source_span_ids=["p0b0l0s0"]),
            ParagraphBlock(text="둘째 문장", source_span_ids=["p0b1l0s0"]),
        ],
    )
    with open(os.path.join(base, "source.json"), "w", encoding="utf-8") as fh:
        json.dump(doc.model_dump(), fh, ensure_ascii=False)
    return storage, doc_id


def test_build_segments_pairs_source_and_translation(tmp_path):
    storage, doc_id = _seed(tmp_path)
    segs = process.build_segments(storage, doc_id, "en", translator=MockTranslator())
    assert [s["index"] for s in segs] == [0, 1]
    assert [s["source"] for s in segs] == ["첫 문장", "둘째 문장"]
    assert all(s["translated"].startswith("[en]") for s in segs)
    assert all(s["source_bbox"] is None for s in segs)         # no source.pdf in tmp


def test_retranslate_one_segment_updates_cache(tmp_path):
    storage, doc_id = _seed(tmp_path)
    process.build_segments(storage, doc_id, "en", translator=MockTranslator())  # warms cache
    res = process.retranslate_one_segment(
        storage, doc_id, "en", index=1, guidance="be concise",
        translator=MockTranslator(),
    )
    assert res["index"] == 1
    assert "[g:be concise]" in res["new_text"]
    assert res["old_text"] != res["new_text"]
    # the change is persisted to translated.en.json at that index only
    cache = os.path.join(process.doc_dir(storage, doc_id), "translated.en.json")
    with open(cache, encoding="utf-8") as fh:
        model = json.load(fh)
    assert model["blocks"][1]["text"] == res["new_text"]
    assert "[g:" not in model["blocks"][0]["text"]             # index 0 untouched


def test_retranslate_index_out_of_range(tmp_path):
    storage, doc_id = _seed(tmp_path)
    try:
        process.retranslate_one_segment(
            storage, doc_id, "en", index=99, guidance="", translator=MockTranslator()
        )
        assert False, "expected IndexError"
    except IndexError:
        pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; python -m pytest tests/test_retranslate.py -v`
Expected: FAIL (`AttributeError: module 'pipeline.process' has no attribute 'build_segments'`).

- [ ] **Step 3: Implement**

In `backend/pipeline/process.py`:

(a) Update the imports at the top (L14, L19) to pull in the new helpers:
```python
from models import SemanticDocument, iter_segments, iter_text_fields
```
and add to the existing translator import (L19):
```python
from pipeline.translator import translate_document, translate_segment
```

(b) After `save_edited_model` (≈L234) add:
```python
def save_translated_model(
    storage_dir: str, doc_id: str, target_lang: str, doc: SemanticDocument
) -> None:
    """Overwrite the per-language translation cache (translated.{lang}.json)."""
    cache = os.path.join(doc_dir(storage_dir, doc_id), f"translated.{target_lang}.json")
    _write_json(cache, doc.model_dump())


def build_segments(storage_dir: str, doc_id: str, target_lang: str, translator=None):
    """Sentence list for 재번역 모드: [{index, source, translated, source_bbox|null}].

    Pairs the source model with the (cached/auto) translation field-by-field. Attaches a
    page-fraction bbox from the persisted source.pdf when present (PDF path); HWP/office
    uploads have no source.pdf, so source_bbox is null. Non-translatable (empty/atomic)
    segments are skipped but original field indices are preserved."""
    from pipeline import extractor
    from pipeline.translator import _is_atomic

    source = load_source_model(storage_dir, doc_id)
    if translator is not None:
        translated = translate_document(source.model_copy(deep=True), target_lang,
                                        translator=translator)
        save_translated_model(storage_dir, doc_id, target_lang, translated)
    else:
        translated = get_translated_model(storage_dir, doc_id, target_lang)

    span_map: dict = {}
    page_sizes: list = []
    pdf_path = os.path.join(doc_dir(storage_dir, doc_id), "source.pdf")
    if os.path.exists(pdf_path):
        span_map, page_sizes = extractor.extract_span_bboxes(pdf_path)

    out = []
    src_segs = list(iter_segments(source))
    tr_fields = list(iter_text_fields(translated))
    for i, (get, _set, span_ids) in enumerate(src_segs):
        s = get()
        if not s.strip() or _is_atomic(s):
            continue
        t = tr_fields[i][0]() if i < len(tr_fields) else ""
        bbox = (extractor.segment_source_bbox(span_ids, span_map, page_sizes)
                if span_map else None)
        out.append({"index": i, "source": s, "translated": t, "source_bbox": bbox})
    return out


def retranslate_one_segment(
    storage_dir: str, doc_id: str, target_lang: str, index: int, guidance: str,
    translator=None,
):
    """Re-translate one segment (by field index) from the source with guidance, write it
    back into translated.{lang}.json, and return {index, old_text, new_text}. Raises
    IndexError if the index is out of range."""
    source = load_source_model(storage_dir, doc_id)
    src_fields = list(iter_text_fields(source))
    if index < 0 or index >= len(src_fields):
        raise IndexError(f"segment index {index} out of range (0..{len(src_fields) - 1})")
    src_text = src_fields[index][0]()
    new_text = translate_segment(
        src_text, source.source_lang, target_lang, guidance, translator=translator
    )

    translated = get_translated_model(storage_dir, doc_id, target_lang)
    tr_fields = list(iter_text_fields(translated))
    old_text = tr_fields[index][0]()
    tr_fields[index][1](new_text)
    save_translated_model(storage_dir, doc_id, target_lang, translated)
    return {"index": index, "old_text": old_text, "new_text": new_text}
```
(The `translator=` parameter on `build_segments` exists only so tests inject `MockTranslator` deterministically; in production both endpoints pass `translator=None`, which routes through `get_translated_model`'s normal cache + `get_translator()`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; python -m pytest tests/test_retranslate.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full suite**

Run: `cd backend; python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/pipeline/process.py backend/tests/test_retranslate.py
git commit -m "feat(process): build_segments + retranslate_one_segment + save_translated_model

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: API endpoints (thin wrappers)

**Files:**
- Modify: `backend/main.py`
- Test: `backend/tests/test_retranslate_api.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_retranslate_api.py`:
```python
import json
import os

import pytest
from fastapi.testclient import TestClient

import main
from models import ParagraphBlock, SemanticDocument
from pipeline import process


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    base = process.doc_dir(str(tmp_path), "doc1")
    os.makedirs(base, exist_ok=True)
    doc = SemanticDocument(
        source_lang="ko",
        blocks=[ParagraphBlock(text="첫 문장", source_span_ids=["p0b0l0s0"])],
    )
    with open(os.path.join(base, "source.json"), "w", encoding="utf-8") as fh:
        json.dump(doc.model_dump(), fh, ensure_ascii=False)
    return TestClient(main.app)


def test_segments_endpoint(client):
    r = client.get("/api/documents/doc1/segments?lang=en")
    assert r.status_code == 200
    body = r.json()
    assert body["lang"] == "en"
    assert body["segments"][0]["source"] == "첫 문장"
    assert body["segments"][0]["source_bbox"] is None


def test_retranslate_endpoint_and_out_of_range(client):
    client.get("/api/documents/doc1/segments?lang=en")          # warm cache
    r = client.post(
        "/api/documents/doc1/retranslate-segment",
        json={"lang": "en", "index": 0, "preset": "concise", "guidance": "법률 용어 유지"},
    )
    assert r.status_code == 200
    assert r.json()["new_text"] != r.json()["old_text"]
    bad = client.post(
        "/api/documents/doc1/retranslate-segment",
        json={"lang": "en", "index": 50},
    )
    assert bad.status_code == 422
```
(This runs offline: with no `ANTHROPIC_API_KEY`, `get_translator()` returns `MockTranslator`. Ensure the test env has no key, or set `monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)` in the fixture if your shell exports one.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend; python -m pytest tests/test_retranslate_api.py -v`
Expected: FAIL (404 — endpoints don't exist yet).

- [ ] **Step 3: Implement**

In `backend/main.py`:

(a) Add the pydantic import near the FastAPI import (L22 area):
```python
from pydantic import BaseModel
```

(b) Add the request model + `translator` import. After `DOCX_MIME` (≈L35) add:
```python
from pipeline.translator import build_guidance


class RetranslateBody(BaseModel):
    lang: str
    index: int
    preset: str | None = None
    guidance: str | None = None
```

(c) After the `/translate` endpoint (≈L244) add the two endpoints:
```python
@app.get("/api/documents/{doc_id}/segments")
def segments(doc_id: str, lang: str):
    """Sentence list for 재번역 모드: source↔translation pairs with original-page bbox."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    try:
        items = process.build_segments(STORAGE, doc_id, lang)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Segments unavailable: {e}")
    return {"lang": lang, "segments": items}


@app.post("/api/documents/{doc_id}/retranslate-segment")
def retranslate_segment(doc_id: str, body: RetranslateBody):
    """Re-translate one segment with direction guidance; persist to the translation cache."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    guidance = build_guidance(body.preset, body.guidance)
    try:
        return process.retranslate_one_segment(
            STORAGE, doc_id, body.lang, body.index, guidance
        )
    except IndexError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Re-translation failed: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend; python -m pytest tests/test_retranslate_api.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full suite**

Run: `cd backend; python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/main.py backend/tests/test_retranslate_api.py
git commit -m "feat(api): /segments and /retranslate-segment endpoints

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Frontend types + API client

**Files:**
- Modify: `frontend/src/lib/types.ts`
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Add types**

In `frontend/src/lib/types.ts`, after `ResultResponse` (≈L130) add:
```ts
// GET /api/documents/{id}/segments?lang= — the 재번역 모드 sentence list.
export interface SourceBBox {
  page: number; // 0-based page index in page_images
  x: number;    // all fractions of the page (0..1)
  y: number;
  w: number;
  h: number;
}

export interface Segment {
  index: number;          // field index into the model (stable; used by retranslate)
  source: string;
  translated: string;
  source_bbox: SourceBBox | null; // null for HWP/office (no source.pdf) or unlocated
}

export interface SegmentsResponse {
  lang: string;
  segments: Segment[];
}

// POST /api/documents/{id}/retranslate-segment
export interface RetranslateResponse {
  index: number;
  old_text: string;
  new_text: string;
}
```

- [ ] **Step 2: Add API methods**

In `frontend/src/lib/api.ts`:

Extend the type import (L1-7):
```ts
import type {
  EditorConfigResponse,
  HealthResponse,
  JobStatus,
  ResultResponse,
  RetranslateResponse,
  SegmentsResponse,
  StartResponse,
} from "./types";
```

Add two methods to the `api` object (after `editorConfig`, ≈L61):
```ts
  segments: (docId: string, lang: string) =>
    fetch(
      `/api/documents/${docId}/segments?lang=${encodeURIComponent(lang)}`,
    ).then((r) => asJson<SegmentsResponse>(r)),

  retranslateSegment: (
    docId: string,
    body: { lang: string; index: number; preset?: string | null; guidance?: string | null },
  ) =>
    fetch(`/api/documents/${docId}/retranslate-segment`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => asJson<RetranslateResponse>(r)),
```

- [ ] **Step 3: Type-check / build**

Run: `cd frontend; npm run build`
Expected: compiles with no type errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/types.ts frontend/src/lib/api.ts
git commit -m "feat(web): segments + retranslate API client and types

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 8: OnlyOffice connector exposure + helpers

> Adjust the exact connector calls below to whatever Task 1's spike confirmed works on the running DS. If the spike found `createConnector` unavailable, skip the right-pane helpers and have `RetranslatePanel` (Task 9) use the copy fallback only.

**Files:**
- Create: `frontend/src/lib/onlyofficeConnector.ts`
- Modify: `frontend/src/components/OnlyOfficeEditor.tsx`

- [ ] **Step 1: Create the connector helpers**

Create `frontend/src/lib/onlyofficeConnector.ts`:
```ts
/* eslint-disable @typescript-eslint/no-explicit-any */
// Helpers over the OnlyOffice editor connector (editor.createConnector()). Data is handed
// to callCommand via Asc.scope — the documented, injection-SAFE mechanism. The command
// function is serialized and run inside the editor; closures do NOT survive, and we must
// NEVER build the command from an interpolated string (new Function/eval = code injection).
// Confirmed against the DS in the connector spike.

// `Api` and `Asc` are globals inside the editor's callCommand context (not the page).
declare const Api: any;
declare const Asc: any;

// Highlight `text` in the editor by searching and selecting the first match (the editor's
// native selection both highlights and scrolls it into view; the next call replaces it).
export function highlightInEditor(connector: any, text: string): Promise<void> {
  return new Promise((resolve) => {
    try {
      (window as any).Asc.scope.needle = text; // pass data safely (no eval/new Function)
      connector.callCommand(
        function () {
          var oDocument = Api.GetDocument();
          var aRanges = oDocument.Search(Asc.scope.needle, false);
          if (aRanges && aRanges.length) {
            aRanges[0].Select();
          }
        },
        () => resolve(),
      );
    } catch {
      resolve();
    }
  });
}

// Replace the first occurrence of `oldText` with `newText`. Resolves true if applied.
export function searchAndReplace(
  connector: any,
  oldText: string,
  newText: string,
): Promise<boolean> {
  return new Promise((resolve) => {
    try {
      connector.executeMethod(
        "SearchAndReplace",
        [{ searchString: oldText, replaceString: newText, matchCase: true }],
        () => resolve(true),
      );
    } catch {
      resolve(false);
    }
  });
}
```

- [ ] **Step 2: Expose the connector from the editor component**

In `frontend/src/components/OnlyOfficeEditor.tsx`, add an `onConnector` prop and create the connector on `onDocumentReady`. Change the component signature (L40):
```ts
export function OnlyOfficeEditor({
  docId,
  lang,
  onConnector,
}: {
  docId: string;
  lang: string;
  onConnector?: (connector: any | null) => void;
}) {
```
Add an `events.onDocumentReady` handler in the `new window.DocsAPI.DocEditor(id, {...})` config (alongside `onError`, ≈L60):
```ts
          events: {
            onError: (e: any) =>
              setError(typeof e?.data === "string" ? e.data : "편집기 오류"),
            onDocumentReady: () => {
              try {
                onConnector?.(editorRef.current?.createConnector?.() ?? null);
              } catch {
                onConnector?.(null);
              }
            },
          },
```
In the cleanup (the `return () => {...}` of the effect, ≈L70) clear it:
```ts
    return () => {
      cancelled = true;
      onConnector?.(null);
      try {
        editorRef.current?.destroyEditor?.();
      } catch {
        /* editor may not have finished mounting */
      }
      editorRef.current = null;
    };
```
Add `onConnector` to the effect dependency array is NOT needed (it is a stable callback from the parent); keep `[docId, lang]`.

- [ ] **Step 3: Build**

Run: `cd frontend; npm run build`
Expected: compiles, no type errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/onlyofficeConnector.ts frontend/src/components/OnlyOfficeEditor.tsx
git commit -m "feat(web): expose OnlyOffice connector + search/replace/highlight helpers

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 9: RetranslatePanel component

**Files:**
- Create: `frontend/src/components/RetranslatePanel.tsx`

- [ ] **Step 1: Create the panel**

Create `frontend/src/components/RetranslatePanel.tsx`:
```tsx
"use client";

/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Segment } from "@/lib/types";
import { searchAndReplace, highlightInEditor } from "@/lib/onlyofficeConnector";

const PRESETS: { key: string; label: string }[] = [
  { key: "concise", label: "간략하게" },
  { key: "detailed", label: "상세하게" },
  { key: "formal", label: "격식·문어체" },
  { key: "natural", label: "자연스럽게(의역)" },
];

export function RetranslatePanel({
  docId,
  lang,
  connector,
  onActiveSegment,
}: {
  docId: string;
  lang: string;
  connector: any | null;
  onActiveSegment: (seg: Segment | null) => void;
}) {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [active, setActive] = useState<number | null>(null);
  const [preset, setPreset] = useState<string>("concise");
  const [guidance, setGuidance] = useState<string>("");
  const [preview, setPreview] = useState<{ index: number; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    api
      .segments(docId, lang)
      .then((r) => setSegments(r.segments))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [docId, lang]);

  function select(seg: Segment) {
    setActive(seg.index);
    setPreview(null);
    setNote(null);
    onActiveSegment(seg);
    if (connector) highlightInEditor(connector, seg.translated);
  }

  async function doPreview(seg: Segment) {
    setBusy(true);
    setNote(null);
    try {
      const res = await api.retranslateSegment(docId, {
        lang,
        index: seg.index,
        preset,
        guidance,
      });
      setPreview({ index: seg.index, text: res.new_text });
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function apply(seg: Segment) {
    if (!preview) return;
    let applied = false;
    if (connector) applied = await searchAndReplace(connector, seg.translated, preview.text);
    // reflect locally so the list + future selections use the new text
    setSegments((prev) =>
      prev.map((s) => (s.index === seg.index ? { ...s, translated: preview.text } : s)),
    );
    if (!applied) {
      try {
        await navigator.clipboard.writeText(preview.text);
        setNote("에디터에 자동 반영하지 못했습니다 — 새 번역을 복사했어요. 해당 문장에 붙여넣어 주세요.");
      } catch {
        setNote("에디터에 자동 반영하지 못했습니다. 아래 새 번역을 직접 교체해 주세요.");
      }
    } else {
      setNote("적용됨");
    }
    setPreview(null);
  }

  if (error) {
    return (
      <p className="mono p-6 text-sm" style={{ color: "var(--vermillion-deep)" }}>
        문장 목록 오류 — {error}
      </p>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto px-4 py-4">
      <h2 className="slug mb-3 px-1">재번역 — 문장을 선택하세요</h2>
      <ul className="space-y-2">
        {segments.map((seg) => (
          <li
            key={seg.index}
            onMouseEnter={() => connector && highlightInEditor(connector, seg.translated)}
            className={`rounded border p-2 ${
              active === seg.index ? "border-rule bg-paper-2" : "border-transparent"
            }`}
          >
            <button className="block w-full text-left" onClick={() => select(seg)}>
              <span className="mono block text-xs opacity-60">{seg.source}</span>
              <span className="block text-sm">{seg.translated}</span>
            </button>

            {active === seg.index && (
              <div className="mt-2 border-t border-rule pt-2">
                <div className="flex flex-wrap gap-1.5">
                  {PRESETS.map((p) => (
                    <button
                      key={p.key}
                      onClick={() => setPreset(p.key)}
                      className={`mono rounded px-2 py-0.5 text-xs ${
                        preset === p.key ? "btn-ink" : "border border-rule"
                      }`}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
                <textarea
                  value={guidance}
                  onChange={(e) => setGuidance(e.target.value)}
                  placeholder="추가 지시 (선택) — 예: 법률 용어는 원문 유지"
                  className="mono mt-2 w-full rounded border border-rule p-2 text-xs"
                  rows={2}
                />
                <div className="mt-2 flex items-center gap-2">
                  <button className="btn-ink" disabled={busy} onClick={() => doPreview(seg)}>
                    {busy ? "번역 중…" : "미리보기"}
                  </button>
                  {preview?.index === seg.index && (
                    <button className="btn-vermillion" onClick={() => apply(seg)}>
                      적용
                    </button>
                  )}
                </div>
                {preview?.index === seg.index && (
                  <p className="mt-2 rounded bg-paper-2 p-2 text-sm">{preview.text}</p>
                )}
                {note && <p className="slug mt-2">{note}</p>}
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
```
(If a CSS class such as `bg-paper-2` doesn't exist in this project's stylesheet, substitute an existing neutral background class — check `frontend/src/app/globals.css` for the available tokens; this is styling only and does not affect behavior.)

- [ ] **Step 2: Build**

Run: `cd frontend; npm run build`
Expected: compiles, no type errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/RetranslatePanel.tsx
git commit -m "feat(web): RetranslatePanel — sentence list, guidance, preview/apply

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 10: Compare page — mode toggle, hidden editor, left overlay

**Files:**
- Modify: `frontend/src/app/c/[id]/page.tsx`

- [ ] **Step 1: Add state, connector handle, mode toggle, and keep the editor mounted**

In `frontend/src/app/c/[id]/page.tsx`:

Update imports (L1-8):
```tsx
"use client";

import { useEffect, useState, type CSSProperties } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import { api, LANGS } from "@/lib/api";
import type { ResultResponse, Segment } from "@/lib/types";
import { OnlyOfficeEditor } from "@/components/OnlyOfficeEditor";
import { RetranslatePanel } from "@/components/RetranslatePanel";
```

Add state after the existing `doc`/`error` state (≈L19):
```tsx
  /* eslint-disable-next-line @typescript-eslint/no-explicit-any */
  const [connector, setConnector] = useState<any | null>(null);
  const [mode, setMode] = useState<"edit" | "retranslate">("edit");
  const [activeSeg, setActiveSeg] = useState<Segment | null>(null);
```

- [ ] **Step 2: Add the mode toggle button to the action bar**

Replace the action-bar right-hand `<div className="flex items-center gap-3">…</div>` (≈L98-106) with one that includes the toggle:
```tsx
        <div className="flex items-center gap-3">
          <button
            className={mode === "retranslate" ? "btn-vermillion" : "btn-ink"}
            onClick={() =>
              setMode((m) => (m === "edit" ? "retranslate" : "edit"))
            }
          >
            {mode === "retranslate" ? "재번역 모드 ●" : "재번역 모드"}
          </button>
          <span className="slug">편집 내용 자동 저장</span>
          <Link href="/" className="btn-ink">
            새 문서
          </Link>
          <a href={api.downloadUrl(doc.doc_id, lang)} className="btn-vermillion">
            docx 내보내기
          </a>
        </div>
```

- [ ] **Step 3: Render the left overlay on the active segment's page**

In the left pane's page-images branch (≈L122-139), wrap the `<img>` so an overlay can be absolutely positioned over the active segment's page. Replace the `<figure>…</figure>` block with:
```tsx
              {doc.page_images.map((name, i) => (
                <figure
                  key={name}
                  className="mx-auto mb-8"
                  style={{ width: "min(100%, calc(var(--page-w-mm) * 1mm))" }}
                >
                  <div className="relative">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={`${doc.assets_prefix}/${name}`}
                      loading="lazy"
                      alt={`원본 ${i + 1}쪽`}
                      className="sheet w-full"
                    />
                    {mode === "retranslate" &&
                      activeSeg?.source_bbox &&
                      activeSeg.source_bbox.page === i && (
                        <div
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
                  </div>
                  <figcaption className="slug mt-2 text-center">— {i + 1} —</figcaption>
                </figure>
              ))}
```

- [ ] **Step 4: Swap the right pane between editor and panel (keep the editor mounted)**

Replace the right-pane `<div>` (≈L145-148) with one that always mounts the editor (hidden in re-translate mode so the connector/session survive) and overlays the panel:
```tsx
        {/* right: editor (always mounted; hidden in 재번역 모드 so connector/session live) */}
        <div className="relative flex flex-1 min-h-0 flex-col lg:flex-none lg:w-1/2">
          <div className={mode === "retranslate" ? "hidden" : "flex h-full w-full flex-col"}>
            <OnlyOfficeEditor docId={doc.doc_id} lang={lang} onConnector={setConnector} />
          </div>
          {mode === "retranslate" && (
            <RetranslatePanel
              docId={doc.doc_id}
              lang={lang}
              connector={connector}
              onActiveSegment={setActiveSeg}
            />
          )}
        </div>
```

- [ ] **Step 5: Build**

Run: `cd frontend; npm run build`
Expected: compiles, no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/c/[id]/page.tsx
git commit -m "feat(web): 재번역 모드 toggle, sentence panel, left-pane source highlight

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 11: End-to-end verification (manual)

**Files:** none (verification only).

- [ ] **Step 1: Start the stack**

Backend on :8000 (existing), DS on :8080. Build + serve the frontend:
Run: `cd frontend; npm run build; npm run start`

- [ ] **Step 2: Enter 재번역 모드 on a PDF doc**

Open `http://localhost:3000/c/501c95260b57?lang=en`. Click "재번역 모드".
Expected: right pane switches to the sentence list (source↔translation rows).

- [ ] **Step 3: Verify both-pane highlight**

Hover/click a sentence row.
Expected: left pane scrolls/boxes the matching sentence on the original page (PDF); right pane (if connector works) selects/scrolls to the matching translated sentence.

- [ ] **Step 4: Verify guided re-translation + apply**

Pick "간략하게" (or another preset) + optionally type a custom instruction → "미리보기".
Expected: a new, shorter translation appears. Click "적용".
Expected: the row updates to the new text; toggling back to 편집 모드 shows the editor with the replacement applied (or a "복사" notice if the connector couldn't apply — then paste manually).

- [ ] **Step 5: Verify HWP graceful degradation**

Open an HWP-ingested doc in 재번역 모드.
Expected: sentence list + right-pane highlight/apply still work; left-pane overlay is simply absent (source_bbox null), no errors.

- [ ] **Step 6: Final backend regression**

Run: `cd backend; python -m pytest -q`
Expected: all pass.

---

## Self-Review

**Spec coverage** (spec → task):
- 재번역 모드 토글 / 문장 목록 → Tasks 9, 10.
- 원문 기준 재번역 + 가이드 프리셋 + 커스텀 프롬프트 → Tasks 2, 5, 6, 9.
- `GET /segments` (source↔translated, source_bbox) → Tasks 4, 5, 6.
- `POST /retranslate-segment` (인덱스, translated.json write-back) → Tasks 5, 6.
- 적용 = connector SearchAndReplace + translated.json 정합 → Tasks 5, 8, 9.
- 양쪽 하이라이트 (우 connector / 좌 PDF bbox 오버레이) → Tasks 8, 9, 10; bbox data → Tasks 3, 4, 5.
- connector 스파이크 게이트 + 폴백(복사) → Tasks 1, 9.
- HWP 좌측 생략 / null 무해 처리 → Tasks 4 (null path), 10 (overlay guard), 11 step 5.
- `.docx` 진실 유지 / edited.json 미사용 / _ensure_docx 불변 → honored (no task touches them).
- 테스트(guidance, segments/retranslate, bbox) → Tasks 2, 4, 5, 6.

No spec requirement is left without a task.

**Type/name consistency:** `iter_segments` (3-tuple), `iter_text_fields` (2-tuple delegate); `extract_span_bboxes`/`segment_source_bbox`; `build_segments`/`retranslate_one_segment`/`save_translated_model`; `build_guidance`/`translate_segment`/`PRESET_GUIDANCE`; `Segment`/`SegmentsResponse`/`RetranslateResponse`/`SourceBBox`; `segments()`/`retranslateSegment()`; `searchAndReplace()`/`highlightInEditor()`; `onConnector` prop. Preset keys (`concise|detailed|formal|natural`) match between `PRESET_GUIDANCE` (Task 2) and `PRESETS` (Task 9). Field name `source_bbox` is consistent backend↔frontend. Used consistently across tasks.

**Placeholder scan:** none — every code step contains the actual code; the one styling caveat (`bg-paper-2`) names a concrete check and fallback.
