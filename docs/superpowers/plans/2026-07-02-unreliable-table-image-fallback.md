# Unreliable-Table Image Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. TDD.

**Goal:** Tables whose vision grid is structurally inconsistent render as a clipped image of the original region (caption preserved as text) instead of a drifting/duplicated text table.

**Architecture:** One pure helper + one fallback branch in `backend/pipeline/reconciler.py`, inserted after the grid-rebuild adoption and before the `TableBlock` append (~line 405). Reuses `extractor.render_clip`, `FigureBlock`, `CaptionBlock`.

## Global Constraints

- Deterministic only, no API. Never `git add -A`; never touch protected files (`backend/auth.py`, `backend/documents_store.py`, `frontend/**`, `backend/_*.py`, `test-pdfs/`, root images).
- `iter_segments`/`build_segment_index_map` untouched.
- Run from `backend/` with `.venv/Scripts/python.exe`. Band + table suites green after each task; full suite at the end (15 pre-existing test_auth/test_documents_store failures are not ours).
- Commits end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: `_table_structure_unreliable` helper (TDD)

**Files:** Modify `backend/pipeline/reconciler.py`; Test `backend/tests/test_table_fallback.py` (create).

- [ ] **Step 1: Failing tests** (`backend/tests/test_table_fallback.py`):

```python
"""Unreliable-table detection + image fallback."""
from models import TableCell
from pipeline.reconciler import _table_structure_unreliable


def _row(*colspans):
    return [TableCell(text="x", colspan=c) for c in colspans]


def test_watergun_like_ragged_mix_is_unreliable():
    # 17 rows whose column sums alternate 6/5/7 (the plug-gauge pattern)
    rows = []
    for i in range(17):
        s = (6, 5, 7)[i % 3]
        rows.append(_row(*([1] * s)))
    assert _table_structure_unreliable(rows) is True


def test_regular_table_is_reliable():
    rows = [_row(1, 1, 1, 1) for _ in range(8)]
    assert _table_structure_unreliable(rows) is False


def test_merged_header_still_reliable():
    # header uses colspans but sums to the same width — not ragged
    rows = [_row(2, 2)] + [_row(1, 1, 1, 1) for _ in range(7)]
    assert _table_structure_unreliable(rows) is False


def test_small_tables_never_fall_back():
    rows = [_row(1, 1), _row(1, 1, 1), _row(1)]
    assert _table_structure_unreliable(rows) is False


def test_mild_raggedness_kept_as_text():
    # 23% ragged (7 of 30) — below the 0.3 threshold
    rows = [_row(*([1] * (7 if i % 4 == 2 else 6))) for i in range(28)]
    assert _table_structure_unreliable(rows) is False


def test_zero_modal_is_unreliable():
    rows = [[] for _ in range(6)]
    assert _table_structure_unreliable(rows) is True
```

- [ ] **Step 2: Verify failure.**
- [ ] **Step 3: Implement** in `reconciler.py` (near the other table helpers):

```python
_TABLE_RAGGED_FRACTION = 0.3


def _table_structure_unreliable(rows) -> bool:
    """True when the vision grid is too inconsistent to render as a real table: on a
    5+ row table, the per-row column count (sum of colspans) disagrees with the modal
    count on >=30% of rows (or the modal is 0). Complex merged layouts — diagonal
    split headers, nested row groups — read this way, and a drifted-column text table
    is strictly worse than an image of the original region."""
    if len(rows) < 5:
        return False
    sums = [sum(c.colspan for c in r) for r in rows]
    modal = Counter(sums).most_common(1)[0][0]
    if modal <= 0:
        return True
    ragged = sum(1 for s in sums if s != modal)
    return ragged / len(sums) >= _TABLE_RAGGED_FRACTION
```

(`Counter` is already imported in reconciler.py — verify.)

- [ ] **Step 4: Tests pass. Step 5: Commit** — `feat(tables): detect structurally unreliable vision tables`.

---

### Task 2: fallback branch in the table path (TDD)

**Files:** Modify `backend/pipeline/reconciler.py` (insert after the grid-rebuild block, immediately before `local.append((_top_y(span_pool, by_id), TableBlock(...)))`); extend `backend/tests/test_table_fallback.py`.

**Interfaces:** consumes Task 1's helper; emits `FigureBlock` (+ optional `CaptionBlock`) instead of `TableBlock` when unreliable.

- [ ] **Step 1: Failing test.** Drive `reconcile()` with a synthetic fixture. COPY the fixture pattern from an existing reconcile-driving test (see `backend/tests/` — e.g. the image-table or chrome tests build a RawDocument + page_blocks and monkeypatch `extractor.render_clip`). Requirements for the new test:

```python
def test_ragged_table_falls_back_to_image(monkeypatch, tmp_path):
    """A >=30%-ragged vision table reconciles to FigureBlock (+ CaptionBlock) with a
    warning; its spans count as referenced (no coverage-gate misses)."""
    # fixture: one page, spans covering a ragged 6-row table (rows alternate 2/3 cells),
    # analyzer page_blocks = [{type: 'table', caption: '표 1', rows: [...], source_span_ids: [...]}]
    # monkeypatch extractor.render_clip -> writes nothing, returns ('imgtable_p0_0.png', bbox)
    # assert: no TableBlock in doc.blocks; exactly one FigureBlock with image_ref 'imgtable_p0_0.png';
    #         a CaptionBlock '표 1' precedes it; report.warnings mention 'unreliable structure';
    #         report.missing_span_ids == []
```

Write it fully (real code, not the sketch above) following the neighboring fixtures' style, and a companion `test_clean_table_still_reconciles_to_tableblock` (regular 4x4 → TableBlock, no fallback warning).

- [ ] **Step 2: Verify failure. Step 3: Implement** — insert before the TableBlock append:

```python
                if tb_bbox and _table_structure_unreliable(rows):
                    fb = list(gbbox) if gbbox else list(tb_bbox)
                    pad = 3.0
                    fb = [fb[0] - pad, fb[1] - pad, fb[2] + pad, fb[3] + pad]
                    name = f"imgtable_p{page_index}_{len(local)}.png"
                    try:
                        image_ref, geom = extractor.render_clip(
                            pdf_path, page_index, fb, asset_dir, name)
                    except Exception as e:  # noqa: BLE001 — keep the text table on clip failure
                        report.warnings.append(
                            f"unreliable-table clip failed p{page_index}: {e}")
                        image_ref, geom = "", None
                    if image_ref and geom:
                        occupied.append((geom[1], geom[3]))
                        figure_regions.append(tuple(geom))
                        cap = b.get("caption")
                        if cap:
                            local.append((geom[1] - 0.1,
                                          CaptionBlock(text=cap, page=page_index)))
                        local.append((
                            geom[1],
                            FigureBlock(
                                image_ref=image_ref,
                                width=round(geom[2] - geom[0], 1),
                                height=round(geom[3] - geom[1], 1),
                                align=_figure_align(geom, page_w),
                                page=page_index,
                            ),
                        ))
                        referenced.update(span_pool)
                        report.warnings.append(
                            f"table on p{page_index} rendered as image (unreliable structure)")
                        continue
```

(`CaptionBlock` import: check the models import list at the top of reconciler.py and add it if absent.)

- [ ] **Step 4: New tests + full table/band suites pass. Step 5: Commit** — `feat(tables): image fallback for structurally unreliable tables`.

---

### Task 3: dev-local integration check

**Files:** Create `backend/tests/test_table_fallback_watergun.py` (skippable like the F1 pharma test); create the isolated copy first:

- [ ] **Step 1:** Copy `backend/storage/test-watergun` → `backend/storage/_tbl-verify-watergun` (shutil in a tiny setup script or PowerShell; do NOT modify the original).
- [ ] **Step 2: Test** (skipif fixture missing):

```python
def test_watergun_plug_gauge_table_falls_back():
    from pipeline import process
    doc, report = process.rebuild_source_model(pdf, DOC, STORAGE, source_lang="ko")
    # p21 (0-indexed) plug-gauge table must now be a figure, not a 17-row ragged table
    p21_tables = [b for b in doc.blocks if b.type == "table" and b.page == 21]
    assert not any(len(t.rows) >= 15 for t in p21_tables)
    assert any("unreliable structure" in w for w in report["warnings"])
    # clean tables survive as tables (rubber packing p22, nozzle p23)
    assert any(b.type == "table" and b.page == 22 for b in doc.blocks)
    assert any(b.type == "table" and b.page == 23 for b in doc.blocks)
```

Write it fully with the same STORAGE/skipif scaffolding as `test_band_integration_pharma.py`.

- [ ] **Step 3:** Run it (must PASS against the dev copy; if the fallback doesn't fire, debug the threshold wiring — print the p21 table's row sums). **Step 4:** Full suite. **Step 5: Commit** — `test(tables): watergun cached-analysis fallback check`.

## Self-Review
- Threshold verified against real data (53% broken / 23% mild / 0% clean). Fallback placed post-rebuild so corrected tables aren't degraded. Caption survives as translatable text. Clip failure keeps the text table.
