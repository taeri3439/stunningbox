# HTML Preview Layout-Fidelity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the 용어 관리 HTML preview (`/api/documents/{id}/preview`) to DOCX parity by having `html_renderer.py` consume the same block formatting fields `docx_renderer.py` uses, emitted as CSS (pt units).

**Architecture:** Extract the two subtle tuned pure helpers (`scaled_pt`, `widths_would_cram`) into a shared `renderers/_layout.py` (also home to `dominant_body_pt`, `body_line_spacing`, `content_pt`), then rewrite `render_html` to wrap content in a page-sized "sheet" and emit per-block inline CSS for typography/alignment/indent/line-height, table column widths + cell styling, figure size/float, dividers, and running header/footer bands. Backend-only; no model/endpoint/frontend changes. The `render_html` signature, glossary `<mark>` injection, and `blk{i}` anchors are unchanged.

**Tech Stack:** Python 3 · FastAPI · pydantic models (`backend/models.py`) · pytest. The HTML renderer is plain string building (no python-docx).

---

## 사전 메모 (읽고 시작)

- **브랜치:** `html-preview-layout-2026-06-16` (origin/main 기반, spec 커밋 포함).
- **로컬 전용 미커밋 파일 — 절대 커밋 금지:** `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`. 각 `git add`는 **해당 파일 경로만** 지정 (`git add -A`/`git add .` 금지).
- **테스트:** 백엔드는 pytest 보유 → 전 태스크 TDD. `cd backend && python -m pytest <path> -v`. 전체 회귀: `cd backend && python -m pytest -q` (베이스라인 약 246 passed, 1 skipped).
- **단위:** CSS `pt`를 그대로 사용(1pt=1/72in). docx의 pt 기반 스케일/지오메트리를 그대로 옮김.
- **수동 확인(컨트롤러 담당):** 구현자는 pytest + 커밋만. 브라우저 시각 확인은 컨트롤러가 로컬 앱(backend :8001, frontend prod build)에서 용어 관리 모드로 수행.

## File Structure

| 파일 | 역할 | 변경 |
|---|---|---|
| `backend/renderers/_layout.py` | 두 렌더러 공유 순수 헬퍼 (scaled_pt, widths_would_cram, dominant_body_pt, body_line_spacing, content_pt, MAX_LINE_PITCH, INK_HEX) | **신규** |
| `backend/renderers/docx_renderer.py` | DOCX 렌더러 | `_scaled_pt`/`_widths_would_cram` 로컬 정의 삭제 → `_layout`에서 import (동작 불변) |
| `backend/renderers/html_renderer.py` | HTML 미리보기 렌더러 | 타이포·시트·표·그림·구분선·러닝밴드 (대폭) |
| `backend/tests/test_layout_helpers.py` | `_layout` 순수 함수 단위테스트 | **신규** |
| `backend/tests/test_render_html_layout.py` | html_renderer 레이아웃 테스트 | **신규** |
| `backend/tests/test_render_html_highlight.py` | 기존 하이라이트 테스트 | 회귀 확인만 (무변경) |

---

## Task 1: 공유 레이아웃 헬퍼 모듈 + docx 이관

**Files:**
- Create: `backend/renderers/_layout.py`
- Create test: `backend/tests/test_layout_helpers.py`
- Modify: `backend/renderers/docx_renderer.py` (remove local `_scaled_pt` lines ~226-231 and `_widths_would_cram` lines ~432-451; import from `_layout`)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_layout_helpers.py`:

```python
from models import ParagraphBlock, SemanticDocument, TableCell
from renderers._layout import (
    content_pt,
    dominant_body_pt,
    scaled_pt,
    widths_would_cram,
)


def test_scaled_pt_maps_to_11pt_body():
    assert scaled_pt(10, 10) == 11          # body size → 11pt
    assert scaled_pt(20, 10) == 22          # 2x body → 22pt
    assert scaled_pt(100, 10) == 30         # clamped to 30 max
    assert scaled_pt(1, 10) == 8            # clamped to 8 min
    assert scaled_pt(None, 10) is None      # no size → no scaling
    assert scaled_pt(10, None) is None


def test_content_pt_from_geometry_and_fallback():
    doc = SemanticDocument(page_width=595.0, margin_left=72.0, margin_right=72.0)
    assert content_pt(doc) == 451.0         # 595 - 72 - 72
    assert content_pt(SemanticDocument()) == 455.0  # A4 text-width fallback


def test_dominant_body_pt_is_most_common_paragraph_size():
    doc = SemanticDocument(blocks=[
        ParagraphBlock(text="a", font_size=10.0),
        ParagraphBlock(text="b", font_size=10.0),
        ParagraphBlock(text="c", font_size=18.0),
    ])
    assert dominant_body_pt(doc) == 10.0
    assert dominant_body_pt(SemanticDocument()) is None


def test_widths_would_cram_flags_long_latin_in_narrow_col():
    rows = [[TableCell(text="Classification"), TableCell(text="x")]]
    # col 0 gets only 10% of a 400pt content width = 40pt; "Classification" (14 chars)
    # at 11pt needs ~14*11*0.58+7 ≈ 96pt → crams.
    assert widths_would_cram(rows, [0.1, 0.9], 2, 400.0, 11.0) is True
    # Wide enough → no cram.
    assert widths_would_cram(rows, [0.6, 0.4], 2, 400.0, 11.0) is False
    # CJK never crams (no Latin runs).
    cjk = [[TableCell(text="분류표"), TableCell(text="값")]]
    assert widths_would_cram(cjk, [0.1, 0.9], 2, 400.0, 11.0) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_layout_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'renderers._layout'`.

- [ ] **Step 3: Create `_layout.py`**

Create `backend/renderers/_layout.py`:

```python
"""Pure layout helpers shared by the DOCX and HTML renderers — font-size scaling, table
column-cram detection, and document-level body metrics. No renderer-specific (python-docx
or HTML) dependencies, so both renderers import the same tuned logic instead of drifting."""
from __future__ import annotations

import re
import statistics
from collections import Counter

from models import ListItemBlock, ParagraphBlock, SemanticDocument

MAX_LINE_PITCH = 1.4  # cap on rendered line pitch (multiple of font size); see docx_renderer
INK_HEX = "1B1A17"    # near-black body/heading ink (hex, no leading '#')


def scaled_pt(size: float | None, body_pt: float | None) -> int | None:
    """Map a source font size onto an 11pt body, preserving the original ratio (clamped
    8..30pt). None when we can't scale (→ keep the default)."""
    if not size or not body_pt:
        return None
    return max(8, min(30, round(11 * size / body_pt)))


def widths_would_cram(rows, fractions: list[float], ncols: int,
                      content_pt: float, font_pt: float) -> bool:
    """A fixed source-proportional width 'crams' a column when it's narrower than that
    column's longest UNBREAKABLE Latin token (forcing an ugly one-glyph-per-line break).
    CJK wraps anywhere so it never crams."""
    longest = [0] * ncols
    for row in rows:
        c = 0
        for cell in row:
            cs = max(1, getattr(cell, "colspan", 1) or 1)
            if cs == 1 and c < ncols:
                runs = re.findall(r"[A-Za-z0-9][A-Za-z0-9.\-/(),]*",
                                  getattr(cell, "text", "") or "")
                longest[c] = max(longest[c], max((len(x) for x in runs), default=0))
            c += cs
    return any(longest[i] * font_pt * 0.58 + 7 > fractions[i] * content_pt + 1
               for i in range(ncols))


def dominant_body_pt(doc: SemanticDocument) -> float | None:
    """Most common font size (pt) among body paragraphs/list items; fallback to any sized
    block, else None (→ no scaling)."""
    sizes = [round(b.font_size, 1) for b in doc.blocks
             if isinstance(b, (ParagraphBlock, ListItemBlock)) and b.font_size]
    if not sizes:
        sizes = [round(b.font_size, 1) for b in doc.blocks if getattr(b, "font_size", None)]
    return Counter(sizes).most_common(1)[0][0] if sizes else None


def body_line_spacing(doc: SemanticDocument) -> float | None:
    """Median line spacing of body paragraphs/list items (used where a block has none)."""
    vals = [b.line_spacing for b in doc.blocks
            if isinstance(b, (ParagraphBlock, ListItemBlock)) and b.line_spacing]
    return round(statistics.median(vals), 2) if vals else None


def content_pt(doc: SemanticDocument) -> float:
    """Usable text width (pt) = page width minus L/R margins. A4 text-width fallback when
    the source carries no geometry (legacy source.json)."""
    if doc.page_width:
        return doc.page_width - (doc.margin_left or 0) - (doc.margin_right or 0)
    return 455.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_layout_helpers.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Migrate `docx_renderer.py` to import the shared helpers**

In `backend/renderers/docx_renderer.py`:

(a) Delete the local `_scaled_pt` definition (currently lines ~226-231):
```python
def _scaled_pt(size: float | None, body_pt: float | None) -> int | None:
    """Map a source font size onto an 11pt body, preserving the original ratio.
    Clamped to a sane range. None when we can't scale (→ keep the style default)."""
    if not size or not body_pt:
        return None
    return max(8, min(30, round(11 * size / body_pt)))
```

(b) Delete the local `_widths_would_cram` definition (currently lines ~432-451, the whole `def _widths_would_cram(...)` through its `return any(...)`).

(c) Add an import near the top of the file (after the existing `from models import ...` block):
```python
from renderers._layout import scaled_pt as _scaled_pt, widths_would_cram as _widths_would_cram
```
(All existing call sites use `_scaled_pt(...)` / `_widths_would_cram(...)` — the aliased import keeps them working unchanged.)

- [ ] **Step 6: Run the FULL backend suite — docx behavior must be unchanged**

Run: `cd backend && python -m pytest -q`
Expected: same as baseline (≈246 passed, 1 skipped) + 4 new from Step 1. The existing docx render/col-width tests (e.g. `test_render_table_colwidth.py`, `test_render_pagination.py`) must still pass — proving the extraction didn't change docx output.

- [ ] **Step 7: Commit**

```bash
git add backend/renderers/_layout.py backend/tests/test_layout_helpers.py backend/renderers/docx_renderer.py
git commit -m "refactor(backend): share scaled_pt/widths_would_cram via renderers/_layout"
```

---

## Task 2: HTML 타이포그래피 + 종이 시트

`render_html`이 지배적 본문 크기 기준으로 블록별 서식(크기/굵기/이탤릭/밑줄/색/정렬/들여쓰기/줄간격)을 inline CSS로 emit하고, 본문을 `page_width` 폭의 시트로 감싼다. 표/그림/구분선/밴드는 후속 태스크.

**Files:**
- Create test: `backend/tests/test_render_html_layout.py`
- Modify: `backend/renderers/html_renderer.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_render_html_layout.py`:

```python
from models import (
    CaptionBlock,
    HeadingBlock,
    ListItemBlock,
    ParagraphBlock,
    SemanticDocument,
)
from renderers.html_renderer import render_html


def test_typography_emitted_as_inline_css():
    doc = SemanticDocument(blocks=[
        ParagraphBlock(text="body", font_size=10.0),  # sets dominant body = 10pt
        ParagraphBlock(text="big centered", font_size=20.0, align="center"),
        ParagraphBlock(text="bold red", bold=True, color="CC0000"),
        ParagraphBlock(text="indented", indent=24.0),
        ParagraphBlock(text="loose", line_spacing=2.0),
    ])
    html = render_html(doc)
    assert "font-size:22pt" in html        # 20pt scaled against 10pt body → 22pt
    assert "text-align:center" in html
    assert "font-weight:700" in html
    assert "color:#CC0000" in html
    assert "margin-left:24pt" in html
    assert "line-height:1.4" in html       # 2.0 capped at MAX_LINE_PITCH


def test_page_sheet_uses_geometry():
    doc = SemanticDocument(page_width=595.0, margin_left=72.0, margin_right=72.0,
                           margin_top=72.0, margin_bottom=72.0,
                           blocks=[ParagraphBlock(text="x")])
    html = render_html(doc)
    assert 'class="page"' in html
    assert "width:595pt" in html
    assert "padding:72pt 72pt 72pt 72pt" in html


def test_page_sheet_fallback_without_geometry():
    html = render_html(SemanticDocument(blocks=[ParagraphBlock(text="x")]))
    assert 'class="page"' in html
    assert "width:595pt" not in html       # no inline width → CSS default (max-width:820px)


def test_list_indent_prefers_measured_then_level():
    doc = SemanticDocument(blocks=[
        ListItemBlock(marker="1)", level=2, text="lvl only"),
        ListItemBlock(marker="2)", level=2, text="measured", indent=30.0),
    ])
    html = render_html(doc)
    assert "margin-left:3em" in html       # level 2 * 1.5em, no measured indent
    assert "margin-left:30pt" in html      # measured indent wins


def test_caption_still_rendered():
    html = render_html(SemanticDocument(blocks=[CaptionBlock(text="Fig 1")]))
    assert 'class="caption"' in html and "Fig 1" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_render_html_layout.py -v`
Expected: FAIL (e.g. `font-size:22pt` not found — current renderer emits no inline typography).

- [ ] **Step 3: Rewrite `render_html` + add `_inline_style`**

In `backend/renderers/html_renderer.py`, update the imports block and replace `render_html` (lines ~35-94). Add the `_inline_style` helper above `render_html`.

Change the imports at the top (add `DividerBlock` and `RunningBand` for later tasks, and the `_layout` import):
```python
from models import (
    CaptionBlock,
    DividerBlock,
    FigureBlock,
    HeadingBlock,
    ListItemBlock,
    ParagraphBlock,
    RunningBand,
    SemanticDocument,
    TableBlock,
)
from renderers._layout import (
    INK_HEX,
    MAX_LINE_PITCH,
    body_line_spacing,
    content_pt,
    dominant_body_pt,
    scaled_pt,
)
```

Add `_inline_style` (place right after `_mark`):
```python
def _inline_style(block, body_pt: "float | None", body_ls: "float | None") -> str:
    """Per-block CSS declarations mirroring docx_renderer._apply_style: scaled font size,
    weight, italic/underline, source colour, alignment, measured indent, capped line pitch.
    Font family is intentionally omitted (system stack is safer for CJK in the browser)."""
    d: list[str] = []
    size = scaled_pt(getattr(block, "font_size", None), body_pt)
    if size is not None:
        d.append(f"font-size:{size}pt")
    bold = getattr(block, "bold", None)
    if bold is True:
        d.append("font-weight:700")
    elif bold is False:
        d.append("font-weight:400")
    if getattr(block, "italic", None):
        d.append("font-style:italic")
    if getattr(block, "underline", None):
        d.append("text-decoration:underline")
    color = getattr(block, "color", None)
    if color:
        d.append(f"color:#{color}")
    align = getattr(block, "align", None)
    if align in ("center", "right", "justify"):
        d.append(f"text-align:{align}")
    indent = getattr(block, "indent", None)
    if indent:
        d.append(f"margin-left:{indent:g}pt")
    ls = getattr(block, "line_spacing", None) or body_ls
    if ls:
        d.append(f"line-height:{round(min(max(ls, 1.15), MAX_LINE_PITCH), 2):g}")
    return ";".join(d)
```

Replace `render_html` with:
```python
def render_html(doc: SemanticDocument, asset_url_prefix: str = "assets",
                highlight_pattern: "re.Pattern | None" = None,
                anchor_blocks: bool = False) -> str:
    body_pt = dominant_body_pt(doc)
    body_ls = body_line_spacing(doc)
    cpt = content_pt(doc)

    parts: list[str] = []
    prev_page: int | None = None
    for i, block in enumerate(doc.blocks):
        page = getattr(block, "page", None)
        if page is not None and prev_page is not None and page != prev_page:
            parts.append('<div class="page-gap" data-page-break="true"></div>')
        if page is not None:
            prev_page = page

        anchor = f' id="blk{i}"' if anchor_blocks else ""
        sty = _inline_style(block, body_pt, body_ls)
        sa = f' style="{sty}"' if sty else ""
        if isinstance(block, HeadingBlock):
            lvl = min(max(block.level, 1), 6)
            marker = f'<span class="marker">{escape(block.marker)}</span> ' if block.marker else ""
            parts.append(f"<h{lvl}{anchor}{sa}>{marker}{_mark(block.text, highlight_pattern)}</h{lvl}>")
        elif isinstance(block, ListItemBlock):
            ls_sty = sty
            if getattr(block, "indent", None) is None and block.level:
                extra = f"margin-left:{block.level * 1.5:g}em"
                ls_sty = f"{sty};{extra}" if sty else extra
            lsa = f' style="{ls_sty}"' if ls_sty else ""
            parts.append(
                f'<div class="li"{anchor}{lsa}>'
                f'<span class="marker">{escape(block.marker)}</span> '
                f"{_mark(block.text, highlight_pattern)}</div>"
            )
        elif isinstance(block, ParagraphBlock):
            parts.append(f"<p{anchor}{sa}>{_mark(block.text, highlight_pattern)}</p>")
        elif isinstance(block, TableBlock):
            parts.append(_render_table(block, highlight_pattern, anchor))
        elif isinstance(block, FigureBlock):
            if block.image_ref:
                parts.append(
                    f'<figure{anchor}><img src="{asset_url_prefix}/{escape(block.image_ref)}" '
                    f'alt="figure"/></figure>'
                )
        elif isinstance(block, CaptionBlock):
            parts.append(f'<p class="caption"{anchor}{sa}>{_mark(block.text, highlight_pattern)}</p>')

    # Page sheet sized to the source paper when geometry is present; else CSS default.
    if doc.page_width:
        pg = (f"width:{doc.page_width:g}pt;"
              f"padding:{(doc.margin_top or 36):g}pt {(doc.margin_right or 36):g}pt "
              f"{(doc.margin_bottom or 36):g}pt {(doc.margin_left or 36):g}pt")
        page_open = f'<div class="page" style="{pg}">'
    else:
        page_open = '<div class="page">'
    body = "\n".join(parts)
    return f"""<!doctype html>
<html lang="{escape(doc.target_lang or doc.source_lang)}">
<meta charset="utf-8">
<style>
  html {{ background: #eef1f4; }}
  body {{ font-family: system-ui, "Malgun Gothic", sans-serif; line-height: 1.5;
          margin: 0; padding: 1.5rem 0; color: #1a1a1a; }}
  .page {{ background: #fff; max-width: 820px; margin: 0 auto; padding: 2rem 1.25rem;
           box-shadow: 0 1px 4px rgba(0,0,0,.12); }}
  h1,h2,h3,h4,h5,h6 {{ line-height: 1.3; margin: 1.2em 0 .5em; }}
  .marker {{ color: #555; font-weight: 600; }}
  .li {{ margin: .25em 0; }}
  .caption {{ text-align: center; color: #555; font-size: .9em; font-style: italic; }}
  table {{ border-collapse: collapse; width: 100%; margin: 1em 0; }}
  th, td {{ border: 1px solid #999; padding: .35em .55em; text-align: left; vertical-align: top; }}
  th {{ background: #f3f3f3; }}
  figure {{ margin: 1em 0; text-align: center; }}
  figure img {{ max-width: 100%; height: auto; }}
  .band {{ display: flex; justify-content: space-between; gap: 1em; color: #555;
           font-size: 9pt; padding: .25em 0; }}
  .page-gap {{ height: 1.5rem; }}
  mark.gloss {{ background: #fde68a; color: inherit; padding: 0 .05em; border-radius: 2px; }}
</style>
<body>
{page_open}
{body}
</div>
</body>
</html>"""
```

> Note: `_render_table` keeps its current signature in this task (table styling is Task 3). `render_fragment` still works (it slices `<body>…</body>`; the `.page` div rides along).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_render_html_layout.py tests/test_render_html_highlight.py -v`
Expected: new layout tests PASS; the existing highlight tests still PASS (mark/anchors unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/renderers/html_renderer.py backend/tests/test_render_html_layout.py
git commit -m "feat(backend): HTML preview typography + page sheet from geometry"
```

---

## Task 3: 표 (열 폭 + 셀 서식)

**Files:**
- Modify: `backend/renderers/html_renderer.py` (`_render_table` + the table call site in `render_html`)
- Modify test: `backend/tests/test_render_html_layout.py`

- [ ] **Step 1: Write the failing test (append to `test_render_html_layout.py`)**

```python
from models import TableBlock, TableCell  # add to imports at top of the test file


def test_table_colgroup_and_cell_styles():
    doc = SemanticDocument(page_width=595.0, margin_left=72.0, margin_right=72.0, blocks=[
        TableBlock(
            col_widths=[0.7, 0.3],
            rows=[[
                TableCell(text="설명입니다", align="center", valign="center",
                          shading="EEEEEE"),
                TableCell(text="값", vertical=True),
            ]],
        ),
    ])
    html = render_html(doc)
    assert "<colgroup>" in html
    assert "table-layout:fixed" in html
    assert "text-align:center" in html
    assert "vertical-align:middle" in html        # valign center → middle
    assert "background:#EEEEEE" in html
    assert "writing-mode:vertical-rl" in html


def test_table_cram_falls_back_to_autofit():
    # 5% width for a long Latin word → would cram → no fixed colgroup.
    doc = SemanticDocument(page_width=595.0, margin_left=72.0, margin_right=72.0, blocks=[
        TableBlock(col_widths=[0.05, 0.95],
                   rows=[[TableCell(text="Classification"), TableCell(text="x")]]),
    ])
    html = render_html(doc)
    assert "table-layout:fixed" not in html
    assert "<colgroup>" not in html
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_render_html_layout.py -v -k table`
Expected: FAIL (no colgroup / cell styles yet).

- [ ] **Step 3: Implement enhanced `_render_table`**

In `render_html`, change the table branch call to pass `body_pt` and `cpt`:
```python
        elif isinstance(block, TableBlock):
            parts.append(_render_table(block, highlight_pattern, anchor, body_pt, cpt))
```

Replace the existing `_render_table` (lines ~103-118) with:
```python
_VALIGN = {"top": "top", "center": "middle", "bottom": "bottom"}


def _cell_style(cell) -> str:
    d: list[str] = []
    align = getattr(cell, "align", None)
    if align in ("center", "right", "justify"):
        d.append(f"text-align:{align}")
    valign = _VALIGN.get(getattr(cell, "valign", None) or "")
    if valign:
        d.append(f"vertical-align:{valign}")
    shading = getattr(cell, "shading", None)
    if shading:
        d.append(f"background:#{shading}")
    color = getattr(cell, "color", None)
    if color:
        d.append(f"color:#{color}")
    if getattr(cell, "vertical", False):
        d.append("writing-mode:vertical-rl")
    return ";".join(d)


def _render_table(block: TableBlock, highlight_pattern: "re.Pattern | None" = None,
                  anchor: str = "", body_pt: "float | None" = None,
                  content_pt_val: float = 455.0) -> str:
    ncols = max((sum(max(1, c.colspan) for c in row) for row in block.rows), default=0)
    colgroup, table_style = "", ""
    fr = block.col_widths
    if (fr and ncols and len(fr) == ncols
            and not widths_would_cram(block.rows, fr, ncols, content_pt_val, body_pt or 11.0)):
        cols = "".join(f'<col style="width:{round(f * content_pt_val, 1):g}pt">' for f in fr)
        colgroup = f"<colgroup>{cols}</colgroup>"
        table_style = ' style="table-layout:fixed"'

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
            cs = _cell_style(cell)
            if cs:
                attrs += f' style="{cs}"'
            cells.append(f"<{tag}{attrs}>{_mark(cell.text, highlight_pattern)}</{tag}>")
        rows_html.append("<tr>" + "".join(cells) + "</tr>")
    caption = f"<caption>{escape(block.caption)}</caption>" if block.caption else ""
    return f"<table{anchor}{table_style}>{caption}{colgroup}{''.join(rows_html)}</table>"
```

Add `widths_would_cram` to the `_layout` import block at the top of the file (already imported in Task 2 if you included it; if not, add it):
```python
from renderers._layout import (
    INK_HEX, MAX_LINE_PITCH, body_line_spacing, content_pt, dominant_body_pt,
    scaled_pt, widths_would_cram,
)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_render_html_layout.py -v -k table`
Expected: PASS (both table tests).

- [ ] **Step 5: Commit**

```bash
git add backend/renderers/html_renderer.py backend/tests/test_render_html_layout.py
git commit -m "feat(backend): HTML preview table col-widths + cell styling"
```

---

## Task 4: 그림 (폭/정렬/플로트) + 구분선

**Files:**
- Modify: `backend/renderers/html_renderer.py` (figure branch + new DividerBlock branch)
- Modify test: `backend/tests/test_render_html_layout.py`

- [ ] **Step 1: Write the failing test (append)**

```python
from models import DividerBlock, FigureBlock  # add to imports at top of the test file


def test_figure_width_align_and_float():
    doc = SemanticDocument(page_width=595.0, margin_left=72.0, margin_right=72.0, blocks=[
        FigureBlock(image_ref="fig1.png", width=200.0, align="right"),
        FigureBlock(image_ref="fig2.png", wrap="left"),
    ])
    html = render_html(doc)
    assert "width:200pt" in html
    assert "text-align:right" in html
    assert "float:left" in html


def test_divider_rendered():
    doc = SemanticDocument(blocks=[DividerBlock(thickness=1.5, color="888888")])
    html = render_html(doc)
    assert "<hr" in html
    assert "border-top:1.5pt solid #888888" in html
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_render_html_layout.py -v -k "figure or divider"`
Expected: FAIL (no width/float; `<hr>` absent — DividerBlock currently dropped).

- [ ] **Step 3: Implement figure formatting + DividerBlock branch**

In `render_html`, replace the figure branch with:
```python
        elif isinstance(block, FigureBlock):
            if block.image_ref:
                fig_sty, img_sty = [], ""
                wrap = getattr(block, "wrap", None)
                if wrap in ("left", "right"):
                    fig_sty.append(f"float:{wrap};max-width:50%;margin:.3em 1em")
                elif block.align in ("center", "right"):
                    fig_sty.append(f"text-align:{block.align}")
                if block.width:
                    img_sty = f' style="width:{min(block.width, cpt):g}pt;height:auto"'
                fa = f' style="{";".join(fig_sty)}"' if fig_sty else ""
                parts.append(
                    f'<figure{anchor}{fa}>'
                    f'<img src="{asset_url_prefix}/{escape(block.image_ref)}" alt="figure"{img_sty}/>'
                    f"</figure>"
                )
```

Add a DividerBlock branch (after the CaptionBlock branch, before the loop ends):
```python
        elif isinstance(block, DividerBlock):
            th = block.thickness or 0.75
            col = block.color or INK_HEX
            parts.append(
                f'<hr{anchor} style="border:0;border-top:{th:g}pt solid #{col};margin:.5em 0">'
            )
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_render_html_layout.py -v -k "figure or divider"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/renderers/html_renderer.py backend/tests/test_render_html_layout.py
git commit -m "feat(backend): HTML preview figure size/float + divider rendering"
```

---

## Task 5: 러닝 헤더/푸터 밴드 (페이지 경계 반복)

**Files:**
- Modify: `backend/renderers/html_renderer.py` (`_render_band` helper + band emission in `render_html`)
- Modify test: `backend/tests/test_render_html_layout.py`

- [ ] **Step 1: Write the failing test (append)**

```python
from models import RunningBand  # add to imports at top of the test file


def test_running_bands_rendered_and_repeated_per_page():
    doc = SemanticDocument(
        running_header=RunningBand(left="KFI", right="p.1", rule=True, rule_color="999999"),
        running_footer=RunningBand(center="footer"),
        blocks=[
            ParagraphBlock(text="page0", page=0),
            ParagraphBlock(text="page1", page=1),  # page change → footer+header repeat
        ],
    )
    html = render_html(doc)
    assert html.count('class="band"') >= 3          # top header + (footer+header at break) + bottom footer
    assert "KFI" in html and "footer" in html
    assert "border-bottom:" in html                  # header rule
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_render_html_layout.py -v -k running`
Expected: FAIL (no bands emitted).

- [ ] **Step 3: Implement `_render_band` + emit at sheet edges and page breaks**

Add the helper (near `_render_table`):
```python
def _render_band(band: RunningBand, footer: bool = False) -> str:
    """A running header/footer: left | center | right as a flex band, with the optional
    horizontal rule on the inner edge (header → bottom, footer → top)."""
    spans = "".join(
        f"<span>{escape(t or '')}</span>"
        for t in (band.left, band.center, band.right)
    )
    style = ""
    if band.rule:
        edge = "top" if footer else "bottom"
        style = (f' style="border-{edge}:{(band.rule_size or 0.75):g}pt solid '
                 f'#{band.rule_color or INK_HEX}"')
    return f'<div class="band"{style}>{spans}</div>'
```

In `render_html`, emit the header at the very top of the page body and repeat bands at each page change. Specifically:

(a) Initialise the parts list with the running header (replace `parts: list[str] = []`):
```python
    parts: list[str] = []
    if doc.running_header:
        parts.append(_render_band(doc.running_header))
```

(b) On a page change, emit footer-of-previous-page + gap + header-of-next-page (replace the existing page-break block inside the loop):
```python
        page = getattr(block, "page", None)
        if page is not None and prev_page is not None and page != prev_page:
            if doc.running_footer:
                parts.append(_render_band(doc.running_footer, footer=True))
            parts.append('<div class="page-gap" data-page-break="true"></div>')
            if doc.running_header:
                parts.append(_render_band(doc.running_header))
        if page is not None:
            prev_page = page
```

(c) After the loop (just before building `body`), append the trailing footer:
```python
    if doc.running_footer:
        parts.append(_render_band(doc.running_footer, footer=True))
```

- [ ] **Step 4: Run to verify it passes + full suite regression**

Run: `cd backend && python -m pytest tests/test_render_html_layout.py tests/test_render_html_highlight.py -v`
Expected: PASS (incl. running bands).
Run: `cd backend && python -m pytest -q`
Expected: full suite green (no regressions).

- [ ] **Step 5: Commit**

```bash
git add backend/renderers/html_renderer.py backend/tests/test_render_html_layout.py
git commit -m "feat(backend): HTML preview running header/footer bands"
```

---

## Task 6: 통합 검증 + 마무리

**Files:** 없음 (검증/정리).

- [ ] **Step 1: Full suites**

Run: `cd backend && python -m pytest -q` → green (baseline + new layout/helper tests).

- [ ] **Step 2: 수동 시각 확인 (컨트롤러)**

로컬 백엔드를 재시작(새 html_renderer 로드)하고 용어 관리 모드 미리보기를 본다. 좌측 원본과 대조해 확인:
- 정렬(가운데/우측 제목), 들여쓰기, 글자 크기 계층, 줄간격이 원본/DOCX와 유사.
- 표 열 폭이 원본 비율(가능 시), 셀 음영/정렬/세로글자.
- 그림 폭/정렬/플로트, 구분선 표시.
- 러닝 헤더/푸터가 페이지 경계마다 보임.
- 용어 하이라이트(`<mark>`)·블록 점프(`#blk{n}`) 정상.
- 종이 폭(A4 등) 시트로 보임.
- 회귀: 빈/레거시 지오메트리 문서도 깨지지 않음(폴백).

- [ ] **Step 3: finishing-a-development-branch 스킬로 마무리**

`superpowers:finishing-a-development-branch` 호출 → 테스트 통과 확인 후 옵션 제시(머지/PR/유지/폐기). **커밋 전 로컬 우회 3파일 미포함 재확인** (`git status`로 `auth.py`/`documents_store.py`/`proxy.ts`가 unstaged인지).

---

## Self-Review (작성자 점검)

**Spec coverage:**
- §1 종이 시트 → Task 2 (page_open + .page CSS). ✓
- §2 타이포(`_inline_style`: font-size 스케일/굵기/이탤릭/밑줄/색/정렬/들여쓰기/줄간격, font 가족 제외) → Task 2. ✓ body_pt/body_ls → `_layout`(Task 1). ✓
- §3 표(col_widths+colgroup+fixed, 끼임 폴백, 셀 align/valign/shading/vertical/color) → Task 3. ✓
- §4 지배적 본문 크기 → Task 1 `dominant_body_pt` + Task 2 사용. ✓
- §5 그림(width/align/wrap float) → Task 4. ✓
- §6 구분선 → Task 4. ✓
- §7 러닝밴드(페이지 경계 반복) → Task 5. ✓
- §8 페이지 구분(page-gap) → Task 2(가시 갭) + Task 5(밴드). ✓
- 회귀(mark/anchor/폴백) → 각 태스크 테스트 + Task 5/6 전체 스위트. ✓
- DRY(scaled_pt/widths_would_cram 공유) → Task 1. ✓

**Placeholder scan:** 모든 코드 단계에 실제 코드. "적절히" 류 없음. ✓

**Type consistency:** `_layout` exports (scaled_pt, widths_would_cram, dominant_body_pt, body_line_spacing, content_pt, MAX_LINE_PITCH, INK_HEX) = Task 1 정의 = Task 2-5 사용. `_render_table(block, highlight_pattern, anchor, body_pt, content_pt_val)` — Task 2 호출(old sig)에서 Task 3에서 호출+정의를 함께 변경(같은 파일). `_inline_style(block, body_pt, body_ls)`·`_render_band(band, footer)`·`_cell_style(cell)`·`_VALIGN` 일관. `INK_HEX`는 '#' 없는 hex(코드에서 `#{INK_HEX}`로 사용). ✓

**알려진 트레이드오프(의도):** font 가족 미적용(시스템 스택); 연속 스크롤이라 러닝밴드 "페이지 반복"은 근사; 진짜 페이지네이션·bbox 절대배치는 범위 외(spec 비범위와 일치).
