# Running-Band Dedup/Clustering/Page-Numbers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. TDD per task.

**Goal:** Fix running header/footer band duplication, merged-chunk misclassification, and lost page numbers (PDF reconciler + docx renderer).

**Architecture:** Three pure helpers in `backend/pipeline/reconciler.py` (span clustering, side dedup, page-number spec) wired into the existing chrome branch / `_pick_band` / band assembly, plus `RunningBand.page_number_format` and a format-aware page-number emitter in `backend/renderers/docx_renderer.py` + `NUMPAGES` support in `_ooxml`.

**Tech Stack:** Python 3, pydantic, python-docx, pytest.

## Global Constraints

- Deterministic only — no LLM/API calls anywhere in these changes.
- Do NOT touch `iter_segments` / `build_segment_index_map` (models.py band seg-ids unchanged).
- Do NOT stage/modify `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`, `backend/_*.py` scratch, `test-pdfs/`, root images/logs. Never `git add -A`.
- Existing tests must stay green: run the band-related suites after each task (`python -m pytest tests/test_running_chrome_image.py tests/test_text_formatting.py -q`) and the full suite in the final task (15 pre-existing failures in test_auth/test_documents_store are NOT ours).
- Commit messages end with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`
- Run everything from `backend/` with `.venv/Scripts/python.exe`.

---

### Task 1: `_split_chrome_clusters` + chrome-branch wiring

**Files:**
- Modify: `backend/pipeline/reconciler.py` (add helper near `_chrome_side`; rewrite the `btype == "chrome"` branch at ~lines 177-189)
- Test: `backend/tests/test_band_clustering.py` (create)

**Interfaces:**
- Produces: `_split_chrome_clusters(spans, page_w) -> list[tuple[str, float, float]]` — `(text, x_center, y_center)` per visually-separate run, x-ordered. Consumed by the chrome branch; later tasks don't call it directly.

- [ ] **Step 1: Write failing tests** (`backend/tests/test_band_clustering.py`):

```python
"""Chrome span clustering: split a merged header/footer block into side runs by x-gap."""
from types import SimpleNamespace

from pipeline.reconciler import _split_chrome_clusters


def _span(text, x0, x1, y0=10.0, y1=20.0):
    return SimpleNamespace(text=text, bbox=(x0, y0, x1, y1))


def test_wide_gap_splits_into_two_clusters():
    spans = [_span("Sun Pharmaceutical Industries Ltd.", 50, 220),
             _span("Confidential", 400, 470)]
    out = _split_chrome_clusters(spans, page_w=612.0)
    assert [t for t, _, _ in out] == ["Sun Pharmaceutical Industries Ltd.", "Confidential"]
    assert out[0][1] < 0.4 * 612 and 0.4 * 612 < out[1][1] < 0.6 * 612  # left, center


def test_adjacent_spans_stay_one_cluster():
    spans = [_span("2.7.2 Summary of", 50, 150), _span("Clinical Pharmacology", 155, 300)]
    out = _split_chrome_clusters(spans, page_w=612.0)
    assert len(out) == 1
    assert out[0][0] == "2.7.2 Summary of Clinical Pharmacology"


def test_three_way_split_left_center_right():
    spans = [_span("Company", 40, 120), _span("Confidential", 280, 340), _span("Page 3 of 32", 500, 580)]
    out = _split_chrome_clusters(spans, page_w=612.0)
    assert [t for t, _, _ in out] == ["Company", "Confidential", "Page 3 of 32"]


def test_empty_and_bboxless_spans_skipped():
    spans = [SimpleNamespace(text="x", bbox=None), _span("  ", 10, 20)]
    assert _split_chrome_clusters(spans, page_w=612.0) == []


def test_no_page_width_uses_point_fallback_gap():
    spans = [_span("A", 0, 10), _span("B", 60, 70)]  # 50pt gap > 24pt fallback
    out = _split_chrome_clusters(spans, page_w=0)
    assert [t for t, _, _ in out] == ["A", "B"]
```

- [ ] **Step 2: Run to verify failure**
Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_band_clustering.py -q` — expect ImportError/failures.

- [ ] **Step 3: Implement.** Add near `_chrome_side` in `reconciler.py`:

```python
def _split_chrome_clusters(spans, page_w: float) -> list[tuple[str, float, float]]:
    """Group a chrome block's spans into visually separate runs by horizontal gap.

    The analyzer sometimes returns a page's whole header/footer line as ONE block
    ("Company   …   Confidential   Page 3 of 32"); classifying that by its median x
    buries the side structure and bakes run-on whitespace into one band cell. A gap
    wider than 8% of the page width (24pt when the width is unknown) starts a new
    cluster. Returns (text, x_center, y_center) per cluster, in x order."""
    items = sorted((s for s in spans if getattr(s, "bbox", None)), key=lambda s: s.bbox[0])
    gap = 0.08 * page_w if page_w else 24.0
    clusters: list[list] = []
    cur: list = []
    for s in items:
        if cur and s.bbox[0] - cur[-1].bbox[2] > gap:
            clusters.append(cur)
            cur = []
        cur.append(s)
    if cur:
        clusters.append(cur)
    out: list[tuple[str, float, float]] = []
    for cl in clusters:
        text = " ".join((s.text or "").strip() for s in cl if (s.text or "").strip())
        if not text:
            continue
        x0 = min(s.bbox[0] for s in cl)
        x1 = max(s.bbox[2] for s in cl)
        y = statistics.median((s.bbox[1] + s.bbox[3]) / 2 for s in cl)
        out.append((text, (x0 + x1) / 2, y))
    return out
```

Rewrite the chrome branch (currently computes block-level `ys`/`xs` medians) to:

```python
            if btype == "chrome":
                referenced.update(ids)  # accounted for, but reproduced as header/footer
                spans_ = [by_id[i] for i in ids if i in by_id]
                if page_h:
                    for ctext, cxc, cy in _split_chrome_clusters(spans_, page_w):
                        side = _chrome_side(cxc, page_w)
                        if cy < 0.2 * page_h:
                            header_items.append((ctext, side, page_index))
                        elif cy > 0.8 * page_h:
                            footer_items.append((ctext, side, page_index))
                continue
```

- [ ] **Step 4: Run tests** — new file passes AND `tests/test_running_chrome_image.py tests/test_text_formatting.py` stay green. If a chrome fixture in those suites asserts on the old merged-text behavior, fix the expectation ONLY if the new behavior is the spec-correct one (split sides); report it.

- [ ] **Step 5: Commit** — `git add backend/pipeline/reconciler.py backend/tests/test_band_clustering.py` + message `fix(bands): split merged chrome blocks into side runs by span gap`.

---

### Task 2: `_dedupe_band_sides` in `_pick_band`

**Files:**
- Modify: `backend/pipeline/reconciler.py` (helper + 1 call in `_pick_band` ~line 1315)
- Test: `backend/tests/test_band_dedup.py` (create)

**Interfaces:**
- Produces: `_dedupe_band_sides(left, center, right) -> tuple[str|None, str|None, str|None]`; `_pick_band` output bands never repeat a text fragment across sides.

- [ ] **Step 1: Failing tests** (`backend/tests/test_band_dedup.py`):

```python
"""Cross-side band dedup: a center that restates split left/right must not render twice."""
from pipeline.reconciler import _dedupe_band_sides, _pick_band


def test_center_concatenation_of_left_right_dropped():
    l, c, r = _dedupe_band_sides(
        "2.7.2 Summary of Clinical Pharmacology Studies",
        "2.7.2 Summary of Clinical Pharmacology Studies   CB-03-01 (clascoterone) cream 1%",
        "CB-03-01 (clascoterone) cream 1%")
    assert c is None and l and r


def test_center_equal_to_left_dropped():
    l, c, r = _dedupe_band_sides("Confidential", "Confidential", None)
    assert c is None and l == "Confidential"


def test_distinct_sides_kept():
    l, c, r = _dedupe_band_sides("Left", "Middle", "Right")
    assert (l, c, r) == ("Left", "Middle", "Right")


def test_left_equal_right_keeps_one():
    l, c, r = _dedupe_band_sides("Same", None, "Same")
    assert l == "Same" and r is None


def test_pick_band_applies_dedup():
    # pages alternate: merged center block vs split left/right — voting fills all 3 sides
    items = []
    for pg in (0, 2, 4):
        items += [("Alpha Corp", "left", pg), ("Page Title", "right", pg)]
    for pg in (1, 3, 5):
        items += [("Alpha Corp Page Title", "center", pg)]
    band = _pick_band(items, None)
    assert band.left == "Alpha Corp" and band.right == "Page Title" and band.center is None
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.** Helper above `_pick_band`:

```python
def _dedupe_band_sides(left, center, right):
    """Never render the same band text twice. Mixed analyzer output (merged one-block
    pages vs split left/right pages) can fill CENTER with the concatenation of LEFT
    and RIGHT via per-side voting — prefer the split sides and drop the restatement."""
    def norm(t):
        return re.sub(r"\s+", " ", t or "").strip()

    l, c, r = norm(left), norm(center), norm(right)
    if c and (c == l or c == r or (l and r and l in c and r in c)):
        center = None
    if l and r and l == r:
        right = None
    return left, center, right
```

In `_pick_band`, after picking the three sides and before `if not (left or center or right or band_rule)`, insert `left, center, right = _dedupe_band_sides(left, center, right)`.

- [ ] **Step 4: Run tests** — new + band suites green.
- [ ] **Step 5: Commit** — `fix(bands): drop a center band text that restates the split sides`.

---

### Task 3: Page-number formats (`_page_number_spec` + model field + docx fields)

**Files:**
- Modify: `backend/pipeline/reconciler.py` (replace `_page_number_side` ~line 1407 with `_page_number_spec`; update the call at ~line 678)
- Modify: `backend/models.py` (`RunningBand.page_number_format: Optional[str] = None` after `page_number`)
- Modify: `backend/renderers/docx_renderer.py` (`_render_band` page-number emission → `_append_page_number`)
- Modify: `backend/renderers/_ooxml.py` (add `add_numpages_field`, mirroring `add_page_field` with instr text `NUMPAGES`) — read the existing `add_page_field` first and mirror its exact XML pattern.
- Test: `backend/tests/test_band_page_numbers.py` (create)

**Interfaces:**
- Produces: `_page_number_spec(items) -> tuple[str, str] | None` ((side, format), format uses `{n}`/`{m}`); `RunningBand.page_number_format`; `_append_page_number(paragraph, fmt)` in docx_renderer.

- [ ] **Step 1: Failing tests** (`backend/tests/test_band_page_numbers.py`):

```python
"""Page-number chrome: recognize 'Page N of M' / 'N/M' / '- N -' / bare digits and
render PAGE (+ NUMPAGES) fields in the band."""
import docx as _docx
from pipeline.reconciler import _page_number_spec
from models import RunningBand, SemanticDocument
from renderers.docx_renderer import render_docx


def test_page_n_of_m_detected():
    items = [(f"Page {i} of 32", "right", i) for i in range(1, 6)]
    assert _page_number_spec(items) == ("right", "Page {n} of {m}")


def test_bare_digits_legacy_still_detected():
    items = [(str(i), "center", i) for i in (2, 3, 4)]
    assert _page_number_spec(items) == ("center", "{n}")


def test_slash_format():
    items = [(f"{i}/20", "right", i) for i in (1, 2, 3)]
    assert _page_number_spec(items) == ("right", "{n}/{m}")


def test_non_number_chrome_ignored():
    items = [("Confidential", "center", 0), ("Confidential", "center", 1)]
    assert _page_number_spec(items) is None


def test_single_hit_not_enough():
    assert _page_number_spec([("Page 1 of 2", "right", 0)]) is None


def test_docx_renders_page_and_numpages_fields(tmp_path):
    doc = SemanticDocument(
        source_lang="en",
        blocks=[],
        running_footer=RunningBand(left="Acme", page_number="right",
                                   page_number_format="Page {n} of {m}"),
    )
    out = str(tmp_path / "o.docx")
    render_docx(doc, out, str(tmp_path))
    d = _docx.Document(out)
    xml = d.sections[0].footer._element.xml
    assert "PAGE" in xml and "NUMPAGES" in xml
    assert "Page " in xml and " of " in xml  # literal chrome around the fields
```

- [ ] **Step 2: Run to verify failure.**

- [ ] **Step 3: Implement.**

`models.py` — add after `page_number`:
```python
    page_number_format: Optional[str] = None  # e.g. "Page {n} of {m}" — {n}=PAGE, {m}=NUMPAGES
```

`reconciler.py` — replace `_page_number_side` with:
```python
_PN_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)^page\s+(\d+)\s+of\s+(\d+)$"), "Page {n} of {m}"),
    (re.compile(r"^(\d+)\s*/\s*(\d+)$"), "{n}/{m}"),
    (re.compile(r"^-\s*(\d+)\s*-$"), "- {n} -"),
]


def _page_number_spec(items: list) -> tuple[str, str] | None:
    """(side, format) where page-number chrome recurs (>=2 pages). Verbose forms
    ("Page 3 of 32") carry a format with {n}/{m} placeholders; bare digit/roman runs
    keep the legacy plain PAGE field ("{n}")."""
    by_side: dict[str, list[str]] = {}
    for text, side, _pg in items:
        t = (text or "").strip()
        if not t:
            continue
        for pat, fmt in _PN_PATTERNS:
            if pat.match(t):
                by_side.setdefault(side, []).append(fmt)
                break
        else:
            if re.fullmatch(r"[\divxlcDIVXLC\-\.\s/]+", t):
                by_side.setdefault(side, []).append("{n}")
    best: tuple[str, str, int] | None = None
    for side, fmts in by_side.items():
        if len(fmts) < 2:
            continue
        fmt = Counter(fmts).most_common(1)[0][0]
        if best is None or len(fmts) > best[2]:
            best = (side, fmt, len(fmts))
    return (best[0], best[1]) if best else None
```

Call-site (~line 678) becomes:
```python
    pn = _page_number_spec(footer_items)
    if pn:
        footer_band = footer_band or RunningBand()
        footer_band.page_number, footer_band.page_number_format = pn
```

`docx_renderer.py` — in `_render_band` replace `if band.page_number == align: _ooxml.add_page_field(cp)` with `if band.page_number == align: _append_page_number(cp, band.page_number_format)` and add:
```python
def _append_page_number(paragraph, fmt: str | None) -> None:
    """Emit page-number chrome: literal runs around a PAGE field ({n}) and, when the
    source showed a total ("Page 3 of 32"), a NUMPAGES field ({m})."""
    fmt = fmt or "{n}"
    pos = 0
    for m in re.finditer(r"\{[nm]\}", fmt):
        if m.start() > pos:
            paragraph.add_run(fmt[pos:m.start()])
        (_ooxml.add_page_field if m.group() == "{n}" else _ooxml.add_numpages_field)(paragraph)
        pos = m.end()
    if pos < len(fmt):
        paragraph.add_run(fmt[pos:])
```
(Ensure `import re` exists in docx_renderer; add if missing.)

`_ooxml.py` — read `add_page_field` and add `add_numpages_field` identical but with the `NUMPAGES` instruction text.

- [ ] **Step 4: Run tests** — new file + `tests/test_running_chrome_image.py` green. The legacy fixture asserting `page_number == "right"` must still pass (bare-digit rule kept, no variation requirement). If it asserted through the old function name, update the fixture's call only if it imported `_page_number_side` directly.
- [ ] **Step 5: Commit** — `feat(bands): reproduce 'Page N of M' page-number chrome via PAGE/NUMPAGES fields`.

---

### Task 4: Integration verification (no API)

**Files:**
- Create: `backend/tests/test_band_integration_pharma.py` — SKIPPED unless the cached fixture exists (storage is dev-local, not CI):

```python
"""End-to-end (cached-analysis) check on the real pharma fixture: rebuilt bands are
deduplicated and page numbers detected. Skips when the dev storage copy is absent."""
import os

import pytest

STORAGE = os.path.join(os.path.dirname(__file__), "..", "storage")
DOC = "_hf-verify-pharma"


@pytest.mark.skipif(
    not os.path.exists(os.path.join(STORAGE, DOC, "page_blocks.json")),
    reason="dev-local cached fixture not present",
)
def test_pharma_bands_dedup_and_page_number():
    from pipeline import process

    pdf = os.path.join(STORAGE, DOC, "source.pdf")
    doc, _report = process.rebuild_source_model(pdf, DOC, os.path.abspath(STORAGE), source_lang="ko")
    h = doc.running_header
    assert h is not None
    sides = [s for s in (h.left, h.center, h.right) if s]
    # no side text may contain another side's text (the duplication bug)
    for a in sides:
        for b in sides:
            if a is not b:
                assert a not in b
    f = doc.running_footer
    assert f is not None and f.page_number
    assert f.page_number_format == "Page {n} of {m}"
```

- [ ] **Step 1: Run it** — `cd backend && .venv/Scripts/python.exe -m pytest tests/test_band_integration_pharma.py -q -s`. Must PASS (the `_hf-verify-pharma` copy exists in dev storage). If assertions fail, the earlier tasks missed a case — diagnose and fix (report which).
- [ ] **Step 2: Full suite** — `.venv/Scripts/python.exe -m pytest -q`. Green except the 15 pre-existing test_auth/test_documents_store failures.
- [ ] **Step 3: Commit** — `test(bands): pharma cached-analysis integration check`.

## Self-Review
- Spec A→T1, B→T2, C→T3, verification→T4. No placeholders; complete code given. Types consistent (`(text, side, page)` items; `(side, fmt)` spec; Optional model field). Legacy digit behavior preserved (T3 test).
