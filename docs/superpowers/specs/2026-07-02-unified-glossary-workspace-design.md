# Unified 용어 Workspace (fold 고유명사 into 용어) — Design

**Date:** 2026-07-02
**Status:** Approved (design)
**Branch:** `unify-glossary-workspace-2026-07-02`
**Scope:** Frontend information-architecture refactor. Merge the separate **고유명사 (proper-noun review)** tool tab into the **용어 (glossary)** workspace so terminology control has a single entry point. **No backend / engine changes.**

## Problem

용어 and 고유명사 are presented as two co-equal top-level tools, but they operate on the **same object** (the document glossary) via the **same write action** (`saveGlossaryTerm` → retranslate/render):

- 고유명사's "원문 유지 / 번역어 지정" both call `api.saveGlossaryTerm(..., apply:true)` — identical to 용어's "+ 용어 추가".
- A term handled in 고유명사 then shows up in the 용어 list. So 고유명사 is really a **discovery feed into the glossary**, and 용어 is the **management/audit view of that glossary** — two ends of one workflow shown as peers.
- Navigation is doubled (top header tabs **and** right-panel sub-tabs surface the same destinations).

The engines are **not** redundant (deterministic glossary audit vs LLM proper-noun discovery); the redundancy is in the **surfacing (IA)**. This is a UX/IA consolidation, not a feature deletion.

Additionally, 고유명사 currently runs an **LLM pass automatically on every tab open** (cost + latency each time), which we want to make on-demand.

## Approach (chosen)

Collapse the proper-noun tool into a single **용어 workspace** with a vertical stack:

1. **Top section — "고유명사 자동 검출" (on-demand):** initially just a **[검출] button** (zero LLM calls on open). Pressing it lazily calls `api.properNounReport` and renders the existing candidate list. Each candidate keeps its current `원문 유지` / `번역어 지정` actions (unchanged `saveGlossaryTerm(apply:true)` → retranslate + re-render).
2. **Bottom section — "내 용어집" (unchanged):** the existing glossary manager (deterministic status audit, per-term edit, `+ 용어 추가`, `직접 교체`, location chips).

Handling a candidate folds it into the glossary → on the next detect/refresh it disappears from candidates and appears in the glossary list below — a natural "discover → manage" flow, now within one screen.

## Information architecture changes

- **Top tabs:** `문서 편집 · 재번역 · 용어` (remove the standalone **고유명사** tab).
- **Right-panel sub-tabs:** `재번역 · 용어` (remove **고유명사 검수**).
- **`mode` state** in [page.tsx](../../../frontend/src/app/c/[id]/page.tsx): `"edit" | "retranslate" | "manage" | "proper-nouns"` → drop **`"proper-nouns"`**. Proper-noun UI lives inside `"manage"`.
- The right pane label for `manage` reads **"용어 검수"** (unchanged string is fine).

## Components

### `GlossaryManagePanel` → the unified workspace
[frontend/src/components/GlossaryManagePanel.tsx](../../../frontend/src/components/GlossaryManagePanel.tsx)
- Add a **top section** rendering proper-noun candidates. State: `pnState: "idle" | "loading" | "done" | "error"`, `pnTerms: ProperNounReport[]`, `pnError`.
- `idle`: show heading **"고유명사 자동 검출"** + **[검출]** button + one-line hint. No LLM call.
- On click: `pnState="loading"` (`분석 중…`), call `api.properNounReport(docId, lang)`; on success `pnState="done"` + store terms (and call `onPnTermsLoaded` so the page can outline them on the original pane); on failure `pnState="error"` with an inline message that does **not** break the glossary section below.
- `done` with 0 terms: "번역된·용어집 외 고유명사가 없습니다." + a **[다시 검출]** affordance.
- Candidate rows reuse the existing per-candidate UI (source → translated, category · count, `번역어 지정` input, `원문 유지` / `번역어 지정` buttons, location chips). Applying a candidate calls the same `api.saveGlossaryTerm(..., apply:true)` and then `onChanged()` (refresh preview/editor/report). After an apply, **re-run detection** (or drop the applied candidate locally) so it doesn't linger.
- Below the candidates: a divider, then the **existing glossary manager UI unchanged**.
- Candidates are visually distinguished from glossary rows (e.g. a `✦ 제안` marker / accent) so the proposal state doesn't read like an applied/missed glossary status.

### `ProperNounReviewPanel` → presentational candidate section
[frontend/src/components/ProperNounReviewPanel.tsx](../../../frontend/src/components/ProperNounReviewPanel.tsx)
- Refactor the candidate list + row actions into a **presentational sub-component** consumed by `GlossaryManagePanel`'s top section (no self-owned tab, no auto-`useEffect` LLM fetch). It receives `terms`, `busy`, `onApply(source, target)`, `onJump`. The fetch/trigger/state lives in the parent (`GlossaryManagePanel`).
- The standalone panel + its auto-run `useEffect` are removed from the page's mode switch.

### Page wiring
[frontend/src/app/c/[id]/page.tsx](../../../frontend/src/app/c/[id]/page.tsx)
- Remove `"proper-nouns"` from `mode`; remove the **고유명사** top tab and the **고유명사 검수** sub-tab; remove `<ProperNounReviewPanel>` from the mode render.
- Keep the existing `pnReport` shared-up state used to outline proper-noun locations on the **original (left) pane**, but now it is populated via a callback from `GlossaryManagePanel` **after detection runs** (before detection: no proper-noun outlines, only glossary outlines). Glossary-term outlines behave as today.
- `requestMode` gate is unchanged: entering `manage` still force-saves so the deterministic glossary audit + preview are fresh. **Proper-noun detection stays off the entry path** (button-triggered), so opening 용어 incurs **no LLM call**.

## Data flow

- Open 용어 → force-save gate → `glossary-report` (deterministic, no LLM) renders the glossary section immediately.
- User clicks **검출** → `GET /api/documents/{id}/proper-nouns` (LLM pass + verify) → candidate section fills; page outlines their locations on the original pane.
- User handles a candidate → `saveGlossaryTerm(apply:true)` → retranslate + re-render → `onChanged()` refreshes preview/editor/glossary-report; candidate list re-detects or drops the handled item.

## Preserved (guardrails — must not change)

- Deterministic glossary audit [glossary_audit.py](../../../backend/pipeline/glossary_audit.py) and LLM+verify proper-noun engine [proper_noun_audit.py](../../../backend/pipeline/proper_noun_audit.py) — untouched.
- Apply mechanism (`saveGlossaryTerm` → `update_glossary_term` / `add_glossary_term` + `apply_glossary_term`) — untouched.
- Backend endpoints `glossary-report`, `proper-nouns` — untouched (only the frontend call timing changes to on-demand).
- The instant-reflect (force-save) gate on view transitions — untouched.
- `직접 교체 (임의 단어)` (BulkReplacePanel) — kept, still collapsed at the bottom.

## Edge cases & states

- Detection **not run** (idle): glossary section fully usable; no proper-noun outlines on the left pane.
- Detection **loading**: `분석 중…`; glossary section stays interactive.
- Detection **error**: inline error in the candidate section only; glossary section unaffected.
- Detection **0 candidates**: explicit empty message + re-detect affordance.
- Candidate **applied**: folds into glossary; removed from candidate list; appears in glossary list on report refresh.
- `lang` / `docId` change or `reloadKey` bump: glossary report re-fetches as today; proper-noun candidates **reset to idle** (do not auto-re-run the LLM).

## Testing

**Frontend (build gate — no FE unit harness):**
- `npm run build` clean (types + lint).

**Live (browser, prod build) — the acceptance pass:**
1. Open 용어 → glossary section renders immediately, **no** `proper-nouns` network call fired on open (verify via backend log / devtools).
2. Click **검출** → `GET /proper-nouns` fires once; candidates render with `✦ 제안` styling; original pane outlines their locations.
3. Handle one candidate (`번역어 지정`) → applies, re-render + editor reflect (round-trip), candidate leaves the list and the term appears in the glossary list below.
4. Confirm **고유명사** no longer exists as a top tab or sub-tab; only `문서 편집 · 재번역 · 용어` remain.
5. `직접 교체` still works; existing glossary edit/add/status/location-chip behavior unchanged.
6. Regression: 재번역 and 문서 편집 sync (both directions) still pass.

## Risk

Low — frontend-only IA refactor; engines, endpoints, and the apply path are untouched. Main risk is wiring regressions (mode/tab removal, shared `pnReport` outline timing). Fallback: revert the frontend changes; backend is unaffected.
