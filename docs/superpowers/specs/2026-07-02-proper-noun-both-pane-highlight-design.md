# Proper-noun click → highlight in both panes — Design

**Date:** 2026-07-02
**Status:** Approved (design + scope)
**Branch:** `unify-glossary-workspace-2026-07-02` (extends the unified 용어 workspace — same branch)
**Scope:** Frontend-only. Clicking a 고유명사 candidate highlights that term in **both** the 원본 (source) and 번역본 (translation) panes. **No backend changes.**

## Problem

In the unified 용어 workspace, a detected proper-noun candidate has data for both panes (`source`, `translated`/translated_form, per-location `block_id` + `source_bbox`), but clicking it only *scrolls*. There is no cross-pane highlight tying the source term to its translated form, so the user can't visually locate a proper noun on both sides at once.

## Grounded constraints (from code investigation)

- **번역본 pane is always flat HTML** (server `/preview`, same-origin via `/api` proxy). Its `contentDocument` is scriptable → we can inject a **word-level** highlight of the translated form with no re-fetch (mirrors `SegmentPreview.applyActive`). Glossary terms already highlight there as `<mark class="gloss">` (amber). The proper-noun highlight must use a **distinct** color (indigo) to avoid confusion.
- **원본 pane, PDF**: page images with absolute-positioned overlay boxes from `source_bbox`, which is **segment/sentence-level, not word-level** (word coordinates do not exist in the data). So the source side can strongly emphasize the **sentence box** containing the term (dimming the others) — not the individual word. The proper-noun overlays already render but are `pointer-events-none` and undifferentiated.
- **원본 pane, HWP/office** (in 용어 mode): a **cross-origin static iframe** (`doc.source_html`) with no coordinates → source-side highlight is **out of scope** (chosen: frontend-only). The translation-side highlight still works for these docs (번역본 is flat HTML regardless).

## Approach

Lift an `activePnTerm: ProperNounReport | null` to the page. Clicking a candidate (its header row) **or** its source-pane box toggles it active. Both panes react to `activePnTerm`:

1. **번역본 (all doc types):** `TranslatedPreview` gains a `highlightTerm` prop (= the active term's `translated`). On change (after the iframe is loaded), it unwraps any prior `<mark class="pn-active">`, then walks text nodes and wraps case-insensitive occurrences of the term in `<mark class="pn-active">` (indigo), and scrolls the first into view. No re-fetch.
2. **원본 (PDF):** the proper-noun overlay boxes become interactive `<button>`s. The active term's box(es) render **solid/strong** (`--primary-soft` fill + `2px --primary` outline, full opacity) while the other proper-noun boxes **dim** (opacity ~0.3). The page scrolls the source pane to the active term's first located page.
3. **Panel:** the active candidate row gets a selected ring; clicking it again clears the selection (both panes un-highlight).

The existing per-occurrence `#N` chips keep their jump behavior. All data is already client-side (`pnReport`); no backend or new API call.

## Components

### `TranslatedPreview` — inject the active-term highlight
[frontend/src/components/TranslatedPreview.tsx](../../../frontend/src/components/TranslatedPreview.tsx): add optional prop `highlightTerm?: string | null`; track a `loaded` gate (set false on `reloadKey` change, true `onLoad`); an `applyHighlight()` that unwraps prior `mark.pn-active`, injects a one-time `<style id="pn-active-style">`, TreeWalk-wraps occurrences, and scrolls the first mark into view; run it in a `useEffect([highlightTerm, loaded])`.

### `ProperNounCandidates` — active row + activation
[frontend/src/components/ProperNounCandidates.tsx](../../../frontend/src/components/ProperNounCandidates.tsx): add optional props `activeSource?: string | null` and `onActivate?: (term: ProperNounReport) => void`. The header row (source→translated line) becomes clickable (`cursor-pointer`, calls `onActivate(t)`); the `<li>` shows a selected ring when `t.source === activeSource`. The 처리 inputs/buttons and #N chips are unaffected (separate rows; header click doesn't reach them).

### `GlossaryManagePanel` — thread props through
[frontend/src/components/GlossaryManagePanel.tsx](../../../frontend/src/components/GlossaryManagePanel.tsx): add optional props `activePnSource?: string | null` and `onActivatePn?: (term: ProperNounReport | null) => void`; pass them to `ProperNounCandidates` as `activeSource` / `onActivate`.

### `page.tsx` — state, source overlays, wiring
[frontend/src/app/c/[id]/page.tsx](../../../frontend/src/app/c/[id]/page.tsx):
- `const [activePnTerm, setActivePnTerm] = useState<ProperNounReport | null>(null)`; a `togglePn(term)` (toggle by source); a clear-on-fold effect (`activePnTerm` no longer in `pnReport` → null); a scroll-on-activate effect (scroll source pane to the active term's first located page).
- Convert the proper-noun overlay `<div>` (currently `pointer-events-none`) to a `<button>` with `onClick={() => togglePn(t)}` and active/dim styling based on `activePnTerm?.source`.
- Pass `highlightTerm={activePnTerm?.translated ?? null}` to `TranslatedPreview`; pass `activePnSource` + `onActivatePn={togglePn}` to `GlossaryManagePanel`.

## Edge cases

- **Fold/apply:** applying a candidate removes it from `pnReport`; the clear-on-fold effect nulls `activePnTerm`; `reloadKey` bump reloads the preview and the highlight effect clears (highlightTerm → null). No stale highlight.
- **Toggle off:** clicking the active row/box again clears both panes.
- **HWP/office:** source overlays don't exist there (no bbox), so only the 번역본 highlight applies — acceptable per scope.
- **No occurrence in preview text:** if the translated form isn't found as text (e.g. differing casing beyond case-fold, or it lives only in a table cell already wrapped), nothing is marked — harmless; the source box + scroll still work.
- **Overlap with glossary mark:** proper nouns aren't glossary values, so overlap is rare; unwrap-before-rewrap prevents nesting on re-runs.

## Testing

**Frontend:** `npm run build` clean per task.

**Live (browser, prod build) — acceptance:** on a PDF doc (test-watergun), 용어 → 검출 → click a candidate (e.g. 방수총) → (a) its translated form (Water Cannon) is highlighted indigo in every 번역본 occurrence + scrolled into view; (b) its source sentence box(es) light up strong on the 원본 pane while other boxes dim + scrolled into view; (c) the panel row shows a selected ring. Click again → both clear. Confirm the amber glossary highlight is visually distinct. Confirm no `/preview` re-fetch fires on click (client-side injection). Regression: 재번역 + 문서 편집 unaffected.

## Risk

Low — additive frontend-only. The one non-trivial piece is the TreeWalker DOM injection (collect target text nodes first, then mutate; unwrap prior marks before re-wrapping). Fallback: revert the frontend changes; backend untouched.
