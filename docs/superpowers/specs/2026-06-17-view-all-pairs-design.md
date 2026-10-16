# View All Sentence Pairs (per file) — Design

**Date:** 2026-06-17
**Surfaces:** Style Library `/app/style` — single-file detection card + `BatchReviewTable` rows.
**Status:** Approved (design); pending implementation plan.

## Goal
Let a user open a **full, scrollable list of every 원문↔번역 pair** detected in a reference file
(before committing), to fully evaluate the detection — instead of only the ~4 sample pairs the
detection card / triage row currently shows.

## Context
`POST /api/style/references/analyze` returns `StyleAnalysis` with `sample_pairs` truncated to 4
(a cheap, embedding-free preview). `reference_intake.analyze` already computes **all** pairs for
`interleaved`/`columns`/`target_only`; `sequential`/`paired_files` only have **positional
estimates** before commit (real alignment needs embeddings at commit time). `source_only` has no
pairs. The browser already holds the `File` objects, so a full-pairs view can re-send the file
on demand without server-side temp storage.

## Locked decisions (approved)
1. **Backend:** a **dedicated endpoint** `POST /api/style/references/pairs` (not an `analyze`
   flag) — keeps the just-merged `analyze` response shape untouched.
2. **Frontend:** a **modal** (scrollable) opened by a **"전체 N쌍 보기"** button, lazily fetching
   on open.
3. **Both surfaces:** the button appears in the single-file detection card AND each batch table row.

## Backend
New route in `backend/main.py`: `POST /api/style/references/pairs`, owner-scoped like the existing
style routes, multipart form `target` (file, required), optional `source` (file), optional `mode`
(override). Reuses the existing helpers — stage the upload like `_stage_style_files`, then call
`reference_intake.classify` (honoring the `mode` override) and `reference_intake.extract_for_mode`
to get `(style_segments, pairs)`. **No embedding.** Response:
```json
{ "mode": "interleaved", "estimated": false, "count": 152,
  "pairs": [ { "source": "…", "target": "…" }, … ] }
```
- `interleaved`/`columns`/`target_only` → all real pairs (`estimated: false`). `target_only` has
  no pairs → `pairs: []` (style-only reference).
- `sequential`/`paired_files` → positional **estimated** pairs (`estimated: true`), matching how
  `analyze` previews them (no embedding pre-commit).
- `source_only` → `pairs: []`, `count: 0` (and the route returns 200, not an error — the caller
  just shows "번역 내용 없음"). Reuse `analyze`'s estimate path so counts line up.
- No artificial cap: one file at a time, returning all its pairs is acceptable (hundreds max).

## Frontend
- `frontend/src/lib/api.ts`: add `styleReferencePairs({ target, source?, mode? }) =>
  Promise<{ mode: StyleMode; estimated: boolean; count: number; pairs: {source;target}[] }>`
  (multipart POST, mirrors `analyzeStyleReference`). Add a `StylePairs` type.
- New component `frontend/src/app/app/style/PairListModal.tsx`: a fixed overlay with a scrollable
  body listing all pairs (원문 left / 번역 right, same styling as the existing sample-pair rows),
  a header (`전체 N쌍`), an **estimated** badge ("추정 — 정확한 짝은 커밋 시 확정") when
  `estimated`, a loading state while fetching, an error state with retry, and close (X / backdrop /
  Esc). Props: `{ file: File; source?: File | null; mode?: "" | StyleMode; onClose }` — it fetches
  on mount.
- Wire a **"전체 N쌍 보기"** button:
  - In `page.tsx` single-file detection card (next to the sample pairs), shown when
    `analysis.pair_count > 0`. Opens the modal for `target` (+ `source`, `modeOverride`).
  - In `BatchReviewTable.tsx` expanded row, shown when the row has pairs. The row passes its
    `file` + `modeOverride`; the table exposes an `onViewPairs(id)` callback so the page (which
    owns the modal state + file) opens it. (Keep the modal owned by the page, not the table, so
    only one modal renders.)
- Only one modal at a time (page-level `pairsModal` state: the file/source/mode to show, or null).

## Error handling
- Fetch failure → modal shows an error + retry; never blocks the page.
- `source_only` / zero pairs → the button isn't shown (gated on `pair_count > 0`), so the modal is
  only reachable for files that actually have pairs.

## Testing
- Backend pytest (`tests/test_style_api.py` or a new `test_pairs_endpoint`): interleaved file →
  `count` == analyze's `pair_count`, all pairs returned (not truncated); a Korean-only/source_only
  input → `pairs: []`; sequential → `estimated: true`. Use the existing `_txt` fixtures / TestClient.
- Frontend: `next build` typecheck + browser check (open modal from both surfaces; full list
  scrolls; estimated badge on a sequential file).

## Out of scope
- Editing pairs in the modal (read-only view). Real alignment for sequential/paired pre-commit
  (still estimated until commit). Pagination (return all; revisit only if a file is huge).
