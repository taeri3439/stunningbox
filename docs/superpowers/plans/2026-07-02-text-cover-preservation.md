# Text-Cover Preservation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. TDD.

**Goal:** White/text covers (huge title + sparse text + logo) get the same cover treatment as designed covers: redacted full-bleed background, dedicated cover page (renderer break already exists), and page-0 chrome kept as translated body text.

**Architecture:** `backend/pipeline/reconciler.py` only: one pure helper (`_looks_like_text_cover`), one condition change at the cover gate (~line 154), one addition in the chrome branch (page-0 + cover → also emit aligned ParagraphBlocks). Spans carry `size` (see `extractor.py` RawSpan `size=round(span.get("size", 0.0), 2)`).

## Global Constraints

- Deterministic only. Never `git add -A`; protected files (`backend/auth.py`, `backend/documents_store.py`, `frontend/**`, `backend/_*.py`, `test-pdfs/`, root images) untouched.
- `iter_segments`/`build_segment_index_map` untouched. Existing designed-cover behavior unchanged.
- From `backend/`: `./.venv/Scripts/python.exe -m pytest …`. Full suite at the end (15 pre-existing test_auth/test_documents_store failures excluded).
- Commits end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: `_looks_like_text_cover` + relaxed cover gate (TDD)

**Files:** Modify `backend/pipeline/reconciler.py`; Test `backend/tests/test_text_cover.py` (create).

- [ ] **Step 1: Failing tests:**

```python
"""White text-cover detection (sparse page-0 with a display-size title)."""
from types import SimpleNamespace

from pipeline.reconciler import _looks_like_text_cover


def _span(size, text="x"):
    return SimpleNamespace(text=text, size=size, bbox=(0, 0, 10, 10))


def test_sparse_page_with_display_title_is_cover():
    spans = [_span(42.0), _span(16.0), _span(12.0), _span(11.0)]
    assert _looks_like_text_cover(spans, n_pages=21) is True


def test_many_spans_is_not_cover():
    spans = [_span(12.0) for _ in range(30)] + [_span(42.0)]
    assert _looks_like_text_cover(spans, n_pages=21) is False


def test_no_display_font_is_not_cover():
    spans = [_span(14.0), _span(12.0)]
    assert _looks_like_text_cover(spans, n_pages=21) is False


def test_single_page_doc_is_not_cover():
    spans = [_span(42.0)]
    assert _looks_like_text_cover(spans, n_pages=1) is False


def test_empty_page_is_not_cover():
    assert _looks_like_text_cover([], n_pages=5) is False
```

- [ ] **Step 2: Verify failure. Step 3: Implement:**

Constants (next to the existing COVER_* constants ~line 42-47):

```python
COVER_MAX_SPANS = 18  # a text cover is sparse; content pages carry far more spans
COVER_DISPLAY_FONT_PT = 26.0  # a cover carries a display-size title
```

Helper (near `_page_nonwhite_frac` usage / other cover helpers):

```python
def _looks_like_text_cover(page_spans, n_pages: int) -> bool:
    """A WHITE cover (huge title + sparse text + whitespace) fails the non-white
    fraction check but is still a cover: sparse span count + a display-size title.
    Single-page documents have no cover."""
    if n_pages < 2 or not page_spans or len(page_spans) > COVER_MAX_SPANS:
        return False
    return max((getattr(s, "size", 0.0) or 0.0) for s in page_spans) >= COVER_DISPLAY_FONT_PT
```

Cover gate (~line 154) — change the nonwhite condition to an OR:

```python
        if (page_index == 0 and pg and cover_bg is None and page_w and page_h
                and _body_blocks <= COVER_MAX_BODY_BLOCKS
                and (_page_nonwhite_frac(pg.render_path, asset_dir) >= COVER_PAGE_NONWHITE_FRAC
                     or _looks_like_text_cover(page_spans, len(raw.pages)))):
```

- [ ] **Step 4: New tests + cover-related existing tests pass** (`-k "cover or chrome or band"`). **Step 5: Commit** — `feat(cover): treat sparse display-title white first pages as covers`.

---

### Task 2: page-0 chrome kept as body text on covers (TDD)

**Files:** Modify `backend/pipeline/reconciler.py` (chrome branch); extend `backend/tests/test_text_cover.py`.

**Why safe:** with a cover background the docx section sets `different_first_page_header_footer`, so running bands never draw on the cover page — emitting page-0 chrome as body text cannot double-render.

- [ ] **Step 1: Failing test** — reconcile-level. Copy the fixture pattern from an existing reconcile-driving test (RawDocument + page_blocks; monkeypatch `figure_overlay.render_redacted_clip` to return "cover_bg.png" without rendering, and `_page_nonwhite_frac` to return 0.05). Fixture: 2 pages; page-0 blocks = `{type:"chrome", text:"KFS 0011", source_span_ids:[…top-right span…]}` + `{type:"heading", text:"소방장비기본규격", …}` with a 42pt span; page-1 = one paragraph. Assert:
  - `doc.cover_background == "cover_bg.png"`;
  - some page-0 `ParagraphBlock` has text `"KFS 0011"` with `align == "right"`;
  - the chrome span ids are in `referenced` (report has no missing spans);
  - a control fixture WITHOUT cover (content page) does NOT emit the chrome paragraph (current behavior preserved).

Write the test fully in the neighboring fixtures' style.

- [ ] **Step 2: Verify failure. Step 3: Implement** — in the chrome branch (now cluster-based after the band feature), after computing each cluster's `(ctext, cxc, cy)` and side, add:

```python
                        if page_index == 0 and cover_bg is not None:
                            # A cover's chrome (standard number, issue tag) is part of the
                            # cover design and usually appears ONLY here, so band voting
                            # drops it. Keep it as body text on the cover page; the band
                            # never renders on a cover (different first-page header).
                            local.append((cy, ParagraphBlock(
                                text=ctext, page=0, align=side, source_span_ids=[])))
```

placed so it runs IN ADDITION to the header/footer_items recording (do not `continue` before recording). Keep `referenced.update(ids)` as-is. (`ParagraphBlock` is already imported.)

- [ ] **Step 4: Tests pass. Step 5: Commit** — `feat(cover): keep page-0 chrome as translated body text on covers`.

---

### Task 3: dev-local integration check

**Files:** Create `backend/tests/test_text_cover_ladder.py` (skippable, same scaffolding as `test_band_integration_pharma.py`); create `backend/storage/_cover-verify-ladder` by copying `backend/storage/test-ladder` first (do not modify the original).

- [ ] **Step 1: Test:** rebuild via `process.rebuild_source_model(pdf, DOC, STORAGE, source_lang="ko")`, then assert `doc.cover_background` is truthy AND any page-0 block's text contains `"KFS 0011"` AND `os.path.exists(asset_dir/cover_bg.png)`.
- [ ] **Step 2: Run — must PASS** (debug with printed page-0 span count/max size if the gate doesn't fire; adjust only if the real data legitimately contradicts — report it).
- [ ] **Step 3: Full suite.** **Step 4: Commit** — `test(cover): ladder cached-analysis text-cover check`.

## Self-Review
- Spec 1→T1, 2→T2, verification→T3. Existing designed-cover path untouched (OR-condition only widens). Chrome recording for bands unchanged (addition, not replacement). Complete code/fixture directions given.
