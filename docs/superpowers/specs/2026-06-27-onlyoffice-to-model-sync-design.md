# OnlyOffice → Model Reverse-Sync (bidirectional editor/HTML coherence) — Design

**Date:** 2026-06-27
**Status:** Approved (design); pending implementation plan

## Goal

Make edits a user makes in the **문서 편집** OnlyOffice editor (text, formatting, AND structure) flow back into the SemanticDocument model, so the HTML model-based views (재번역 / 용어 관리 / 고유명사) reflect them — and so a later HTML-mode re-render no longer clobbers editor edits. Full bidirectional coherence.

## Motivation

Today the OnlyOffice save callback (`backend/main.py:632`) only overwrites `output.{lang}.docx`; it never touches the model. `save_edited_model` (writes `edited.{lang}.json`, which `get_export_model` prefers) has **no production caller**, so `get_export_model` always falls back to `translated.{lang}.json`. Consequences (confirmed by code trace):
- HTML-mode changes (재번역/용어/고유명사) DO reach the editor (they update the model + re-render the docx).
- **Editor edits do NOT reach the HTML views** (the views render from the model; the model never sees the edit).
- **Data-loss edge:** editing in OnlyOffice then triggering a full re-render from a HTML mode regenerates the docx from the model → editor edits lost.

The user edits text, formatting, and structure in the editor, and wants all of it reflected. See [[onlyoffice-integration]] for the sync asymmetry note.

## The core constraint (why this is hard)

The system assumes the **source model and translated model are structurally parallel** — paired strictly by positional index over `iter_segments`/`iter_text_fields`. `build_segments` (`process.py:450-460`) zips `iter_segments(source)[i]` with `iter_text_fields(translated)[i]`; `retranslate_one_segment` writes by the same index and raises `IndexError` if the two diverge; glossary/proper-noun reports and the `data-seg` map (`build_segment_index_map`) inherit this. There is **no model-to-model alignment component** (`reconciler.py` is a PDF-ingest stage, not a re-aligner). A structural editor edit (add/remove/reorder block) breaks this parallelism, and a blanket `extract_docx` re-parse is unsafe: `render_docx → extract_docx` is **not** an identity (it loses `source_span_ids`, collapses `.lines`, folds list markers into text, drops dividers/figure-overlays, changes segment counts), so it would corrupt the model on the first save of even an *unedited* doc.

## Key insight: `blk{i}` bookmarks are a durable source-linkage

`render_docx` stamps a `blk{i}` bookmark per block (`docx_renderer.py:_bookmark_block`, `i` = index into `doc.blocks`). Standard Word/OnlyOffice behavior preserves bookmarks across edits: editing text inside a bookmarked block keeps its bookmark; deleting the whole block removes it; a newly-added block has none. Therefore, reading the bookmark ids out of the edited docx tells us, **deterministically and without fuzzy text matching**:
- surviving `blk{i}` → this edited block maps to original/source block `i` (even if reordered);
- block with no bookmark → user-**inserted** (no source counterpart);
- a `blk{i}` absent from the saved docx → source block `i` was **deleted**.

This collapses the scary "re-align after structural edits" problem into "read bookmark ids." **The entire design hinges on bookmark survival**, which is therefore the first thing validated (below); a text-similarity fallback covers blocks where the bookmark is missing/ambiguous.

## ⚠️ Linchpin assumption — validate FIRST

**Does OnlyOffice/DS preserve `blk{i}` bookmarks through an edit→save round-trip?** Phase 1 begins with a spike: render a doc, open it in the DS editor, perform a text edit + a paragraph insert + a paragraph delete + a reorder, trigger save, and inspect the saved `output.docx` for the bookmark set (existing preserved? insert has none? deleted one gone?). If preservation is incomplete, augment with the **hybrid fallback**: bookmarks are primary; for blocks whose bookmark is missing/duplicated, fall back to text-similarity alignment (sequence alignment of edited block texts against the prior model's block texts). The architecture below is unchanged either way — only the source of the linkage differs.

## Architecture

### Reframing that shrinks the blast radius
Once `edited.{lang}.json` exists, the **edited model becomes the single model** that `get_export_model` returns — so `render_html` (preview/source-preview), the `data-seg` map, and the `build_segments` list are all positional **over the edited model** and stay mutually consistent automatically. The source model is needed only to supply, per edited segment: the **original source text** (source pane / retranslate input / audits) and the **`source_bbox`** (PDF hotspots). So the only thing that must change from "positional parallelism" to "linkage-based" is the **edited↔source segment mapping** that those three consumers use — not the rendering/`data-seg` path.

### Components

1. **Bookmark-aware docx parser** — `backend/pipeline/docx_sync.py` (new). Walks the edited `output.{lang}.docx` in document order and produces a `SemanticDocument` (the edited model) PLUS a per-block `source_block: int | None` (the `blk{i}` id, or `None` for inserted). Reuses `extract_docx`'s block-type detection (heading/list/caption/paragraph/table/figure) and text reading, but additionally reads the `w:bookmarkStart @w:name="blk{i}"` covering each block. Handles: multi-line `.lines` (split a paragraph on `<w:br/>` runs), list/heading markers (strip the known marker prefix carried from the source block so it isn't doubled on re-render), table cells (positional within the table block).

2. **`align_edited_to_source(edited, edited_block_links, source)` → `list[int | None]`** — `backend/pipeline/docx_sync.py`. Maps each edited *segment* (in `iter_segments(edited)` order) to a source *segment* index (in `iter_segments(source)` order), or `None` if inserted. Deterministic from the block links + sub-position (segment j within block → segment j of the linked source block, when sub-counts match); the text-similarity fallback fills gaps. This is the new alignment that replaces positional parallelism.

3. **Save-callback integration** — `backend/main.py` `onlyoffice_callback`: after overwriting `output.docx` (status 2/6), parse it via the bookmark-aware parser, build the edited model + links, run `align_edited_to_source`, persist the edited model to `edited.{lang}.json` (via `save_edited_model`) and the segment→source mapping alongside it (e.g. `edited.{lang}.map.json`). `get_export_model` already prefers `edited.json`, so the HTML views reflect the edit on next load.

4. **Linkage-based pairing** — rework so source lookups use the mapping instead of position:
   - `build_segments`: iterate `iter_segments(edited)`; for edited segment k → `src_idx = mapping[k]`; emit `{index: k, translated: edited_text, source: source_seg(src_idx) or "", source_bbox: bbox(src_idx) or None, block: edited_block_index, inserted: src_idx is None}`.
   - `retranslate_one_segment`: operate on the edited model; translate from the linked source; **inserted segments (no source) are not re-translatable** — the API returns a clear status and the UI disables 재번역 for them (free-edit only).
   - `glossary_report` / `proper_noun_report`: pair via the mapping; inserted segments excluded (no source to audit).
   - When no `edited.json` exists (editor never used), the mapping is the identity and all of the above behave exactly as today.

5. **Formatting sync (Phase 3)** — the parser reads block/run properties into the edited model's style fields. **Directly syncable** (renderer does not transform them): `bold`, `italic`, `underline`, `align`, `color`. **Transform-sensitive**: `font_size` is scaled to `body_pt` and `line_spacing` is divided by 1.15 / capped on render (`docx_renderer.py`), so naive read-back + re-render compounds. Mitigation: invert the known transform on read-back using the same `body_pt`/scale the renderer used (or, if inversion proves unstable in the spike, store the read-back size as an absolute and add a renderer flag to skip re-scaling already-absolute sizes). The frontend HTML preview already renders these style fields, so synced formatting shows immediately.

### Data flow (one editor edit)
1. User edits in OnlyOffice → DS posts status 2/6 → callback overwrites `output.{lang}.docx`.
2. Callback parses the docx (bookmark-aware) → edited model + block links → `align_edited_to_source` → writes `edited.{lang}.json` + mapping.
3. User opens 재번역/용어/고유명사 → `get_export_model` returns the edited model → `render_html`/`build_segments` reflect the edit; source text/bbox come via the mapping.
4. A subsequent HTML-mode change re-renders `output.docx` from the **edited** model → editor edits preserved (no data loss).

## Phases (each a working increment)
- **Phase 1 — Linchpin spike + text sync.** Validate bookmark survival (spike). Build the bookmark-aware parser for the text of existing blocks + the identity-preserving path (unedited save ⇒ model unchanged). Persist `edited.json`. Verify HTML views reflect a text edit + the data-loss case is fixed. (Solves the common case A; de-risks everything.)
- **Phase 2 — Structure + linkage-based pairing.** `align_edited_to_source` (insert/delete/reorder via bookmark links + fallback); rework `build_segments`/retranslate/reports to be linkage-based; handle inserted (translation-only) + deleted segments end-to-end (incl. UI affordances for inserted segments). (Solves C.)
- **Phase 3 — Formatting sync.** Read-back bold/italic/underline/align/color directly; handle size/line-spacing via inversion (or absolute-flag). (Solves B.)

## Error handling / edge cases
- **No `edited.json`** (editor never used) → mapping is identity → current behavior unchanged.
- **Full re-translate / language switch** must define precedence vs `edited.json`: a fresh full translation writes `translated.{lang}.json`; `edited.json` (the user's authoritative edits) still wins in `get_export_model`. Decision: a full (re)translate of a lang **clears that lang's `edited.json`** (the user is regenerating from scratch); document this so edits aren't silently shadowed by a stale edited model. Per-lang, so other langs are untouched.
- **Within-block structural change** (added/removed line in a `.lines` block; added/removed table cell) — sub-position alignment can't always be exact; degrade to best-effort + flag via the fallback rather than corrupt indices.
- **Inserted segment** → `source=""`, `inserted: true`; excluded from retranslate (no source) + audits; shown in views; editable text.
- **Bookmark unreliable** (spike fails) → hybrid: text-similarity fallback for missing/ambiguous links.
- **Parser failure** on a save → log + keep the prior `edited.json` (don't corrupt); the docx is still saved (editor unaffected).

## Testing
- **Spike** (Phase 1): a real DS edit→save→bookmark-inspection (documented, may be manual/scripted) — recorded result drives whether the fallback is needed.
- **Backend (pytest):** bookmark-aware parser on fixtures (render_docx → simulate edits by manipulating the docx XML: edit text in a `blk{i}`, drop a bookmark = delete, add an unbookmarked paragraph = insert, reorder) → assert the edited model + `source_block` links; `align_edited_to_source` on insert/delete/reorder/within-block-line-change fixtures → assert the segment→source mapping; identity case (unedited render→parse) ⇒ model + identity mapping unchanged (the safety property that a full re-extract failed); `build_segments`/retranslate/reports against an edited model with inserts/deletes; formatting read-back (bold/align/color exact; size inversion round-trips). Reuse `MockTranslator`.
- **Frontend:** `tsc`/`eslint` + browser: editor text edit → reflected in 재번역/용어/고유명사; an inserted paragraph shows in the views with 재번역 disabled; a re-render after an editor edit preserves the edit.

## Critical files
- New: `backend/pipeline/docx_sync.py` (bookmark-aware parser + `align_edited_to_source`).
- Modify: `backend/main.py` (`onlyoffice_callback` integration), `backend/pipeline/process.py` (`build_segments`, `retranslate_one_segment`, glossary/proper-noun reports → linkage-based; persist mapping), `backend/renderers/docx_renderer.py` (Phase 3 size-flag if needed), the frontend `/c` page + `RetranslatePanel` (inserted-segment affordance).
- Reuse: `extract_docx` detection logic, `save_edited_model`/`edited_path`, `get_export_model`, `iter_segments`/`iter_text_fields`, `align.py` (sequence-alignment reference for the fallback), `MockTranslator`.

## Out of scope
- Syncing in-figure overlay text edits (overlays are baked into a PNG, not docx-editable).
- Real-time/live sync while editing (sync happens on DS save: disconnect / forcesave).
- Re-architecting the source model (it stays the immutable original; only the edited *translated* model gains insert/delete/reorder relative to it).
