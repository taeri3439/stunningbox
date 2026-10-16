# Font Style Fidelity: Marker Colour + Heading Weight — Design

**Status:** diagnosed against the real vet report (source PDF spans + rendered docx). Scope locked by the user: **marker colour + heading bold** (not full per-span colour).

## Problem (evidence-based)

Two style-hierarchy losses confirmed on the veterinary report:

1. **Intra-line colour split is flattened to one colour.** A figure/table title line mixes two source colours — an accent-coloured label prefix and a differently-coloured title. Real spans on the FIGURE 3 heading (source.pdf page index 5):
   - `"FIGURE 3"` → font `FranklinGothic-DemiCond`, colour **`D4B031`** (gold)
   - `"BREAKDOWN OF PRIMARY INTERVIEWS: …"` → font `FranklinGothic-MediumCon`, colour **`0D5775`** (teal)

   The model stores **one `color` per block** (the dominant, `_dominant_inline` → teal), and the renderer builds the heading as a **single run** `f"{marker} {text}"` ([docx_renderer.py:211](../../backend/renderers/docx_renderer.py)) coloured uniformly ([docx_renderer.py:450](../../backend/renderers/docx_renderer.py)). The gold `FIGURE 3` is lost → renders teal. Section numbers (`2.1.2.1`/`2.1.2.2`) are the **same** colour as their titles (`007C9E`), so they're unaffected — only accent-coloured `FIGURE N`/`TABLE N` labels lose their colour.

2. **Heading weight is not captured.** All 29 headings capture `bold=False`. Source headings use `FranklinGothic-MediumCon` / `-DemiCond` (Medium/Demi weight — heavier than body `FranklinGothic-Book`), but PyMuPDF reports `flags=0` (no bold bit) and the name lacks the literal `"bold"`, so `extractor.py:46`'s `bool(flags & _BOLD_FLAG) or "bold" in font.lower()` → False. The weight tier of the hierarchy is flattened; every heading renders at regular weight.

Figure-INTERNAL text is unaffected (per-overlay colour is captured and preserved).

## Locked scope

- **Marker colour:** capture the marker's own source colour and render the marker in it when it differs from the block colour. Covers the `FIGURE N`/`TABLE N` accent-label case exactly. NOT general per-span colour (that's a larger analyzer change, explicitly out of scope).
- **Heading weight:** recover heading bold from the font-name weight class (Medium/Demi/Semibold/Bold/Black/Heavy) for HEADING blocks only, so body text is untouched.

## Design

### 1. Model (`backend/models.py`)
Add `marker_color: Optional[str] = None` to `HeadingBlock` (hex `RRGGBB`; None → marker renders in the block colour, i.e. today's behaviour). Only `HeadingBlock` — `ListItemBlock` markers (TOC/list numbers) are the same colour as their text in this template and are out of scope.

### 2. Capture — marker colour (`backend/pipeline/reconciler.py`)
New helper `_marker_color(ids, by_id, marker) -> str | None`: from the block's source spans in reading order (sort by `(y-band, x)` using `RawSpan.bbox`), accumulate leading text until it covers the marker string; if that leading text starts with the marker, return the dominant non-ink colour of those leading spans (`_is_ink`-filtered, `Counter.most_common`), else None. In the heading branch (reconciler.py ~549-567), compute `mcol = _marker_color(ids, by_id, b.get("marker"))` and pass `marker_color = mcol if (mcol and mcol != color) else None` to `HeadingBlock`. (Same-colour markers → None → no separate run, no churn.)

### 3. Capture — heading weight (`backend/pipeline/reconciler.py`, `_dominant_style`)
`_dominant_style(ids, by_id, heading=True)` already runs for headings. After the existing flag-based `bold`, add: if `heading and not bold`, and a majority of the block's span font names match a heavy-weight token (`bold|semibold|demibold|demi|medium|black|heavy`), set `bold = True`. Regex `_HEAVY_WEIGHT_RE` module-level. Body text (`heading=False`) keeps the current flag/`"bold"`-name behaviour — no change, so a "Medium" body font is not affected.

### 4. Render — marker run (`backend/renderers/docx_renderer.py`, HeadingBlock branch)
When `block.marker` and `block.marker_color` are set: build the heading paragraph and add the marker (`f"{block.marker} "`) and the title (`block.text`) as **two separate runs**; after `_apply_style` (which sizes/weights/fonts/colours all runs with the block colour), override the marker run's colour to `block.marker_color`. When `marker_color` is None, keep the current single-string path unchanged. Both runs stay in one paragraph, so `docx_sync.read_block_lines` / `_strip_marker` round-trips identically (concatenated text `"{marker} {title}"` → strip marker → title). Page-parity `_mark_break`, parity heading spacing, and `_bookmark_block` apply exactly as today (the branch still returns the one heading paragraph).

## Testing

**Deterministic (per task, TDD):**
- `_marker_color`: two-colour leading marker → returns the marker's colour; single-colour block → returns None; marker text absent from leading spans → None; ink-only marker → None.
- reconciler heading integration: a heading whose marker span colour differs from the title → `HeadingBlock.marker_color` set; same-colour → None.
- `_dominant_style` weight: heading spans named `*-MediumCon`/`*-DemiCond` with no bold flag → `bold=True`; body (`heading=False`) with same names → unchanged; genuinely regular heading (`*-Book`) → not forced bold.
- renderer: a `HeadingBlock(marker="FIGURE 3", marker_color="D4B031", color="0D5775")` renders **two runs**, marker run colour `D4B031`, title run colour `0D5775`; a heading with `marker_color=None` renders one run (unchanged); round-trip through `docx_sync` yields the original title text.

**REAL-SCREEN verification (the standing bar):** rebuild the vet source model (cached analysis, no API), render EN→KO, open in the browser; confirm on-screen that `FIGURE 3` renders **gold** while its title stays **teal**, and that headings render **bold** (heavier than body) — matching the original. Screenshot the FIGURE 3 heading before/after. Also confirm no regression: section-number markers (same colour as title) render as one visual colour, and body text weight is unchanged.

**Regression:** existing renderer/reconciler/docx_sync suites stay green; `marker_color` defaults None so old cached models and ko→en are unaffected; the heading round-trip identity test in `test_docx_sync_*` still passes.

## Risks / decisions

- **`add_heading("")` + manual runs:** verify python-docx leaves no stray empty run that would mis-colour; if it does, drop it. (Implementation detail; the plan pins it.)
- **"medium" as a bold trigger for headings:** intentional — these headings are Medium/Demi weight and must read heavier than body; the substitute font (NanumGothic) is binary-weight, so bold is the faithful approximation. Scoped to headings only to avoid bolding a hypothetical Medium body font.
- **Re-capture required:** `marker_color`/recovered bold only populate on a fresh reconcile (`rebuild_source_model`, cached-analysis, no API). Existing translated caches show the fix after a rebuild/re-render — expected, same as prior analysis-time fidelity fixes.
- **Marker not independently editable in the editor:** the marker run isn't a translated segment (markers are structural). Acceptable — markers aren't translated content.
