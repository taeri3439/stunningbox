# Retranslate Mode: HTML Viewer + Sentence Click-Select — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make 재번역(retranslate) mode use a clickable HTML viewer instead of OnlyOffice, so users select sentences by clicking in the original/translated panes (and the list); OnlyOffice mounts only in 문서 편집.

**Architecture:** Backend `render_html` gains a `wrap_segments` mode that wraps each translatable field in `<span class="seg" data-seg="{i}">` with `i` == `iter_segments` enumeration index (== `build_segments` `index`); a new `/source-preview` endpoint renders the source model the same way. Frontend gets a `SegmentPreview` (same-origin iframe with click-to-select + active-highlight). The `/c` page lifts `segments`+`activeSeg`, routes retranslate's translated pane to `SegmentPreview`, makes PDF originals clickable via bbox hotspots and HWP/office originals via `source-preview`, and `RetranslatePanel` is rewired off the OnlyOffice connector.

**Tech Stack:** FastAPI + Pydantic models (backend), Next.js 16 / React / TypeScript (frontend), pytest (backend tests), tsc + browser verification (frontend — no JS unit-test harness in this repo).

## Global Constraints

- **Index alignment is the #1 risk.** The renderer's `data-seg="{i}"` values MUST equal `build_segments`/`iter_segments` enumeration indices, or clicks select the wrong sentence. Task 1 locks this with a test.
- **No regression to `manage`/`proper-nouns`.** Wrapping is opt-in via `?segments=1`; the existing `previewUrl` (no flag) renders unchanged. The `data-seg` interactive cursor/hover only applies when `SegmentPreview` adds the `seg-clickable` body class.
- **Do NOT commit protected files:** `backend/auth.py`, `backend/documents_store.py`, `frontend/src/proxy.ts`. Never `git add -A`; never stage `backend/_*.py`, `test-pdfs/`, or root images.
- **Frontend "tests"** = `cd frontend && npx tsc --noEmit -p tsconfig.json` (exit 0) + `npx eslint <files>` (exit 0) + the documented browser checks. **Backend tests** = `cd backend && python -m pytest <file> -v`.
- Commit after each task. Branch: `retranslate-html-select-2026-06-25` (already created).

---

### Task 1: Renderer `wrap_segments` + `build_segment_index_map`

**Files:**
- Modify: `backend/models.py` (add `build_segment_index_map` right after `iter_segments`, ~line 340)
- Modify: `backend/renderers/html_renderer.py` (signature + CSS + block/table wrapping)
- Test: `backend/tests/test_render_html_segments.py` (create)

**Interfaces:**
- Produces: `build_segment_index_map(doc: SemanticDocument) -> dict` — keys: `("band", which, attr)`; `(bi, "text")`; `(bi, "line", li)`; `(bi, "caption")`; `(bi, "cell", ci)`; `(bi, "cellline", ci, li)`; `(bi, "overlay", oi)`. Values: the segment index (== `iter_segments` enumeration position). `which` ∈ {"header","footer"}; `ci` = row-major flat cell index.
- Produces: `render_html(doc, asset_url_prefix="assets", highlight_pattern=None, anchor_blocks=False, wrap_segments=False) -> str`. When `wrap_segments=True`, body blocks/table cells/figure overlays are wrapped in `<span class="seg" data-seg="{i}">…</span>`; bands are NOT wrapped (chrome) but their count still offsets body indices.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_render_html_segments.py
import re
from models import (
    SemanticDocument, HeadingBlock, ParagraphBlock, ListItemBlock, CaptionBlock,
    TableBlock, TableCell, FigureBlock, FigureOverlay, RunningBand, iter_segments,
    build_segment_index_map,
)
from renderers.html_renderer import render_html


def _doc():
    return SemanticDocument(
        running_header=RunningBand(left="HdrL", center="", right=""),
        blocks=[
            HeadingBlock(text="Title"),
            ParagraphBlock(text="One sentence."),
            ParagraphBlock(lines=["Line A", "Line B"]),          # 2 segments
            TableBlock(rows=[[TableCell(text="C1"), TableCell(text="C2")]]),  # 2 segments
            FigureBlock(overlays=[FigureOverlay(id="x1", source_text="Cap", target_text="Cap")]),
        ],
    )


def test_index_map_covers_every_iter_segment():
    doc = _doc()
    n = len(list(iter_segments(doc)))
    m = build_segment_index_map(doc)
    assert sorted(m.values()) == list(range(n))  # contiguous 0..n-1, no gaps/dupes


def test_rendered_data_seg_indices_match_iter_segments_body():
    doc = _doc()
    n = len(list(iter_segments(doc)))
    html = render_html(doc, wrap_segments=True)
    found = sorted(int(x) for x in re.findall(r'data-seg="(\d+)"', html))
    # Body segments are wrapped; the header band (index 0) is chrome and not wrapped.
    band_count = sum(1 for _ in range(1) for attr in ("left", "center", "right")
                     if getattr(doc.running_header, attr))
    assert found == list(range(band_count, n))


def test_wrap_segments_false_emits_no_seg_spans():
    html = render_html(_doc(), wrap_segments=False)
    assert "data-seg" not in html and 'class="seg"' not in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_render_html_segments.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_segment_index_map'` (and once that's added, the render assertions fail until wrapping is implemented).

- [ ] **Step 3: Add `build_segment_index_map` to `backend/models.py`**

Insert immediately AFTER the `iter_segments` function (after the `iter_text_fields` helper, ~line 345). This MUST mirror `iter_segments`' order exactly — keep the two functions adjacent and edit together.

```python
def build_segment_index_map(doc: "SemanticDocument") -> dict:
    """Map a location key -> segment index, in the SAME order as iter_segments().

    The renderer uses this to stamp data-seg="{i}" so HTML spans line up with
    build_segments()/iter_segments() indices. MUST stay in lockstep with
    iter_segments above — they are tested together in test_render_html_segments.py.
    Keys: ("band", which, attr) | (bi, "text") | (bi, "line", li) | (bi, "caption")
          | (bi, "cell", ci) | (bi, "cellline", ci, li) | (bi, "overlay", oi)
    """
    m: dict = {}
    i = 0
    for which, band in (("header", doc.running_header), ("footer", doc.running_footer)):
        if band:
            for attr in ("left", "center", "right"):
                if getattr(band, attr):
                    m[("band", which, attr)] = i
                    i += 1
    for bi, block in enumerate(doc.blocks):
        if isinstance(block, HeadingBlock):
            m[(bi, "text")] = i; i += 1
        elif isinstance(block, (ParagraphBlock, ListItemBlock, CaptionBlock)):
            if block.lines:
                for li in range(len(block.lines)):
                    m[(bi, "line", li)] = i; i += 1
            else:
                m[(bi, "text")] = i; i += 1
        elif isinstance(block, TableBlock):
            if block.caption is not None:
                m[(bi, "caption")] = i; i += 1
            ci = 0
            for row in block.rows:
                for cell in row:
                    if cell.lines:
                        for li in range(len(cell.lines)):
                            m[(bi, "cellline", ci, li)] = i; i += 1
                    else:
                        m[(bi, "cell", ci)] = i; i += 1
                    ci += 1
        elif isinstance(block, FigureBlock):
            for oi in range(len(block.overlays)):
                m[(bi, "overlay", oi)] = i; i += 1
    return m
```

- [ ] **Step 4: Add `wrap_segments` to `render_html` in `backend/renderers/html_renderer.py`**

4a. Change the signature (line 88-90) and import the map:

```python
from models import (  # add build_segment_index_map to the existing models import
    ...,
    build_segment_index_map,
)

def render_html(doc: SemanticDocument, asset_url_prefix: str = "assets",
                highlight_pattern: "re.Pattern | None" = None,
                anchor_blocks: bool = False, wrap_segments: bool = False) -> str:
```

4b. Right after the function opens (before the block loop at ~line 95), build the map and a wrapper helper:

```python
    seg_map = build_segment_index_map(doc) if wrap_segments else {}

    def _seg(inner: str, key) -> str:
        """Wrap rendered inner HTML in a clickable segment span (no-op unless wrap_segments)."""
        if not wrap_segments:
            return inner
        i = seg_map.get(key)
        return inner if i is None else f'<span class="seg" data-seg="{i}">{inner}</span>'
```

4c. Wrap each body block's text. Replace the existing emission lines:

```python
        # HeadingBlock (was line 116)
        if isinstance(block, HeadingBlock):
            lvl = min(max(block.level, 1), 6)
            marker = f'<span class="marker">{escape(block.marker)}</span> ' if block.marker else ""
            parts.append(f"<h{lvl}{anchor}{sa}>{marker}{_seg(_mark(block.text, highlight_pattern), (i, 'text'))}</h{lvl}>")
        elif isinstance(block, ListItemBlock):
            ls_sty = sty
            if getattr(block, "indent", None) is None and block.level:
                extra = f"margin-left:{block.level * 1.5:g}em"
                ls_sty = f"{sty};{extra}" if sty else extra
            lsa = f' style="{ls_sty}"' if ls_sty else ""
            parts.append(
                f'<div class="li"{anchor}{lsa}>'
                f'<span class="marker">{escape(block.marker)}</span> '
                f"{_li_body(block, highlight_pattern, _seg, i)}</div>"
            )
        elif isinstance(block, ParagraphBlock):
            parts.append(f"<p{anchor}{sa}>{_para_body(block, highlight_pattern, _seg, i)}</p>")
        ...
        elif isinstance(block, CaptionBlock):
            parts.append(f'<p class="caption"{anchor}{sa}>{_para_body(block, highlight_pattern, _seg, i)}</p>')
```

Note: the block loop variable is `i` (from `for i, block in enumerate(doc.blocks)` at line 99) — that is the `bi` used in the map keys.

4d. Add the body helpers (near `_mark`, ~line 54). They render `.lines` per-line (each its own seg span, joined by `<br>`) when present, else `.text` as one seg:

```python
def _para_body(block, highlight_pattern, seg, bi: int) -> str:
    """Paragraph/Caption body: per-line seg spans (joined by <br>) when .lines, else one .text seg."""
    if block.lines:
        return "<br>".join(
            seg(_mark(line, highlight_pattern), (bi, "line", li))
            for li, line in enumerate(block.lines)
        )
    return seg(_mark(block.text, highlight_pattern), (bi, "text"))


def _li_body(block, highlight_pattern, seg, bi: int) -> str:
    """List item body — same line/text handling as _para_body."""
    return _para_body(block, highlight_pattern, seg, bi)
```

4e. Table cells: `_render_table` (line 247) must wrap each cell. Add `seg` + `bi` params and thread them; replace the cell emission (line 274):

```python
def _render_table(block, highlight_pattern=None, anchor="", body_pt=None,
                  content_pt_val=455.0, seg=None, bi=None):
    ...
    ci = 0
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
            cells.append(f"<{tag}{attrs}>{_cell_body(cell, highlight_pattern, seg, bi, ci)}</{tag}>")
            ci += 1
        rows_html.append("<tr>" + "".join(cells) + "</tr>")
    ...
```

And add a cell-body helper + a no-op default `seg` so non-wrap callers are unaffected:

```python
def _cell_body(cell, highlight_pattern, seg, bi, ci) -> str:
    if seg is None:               # non-wrap callers (defensive)
        seg = lambda inner, key: inner
    if cell.lines:
        return "<br>".join(
            seg(_mark(line, highlight_pattern), (bi, "cellline", ci, li))
            for li, line in enumerate(cell.lines)
        )
    return seg(_mark(cell.text, highlight_pattern), (bi, "cell", ci))
```

Update the `_render_table` call site (line 131) to pass `seg=_seg, bi=i`:

```python
        elif isinstance(block, TableBlock):
            cap_html = ""
            if block.caption is not None:
                cap_html = ""  # caption handled inside _render_table; wrap there if needed
            parts.append(_render_table(block, highlight_pattern, anchor, body_pt, cpt, seg=_seg, bi=i))
```

For the table caption seg-wrap, inside `_render_table` replace the caption line:
```python
    caption = (f"<caption>{(seg or (lambda x, k: x))(escape(block.caption), (bi, 'caption'))}</caption>"
               if block.caption else "")
```

4f. Figure overlays are composited into the PNG (not HTML text), so they are NOT separately rendered as HTML text — but `build_segment_index_map` still counts them so body indices after a figure stay aligned. No span needed in HTML for overlays (they are not clickable in this pass; selecting an in-figure overlay is out of scope per the spec). The map counting keeps subsequent indices correct.

4g. Add `.seg` CSS to the `<style>` block (after the `mark.gloss` rule, ~line 194):

```css
  .seg { display: inline; }
  .seg-clickable .seg { cursor: pointer; border-radius: 2px; }
  .seg-clickable .seg:hover { background: #eef3ff; }
  .seg-active { background: #dbe7ff; outline: 1.5px solid #2563eb; border-radius: 2px; }
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_render_html_segments.py -v`
Expected: PASS (3 tests). Then run the existing renderer tests to confirm no regression:
Run: `cd backend && python -m pytest tests/test_render_html_layout.py tests/test_render_html_highlight.py -v`
Expected: PASS (default `wrap_segments=False` leaves output unchanged).

- [ ] **Step 6: Commit**

```bash
git add backend/models.py backend/renderers/html_renderer.py backend/tests/test_render_html_segments.py
git commit -m "feat(renderer): wrap_segments mode — data-seg spans aligned to iter_segments"
```

---

### Task 2: `/source-preview` endpoint + `/preview?segments=1` + `api.sourcePreviewUrl`

**Files:**
- Modify: `backend/main.py` (preview endpoint ~424; add source-preview endpoint near it)
- Modify: `frontend/src/lib/api.ts` (previewUrl segments flag + sourcePreviewUrl)
- Test: `backend/tests/test_source_preview_endpoint.py` (create)

**Interfaces:**
- Consumes: `render_html(..., wrap_segments=...)` (Task 1); `process.load_source_model`, `process.get_export_model`, `_assets_prefix`, `auth.owner_any`.
- Produces (backend): `GET /api/documents/{doc_id}/preview?lang=&segments=1` → wrapped translated HTML; `GET /api/documents/{doc_id}/source-preview` → wrapped source HTML.
- Produces (api client): `previewUrl(docId, lang, opts?: { segments?: boolean })`; `sourcePreviewUrl(docId)`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_source_preview_endpoint.py
import os
from fastapi.testclient import TestClient

os.environ["DEV_NO_AUTH"] = "1"  # bypass owner guard for the test client
import main  # noqa: E402

client = TestClient(main.app)


def _seed_doc(tmp_path, monkeypatch):
    """Create a minimal source.json under a temp STORAGE and point main.STORAGE at it."""
    import json, models
    doc = models.SemanticDocument(
        source_lang="ko",
        blocks=[models.ParagraphBlock(text="원문 문장", source_span_ids=[])],
    )
    monkeypatch.setattr(main, "STORAGE", str(tmp_path))
    d = os.path.join(str(tmp_path), "docX")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "source.json"), "w", encoding="utf-8") as fh:
        json.dump(doc.model_dump(), fh)
    return "docX"


def test_source_preview_returns_wrapped_html(tmp_path, monkeypatch):
    doc_id = _seed_doc(tmp_path, monkeypatch)
    r = client.get(f"/api/documents/{doc_id}/source-preview")
    assert r.status_code == 200
    assert 'data-seg="0"' in r.text
    assert "원문 문장" in r.text


def test_preview_segments_flag_toggles_spans(tmp_path, monkeypatch):
    doc_id = _seed_doc(tmp_path, monkeypatch)
    # preview needs a translation; with lang==source_lang get_export_model returns source.
    plain = client.get(f"/api/documents/{doc_id}/preview?lang=ko")
    seg = client.get(f"/api/documents/{doc_id}/preview?lang=ko&segments=1")
    assert "data-seg" not in plain.text
    assert 'data-seg="0"' in seg.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_source_preview_endpoint.py -v`
Expected: FAIL — `source-preview` 404 (route missing) and `segments=1` ignored (no `data-seg`).

- [ ] **Step 3: Implement the endpoints in `backend/main.py`**

3a. Update the preview endpoint (line 424-436) to accept `segments`:

```python
@app.get("/api/documents/{doc_id}/preview", response_class=HTMLResponse)
def preview(doc_id: str, lang: str | None = None, segments: bool = False,
            _: str = Depends(auth.owner_any)):
    """Read-only translated HTML with uploaded-glossary 번역어 highlighted + block anchors.
    segments=1 also wraps each translatable field in a data-seg span (재번역 click-select)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    from pipeline.text_replace import compile_terms

    doc = process.get_export_model(STORAGE, doc_id, lang)
    eff_lang = lang or doc.source_lang
    glossary = process.load_doc_glossary(STORAGE, doc_id)
    pattern = compile_terms(list(glossary.values()), eff_lang) if glossary else None
    return render_html(doc, asset_url_prefix=_assets_prefix(doc_id),
                       highlight_pattern=pattern, anchor_blocks=True, wrap_segments=segments)
```

3b. Add the source-preview endpoint immediately after `preview`:

```python
@app.get("/api/documents/{doc_id}/source-preview", response_class=HTMLResponse)
def source_preview(doc_id: str, _: str = Depends(auth.owner_any)):
    """Read-only SOURCE-language HTML with data-seg spans + block anchors, for the
    재번역 left pane on HWP/office uploads (which have no page-image bboxes to click)."""
    if not os.path.exists(process.doc_dir(STORAGE, doc_id)):
        raise HTTPException(404, "Unknown document")
    doc = process.load_source_model(STORAGE, doc_id)
    return render_html(doc, asset_url_prefix=_assets_prefix(doc_id),
                       anchor_blocks=True, wrap_segments=True)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_source_preview_endpoint.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Add the api-client methods in `frontend/src/lib/api.ts`**

Replace `previewUrl` (lines 208-209) and add `sourcePreviewUrl` next to it:

```typescript
  previewUrl: (docId: string, lang: string, opts?: { segments?: boolean }) =>
    `/api/documents/${docId}/preview?lang=${encodeURIComponent(lang)}` +
    (opts?.segments ? "&segments=1" : ""),
  sourcePreviewUrl: (docId: string) => `/api/documents/${docId}/source-preview`,
```

- [ ] **Step 6: Verify the frontend still type-checks + commit**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json`
Expected: exit 0 (the optional `opts` arg keeps existing `previewUrl(docId, lang)` calls valid).

```bash
git add backend/main.py backend/tests/test_source_preview_endpoint.py frontend/src/lib/api.ts
git commit -m "feat(api): /source-preview endpoint + preview segments flag + client urls"
```

---

### Task 3: `SegmentPreview` component

**Files:**
- Create: `frontend/src/components/SegmentPreview.tsx`

**Interfaces:**
- Consumes: `api.previewUrl(docId, lang, {segments:true})` / `api.sourcePreviewUrl(docId)` for `src`.
- Produces: `<SegmentPreview src activeSeg onSelectSeg reloadKey jumpBlock? />` where `src: string`, `activeSeg: number | null`, `onSelectSeg: (index: number) => void`, `reloadKey: number`, `jumpBlock?: number | null`.

- [ ] **Step 1: Create the component**

```tsx
// frontend/src/components/SegmentPreview.tsx
"use client";

import { useCallback, useEffect, useRef } from "react";

/**
 * Clickable same-origin HTML preview for 재번역. The served HTML (render_html with
 * wrap_segments) tags each sentence with <span class="seg" data-seg="{i}">. We attach a
 * delegated click listener inside the iframe and report the clicked segment index up;
 * `activeSeg` drives a .seg-active highlight + scroll. Same-origin (/api proxy) lets us
 * touch contentDocument directly — no postMessage.
 */
export function SegmentPreview({
  src,
  activeSeg,
  onSelectSeg,
  reloadKey,
  jumpBlock,
}: {
  src: string;
  activeSeg: number | null;
  onSelectSeg: (index: number) => void;
  reloadKey: number;
  jumpBlock?: number | null;
}) {
  const ref = useRef<HTMLIFrameElement | null>(null);

  const applyActive = useCallback(() => {
    const doc = ref.current?.contentDocument;
    if (!doc) return;
    doc.querySelectorAll(".seg-active").forEach((el) => el.classList.remove("seg-active"));
    if (activeSeg == null) return;
    const el = doc.querySelector(`[data-seg="${activeSeg}"]`);
    if (el) {
      el.classList.add("seg-active");
      el.scrollIntoView({ block: "nearest" });
    }
  }, [activeSeg]);

  // Wire click delegation + clickable affordance once the iframe document is ready.
  const onLoad = useCallback(() => {
    const doc = ref.current?.contentDocument;
    if (!doc) return;
    doc.body.classList.add("seg-clickable");
    doc.addEventListener("click", (e) => {
      const t = (e.target as HTMLElement).closest("[data-seg]") as HTMLElement | null;
      if (t?.dataset.seg != null) onSelectSeg(Number(t.dataset.seg));
    });
    if (jumpBlock != null) doc.location.hash = `blk${jumpBlock}`;
    applyActive();
    // onSelectSeg/applyActive are stable enough for a once-per-load wire-up.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onSelectSeg, jumpBlock]);

  // Re-highlight when the active segment changes on an already-loaded iframe.
  useEffect(() => {
    applyActive();
  }, [applyActive]);

  // Live jump on an already-loaded iframe (chip click, no reload).
  useEffect(() => {
    const win = ref.current?.contentWindow;
    if (win && jumpBlock != null) win.location.hash = `blk${jumpBlock}`;
  }, [jumpBlock]);

  return (
    <iframe
      ref={ref}
      key={reloadKey}
      src={`${src}${src.includes("?") ? "&" : "?"}v=${reloadKey}`}
      onLoad={onLoad}
      title="번역본 (문장 클릭 선택)"
      className="sheet min-h-0 w-full flex-1 bg-white"
    />
  );
}
```

- [ ] **Step 2: Verify it type-checks**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/SegmentPreview.tsx
git commit -m "feat(viewer): SegmentPreview — clickable HTML segment preview"
```

---

### Task 4: Switch retranslate to HTML + click; rewire RetranslatePanel

This is the cohesive switch: the `/c` page lifts `segments`+`activeSeg`, routes retranslate's translated pane to `SegmentPreview`, wires translated-pane click, and `RetranslatePanel` becomes controlled and drops the OnlyOffice connector. These land together because the panel depends on the lifted `activeSeg` and the page depends on the panel no longer using the connector.

**Files:**
- Modify: `frontend/src/components/RetranslatePanel.tsx`
- Modify: `frontend/src/app/c/[id]/page.tsx`

**Interfaces:**
- Consumes: `SegmentPreview` (Task 3); `api.previewUrl(docId, lang, {segments:true})`, `api.segments`, `api.retranslateSegment`, `api.render`.
- Produces: `<RetranslatePanel docId lang segments activeSeg onSelect onApplied onSegmentText />` — `segments: Segment[]`, `activeSeg: Segment | null`, `onSelect: (seg: Segment) => void`, `onApplied: (bookmark: string | null) => void`, `onSegmentText: (index: number, text: string) => void`.

- [ ] **Step 1: Rewire `RetranslatePanel.tsx` (controlled; drop connector)**

1a. Remove the connector import (line 7):
```tsx
// DELETE: import { searchAndReplace, highlightInEditor } from "@/lib/onlyofficeConnector";
```

1b. Replace the prop interface + drop the internal `segments`/`active`/`error` ownership (parent owns segments + selection now):

```tsx
export function RetranslatePanel({
  docId,
  lang,
  segments,
  activeSeg,
  onSelect,
  onApplied,
  onSegmentText,
}: {
  docId: string;
  lang: string;
  segments: Segment[];
  activeSeg: Segment | null;
  onSelect: (seg: Segment) => void;
  // Bump the editor reload after a re-render so 문서 편집 reflects the change.
  onApplied: (bookmark: string | null) => void;
  // Reflect applied text up so the lifted segments list + preview show it.
  onSegmentText: (index: number, text: string) => void;
}) {
  const [preset, setPreset] = useState<string>("concise");
  const [guidance, setGuidance] = useState<string>("");
  const [preview, setPreview] = useState<{ index: number; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
```

Delete the now-removed state: `const [segments, setSegments]`, `const [error, setError]`, `const [active, setActive]`, and the `useEffect` that fetched segments (lines 41-46) — the parent fetches now.

1c. Rewrite `select` (drop connector highlight; defer selection to the parent):

```tsx
  function select(seg: Segment) {
    onSelect(seg);
    setPreview(null);
    setNote(null);
    setGuidance("");
    setPreset("concise");
  }
```

1d. Rewrite `apply` (drop the connector branch; always re-render + reflect up):

```tsx
  async function apply(seg: Segment) {
    if (!preview) return;
    const newText = preview.text;
    onSegmentText(seg.index, newText); // reflect in the lifted list + refresh preview
    setPreview(null);
    setBusy(true);
    setNote("반영 중…");
    try {
      await api.render(docId, lang); // keep the docx in sync for 문서 편집
      onApplied(seg.block != null ? `blk${seg.block}` : null);
      setNote("반영했습니다.");
    } catch (e) {
      setNote(`반영 실패: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setBusy(false);
    }
  }
```

1e. In the JSX, replace every `active === seg.index` with `activeSeg?.index === seg.index` (the row className at ~line 122 and the controls conditional at ~line 135), and remove the `if (error) return …` block (error handling lifts to the parent's fetch). The `segments.map(...)` now reads the `segments` prop.

- [ ] **Step 2: Update `/c` page state + retranslate wiring in `frontend/src/app/c/[id]/page.tsx`**

2a. Add a lifted segments list + reload key (near the other state, ~line 58):

```tsx
  const [segments, setSegments] = useState<Segment[]>([]);
  const [previewReloadKey, setPreviewReloadKey] = useState(0);
```

2b. Fetch segments when retranslate opens (add an effect near the other effects, ~line 137). `Segment` is already imported via `activeSeg`'s type:

```tsx
  useEffect(() => {
    if (mode !== "retranslate" || !doc) return;
    api
      .segments(doc.doc_id, lang)
      .then((r) => setSegments(r.segments))
      .catch(() => setSegments([]));
  }, [mode, doc, lang]);
```

2c. Route the center pane for retranslate to `SegmentPreview` (the `mode === "manage" || "proper-nouns"` branch at lines 438-448). Import `SegmentPreview` at the top, then:

```tsx
            {mode === "manage" || mode === "proper-nouns" ? (
              <TranslatedPreview docId={doc.doc_id} lang={lang} reloadKey={manageKey} jumpBlock={jumpBlock} />
            ) : mode === "retranslate" ? (
              <SegmentPreview
                src={api.previewUrl(doc.doc_id, lang, { segments: true })}
                activeSeg={activeSeg?.index ?? null}
                onSelectSeg={(idx) => setActiveSeg(segments.find((s) => s.index === idx) ?? null)}
                reloadKey={previewReloadKey}
                jumpBlock={activeSeg?.block ?? null}
              />
            ) : (
              <OnlyOfficeEditor
                docId={doc.doc_id}
                lang={lang}
                onConnector={setConnector}
                reloadKey={editorReloadKey}
                bookmark={editorBookmark}
              />
            )}
```

2d. Update the `RetranslatePanel` mount (lines 488-498) to the new controlled API:

```tsx
        {mode === "retranslate" ? (
          <RetranslatePanel
            docId={doc.doc_id}
            lang={lang}
            segments={segments}
            activeSeg={activeSeg}
            onSelect={setActiveSeg}
            onApplied={(bm) => {
              setEditorBookmark(bm);
              setEditorReloadKey((k) => k + 1);
            }}
            onSegmentText={(index, text) => {
              setSegments((prev) => prev.map((s) => (s.index === index ? { ...s, translated: text } : s)));
              setPreviewReloadKey((k) => k + 1); // refresh the HTML preview with the new text
            }}
          />
        ) : mode === "proper-nouns" ? (
```

- [ ] **Step 3: Verify type-check + lint**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json && npx eslint "src/app/c/[id]/page.tsx" src/components/RetranslatePanel.tsx src/components/SegmentPreview.tsx`
Expected: exit 0 for both.

- [ ] **Step 4: Browser verification (prod build, per the repo's HMR caveat)**

Run a backend + a `BACKEND_URL`-correct dev server (the proxy must reach the backend):
```bash
cd frontend && DEV_NO_AUTH=1 BACKEND_URL=http://127.0.0.1:8003 npx next dev -p 3015 > /tmp/sb_t4.log 2>&1 &
```
Then drive `http://localhost:3015/c/<docId>?lang=en` (a dev-owned doc), enter 재번역, and confirm:
- the center pane is the HTML preview (not OnlyOffice);
- clicking a sentence in the preview highlights it (`.seg-active`) and selects the matching list row;
- clicking a list row highlights the sentence in the preview;
- 재번역 → 적용 refreshes the preview text and does not error.

- [ ] **Step 5: Commit**

```bash
git add "frontend/src/app/c/[id]/page.tsx" frontend/src/components/RetranslatePanel.tsx
git commit -m "feat(retranslate): HTML SegmentPreview + click-select; controlled RetranslatePanel off connector"
```

---

### Task 5: Original-pane click — PDF bbox hotspots + HWP/office source-preview

**Files:**
- Modify: `frontend/src/app/c/[id]/page.tsx`

**Interfaces:**
- Consumes: lifted `segments` + `setActiveSeg` (Task 4); `api.sourcePreviewUrl`; `SegmentPreview`.

- [ ] **Step 1: PDF originals — render all segment bboxes as clickable hotspots**

In the page-images branch (lines 343-420), inside the `<div className="relative" …>` for page `i`, ADD (alongside the existing active-highlight at 361-376) a hotspot layer for retranslate mode. It draws every segment whose `source_bbox.page === i`; the active one is styled, the rest are transparent click targets:

```tsx
                    {mode === "retranslate" &&
                      segments
                        .filter((s) => s.source_bbox && s.source_bbox.page === i)
                        .map((s) => {
                          const b = s.source_bbox!;
                          const isActive = activeSeg?.index === s.index;
                          return (
                            <button
                              key={`hot-${s.index}`}
                              type="button"
                              aria-label={`문장 ${s.index + 1} 선택`}
                              onClick={() => setActiveSeg(s)}
                              className="absolute rounded-[3px]"
                              style={{
                                left: `${b.x * 100}%`,
                                top: `${b.y * 100}%`,
                                width: `${b.w * 100}%`,
                                height: `${b.h * 100}%`,
                                background: isActive ? "var(--primary-soft)" : "transparent",
                                outline: isActive ? "1.5px solid var(--primary)" : "none",
                                cursor: "pointer",
                              }}
                            />
                          );
                        })}
```

Then REMOVE the old single `active-source-highlight` block (lines 361-376) — the hotspot layer above now renders the active highlight too. Keep the scroll effect (lines 128-134) but retarget it to scroll the active hotspot into view: change its body to query the active hotspot. Since hotspots no longer carry the `active-source-highlight` id, give the active hotspot `id="active-source-highlight"` when `isActive` (add `id={isActive ? "active-source-highlight" : undefined}` to the button) so the existing scroll effect keeps working unchanged.

- [ ] **Step 2: HWP/office originals — clickable source-preview in retranslate mode**

In the original pane, the `doc.source_html ? <iframe> : <pageimages>` branch (line 335). For retranslate mode with an HWP/office original, swap the raw hwp5html iframe for a clickable `SegmentPreview` pointed at `source-preview`:

```tsx
          {doc.source_html ? (
            mode === "retranslate" ? (
              <SegmentPreview
                src={api.sourcePreviewUrl(doc.doc_id)}
                activeSeg={activeSeg?.index ?? null}
                onSelectSeg={(idx) => setActiveSeg(segments.find((s) => s.index === idx) ?? null)}
                reloadKey={0}
                jumpBlock={activeSeg?.block ?? null}
              />
            ) : (
              <iframe
                src={doc.source_html}
                title={`원본 — ${doc.title ?? doc.doc_id}`}
                className="sheet min-h-0 w-full flex-1 bg-white"
              />
            )
          ) : (
            <div className="min-h-0 flex-1 overflow-auto">
              {/* page images + hotspots (Step 1) */}
```

- [ ] **Step 3: Verify type-check + lint**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json && npx eslint "src/app/c/[id]/page.tsx"`
Expected: exit 0.

- [ ] **Step 4: Browser verification**

- PDF doc → 재번역: hover/click a sentence region on the page image selects it (preview + list highlight); active region is outlined.
- HWP doc (`source_html` present) → 재번역: the left pane is the clickable source-preview; clicking a source sentence selects it (translated preview + list highlight). Switching to 용어/고유명사/편집 restores the raw hwp5html / editor.

- [ ] **Step 5: Commit**

```bash
git add "frontend/src/app/c/[id]/page.tsx"
git commit -m "feat(retranslate): original-pane click — PDF bbox hotspots + HWP source-preview"
```

---

### Task 6: Full verification

**Files:** none (verification only).

- [ ] **Step 1: Backend suite**

Run: `cd backend && python -m pytest tests/test_render_html_segments.py tests/test_source_preview_endpoint.py tests/test_render_html_layout.py tests/test_render_html_highlight.py -v`
Expected: all PASS. (Optionally run the full `python -m pytest` to confirm no wider regression.)

- [ ] **Step 2: Frontend gates**

Run: `cd frontend && npx tsc --noEmit -p tsconfig.json && npx eslint src/components/SegmentPreview.tsx src/components/RetranslatePanel.tsx "src/app/c/[id]/page.tsx" src/lib/api.ts`
Expected: exit 0.

- [ ] **Step 3: End-to-end browser pass (one PDF doc + one HWP doc)**

For each: enter 재번역, select via (a) the list, (b) a click in the translated preview, (c) a click in the original (PDF hotspot / HWP source-preview) — confirm all three stay in sync (highlight in both panes + the right list row). Re-translate a sentence, apply, confirm the translated preview updates and 문서 편집 shows the change. Confirm 용어 관리 / 고유명사 검수 still render their HTML preview unchanged (no stray hover cursor / no `data-seg` behavior), and 문서 편집 still loads OnlyOffice.

- [ ] **Step 4: Adversarial review (optional, ultracode)**

Dispatch a review workflow (index-alignment correctness, manage/proper-nouns no-regression, two-way sync correctness, apply/render flow) and fix confirmed findings before finishing the branch.

---

## Notes for the implementer
- The `Segment` type (`frontend/src/lib/types.ts`): `{ index, source, translated, source_bbox: { page,x,y,w,h }|null, block: number|null }`.
- `connector`/`setConnector` state stays in `/c` (still used by `OnlyOfficeEditor.onConnector`); it is simply no longer passed to `RetranslatePanel`.
- `previewUrl`'s third arg is optional, so `manage`/`proper-nouns` `TranslatedPreview` (which calls `api.previewUrl(docId, lang)`) is unchanged and renders without `data-seg`.
- Bands/figure overlays are counted in `build_segment_index_map` (to keep body indices aligned) but are not clickable in this pass.
