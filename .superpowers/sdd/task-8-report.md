# Task 8 Report — Stable-ID migration (seg-id / block_id)

## Files touched

### Backend
- `backend/pipeline/glossary_audit.py` — `seg.get("block")` → `seg["block_id"]`; location dict key `"block"` → `"block_id"`
- `backend/pipeline/proper_noun_audit.py` — same in `verify()`; location dict key `"block"` → `"block_id"`
- `backend/tests/test_glossary_audit.py` — `_seg()` helper: `block=` kwarg → `block_id=`; test assertions `l["block"]` → `l["block_id"]`; segment dict values changed to strings ("b0", "b1", etc.)
- `backend/tests/test_proper_noun_audit.py` — same; assertion on location dict updated to `"block_id": "b3"`
- `backend/tests/test_proper_noun_report.py` — monkeypatched `build_segments` dicts: `"block": 1` → `"block_id": "b1"`, `"block": None` → `"block_id": None`

### Frontend
- `frontend/src/lib/types.ts` — `Segment`: removed `block: number | null`, added `id: string` + `block_id: string`; `RetranslateResponse`: `index: number` → `id: string`; `GlossaryLocation`: `block: number | null` → `block_id: string | null`
- `frontend/src/lib/api.ts` — `retranslateSegment` body type: `index: number` → `id: string`
- `frontend/src/components/SegmentPreview.tsx` — props `activeSeg: number | null` → `string | null`; `onSelectSeg: (index: number)` → `(id: string)`; `jumpBlock?: number | null` → `string | null`; removed `Number(t.dataset.seg)` → `t.dataset.seg` (string)
- `frontend/src/components/RetranslatePanel.tsx` — preview state `{ index, text }` → `{ id, text }`; `prevIndexRef` tracks `seg.id` (string) not `seg.index`; `retranslateSegment` body `index: seg.index` → `id: seg.id`; `seg.block` → `seg.block_id`; list key/comparison `seg.index` → `seg.id`; preview guard `preview?.index` → `preview?.id`
- `frontend/src/components/TranslatedPreview.tsx` — prop `jumpBlock: number | null` → `string | null`
- `frontend/src/app/c/[id]/page.tsx` — `jumpBlock` state `number | null` → `string | null`; both `SegmentPreview` instances: `activeSeg` → `activeSeg?.id`, `onSelectSeg` → match by `s.id`, `jumpBlock` → `activeSeg?.block_id`; PDF bbox hotspot `isActive` → `activeSeg?.id === s.id`; `loc.block` → `loc.block_id` in both `proper-nouns` and `manage` `onJump` handlers

## Straggler notes
- `TranslatedPreview.tsx` was not in the original change list but needed updating because `jumpBlock` flows from `setJumpBlock` (now `string`) — caught via TS build.
- `onSegmentText(index: number, text)` callback kept as-is: `index` is still present on `Segment` (as display ordinal), and updating the segment list by ordinal is correct/safe.

## Verification
- Backend: `pytest -q` → 15 failed (all pre-existing `test_auth`/`test_documents_store`), 424 passed, 1 skipped
- Frontend: `npm run build` → compiled successfully, TypeScript clean, 13 static/dynamic routes
