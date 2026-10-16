# Font Style Fidelity (Marker Colour + Heading Weight) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Preserve two lost style-hierarchy signals: an accent-coloured `FIGURE N`/`TABLE N` marker keeps its own colour (instead of flattening to the title colour), and headings render at their source weight (bold) instead of always-regular.

**Architecture:** Capture the marker's own colour at reconcile (`_marker_color`) into a new `HeadingBlock.marker_color`; recover heading weight from the font-name weight class in `_dominant_style`; render the marker as its own coloured run in the docx heading branch. All additive and backward-compatible (`marker_color` defaults None → today's behaviour).

**Tech Stack:** PyMuPDF spans (`RawSpan`), python-docx, pytest.

## Global Constraints

- `RawSpan` fields: `id, page, text, bbox, font, size, bold, color, rotation`. `bbox = (x0, y0, x1, y1)` in PDF points. `color` is a packed int (or None); use `_is_ink(color & 0xFFFFFF)` to drop near-black.
- Colour hex format everywhere: uppercase `RRGGBB`, e.g. `f"{c & 0xFFFFFF:06X}"`.
- `marker_color` is set ONLY when the marker's colour is non-ink AND differs from the block's dominant `color` (same-colour markers stay None → no run split, no churn).
- Heading weight recovery applies to HEADING blocks only (`_dominant_style(..., heading=True)`); body text weight logic is unchanged.
- The heading marker + title must stay in ONE paragraph (so `docx_sync.read_block_lines`/`_strip_marker` round-trips identically).
- `re`, `statistics`, `Counter` are already imported in `reconciler.py`; `RGBColor`, `Pt` already imported in `docx_renderer.py`.
- Do NOT stage `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`, `backend/_*.py`, `test-pdfs/`, `backend/storage/`, root images.

---

### Task 1: `marker_color` model field + capture in reconciler

**Files:**
- Modify: `backend/models.py` (`HeadingBlock`)
- Modify: `backend/pipeline/reconciler.py` (new `_marker_color`; heading-branch wiring ~line 549-567)
- Test: `backend/tests/test_marker_color.py`

**Interfaces:**
- Produces: `HeadingBlock.marker_color: Optional[str]`; `reconciler._marker_color(ids: list[str], by_id: dict, marker: str | None) -> str | None`.

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_marker_color.py
from types import SimpleNamespace
from pipeline.reconciler import _marker_color

def _span(id, text, x0, y0, color):
    return SimpleNamespace(id=id, text=text, bbox=(x0, y0, x0+20, y0+10), color=color, font="", size=12, bold=False)

def test_marker_color_distinct_leading_span():
    # "FIGURE 3" gold (0xD4B031) then title teal (0x0D5775)
    spans = [_span("a", "FIGURE 3", 10, 100, 0xD4B031), _span("b", "BREAKDOWN OF X", 90, 100, 0x0D5775)]
    by_id = {s.id: s for s in spans}
    assert _marker_color(["a", "b"], by_id, "FIGURE 3") == "D4B031"

def test_marker_color_none_when_same_as_title():
    spans = [_span("a", "2.1.2.2", 10, 100, 0x007C9E), _span("b", "Break down", 60, 100, 0x007C9E)]
    by_id = {s.id: s for s in spans}
    # leading marker colour equals the title colour -> _marker_color still returns it (007C9E);
    # the reconciler is what suppresses it when == block colour. Here assert the raw colour.
    assert _marker_color(["a", "b"], by_id, "2.1.2.2") == "007C9E"

def test_marker_color_none_when_ink():
    spans = [_span("a", "1.", 10, 100, 0x000000), _span("b", "Intro", 40, 100, 0x0D5775)]
    by_id = {s.id: s for s in spans}
    assert _marker_color(["a", "b"], by_id, "1.") is None

def test_marker_color_none_when_marker_not_leading():
    spans = [_span("b", "Some title text", 10, 100, 0x0D5775)]
    by_id = {s.id: s for s in spans}
    assert _marker_color(["b"], by_id, "FIGURE 9") is None

def test_marker_color_none_marker_empty():
    assert _marker_color([], {}, None) is None
```

- [ ] **Step 2: Run to verify it fails** — `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_marker_color.py -v` → FAIL (no `_marker_color`).

- [ ] **Step 3: Add the model field.** In `backend/models.py`, `HeadingBlock`, add after `color`:
```python
    marker_color: Optional[str] = None  # marker's own hex 'RRGGBB' when it differs from the
    #                                      block colour (e.g. a gold 'FIGURE 3' on a teal title);
    #                                      None → marker renders in the block colour (default).
```

- [ ] **Step 4: Implement `_marker_color`** in `reconciler.py` (near `_dominant_inline`):
```python
def _marker_color(ids: list[str], by_id: dict, marker: str | None) -> str | None:
    """Colour of the marker's own leading source spans (e.g. an accent-coloured
    'FIGURE 3' label), which can differ from the block's dominant colour. Returns hex
    'RRGGBB', or None when the leading text isn't the marker or carries no non-ink colour."""
    if not marker:
        return None
    spans = [by_id[i] for i in ids if i in by_id and (by_id[i].text or "").strip()]
    if not spans:
        return None
    spans.sort(key=lambda s: (round(s.bbox[1] / 3.0), s.bbox[0]))  # reading order (y-band, x)
    target = "".join(marker.split())
    acc, marker_spans = "", []
    for s in spans:
        acc += "".join((s.text or "").split())
        marker_spans.append(s)
        if len(acc) >= len(target):
            break
    if not target or not acc.startswith(target):
        return None
    cols = [s.color for s in marker_spans
            if isinstance(s.color, int) and not _is_ink(s.color & 0xFFFFFF)]
    if not cols:
        return None
    return f"{Counter(cols).most_common(1)[0][0] & 0xFFFFFF:06X}"
```

- [ ] **Step 5: Wire into the heading branch** (reconciler.py ~549). Just before constructing `HeadingBlock`, compute and pass `marker_color`:
```python
            if btype == "heading":
                mcol = _marker_color(ids, by_id, b.get("marker"))
                local.append(
                    (
                        top,
                        HeadingBlock(
                            level=_as_int(b.get("level"), 1),
                            marker=b.get("marker"),
                            marker_color=(mcol if (mcol and mcol != color) else None),
                            text=b.get("text", ""),
                            page=page_index,
                            font_size=size,
                            bold=bold,
                            font=font,
                            color=color,
                            italic=italic,
                            align=align,
                            line_spacing=ls,
                            source_span_ids=ids,
                        ),
                    )
                )
```

- [ ] **Step 6: Run tests** — `pytest tests/test_marker_color.py -v` → PASS. Also `pytest tests/ -k reconcil -q` stays green.

- [ ] **Step 7: Commit** — `git add backend/models.py backend/pipeline/reconciler.py backend/tests/test_marker_color.py` then `feat(style): capture marker's own colour into HeadingBlock.marker_color`.

---

### Task 2: Heading weight recovery in `_dominant_style`

**Files:**
- Modify: `backend/pipeline/reconciler.py` (`_dominant_style`; module-level `_HEAVY_WEIGHT_RE`)
- Test: `backend/tests/test_heading_weight.py`

**Interfaces:**
- Consumes: `RawSpan.font`, `RawSpan.bold`.
- Produces: `_dominant_style(ids, by_id, heading=True)` returns `bold=True` when heading spans use a heavy named weight even with no PyMuPDF bold flag.

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_heading_weight.py
from types import SimpleNamespace
from pipeline.reconciler import _dominant_style

def _s(font, bold=False, size=13.0):
    return SimpleNamespace(font=font, bold=bold, size=size, color=0, text="X", bbox=(0,0,1,1))

def test_heading_medium_demi_recovered_as_bold():
    spans = [_s("FranklinGothic-MediumCon"), _s("FranklinGothic-DemiCond")]
    by_id = {str(i): s for i, s in enumerate(spans)}
    _, bold = _dominant_style(list(by_id), by_id, heading=True)
    assert bold is True

def test_body_medium_not_forced_bold():
    spans = [_s("SomeFont-Medium"), _s("SomeFont-Medium")]
    by_id = {str(i): s for i, s in enumerate(spans)}
    _, bold = _dominant_style(list(by_id), by_id, heading=False)
    assert bold is False  # body path unchanged: no bold flag, name has no literal "bold"

def test_regular_heading_not_forced_bold():
    spans = [_s("FranklinGothic-Book"), _s("FranklinGothic-Book")]
    by_id = {str(i): s for i, s in enumerate(spans)}
    _, bold = _dominant_style(list(by_id), by_id, heading=True)
    assert bold is False

def test_flag_bold_still_wins():
    spans = [_s("Arial", bold=True), _s("Arial", bold=True)]
    by_id = {str(i): s for i, s in enumerate(spans)}
    _, bold = _dominant_style(list(by_id), by_id, heading=True)
    assert bold is True
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_heading_weight.py -v` → FAIL (`test_heading_medium_demi_recovered_as_bold`).

- [ ] **Step 3: Implement.** Add module-level regex near the other regexes in `reconciler.py`:
```python
# Heavy named weights PyMuPDF often doesn't flag as bold (Franklin Gothic Medium/Demi, etc.).
# Used to recover a HEADING's weight from its font name so the weight tier survives.
_HEAVY_WEIGHT_RE = re.compile(r"bold|semibold|demibold|demi|medium|black|heavy", re.IGNORECASE)
```
Extend `_dominant_style`:
```python
def _dominant_style(ids: list[str], by_id: dict, heading: bool = False):
    spans = [by_id[i] for i in ids if i in by_id]
    sizes = [s.size for s in spans if s.size]
    bolds = [s.bold for s in spans]
    size = None
    if sizes:
        size = round(max(sizes) if heading else statistics.median(sizes), 1)
    bold = None
    if bolds:
        bold = sum(1 for b in bolds if b) * 2 >= len(bolds)  # majority
    # Headings often use a heavier NAMED weight (Medium/Demi/Semibold) that PyMuPDF does
    # not flag as bold; recover it from the font name so the heading weight tier survives
    # translation (the substitute font is binary-weight). Body text keeps the flag/name
    # behaviour above — a "Medium" body font is not forced bold.
    if heading and not bold:
        names = [(s.font or "") for s in spans if s.font]
        if names and sum(1 for n in names if _HEAVY_WEIGHT_RE.search(n)) * 2 >= len(names):
            bold = True
    return size, bold
```

- [ ] **Step 4: Run tests** — `pytest tests/test_heading_weight.py tests/test_marker_color.py -v` → PASS. `pytest tests/ -k reconcil -q` green.

- [ ] **Step 5: Commit** — `git add backend/pipeline/reconciler.py backend/tests/test_heading_weight.py` then `feat(style): recover heading weight from font-name weight class`.

---

### Task 3: Render the marker in its own colour

**Files:**
- Modify: `backend/renderers/docx_renderer.py` (HeadingBlock branch ~line 210-222)
- Test: `backend/tests/test_marker_render.py`

**Interfaces:**
- Consumes: `HeadingBlock.marker_color` (Task 1).

- [ ] **Step 1: Write the failing test**
```python
# backend/tests/test_marker_render.py
from docx import Document
from docx.oxml.ns import qn
from models import SemanticDocument, HeadingBlock
from renderers.docx_renderer import render_docx
from pipeline.docx_sync import apply_docx_edits_to_model

def _render(tmp_path, block):
    doc = SemanticDocument(blocks=[block], target_lang="ko")
    out = str(tmp_path / "o.docx"); render_docx(doc, out, str(tmp_path))
    return out

def _runs_with_color(docx_path):
    d = Document(docx_path)
    p = d.paragraphs[0]
    res = []
    for r in p.runs:
        c = r.font.color.rgb
        res.append((r.text, str(c) if c else None))
    return res

def test_marker_color_renders_two_runs(tmp_path):
    b = HeadingBlock(block_id="H", level=4, marker="FIGURE 3", marker_color="D4B031",
                     text="1차 인터뷰의 구분", color="0D5775", font_size=13.0)
    runs = _runs_with_color(_render(tmp_path, b))
    # marker run gold, title run teal
    joined = "".join(t for t, _ in runs)
    assert "FIGURE 3" in joined and "1차 인터뷰의 구분" in joined
    assert any(t.strip() == "FIGURE 3" and (c or "").upper() == "D4B031" for t, c in runs)
    assert any("인터뷰" in t and (c or "").upper() == "0D5775" for t, c in runs)

def test_no_marker_color_single_run(tmp_path):
    b = HeadingBlock(block_id="H", level=3, marker="2.1.2.2", marker_color=None,
                     text="1차 자료의 구분", color="007C9E", font_size=12.0)
    d = Document(_render(tmp_path, b))
    assert "2.1.2.2 1차 자료의 구분" in d.paragraphs[0].text  # combined, one visual colour

def test_marker_render_roundtrips_through_sync(tmp_path):
    b = HeadingBlock(block_id="H", level=4, marker="FIGURE 3", marker_color="D4B031",
                     text="타이틀", color="0D5775", font_size=13.0)
    out = _render(tmp_path, b)
    model = SemanticDocument(blocks=[b], target_lang="ko")
    synced = apply_docx_edits_to_model(model, out)
    h = [x for x in synced.blocks if isinstance(x, HeadingBlock)][0]
    assert h.text == "타이틀"  # marker stripped, title intact (round-trip lossless)
```

- [ ] **Step 2: Run to verify it fails** — `pytest tests/test_marker_render.py -v` → FAIL.

- [ ] **Step 3: Implement.** Replace the `HeadingBlock` branch (docx_renderer.py ~210-222) with:
```python
        if isinstance(block, HeadingBlock):
            lvl = min(max(block.level, 0), 9)
            mcolor = getattr(block, "marker_color", None)
            mrun = None
            if block.marker and mcolor:
                # Marker in its own colour: two runs in ONE paragraph (an accent 'FIGURE 3'
                # label on a differently-coloured title). docx_sync round-trips on the
                # concatenated text, so keeping both runs in one paragraph is lossless.
                p = d.add_heading("", level=lvl)
                mrun = p.add_run(f"{block.marker} ")
                p.add_run(block.text or "")
            else:
                text = f"{block.marker} {block.text}".strip() if block.marker else block.text
                p = d.add_heading(text, level=lvl)
            _mark_break(p, break_before)
            if parity:
                p.paragraph_format.space_before = Pt(4)
                p.paragraph_format.space_after = Pt(2)
            _apply_style(p, block, body_pt, body_ls, parity, doc.target_lang)
            if mrun is not None:
                try:
                    mrun.font.color.rgb = RGBColor.from_string(mcolor)
                except ValueError:
                    pass  # malformed hex → leave marker in the block colour
            _bookmark_block(p, bid, _bm_counter)
```
Note: `_apply_style` colours every run with the block colour; the `mrun` override runs AFTER, so the marker ends gold and the title stays teal. `add_heading("")` yields a run-less paragraph, so `p.runs` are exactly the two we add.

- [ ] **Step 4: Run tests** — `pytest tests/test_marker_render.py tests/test_docx_sync_apply.py tests/test_page_rhythm_render.py -v` → PASS (sync identity + page-parity heading marks unaffected).

- [ ] **Step 5: Commit** — `git add backend/renderers/docx_renderer.py backend/tests/test_marker_render.py` then `feat(style): render an accent marker in its own colour as a separate run`.

---

## Post-implementation (controller-run, not a task)

- Final whole-branch review (opus) via requesting-code-review; ONE fix subagent for any Critical/Important.
- Full suite: `cd backend && ./.venv/Scripts/python.exe -m pytest -q` (≈15 pre-existing auth failures expected; all else green).
- **REAL-SCREEN verification:** rebuild the vet source model (`process.rebuild_source_model`, cached analysis, no API) so `marker_color` + recovered bold populate; render EN→KO; open in the browser; confirm on-screen that `FIGURE 3` renders **gold (D4B031)** while its title stays **teal**, headings render **bold**, section-number markers stay one colour, body weight unchanged. Screenshot the FIGURE 3 heading and a heading-dense page before/after.

## Self-Review
- Spec coverage: Task 1 ↔ marker_color capture + field; Task 2 ↔ heading weight; Task 3 ↔ marker render. Verification is post-impl.
- Types consistent: `_marker_color(ids, by_id, marker) -> str|None`, `HeadingBlock.marker_color: Optional[str]`, hex `RRGGBB` uppercase throughout, used identically across tasks/tests.
- No placeholders: every step has concrete code + exact pytest commands.
