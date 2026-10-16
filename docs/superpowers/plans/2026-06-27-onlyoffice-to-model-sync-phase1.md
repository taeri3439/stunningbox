# OnlyOffice→Model Reverse-Sync — Phase 1 (text sync) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make TEXT edits a user makes in the OnlyOffice 문서 편집 view flow back into the model (keyed by `blk{i}` bookmarks, updating block text in place) so the HTML views (재번역/용어/고유명사) reflect them and re-renders stop clobbering them.

**Architecture:** On DS save, read each `blk{i}` bookmark's current block text out of the saved `output.{lang}.docx` and update *that block's* `text`/`lines` in a deep copy of the current export model (preserving structure, markers, `.lines`, types, `source_span_ids`), then persist it as `edited.{lang}.json` (which `get_export_model` already prefers). Because structure is preserved, the existing positional source↔translated pairing in `build_segments`/`retranslate` is untouched — no changes there in Phase 1.

**Tech Stack:** FastAPI + python-docx/lxml (backend), pytest. Frontend unchanged in Phase 1.

## Global Constraints

- **Phase 1 is text-only and structure-preserving.** It updates the text of blocks that still carry their `blk{i}` bookmark. It MUST NOT attempt to add/remove/reorder model blocks (that is Phase 2). If the saved docx's bookmark set diverges from the model's block indices (inserted/deleted/reordered), Phase 1 updates only the surviving matched blocks' text and leaves structure as-is.
- **Scope of block kinds in Phase 1:** Heading, Paragraph, ListItem, Caption (single-text and multi-line `.lines`). **Tables and figure overlays are deferred to Phase 2** (table bookmark sits on the first cell only; per-cell sync needs the structural pass).
- **Linchpin = bookmark survival.** Task 1 validates that OnlyOffice/DS preserves `blk{i}` through an edit→save round-trip. If it does not, STOP and revise (the spec's text-similarity fallback becomes primary) before continuing.
- Backend tests: `cd backend && python -m pytest tests/<file> -v` (use `backend/.venv/Scripts/python` if imports fail). Backend imports are rooted at `backend/`.
- Do NOT modify or stage `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`. Never `git add -A`. Do not stage `backend/_*.py` scratch files.
- `render_docx` transforms that matter here: list/heading render the marker INTO the paragraph text (`"{marker} {text}"` for headings; a literal `"{marker} "` first run for list items); `.lines` render as runs separated by `<w:br/>` (`WD_BREAK.LINE`). Phase 1 reads text back accordingly (strip the block's known marker prefix; split on `<w:br/>`).

---

### Task 1: Linchpin spike — bookmark survival through a DS edit→save

**Files:**
- (scratch, do NOT commit) `backend/_spike_bookmarks.py` — a throwaway inspection helper.

**Interfaces:** none produced; this is a gating validation.

This task is a controller/operator validation against the live Document Server (not automatable as a unit test). It answers: does OnlyOffice preserve `blk{i}` bookmarks when a user edits and saves?

- [ ] **Step 1: Ensure the stack is up** — backend on :8003 (with current code), the DS on :8080, a frontend dev server with `BACKEND_URL=http://127.0.0.1:8003`. Pick a doc id with an `output.{lang}.docx` (or open `/c/<id>?lang=<lang>` in 문서 편집 to lazily render one).

- [ ] **Step 2: Capture the baseline bookmark set.** Run the scratch helper to list `blk{i}` names in the freshly-rendered docx:

```python
# backend/_spike_bookmarks.py  (scratch)
import sys
from docx import Document
from docx.oxml.ns import qn

def bookmark_names(path):
    d = Document(path)
    names = []
    for bm in d.element.body.iter(qn("w:bookmarkStart")):
        n = bm.get(qn("w:name"))
        if n and n.startswith("blk"):
            names.append(n)
    return names

if __name__ == "__main__":
    print(sorted(bookmark_names(sys.argv[1]), key=lambda s: int(s[3:])))
```
Run: `cd backend && .venv/Scripts/python _spike_bookmarks.py storage/<doc_id>/output.<lang>.docx`
Expected: a list like `['blk0','blk1',...]`.

- [ ] **Step 3: Edit + save in the DS.** In the 문서 편집 editor: change the text of an existing paragraph, then close/disconnect (status 2) or force-save (status 6) so the `onlyoffice-callback` overwrites `output.<lang>.docx`. (Confirm the callback ran: the docx mtime changed.)

- [ ] **Step 4: Re-inspect the saved docx.** Run the helper again on the now-editor-saved `output.<lang>.docx`.
Expected (PASS): the existing `blk{i}` names are still present (the edited paragraph kept its bookmark). 
If FAIL (bookmarks stripped/renamed): STOP. The bookmark-anchored design does not hold as-is — revise the spec to make the text-similarity fallback (`align.py`-style sequence alignment over block texts) the primary linkage before continuing. Record the outcome.

- [ ] **Step 5: Record the result** in the PR/branch notes and delete the scratch helper (do not commit it).

---

### Task 2: Read each block's edited text from a docx, keyed by bookmark

**Files:**
- Create: `backend/pipeline/docx_sync.py`
- Test: `backend/tests/test_docx_sync_read.py`

**Interfaces:**
- Produces: `read_block_lines(docx_path: str) -> dict[int, list[str]]` — maps a block index `i` (from `blk{i}`) to the list of text lines currently in that bookmarked block (one element when the block is single-line; multiple when the rendered paragraph has `<w:br/>` breaks). Tables are intentionally excluded in Phase 1 (their bookmark is on the first cell; returning that cell's lines would be misleading) — skip a `blk{i}` whose owning paragraph is inside a `w:tbl`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_docx_sync_read.py
import os
from models import SemanticDocument, HeadingBlock, ParagraphBlock, ListItemBlock, CaptionBlock
from renderers.docx_renderer import render_docx
from pipeline.docx_sync import read_block_lines


def _render(tmp_path, doc):
    out = os.path.join(str(tmp_path), "out.docx")
    render_docx(doc, out, str(tmp_path))
    return out


def test_read_single_and_multiline_blocks(tmp_path):
    doc = SemanticDocument(blocks=[
        HeadingBlock(level=1, marker="1.", text="제목"),          # blk0 → "1. 제목"
        ParagraphBlock(text="한 줄 문단."),                        # blk1
        ParagraphBlock(lines=["첫째 줄", "둘째 줄"]),               # blk2 → 2 lines via <w:br/>
        ListItemBlock(marker="-", text="항목"),                    # blk3 → "- 항목"
        CaptionBlock(text="그림 1"),                               # blk4
    ])
    out = _render(tmp_path, doc)
    got = read_block_lines(out)
    assert got[0] == ["1. 제목"]          # marker still baked in at this layer (stripped in Task 3)
    assert got[1] == ["한 줄 문단."]
    assert got[2] == ["첫째 줄", "둘째 줄"]  # split on <w:br/>
    assert got[3] == ["- 항목"]
    assert got[4] == ["그림 1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_docx_sync_read.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pipeline.docx_sync'`.

- [ ] **Step 3: Implement `read_block_lines`**

```python
# backend/pipeline/docx_sync.py
"""Read OnlyOffice-edited block text back out of a rendered .docx, keyed by the
`blk{i}` bookmarks that render_docx stamps. Phase 1: text only (heading/paragraph/
list/caption); tables + figures + structural diffs are Phase 2."""
from docx import Document
from docx.oxml.ns import qn


def _paragraph_lines(p_elem) -> list[str]:
    """Text of a w:p, split into lines on <w:br/> line breaks. Mirrors render_docx's
    `_add_text`, which separates preserved `.lines` with WD_BREAK.LINE (<w:br/>)."""
    lines, cur = [], []
    for node in p_elem.iter(qn("w:t"), qn("w:br")):
        if node.tag == qn("w:br"):
            # A <w:br/> with no w:type (or type="textWrapping") is a line break.
            if node.get(qn("w:type")) in (None, "textWrapping"):
                lines.append("".join(cur))
                cur = []
        else:
            cur.append(node.text or "")
    lines.append("".join(cur))
    return lines


def read_block_lines(docx_path: str) -> dict[int, list[str]]:
    d = Document(docx_path)
    out: dict[int, list[str]] = {}
    for bm in d.element.body.iter(qn("w:bookmarkStart")):
        name = bm.get(qn("w:name")) or ""
        if not name.startswith("blk"):
            continue
        try:
            idx = int(name[3:])
        except ValueError:
            continue
        # The bookmark lives inside its block's paragraph. Find the ancestor w:p.
        p = bm
        while p is not None and p.tag != qn("w:p"):
            p = p.getparent()
        if p is None:
            continue
        # Phase 1: skip table blocks (bookmark is on the first cell only).
        anc = p.getparent()
        in_table = False
        while anc is not None:
            if anc.tag == qn("w:tbl"):
                in_table = True
                break
            anc = anc.getparent()
        if in_table:
            continue
        out[idx] = _paragraph_lines(p)
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_docx_sync_read.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/docx_sync.py backend/tests/test_docx_sync_read.py
git commit -m "feat(sync): read edited block text from docx by blk{i} bookmark"
```

---

### Task 3: Apply edited text into the model in place (structure-preserving)

**Files:**
- Modify: `backend/pipeline/docx_sync.py`
- Test: `backend/tests/test_docx_sync_apply.py`

**Interfaces:**
- Consumes: `read_block_lines` (Task 2); `models` block types; `iter_segments`/structure of `SemanticDocument`.
- Produces: `apply_docx_edits_to_model(model: SemanticDocument, docx_path: str) -> SemanticDocument` — returns a deep copy of `model` with each in-scope block `i` (Heading/Paragraph/ListItem/Caption) whose `blk{i}` is present in `docx_path` updated: its `text` (or `.lines`) replaced by the edited text, with the block's known `marker` prefix stripped from the first line. Blocks not present in the docx (deleted) or extra docx blocks (inserted) are left untouched in Phase 1. Round-trip identity: applying the edits of an UNedited render leaves the model unchanged.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_docx_sync_apply.py
import os
from models import SemanticDocument, HeadingBlock, ParagraphBlock, ListItemBlock
from renderers.docx_renderer import render_docx
from pipeline.docx_sync import apply_docx_edits_to_model
from docx import Document
from docx.oxml.ns import qn


def _render(tmp_path, doc, name="out.docx"):
    out = os.path.join(str(tmp_path), name)
    render_docx(doc, out, str(tmp_path))
    return out


def _model():
    return SemanticDocument(blocks=[
        HeadingBlock(level=1, marker="1.", text="제목", source_span_ids=["s0"]),
        ParagraphBlock(text="원래 문장.", source_span_ids=["s1"]),
        ParagraphBlock(lines=["줄 A", "줄 B"], source_span_ids=["s2"]),
        ListItemBlock(marker="-", text="항목", source_span_ids=["s3"]),
    ])


def test_unedited_roundtrip_is_identity(tmp_path):
    m = _model()
    out = _render(tmp_path, m)
    result = apply_docx_edits_to_model(m, out)
    assert result.model_dump() == m.model_dump()   # no spurious change


def test_text_edit_updates_only_that_block_preserving_structure(tmp_path):
    m = _model()
    out = _render(tmp_path, m)
    # Simulate an OnlyOffice text edit: change blk1's run text in the saved docx.
    d = Document(out)
    for bm in d.element.body.iter(qn("w:bookmarkStart")):
        if bm.get(qn("w:name")) == "blk1":
            p = bm
            while p.tag != qn("w:p"):
                p = p.getparent()
            for t in p.iter(qn("w:t")):
                t.text = "고친 문장."
                break
    d.save(out)

    result = apply_docx_edits_to_model(m, out)
    assert result.blocks[1].text == "고친 문장."
    # structure + other blocks + source linkage preserved:
    assert result.blocks[0].marker == "1." and result.blocks[0].text == "제목"
    assert result.blocks[2].lines == ["줄 A", "줄 B"]
    assert result.blocks[3].marker == "-" and result.blocks[3].text == "항목"
    assert result.blocks[1].source_span_ids == ["s1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_docx_sync_apply.py -v`
Expected: FAIL — `ImportError: cannot import name 'apply_docx_edits_to_model'`.

- [ ] **Step 3: Implement `apply_docx_edits_to_model`**

Add to `backend/pipeline/docx_sync.py`:

```python
from models import (
    SemanticDocument, HeadingBlock, ParagraphBlock, ListItemBlock, CaptionBlock,
)

# Block kinds whose text Phase 1 syncs (single bookmarked paragraph each).
_TEXT_BLOCKS = (HeadingBlock, ParagraphBlock, ListItemBlock, CaptionBlock)


def _strip_marker(first_line: str, marker: str | None) -> str:
    """render_docx prepends `"{marker} "` (list) or `"{marker} {text}"` (heading) — strip
    that exact prefix so the marker isn't doubled on the next render."""
    if marker:
        prefix = f"{marker} "
        if first_line.startswith(prefix):
            return first_line[len(prefix):]
    return first_line


def apply_docx_edits_to_model(model: SemanticDocument, docx_path: str) -> SemanticDocument:
    edits = read_block_lines(docx_path)
    out = model.model_copy(deep=True)
    for i, block in enumerate(out.blocks):
        if i not in edits or not isinstance(block, _TEXT_BLOCKS):
            continue
        lines = list(edits[i])
        if not lines:
            continue
        marker = getattr(block, "marker", None)
        lines[0] = _strip_marker(lines[0], marker)
        if getattr(block, "lines", None):
            # Preserve the .lines shape only when the edited line-count still matches;
            # otherwise fall back to a single reflowed text (Phase 2 handles re-splitting).
            if len(lines) == len(block.lines):
                block.lines = lines
            else:
                block.lines = None
                block.text = "\n".join(lines)
        else:
            block.text = "\n".join(lines) if len(lines) > 1 else lines[0]
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_docx_sync_apply.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/pipeline/docx_sync.py backend/tests/test_docx_sync_apply.py
git commit -m "feat(sync): apply edited docx text into the model in place (structure-preserving)"
```

---

### Task 4: Persist the synced model on the OnlyOffice save callback

**Files:**
- Modify: `backend/main.py` (`onlyoffice_callback`, ~632-656)
- Test: `backend/tests/test_onlyoffice_callback_sync.py`

**Interfaces:**
- Consumes: `apply_docx_edits_to_model` (Task 3); `process.get_export_model`, `process.save_edited_model`, `process.output_docx_path`.
- Produces: after a status 2/6 save, `edited.{lang}.json` reflects the editor's text edits (so `get_export_model` → the HTML views see them).

- [ ] **Step 1: Write the failing test** (drives the callback's persistence directly, isolating the DS fetch)

```python
# backend/tests/test_onlyoffice_callback_sync.py
import os, json
import models, main
from renderers.docx_renderer import render_docx
from pipeline import process
from docx import Document
from docx.oxml.ns import qn


def test_callback_persists_edited_model(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    doc_id, lang = "docX", "en"
    d = os.path.join(str(tmp_path), doc_id)
    os.makedirs(os.path.join(d, "assets"), exist_ok=True)
    # seed a translated model (the export model) + render its docx
    m = models.SemanticDocument(source_lang="ko", blocks=[
        models.ParagraphBlock(text="hello world", source_span_ids=["s0"]),
    ])
    with open(os.path.join(d, f"translated.{lang}.json"), "w", encoding="utf-8") as fh:
        json.dump(m.model_dump(), fh)
    out = process.output_docx_path(str(tmp_path), doc_id, lang)
    render_docx(m, out, os.path.join(d, "assets"))
    # simulate an editor text edit in the saved docx
    dd = Document(out)
    for t in dd.element.body.iter(qn("w:t")):
        t.text = "edited world"; break
    dd.save(out)

    # the new helper the callback will call:
    main._sync_model_from_saved_docx(doc_id, lang)

    edited = process.get_export_model(str(tmp_path), doc_id, lang)
    assert edited.blocks[0].text == "edited world"
    assert os.path.exists(process.edited_path(str(tmp_path), doc_id, lang))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_onlyoffice_callback_sync.py -v`
Expected: FAIL — `AttributeError: module 'main' has no attribute '_sync_model_from_saved_docx'`.

- [ ] **Step 3: Implement the sync helper + call it from the callback**

Add a helper near `_ensure_docx` in `backend/main.py`:

```python
def _sync_model_from_saved_docx(doc_id: str, lang: str | None) -> None:
    """After the DS saves output.{lang}.docx, reflect its text edits into the model so the
    HTML views (재번역/용어/고유명사) see them and re-renders don't clobber them. Phase 1:
    structure-preserving text sync. Best-effort — never breaks the save."""
    from pipeline.docx_sync import apply_docx_edits_to_model
    try:
        model = process.get_export_model(STORAGE, doc_id, lang)
        eff_lang = lang or model.source_lang
        out = process.output_docx_path(STORAGE, doc_id, eff_lang)
        if not os.path.exists(out):
            return
        synced = apply_docx_edits_to_model(model, out)
        process.save_edited_model(STORAGE, doc_id, eff_lang, synced)
    except Exception:  # noqa: BLE001 — never fail the editor save on a sync hiccup
        import logging
        logging.getLogger("uvicorn.error").warning(
            "model sync from saved docx failed for %s/%s", doc_id, lang, exc_info=True)
```

Then call it in `onlyoffice_callback`, right after the docx is written (after the `with open(out, "wb")...` block, still inside the `if data.get("status") in (2, 6) ...:` branch):

```python
        with httpx.stream("GET", src, timeout=60) as r:
            r.raise_for_status()
            with open(out, "wb") as fh:
                for chunk in r.iter_bytes():
                    fh.write(chunk)
        _sync_model_from_saved_docx(doc_id, eff_lang)   # ← reflect edits into the model
    return {"error": 0}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_onlyoffice_callback_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/main.py backend/tests/test_onlyoffice_callback_sync.py
git commit -m "feat(sync): reflect editor text edits into the model on the save callback"
```

---

### Task 5: Full Phase-1 verification

**Files:** none (verification only).

- [ ] **Step 1: Backend suite** — Run: `cd backend && python -m pytest tests/test_docx_sync_read.py tests/test_docx_sync_apply.py tests/test_onlyoffice_callback_sync.py -v` → all PASS. Then run the renderer suites to confirm no regression: `python -m pytest tests/test_render_html_segments.py tests/test_render_html_layout.py -v`.

- [ ] **Step 2: Live end-to-end** (the user's actual scenario): with the stack up, open a doc in 문서 편집, edit a paragraph's text, save (disconnect/forcesave), then open 재번역 (or 용어/고유명사) and confirm the edited text now appears in the HTML view + the 문장 list. Then trigger a HTML-mode re-render (e.g. a glossary apply) and confirm the editor edit is preserved (not clobbered).

- [ ] **Step 3: Data-loss case** — edit in 문서 편집 → save → 재번역 apply on a different sentence → reopen 문서 편집 → confirm BOTH edits are present (the editor edit survived the re-render because it was synced into the model first).

---

## Phases 2 & 3 — follow-on plans (after Phase 1 lands + the spike passes)

These get their own plans (their exact code depends on Phase 1's `docx_sync` API and the spike result):

- **Phase 2 — structure + linkage-based pairing.** Detect inserted/deleted/reordered blocks from the bookmark set; build the edited model with a per-segment `source` index mapping (edited segment → source segment, or `None`); rework `build_segments`/`retranslate_one_segment`/glossary+proper-noun reports to be linkage-based (iterate the edited model, resolve source via the mapping); add `Segment.inserted` + the frontend affordance (disable 재번역 for inserted segments); handle tables + within-block line changes. Persist the mapping (`edited.{lang}.map.json`). Define the full-retranslate-clears-`edited.json` precedence.
- **Phase 3 — formatting sync.** Read `bold`/`italic`/`underline`/`align`/`color` from the bookmarked block's runs into the model (directly syncable). Handle `font_size` via inverting `scaled_pt` (`size ≈ rendered * body_pt / 11`, needs `body_pt`) and `line_spacing` (`src ≈ rendered * 1.15`), or store absolute + a renderer skip-rescale flag — decided after a round-trip test.

## Self-review notes
- Phase 1 changes are additive + structure-preserving; `build_segments`/`retranslate` are deliberately untouched (positional pairing still valid because structure is unchanged).
- The `_sync_model_from_saved_docx` is best-effort (try/except) so a parse hiccup never breaks the editor's save (the docx is still written first).
- `read_block_lines` skips table-bookmarks in Phase 1 (table sync = Phase 2), so a doc with tables still syncs its paragraph/heading text safely.
