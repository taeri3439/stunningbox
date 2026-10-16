# Translation consistency + TOC marker dedup — Design

**Date:** 2026-07-02  **Status:** Approved  **Branch:** `fidelity-followups-2026-07-02`
**Scope:** translator (deterministic dedup), reconciler (marker strip), docx renderer (multi-line TOC). Feature 4.

## Problems (evidence)

**(a) Same source, drifting translations.** The watergun plug-gauge table translates 통과/정지 as GO/NO-GO in some rows and PASS/STOP in others — identical source cell texts land in different 80-segment chunks and the LLM picks different renderings. Nothing enforces one-source-one-translation.

**(b) Multi-line TOC entries duplicate their number and lose the leader.** For a wrapped TOC entry the model carries BOTH `marker="2.2.1.2.2."` and `lines[0]="2.2.1.2.2. Profiling…"` (the analyzer strips the marker from `text`, but the raw span lines keep it). The docx renderer prepends `"{marker} "` before rendering `lines` → "2.2.1.2.2. 2.2.1.2.2. Profiling…". And because `_toc_split` runs only when `lines` is None, wrapped TOC entries also lose the dot leader + right-aligned page number.

## Approach

**(a) Translate unique texts once.** In `translator.translate_document`, dedupe the payload before the TM/LLM stage: build the unique-text list preserving first-seen order, run TM/LLM over the unique list, then fan results back out to every occurrence. Identical source segments now get identical translations *by construction* (fixes intra-table drift AND cross-page boilerplate drift), and repeated segments stop costing LLM tokens. `retranslate_one_segment` (single segment) is unaffected.

**(b1) Strip the marker prefix from preserved lines at reconcile.** The reconciler already drops a first recovered line that EQUALS the marker; extend it to strip a marker *prefix* (marker + whitespace) from the first line. Fixes the duplication for every renderer (docx + html preview).

**(b2) Reflow wrapped TOC entries through `_toc_split`.** In the docx list-item branch, when `lines` exist, first try `_toc_split(" ".join(lines))`; when it matches (dot leader + trailing page number), render the entry as a single leader row (existing `_render_toc`); otherwise keep the preserved-lines rendering. Wrapped TOC entries regain the leader + right page number and wrap naturally.

## Guardrails

- (a) changes no signatures; MockTranslator paths and TM reuse keep working (TM lookup now on unique texts — same results). Chunk-progress totals shrink with the payload (harmless).
- (b1) strips only an exact `marker + whitespace` prefix (a marker "2.2.1.2.2." never eats "2.2.1.2.20 …" — the boundary char must be whitespace).
- (b2) falls back to today's rendering whenever the joined text is not leader-shaped.
- Deterministic; no API. `iter_segments` untouched.

## Verification

- (a) unit: spy translator asserts it receives UNIQUE texts and every duplicate occurrence receives the same output; TM+dedup combined; MockTranslator regression.
- (b1) unit: reconcile a list_item whose recovered lines start with the marker → `lines[0]` has no marker prefix; whole-line-marker case still drops the line; near-miss ("2.2.1.2.20") untouched.
- (b2) unit: ListItemBlock with wrapped-TOC lines renders one paragraph containing a right tab + leader (assert via docx XML) and no duplicated number; non-TOC multi-line item renders lines as before.
- Full suite green (pre-existing protected-file failures excluded).
